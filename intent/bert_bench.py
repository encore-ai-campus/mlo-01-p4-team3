"""D-05-02 CPU candidate benchmark: tokenizer/inference RSS and sampled full fine-tuning.

A fresh child process measures each model. This is a hardware/cost comparison;
randomly initialized classification heads are not an intent-accuracy evaluation.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

from tools.evidence_result import write_result
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "koelectra": "monologg/koelectra-base-v3-discriminator",
    "klue_roberta": "klue/roberta-small",
}


def percentile(values: list[float], q: float) -> float:
    """Return the nearest-rank percentile; reject empty or invalid requests."""
    if not values or not 0 <= q <= 100:
        raise ValueError("nonempty values and q in [0,100] required")
    return sorted(values)[max(1, math.ceil(len(values) * q / 100)) - 1]


def estimate_epoch_ms(rows: int, batch: int, first_ms: float, steady_ms: float, prep_ms_per_row: float) -> float:
    """Extrapolate preprocessing plus one first step and ceil(N/B)-1 steady steps."""
    if rows < 1 or batch < 1 or any(not math.isfinite(x) or x < 0 for x in (first_ms, steady_ms, prep_ms_per_row)):
        raise ValueError("positive rows/batch and finite nonnegative timings required")
    return rows * prep_ms_per_row + first_ms + (math.ceil(rows / batch) - 1) * steady_ms


def _memory():
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "peak_rss", "rss", "peak_paged_pool", "paged_pool", "peak_nonpaged_pool",
                "nonpaged_pool", "pagefile", "peak_pagefile", "private")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    api.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    api.restype = wintypes.BOOL
    if not api(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {name: int(getattr(counters, name)) for name in ("rss", "peak_rss", "private")}


def _run_candidate(tag, args):
    import torch
    import transformers
    from huggingface_hub import HfApi
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(7)
    model_id = CANDIDATES[tag]
    info = HfApi().model_info(model_id, token=False)
    revision = info.sha
    if not revision:
        raise ValueError("model revision is missing")
    card = info.card_data.to_dict() if hasattr(info.card_data, "to_dict") else info.card_data or {}
    cache = str(ROOT / "out" / "hf_cache")
    seed_path = ROOT / "fixtures/intent/seed_examples.jsonl"
    rows = [json.loads(line) for line in seed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = sorted({row["label"] for row in rows})
    if len(labels) != 10:
        raise ValueError("expected ten seed labels")
    texts = [row["text"].replace("<P1>", "훈장").replace("<P2>", "주모") for row in rows]
    chosen = [index for label in labels for index in [i for i, row in enumerate(rows) if row["label"] == label][:4]]
    if len(chosen) != 40:
        raise ValueError("need four seed examples per label")
    before_load = _memory()
    log("bert_bench", "info", "후보 로드 시작", candidate=tag, model=model_id, revision=revision)
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, cache_dir=cache,
                                              token=False, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_id, revision=revision, cache_dir=cache, token=False, trust_remote_code=False,
        num_labels=len(labels), dtype=torch.float32, weights_only=True,
        use_safetensors=(tag != "koelectra")).to("cpu")
    loaded = _memory()
    model.eval()

    def infer(text):
        inputs = tokenizer(text, max_length=args.length, padding="max_length", truncation=True, return_tensors="pt")
        return model(**inputs).logits.softmax(-1).argmax(-1).item()

    timings = []
    with torch.inference_mode():
        for text in texts[:10]:
            infer(text)
        for index in range(args.n):
            start = time.perf_counter()
            infer(texts[index % len(texts)])
            timings.append((time.perf_counter() - start) * 1000)
    inference_memory = _memory()
    log("bert_bench", "info", "추론 측정 완료", candidate=tag, calls=len(timings),
        inference_p95_ms=round(percentile(timings, 95), 6))

    start = time.perf_counter()
    encoded = tokenizer([texts[i] for i in chosen], max_length=args.length, padding="max_length",
                        truncation=True, return_tensors="pt")
    targets = torch.tensor([labels.index(rows[i]["label"]) for i in chosen], dtype=torch.long)
    prep_ms = (time.perf_counter() - start) * 1000
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5, foreach=False)
    steps = []
    for offset in range(0, len(chosen), args.batch):
        batch = {key: value[offset:offset + args.batch] for key, value in encoded.items()}
        start = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        loss = model(**batch, labels=targets[offset:offset + args.batch]).loss
        if not torch.isfinite(loss).item():
            raise ValueError("nonfinite training loss")
        loss.backward()
        optimizer.step()
        steps.append((time.perf_counter() - start) * 1000)
    training_memory = _memory()
    steady = statistics.mean(steps[1:])
    metrics = {
        "inference_p50": {"value": percentile(timings, 50), "unit": "ms"},
        "inference_p95": {"value": percentile(timings, 95), "unit": "ms"},
        "inference_calls": {"value": len(timings), "unit": "회"},
        "loaded_rss": {"value": loaded["rss"] / 2**20, "unit": "MiB"},
        "loaded_rss_delta": {"value": (loaded["rss"] - before_load["rss"]) / 2**20, "unit": "MiB"},
        "inference_peak_rss": {"value": inference_memory["peak_rss"] / 2**20, "unit": "MiB"},
        "training_peak_rss": {"value": training_memory["peak_rss"] / 2**20, "unit": "MiB"},
        "training_samples": {"value": len(chosen), "unit": "문장"},
        "training_steps": {"value": len(steps), "unit": "회"},
        "sample_epoch": {"value": (prep_ms + sum(steps)) / 1000, "unit": "s"},
        "first_step": {"value": steps[0], "unit": "ms"},
        "steady_step": {"value": steady, "unit": "ms"},
        "parameters": {"value": sum(p.numel() for p in model.parameters()), "unit": "개"},
    }
    for count in (50, 200, 1000):
        metrics[f"epoch_{count}"] = {"value": estimate_epoch_ms(count, args.batch, steps[0], steady,
                                                               prep_ms / len(chosen)) / 1000, "unit": "s"}
    command = f"python -m tools.perf.bert_bench --candidate {tag} --n {args.n} --threads {args.threads} --length {args.length} --batch {args.batch}"
    output = write_result("D-05-02", tag, metrics, command)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    run = {"task": "D-05-02", "commit": commit, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "model_id": model_id, "revision": revision, "model_card_license": card.get("license"),
           "python": platform.python_version(), "torch": torch.__version__, "transformers": transformers.__version__,
           "device": "cpu", "dtype": "float32", "threads": torch.get_num_threads(), "interop_threads": 1,
           "attention": model.config._attn_implementation, "length": args.length, "batch": args.batch,
           "optimizer": "AdamW(lr=2e-5, foreach=False)", "seed": 7,
           "data_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
           "sample_ids": [rows[i]["example_id"] for i in chosen], "inference_ms": timings,
           "step_ms": steps, "preprocessing_ms": prep_ms, "metrics": metrics}
    run_dir = ROOT / "mlruns" / "intent_candidates" / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("bert_bench", "info", "학습 측정 완료", candidate=tag, sample_epoch_s=metrics["sample_epoch"]["value"],
        epoch_1000_s=metrics["epoch_1000"]["value"], training_peak_rss_mib=metrics["training_peak_rss"]["value"])
    log("bert_bench", "info", "결과 기록", candidate=tag, result=str(output.relative_to(ROOT)),
        run=str(run_dir.relative_to(ROOT)))


def main(argv=None) -> int:
    """Measure candidates sequentially in isolated processes; emit genuine numeric evidence."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidate", choices=sorted(CANDIDATES))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--length", type=int, default=64)
    ap.add_argument("--batch", type=int, default=4)
    args = ap.parse_args(argv)
    if args.n < 1 or args.threads < 1 or not 2 <= args.length <= 512 or not 1 <= args.batch <= 20:
        ap.error("positive calls/threads, length 2..512, batch 1..20 required")
    if args.candidate:
        try:
            _run_candidate(args.candidate, args)
        except Exception as exc:
            log("bert_bench", "error", "후보 측정 실패", candidate=args.candidate, error_type=type(exc).__name__)
            raise
    else:
        for tag in CANDIDATES:
            env = dict(os.environ, TOKENIZERS_PARALLELISM="false", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                       HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_XET="1")
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "intent.bert_bench", "--candidate", tag,
                "--n", str(args.n), "--threads", str(args.threads), "--length", str(args.length),
                "--batch", str(args.batch)], cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
