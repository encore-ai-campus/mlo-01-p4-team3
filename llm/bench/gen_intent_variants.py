"""인프라·응용: 의도 예시 변형 생성(E-06-02 2단계). 로컬 llama.cpp로 원본마다 변형 후보를 만들고 걸러 반영한다.

두 단계로 나눈다(생성은 `llama_cpp`가 있는 파이썬, 반영은 저장소 의존성이 있는 파이썬에서 돌릴 수 있게).

    python -m llm.bench.gen_intent_variants generate --model <gguf> [--threads 8] [--seed 0] [--limit N]
    python -m llm.bench.gen_intent_variants apply

- `generate`: `content/intent/examples_v1.jsonl`의 사람 작성 행마다 `llm.intent_variants.build_messages`로
  요청하고, 원문 출력을 `out/intent_variants/candidates.jsonl`(커밋하지 않음)에 한 줄씩 쓴다. 이미 쓴 원본은
  건너뛰므로 끊겨도 다시 돌리면 이어서 한다. 모델 sha256·생성 설정·시드는 `out/intent_variants/meta.json`.
  모델 경로는 `--model` 또는 환경변수 `PALDO_LLM_MODEL`로 받는다(저장소에 경로를 적지 않는다).
- `apply`: 후보를 `llm.intent_variants.check_variant`(보존 규칙), 마스킹 불변(`mask_text`), 정규화 중복(기존 행·
  시드·앞서 받은 후보)으로 거르고, 원본당 최대 3개를 `generated`(검수 전)로 원본 바로 뒤에 넣는다. 다시 돌리면
  기존 `generated` 행을 지우고 새로 만든다(`generated_reviewed`는 건드리지 않는다). D 검수 시트
  `docs/evidence/perf/E-06-02/variants_review.csv`와 결과 JSON `docs/evidence/perf/E-06-02/variants_v1.json`을 쓴다.

계층: 규칙은 도메인 `llm/intent_variants.py`, 이 파일은 모델 호출·파일 읽기쓰기·흐름 조립만 한다.
`llama_cpp`·`jinja2`는 `generate` 안에서 늦게 import한다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from llm import intent_variants as iv

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "content" / "intent" / "examples_v1.jsonl"
SEED_EXAMPLES = ROOT / "fixtures" / "intent" / "seed_examples.jsonl"
BUNDLE = ROOT / "content" / "bundle_v1.json"
OUT = ROOT / "out" / "intent_variants"
REVIEW_CSV = ROOT / "docs" / "evidence" / "perf" / "E-06-02" / "variants_review.csv"
RESULT_NAME = "variants_v1"


def _log(level: str, msg: str, **extra) -> None:
    """구조화 로그 한 줄(README 5절 필수 필드)을 표준 출력과 `out/logs/llm.jsonl`에 쓴다."""
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "level": level,
              "service": "llm", "component": "gen_intent_variants", "msg": msg, **extra}
    line = json.dumps(record, ensure_ascii=False)
    print(line, flush=True)
    log_dir = Path(os.environ.get("PALDO_LOG_DIR", ROOT / "out" / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "llm.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_jsonl(path: Path) -> list[dict]:
    """JSONL을 dict 목록으로 읽는다(없으면 빈 목록)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """dict 목록을 JSONL(UTF-8, LF)로 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_bytes(data.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """파일 sha256(16진)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entity_words(bundle_path: Path = BUNDLE) -> list[str]:
    """새 정보 검사 어휘: 인물 이름·별칭, 장소(동의어·지도 밖 장소 포함), 역할어. 긴 말부터."""
    from llm.validator import ROLE_WORDS, lexicon_from_bundle
    lex = lexicon_from_bundle(json.loads(bundle_path.read_text(encoding="utf-8")))
    words = {w for group in (*lex.names.values(), *lex.places.values()) for w in group} | set(ROLE_WORDS)
    return sorted(words, key=lambda w: (-len(w), w))


