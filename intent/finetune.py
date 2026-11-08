"""D-07-03: 승인된 의도 예시의 계보 분할·CPU KoELECTRA 학습·동일 평가 비교."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import random
import time
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from contracts.datasets import LabelSource, Split
from intent.baseline import IntentBaseline, load_examples, split_examples
from intent.evaluation import evaluate
from intent.matcher import normalize_people
from intent.parser import BaselineIntentParser
from tools.evidence_result import write_result
from tools.gitrepo import GitRepo
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "monologg/koelectra-base-v3-discriminator"
REVISION = "68b30cd259f34a4b5aa8786392612ba2a2617fcc"
TOKENIZER_VERSION = f"{MODEL_ID}@{REVISION}"


def prepare_split(rows, seed=7, *, include_generated=False):
    """학습용 예시를 계보·대조쌍으로 나눈다. 명시한 개발 실험은 검수 전 변형도 포함한다."""
    allowed = {LabelSource.AUTHORED, LabelSource.GENERATED_REVIEWED, LabelSource.GENERATED}
    if any(r.label_source not in allowed or r.split is not Split.TRAIN for r in rows):
        raise ValueError("expected train rows from the intent example dataset; evaluation rows are forbidden")
    approved = [r for r in rows if include_generated or r.label_source is not LabelSource.GENERATED]
    train, dev = split_examples(approved, seed)
    return train, [replace(row, split=Split.DEV) for row in dev]


def select_label(probabilities, threshold):
    """기준선과 같은 신뢰도 하한을 적용하며 NaN·무한대 출력을 거부한다."""
    if not 0 <= threshold <= 1 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
        raise ValueError("invalid threshold or probabilities")
    if not probabilities:
        return "unsupported", 0.
    label = max(probabilities, key=probabilities.get)
    score = probabilities[label]
    return (label if score >= threshold else "unsupported"), score


def cpu_usage(cpu_seconds, wall_seconds, logical_cpus):
    """프로세스 CPU 사용률(코어 합)과 전체 논리 CPU 수로 정규화한 점유율을 구분한다."""
    if cpu_seconds < 0 or wall_seconds <= 0 or logical_cpus < 1:
        raise ValueError("positive wall time and logical CPU count are required")
    process = cpu_seconds / wall_seconds * 100
    return {"process_cpu_percent": process, "machine_cpu_percent": process / logical_cpus}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BertClassifier:
    """공유 슬롯 파서에 주입하는 평가용 분류기. 기본 운영 해석기는 바꾸지 않는다."""

    def __init__(self, model, tokenizer, labels, model_version, length=64, threshold=.20):
        self.model, self.tokenizer, self.labels = model.eval(), tokenizer, labels
        self.model_version, self.length, self.threshold = model_version, length, threshold
        self.tokenizer_version = TOKENIZER_VERSION

    def probabilities(self, text):
        """정규화된 문장 하나의 10라벨 확률을 CPU에서 반환한다."""
        import torch
        inputs = self.tokenizer(normalize_people(text, {}), return_tensors="pt", truncation=True,
                                padding="max_length", max_length=self.length)
        with torch.inference_mode():
            probabilities = self.model(**inputs).logits.softmax(-1)[0].tolist()
        return dict(zip(self.labels, map(float, probabilities)))

    def predict(self, text):
        """기준선과 같은 임계값으로 분류한다. 임계값을 평가 결과에 맞춰 조정하지 않는다."""
        return select_label(self.probabilities(text), self.threshold)


def main(argv=None) -> int:
    """선택한 사전학습 모델 전체를 CPU로 미세조정하고 결과·모델·계보 기록을 저장한다."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="content/intent/examples_v1.jsonl")
    ap.add_argument("--cache", default=str(ROOT / "out/hf_cache"))
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--length", type=int, default=64)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--include-generated", action="store_true",
                    help="명시적 개발 실험에 검수 전 generated 행도 포함한다")
    args = ap.parse_args(argv)
    if min(args.epochs, args.batch, args.threads) < 1 or args.length < 8 or args.seed < 0:
        ap.error("epochs/batch/threads >= 1, length >= 8, seed >= 0")
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    data_path = Path(args.data)
    rows = load_examples(data_path)
    train, dev = prepare_split(rows, args.seed, include_generated=args.include_generated)
    generated_rows = sum(row.label_source is LabelSource.GENERATED for row in rows)
    unreviewed_excluded = 0 if args.include_generated else generated_rows
    unreviewed_included = generated_rows - unreviewed_excluded
    labels = sorted({row.label for row in train})
    if len(labels) != 10:
        raise ValueError("expected the ten intent labels")
    label2id = {label: i for i, label in enumerate(labels)}
    run_dir = ROOT / "mlruns/intent_bert" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    log("intent_finetune", "info", "분할 완료", train_rows=len(train), dev_rows=len(dev), seed=args.seed,
        unreviewed_excluded=unreviewed_excluded, unreviewed_included=unreviewed_included,
        model=MODEL_ID, revision=REVISION)
    baseline = IntentBaseline().fit(train)
    baseline_report = evaluate(BaselineIntentParser(baseline), dev)
    baseline.save(run_dir / "baseline.joblib")
    log("intent_finetune", "info", "기준선 평가 완료", **{
        key: item["value"] for key, item in baseline_report["metrics"].items()})
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, cache_dir=args.cache,
        token=False, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID, revision=REVISION,
        cache_dir=args.cache, token=False, local_files_only=True, trust_remote_code=False,
        num_labels=len(labels), id2label=dict(enumerate(labels)), label2id=label2id,
        dtype=torch.float32, weights_only=True, use_safetensors=False).to("cpu")
    load_s = time.perf_counter() - started
    started = time.perf_counter()
    inputs = tokenizer([normalize_people(row.text, {}) for row in train], return_tensors="pt",
                       padding="max_length", truncation=True, max_length=args.length)
    targets = torch.tensor([label2id[row.label] for row in train], dtype=torch.long)
    preprocess_s = time.perf_counter() - started
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, foreach=False)
    model.train()
    steps, losses = 0, []
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    for epoch in range(args.epochs):
        order = torch.randperm(len(train), generator=torch.Generator().manual_seed(args.seed + epoch))
        total_loss = 0.
        for start in range(0, len(train), args.batch):
            indices = order[start:start + args.batch]
            batch = {name: values[indices] for name, values in inputs.items()}
            optimizer.zero_grad(set_to_none=True)
            loss = model(**batch, labels=targets[indices]).loss
            if not torch.isfinite(loss):
                raise RuntimeError("nonfinite training loss")
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * len(indices)
            steps += 1
            if steps % 10 == 0:
                log("intent_finetune", "info", "학습 진행", epoch=epoch + 1, steps=steps)
        losses.append(total_loss / len(train))
    cpu_s, wall_s = time.process_time() - cpu_start, time.perf_counter() - wall_start
    usage = cpu_usage(cpu_s, wall_s, os.cpu_count() or 1)
    del optimizer, inputs, targets, batch, loss
    gc.collect()
    model.eval()
    model_dir = run_dir / "model"
    model.save_pretrained(model_dir, safe_serialization=True)
    tokenizer.save_pretrained(model_dir)
    weights_sha = _sha(model_dir / "model.safetensors")
    model_version = "koelectra-intent-v0-" + weights_sha[:12]
    classifier = BertClassifier(model, tokenizer, labels, model_version, args.length)
    bert_report = evaluate(BaselineIntentParser(classifier), dev)
    before_reload = [case["result"] for case in bert_report["cases"]]
    del classifier, model
    gc.collect()
    reloaded_model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True,
        trust_remote_code=False, weights_only=True, use_safetensors=True).to("cpu")
    reloaded_tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    reloaded = evaluate(BaselineIntentParser(BertClassifier(reloaded_model, reloaded_tokenizer, labels,
                                                          model_version, args.length)), dev)
    reload_mismatches = sum(a != b["result"] for a, b in zip(before_reload, reloaded["cases"]))
    if reload_mismatches:
        raise RuntimeError("checkpoint reload changed evaluation predictions")
    metrics = {f"{tag}_{key}": item for tag, report in (("baseline", baseline_report), ("bert", bert_report))
               for key, item in report["metrics"].items()}
    for key, value, unit in (
        ("train_rows", len(train), "문장"), ("dev_rows", len(dev), "문장"),
        ("unreviewed_excluded", unreviewed_excluded, "문장"),
        ("unreviewed_included", unreviewed_included, "문장"),
        ("training_epochs", args.epochs, "회"), ("training_steps", steps, "회"),
        ("training_wall", wall_s, "s"), ("training_cpu", cpu_s, "CPU s"),
        ("process_cpu_percent", usage["process_cpu_percent"], "%"),
        ("machine_cpu_percent", usage["machine_cpu_percent"], "%"),
        ("logical_cpus", os.cpu_count() or 1, "개"), ("training_loss", losses[-1], ""),
        ("lineage_overlap", len({r.split_key.lineage_root for r in train} &
                                 {r.split_key.lineage_root for r in dev}), "건"),
        ("contrast_overlap", len({r.contrast_group for r in train if r.contrast_group} &
                                  {r.contrast_group for r in dev if r.contrast_group}), "건"),
        ("reload_mismatches", reload_mismatches, "건")):
        metrics[key] = {"value": value, "unit": unit}
    source = (f"python -m tools.perf.intent_finetune --data {args.data} --cache {args.cache} "
              f"--epochs {args.epochs} --batch {args.batch} --length {args.length} "
              f"--threads {args.threads} --seed {args.seed}")
    if args.include_generated:
        source += " --include-generated"
    digest = hashlib.sha256()
    for path in sorted((ROOT / "intent").glob("*.py")) + [ROOT / "tools/perf/intent_finetune.py"]:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    run = dict(task="D-07-03", commit=GitRepo(ROOT).head(), source_sha256=digest.hexdigest(),
        command=source, include_generated=args.include_generated,
        data_version=_sha(data_path), feature_version=TOKENIZER_VERSION,
        model_id=MODEL_ID, revision=REVISION, model_version=model_version, weights_sha256=weights_sha,
        license="Apache-2.0 (D-05-02 evidence)", python=platform.python_version(),
        packages={name: version(name) for name in ("torch", "transformers", "scikit-learn")},
        device="cpu", dtype="float32", length=args.length, threads=args.threads, interop_threads=1,
        optimizer="AdamW(lr=2e-5, foreach=False)", seed=args.seed, threshold=.20,
        train_ids=[r.example_id for r in train], dev_ids=[r.example_id for r in dev],
        train_lineages=sorted({r.split_key.lineage_root for r in train}),
        dev_lineages=sorted({r.split_key.lineage_root for r in dev}),
        epoch_losses=losses, model_load_s=load_s, preprocessing_s=preprocess_s, metrics=metrics,
        baseline=baseline_report, bert=bert_report, training_complete=True)
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_name = "finetune" if args.epochs == 1 else f"finetune_{args.epochs}epochs"
    if args.include_generated:
        result_name += "_with_generated"
    result = write_result("D-07-03", result_name, metrics, source)
    log("intent_finetune", "info", "미세조정·평가 완료", model_version=model_version,
        training_wall_s=wall_s, training_cpu_s=cpu_s, **usage, reload_mismatches=reload_mismatches)
    log("intent_finetune", "info", "결과 기록", run=str(run_dir.relative_to(ROOT)),
        result=str(result.relative_to(ROOT)), source_sha256=digest.hexdigest())
    print(json.dumps({"run": str(run_dir.relative_to(ROOT)), "metrics": metrics}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