# ---------------------------------------------------------------- generate
def generate(model_path: str, threads: int, seed: int, limit: int | None) -> int:
    """사람 작성 원본마다 후보를 만들어 `candidates.jsonl`에 덧붙인다."""
    from llm.bench.run_bench import BenchModel

    roots = [r for r in read_jsonl(EXAMPLES) if r["label_source"] == "authored"]
    done = {c["root_id"] for c in read_jsonl(OUT / "candidates.jsonl")}
    todo = [r for r in roots if r["example_id"] not in done][: limit or None]
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "meta.json"
    meta = {"prompt_version": iv.PROMPT_VERSION, "gen_settings": iv.GEN_SETTINGS, "threads": threads,
            "seed": seed, "model_file": Path(model_path).name, "model_sha256": sha256_file(Path(model_path))}
    if meta_path.exists():
        # 이어서 돌릴 때 설정이 다르면 한 파일에 두 실행이 섞여 기록(meta)과 어긋나므로 멈춘다
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        diff = sorted(k for k in meta if saved.get(k) != meta[k])
        if diff:
            raise SystemExit(f"이전 생성과 설정이 다름({', '.join(diff)}): out/intent_variants/를 지우고 처음부터 돌린다")
    else:
        meta_path.write_bytes((json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    _log("info", "생성 시작", roots=len(roots), todo=len(todo), seed=seed, threads=threads)
    model = BenchModel(model_path, threads, iv.GEN_SETTINGS["n_ctx"], seed)
    t_all = time.perf_counter()
    with open(OUT / "candidates.jsonl", "a", encoding="utf-8", newline="") as out:
        for i, row in enumerate(todo, 1):
            idx = roots.index(row)
            prompt = model.render(iv.build_messages(row))
            t0 = time.perf_counter()
            with model.lock:
                model.llm.reset()
                res = model.llm.create_completion(
                    prompt, max_tokens=iv.GEN_SETTINGS["max_tokens"], temperature=iv.GEN_SETTINGS["temperature"],
                    top_p=iv.GEN_SETTINGS["top_p"], top_k=iv.GEN_SETTINGS["top_k"], stop=iv.STOP, seed=seed + idx)
            ms = round((time.perf_counter() - t0) * 1000)
            rec = {"root_id": row["example_id"], "seed": seed + idx, "raw": res["choices"][0]["text"], "ms": ms,
                   "completion_tokens": res.get("usage", {}).get("completion_tokens")}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            if i % 10 == 0 or i == len(todo):
                _log("info", "진행", done=i, todo=len(todo), last_ms=ms,
                     elapsed_s=round(time.perf_counter() - t_all))
    _log("info", "생성 끝", generated=len(todo), elapsed_s=round(time.perf_counter() - t_all))
    return 0


# ---------------------------------------------------------------- apply
def select_variants(roots: list[dict], candidates: dict[str, list[str]], existing_texts: list[str],
                    entities: list[str], normalize, unchanged_by_mask,
                    reviewed_counts: dict[str, int] | None = None) -> tuple[dict[str, list[str]], dict[str, int]]:
    """원본별 통과 후보와 탈락 사유별 수. 중복은 기존 문장·앞서 받은 후보와 정규화해 비교한다.

    원본당 상한은 3개에서 이미 검수 통과한(`generated_reviewed`) 변형 수를 뺀 값이다(`reviewed_counts`).
    """
    reviewed_counts = reviewed_counts or {}
    seen = {normalize(t) for t in existing_texts}
    rejected = {reason: 0 for reason in (*iv.REJECT_REASONS, "duplicate", "masked", "over_limit")}
    accepted: dict[str, list[str]] = {}
    for root in roots:
        keep = []
        for text in candidates.get(root["example_id"], []):
            reason = iv.check_variant(root, text, entities)
            if reason is None and not unchanged_by_mask(text):
                reason = "masked"
            if reason is None and normalize(text) in seen:
                reason = "duplicate"
            if reason is None and len(keep) >= iv.MAX_VARIANTS - reviewed_counts.get(root["example_id"], 0):
                reason = "over_limit"
            if reason:
                rejected[reason] += 1
                continue
            seen.add(normalize(text))
            keep.append(text)
        accepted[root["example_id"]] = keep
    return accepted, rejected


def apply() -> int:
    """후보를 걸러 예시 파일·검수 시트·결과 JSON을 쓴다."""
    from server.app.interactions import mask_text
    from tools.evidence_result import write_result
    from tools.intent_examples import LABELS, normalize_text

    cand_path = OUT / "candidates.jsonl"
    if not cand_path.exists():
        # out/는 커밋하지 않으므로 새로 받은 작업 트리에는 후보가 없다. 그대로 돌리면 반영된 변형을 지운다
        raise SystemExit("out/intent_variants/candidates.jsonl이 없다: generate를 먼저 돌린다(반영된 파일은 그대로 둠)")
    all_rows = read_jsonl(EXAMPLES)
    if REVIEW_CSV.exists():
        with open(REVIEW_CSV, encoding="utf-8-sig", newline="") as f:
            decided = {r["variant_id"] for r in csv.DictReader(f) if (r.get("verdict") or r.get("reason") or "").strip()}
        pending = decided & {r["example_id"] for r in all_rows if r["label_source"] == "generated"}
        if pending:
            raise SystemExit(f"검수 시트에 아직 반영하지 않은 판정 {len(pending)}건: generated_reviewed로 반영한 뒤 다시 돌린다")
        if decided:   # 판정을 모두 반영했으면 기록을 남기고 새 시트를 만든다
            n = 1
            while (archived := REVIEW_CSV.with_name(f"variants_review_round{n}.csv")).exists():
                n += 1
            REVIEW_CSV.rename(archived)
    rows = [r for r in all_rows if r["label_source"] != "generated"]
    roots = [r for r in rows if r["label_source"] == "authored"]
    cand_rows = read_jsonl(cand_path)
    reviewed: dict[str, int] = {}
    for r in rows:
        if r["label_source"] == "generated_reviewed":
            root_id = r["split_key"]["lineage_root"]
            reviewed[root_id] = reviewed.get(root_id, 0) + 1
    candidates = {c["root_id"]: iv.parse_output(c["raw"]) for c in cand_rows}
    texts = [r["text"] for r in rows] + [r["text"] for r in read_jsonl(SEED_EXAMPLES)]
    accepted, rejected = select_variants(roots, candidates, texts, entity_words(), normalize_text,
                                         lambda t: mask_text(t) == t, reviewed)
    out_rows, review = [], []
    taken = {r["example_id"] for r in rows}
    for row in rows:
        out_rows.append(row)
        if row["label_source"] != "authored":
            continue
        n = 0
        for text in accepted.get(row["example_id"], []):
            n += 1
            while f"{row['example_id']}_v{n}" in taken:   # 이미 검수 통과(`generated_reviewed`)한 번호는 피한다
                n += 1
            v = iv.variant_row(row, n, text)
            taken.add(v["example_id"])
            out_rows.append(v)
            review.append({"variant_id": v["example_id"], "root_id": row["example_id"], "label": row["label"],
                           "form": row["form"], "register": iv.note_register(row.get("notes", "")),
                           "original": row["text"], "variant": text, "verdict": "", "reason": ""})
    write_jsonl(EXAMPLES, out_rows)
    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(review[0]) if review else ["variant_id"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(review)
    REVIEW_CSV.write_bytes(("﻿" + buf.getvalue()).encode("utf-8"))   # 엑셀에서 한글이 깨지지 않게 BOM

    total = sum(len(iv.parse_output(c["raw"])) for c in cand_rows)
    meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8")) if (OUT / "meta.json").exists() else {}
    ms = sorted(c["ms"] for c in cand_rows)
    by_label = {label: 0 for label in LABELS}
    for item in review:
        by_label[item["label"]] += 1
    metrics = {"roots": {"value": len(roots), "unit": "개"},
               "roots_generated": {"value": len(cand_rows), "unit": "개"},
               "candidates": {"value": total, "unit": "개"},
               "accepted": {"value": len(review), "unit": "개"},
               "rejected": {"value": sum(rejected.values()), "unit": "개"},
               "generation_total_s": {"value": round(sum(ms) / 1000), "unit": "s"},
               "generation_p50_ms": {"value": ms[len(ms) // 2] if ms else 0, "unit": "ms"}}
    metrics.update({f"rejected_{k}": {"value": v, "unit": "개"} for k, v in rejected.items()})
    metrics.update({f"accepted_{k}": {"value": v, "unit": "개"} for k, v in by_label.items()})
    metrics["min_accepted_per_label"] = {"value": min(by_label.values()), "unit": "개"}
    path = write_result("E-06-02", RESULT_NAME, metrics, "python -m llm.bench.gen_intent_variants apply")
    _log("info", "반영 끝", accepted=len(review), rejected=rejected, prompt_version=meta.get("prompt_version"),
         model_sha256=meta.get("model_sha256"), result=str(path.relative_to(ROOT)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 `generate` 또는 `apply`를 부른다."""
    p = argparse.ArgumentParser(description="의도 예시 변형 생성(E-06-02 2단계)")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--model", default=os.environ.get("PALDO_LLM_MODEL"))
    g.add_argument("--threads", type=int, default=8)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--limit", type=int)
    sub.add_parser("apply")
    a = p.parse_args(argv)
    if a.cmd == "generate":
        if not a.model:
            p.error("--model 또는 PALDO_LLM_MODEL이 필요하다")
        return generate(a.model, a.threads, a.seed, a.limit)
    return apply()


if __name__ == "__main__":
    sys.exit(main())
