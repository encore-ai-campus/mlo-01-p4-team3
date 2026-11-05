"""도메인 규칙: 추궁 답변 평가 실행과 MVP 지표(E-07-07 완료 조건 3, E-07-06 3~5번 정의).

`run_eval`은 사례마다 프롬프트 생성 → `generate`(바깥에서 주입) → 검증 → 폴백을 하고 요청 기록을 돌려준다.
llama.cpp는 import하지 않는다. 생성은 호출하는 쪽이 넘긴다(측정 어댑터 `llm/bench/run_bench.py --eval`,
MVP 노트북 E-07-06).

`generate(messages, *, seed)`가 돌려줄 dict:
    {"text": str, "prompt_ms": float, "gen_ms": float, "prompt_tokens": int, "gen_tokens": int}
    선택: "queue_ms": float(큐 대기, 없으면 0). 실패하면 예외를 올린다(→ 생성 오류, 폴백).

정의(E-07-06 노트북도 이 함수로 센다):
- **성공** = 생성 오류·시간 초과가 없고 검증을 통과해 폴백 없이 나온 답.
- **상태**: `done`(성공) | `failed`(검증 실패, 폴백) | `timeout`(시간 초과, 폴백) | `error`(생성 오류, 폴백).
- **시간 초과(MVP 측정용 정의)**: 요청 합계(`total_ms`)가 그 요청의 `deadline_ms`를 넘으면 사후에 시간 초과로
  센다. 검증 결과와 관계없이 폴백으로 처리한다. 이것은 집계 규칙일 뿐이고 생성을 실제로 중단하지 않는다.
  실제 중단은 서비스(E-08-09, ports.md 4절)가 맡는다.
- **단계 시간**: 프롬프트 처리(`prompt_ms`)·생성(`gen_ms`)은 `generate`가 잰 값, 검증(`validate_ms`)은 여기서
  잰다. **합계** `total_ms` = 큐 대기 + (프롬프트 생성 시작부터 검증 끝까지의 벽시계 시간).
- **사유 분포**: 검증 실패 요청의 대표 사유(`ValidationResult.reason`, 4종 중 첫 사유)별 건수와 비율(전체 대비 %).
  사유 전부의 건수(`all_*`)도 따로 낸다. 시간 초과인 요청도 검증은 하므로 사유가 있으면 함께 센다.
- **백분위**: 최근접 순위(nearest-rank) 방식, **보간하지 않는다**. 정렬한 값의 ceil(q/100·n)번째(1부터)
  값이다(q=0이면 첫 값). 표본 20개의 p95는 19번째 값, 표본 9개의 p95는 최댓값이다.
- **토큰/초**: 요청마다 `gen_tokens / (gen_ms/1000)`의 평균(생성 토큰이 0이거나 생성 시간이 0인 요청은 뺀다).
- **일관성**(k회 생성, 요청별): ① 판정 일치 = k회의 검증 통과 여부가 모두 같음(생성 오류는 실패로 봄)
  ② 사실 요소 보존 = k회 모두 생성문이 있고 `fact_changed` 사유가 없음 ③ 문자 유사도 = k회 생성문(검증 전,
  `normalize_output` 뒤) 모든 쌍의 `difflib.SequenceMatcher(None, a, b).ratio()` 평균(k=5면 10쌍).
  ①·②는 요청 비율(%), ③은 요청 평균의 평균.

표준 라이브러리와 `llm`만 쓴다.
"""
from __future__ import annotations

import difflib
import itertools
import math
import time
from typing import Callable

from llm.prompt import build_messages
from llm.validator import FACT_CHANGED, REASONS, Lexicon, fallback_text, normalize_output, validate

DONE, FAILED, TIMEOUT, ERROR = "done", "failed", "timeout", "error"


def percentile(values: list[float], q: float) -> float:
    """최근접 순위 백분위(보간 없음). q는 0~100. 표본이 없으면 ValueError."""
    if not values:
        raise ValueError("표본이 없음")
    if not 0 <= q <= 100:
        raise ValueError(f"q는 0~100: {q}")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def run_case(case: dict, generate: Callable, *, seed: int, lexicon: Lexicon,
             clock: Callable[[], float] = time.perf_counter) -> dict:
    """사례 1건: 프롬프트 → 생성 → 검증 → 폴백. 요청 기록 dict를 돌려준다(필드는 `run_eval` 참고)."""
    req = case["request"]
    t0 = clock()
    messages = build_messages(req)
    rec = {"case_id": case.get("case_id", req["job_id"]), "seed": seed, "raw": None, "generated": None,
           "valid": False, "reasons": [], "details": [], "prompt_ms": 0.0, "gen_ms": 0.0, "validate_ms": 0.0,
           "queue_ms": 0.0, "prompt_tokens": 0, "gen_tokens": 0, "error_type": None}
    try:
        out = generate(messages, seed=seed)
    except Exception as exc:   # 생성 오류도 기록하고 폴백한다
        rec["error_type"] = type(exc).__name__
        out = None
    if out is not None:
        rec.update(raw=out["text"], prompt_ms=float(out["prompt_ms"]), gen_ms=float(out["gen_ms"]),
                   prompt_tokens=int(out["prompt_tokens"]), gen_tokens=int(out["gen_tokens"]),
                   queue_ms=float(out.get("queue_ms", 0.0)))
        v0 = clock()
        text = normalize_output(out["text"])
        result = validate(text, req, lexicon, case.get("allowed_places"))
        rec["validate_ms"] = (clock() - v0) * 1000
        rec.update(generated=text, valid=result.ok, reasons=list(result.reasons), details=list(result.details))
    rec["total_ms"] = rec["queue_ms"] + (clock() - t0) * 1000
    if rec["error_type"]:
        status = ERROR
    elif rec["total_ms"] > req.get("deadline_ms", 15000):
        status = TIMEOUT
    elif not rec["valid"]:
        status = FAILED
    else:
        status = DONE
    rec["status"] = status
    rec["fallback"] = status != DONE
    rec["text"] = rec["generated"] if status == DONE else fallback_text(req)
    rec["fallback_has_fact"] = (req["fact_sentence"] in rec["text"]) if rec["fallback"] else None
    return rec


def run_eval(cases: list[dict], generate: Callable, *, seed: int, lexicon: Lexicon,
             clock: Callable[[], float] = time.perf_counter, on_record: Callable[[dict], None] | None = None) -> list[dict]:
    """사례들을 차례로 돌려 요청 기록 목록을 돌려준다(순수: 생성·시계는 주입).

    기록 필드: case_id, seed, status(done|failed|timeout|error), fallback, text(화면에 보일 문장),
    raw(생성 원문), generated(다듬은 생성문), valid, reasons, details, prompt_ms, gen_ms, validate_ms, queue_ms,
    total_ms, prompt_tokens, gen_tokens, error_type, fallback_has_fact. on_record는 기록마다 부른다(로그용).
    """
    records = []
    for case in cases:
        rec = run_case(case, generate, seed=seed, lexicon=lexicon, clock=clock)
        records.append(rec)
        if on_record:
            on_record(rec)
    return records


def _m(value: float, unit: str, digits: int = 2) -> dict:
    return {"value": round(value, digits), "unit": unit}


def success_metrics(records: list[dict]) -> dict:
    """성공률·폴백률·사유 분포·오류·시간 초과·폴백 동등성(결과 JSON metrics 형식)."""
    if not records:
        raise ValueError("기록이 없음")
    n = len(records)
    count = lambda pred: sum(1 for r in records if pred(r))   # noqa: E731
    out = {
        "n_requests": {"value": n, "unit": "건"},
        "success_count": {"value": count(lambda r: r["status"] == DONE), "unit": "건"},
        "success_rate_pct": _m(100 * count(lambda r: r["status"] == DONE) / n, "%"),
        "fallback_count": {"value": count(lambda r: r["fallback"]), "unit": "건"},
        "fallback_rate_pct": _m(100 * count(lambda r: r["fallback"]) / n, "%"),
        "validation_fail_count": {"value": count(lambda r: r["status"] != ERROR and not r["valid"]), "unit": "건"},
        "error_count": {"value": count(lambda r: r["status"] == ERROR), "unit": "건"},
        "timeout_count": {"value": count(lambda r: r["status"] == TIMEOUT), "unit": "건"},
    }
    for code in REASONS:
        c = count(lambda r: r["status"] != ERROR and not r["valid"] and r["reasons"][0] == code)
        out[f"reason_{code}_count"] = {"value": c, "unit": "건"}
        out[f"reason_{code}_pct"] = _m(100 * c / n, "%")
        out[f"all_{code}_count"] = {"value": count(lambda r: code in r["reasons"]), "unit": "건"}
    fb = [r for r in records if r["fallback"]]
    out["fallback_has_fact_pct"] = _m(100 * sum(r["fallback_has_fact"] for r in fb) / len(fb), "%") if fb \
        else {"value": 100.0, "unit": "%"}
    return out


def latency_metrics(records: list[dict]) -> dict:
    """단계별(프롬프트 처리·생성·검증)·합계 p50·p95와 생성 토큰/초 평균(결과 JSON metrics 형식).

    생성 오류 요청은 단계 시간이 없으므로 단계별 값에서 빼고, 합계에는 넣는다.
    """
    if not records:
        raise ValueError("기록이 없음")
    ok = [r for r in records if r["status"] != ERROR]
    out = {}
    for key in ("prompt", "gen", "validate"):
        vals = [r[f"{key}_ms"] for r in ok]
        if vals:
            out[f"{key}_p50_ms"] = _m(percentile(vals, 50), "ms")
            out[f"{key}_p95_ms"] = _m(percentile(vals, 95), "ms", 3 if key == "validate" else 2)
    total = [r["total_ms"] for r in records]
    out["total_p50_ms"] = _m(percentile(total, 50), "ms")
    out["total_p95_ms"] = _m(percentile(total, 95), "ms")
    out["total_max_ms"] = _m(max(total), "ms")
    tps = [r["gen_tokens"] / (r["gen_ms"] / 1000) for r in ok if r["gen_tokens"] > 0 and r["gen_ms"] > 0]
    if tps:
        out["gen_tps_mean"] = _m(sum(tps) / len(tps), "tok/s")
    if ok:
        out["prompt_tokens_mean"] = _m(sum(r["prompt_tokens"] for r in ok) / len(ok), "tok")
        out["gen_tokens_mean"] = _m(sum(r["gen_tokens"] for r in ok) / len(ok), "tok")
    return out


def similarity(texts: list[str]) -> float:
    """모든 쌍의 `difflib.SequenceMatcher` 비율 평균. 2개 미만이면 1.0."""
    pairs = list(itertools.combinations(texts, 2))
    if not pairs:
        return 1.0
    return sum(difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs) / len(pairs)


def consistency_case(runs: list[dict]) -> dict:
    """한 요청의 k회 기록 → {verdict_agree, facts_kept, similarity}. 생성 오류 회차의 생성문은 빈 문자열로 본다."""
    if not runs:
        raise ValueError("기록이 없음")
    verdicts = {bool(r["valid"]) and r["status"] != ERROR for r in runs}
    facts_kept = all(r["generated"] is not None and FACT_CHANGED not in r["reasons"] for r in runs)
    return {"verdict_agree": len(verdicts) == 1, "facts_kept": facts_kept,
            "similarity": similarity([r["generated"] or "" for r in runs])}


def consistency_metrics(runs_by_seed: list[list[dict]]) -> dict:
    """시드별 기록 목록(같은 사례 순서) → 일관성 지표(결과 JSON metrics 형식)."""
    if not runs_by_seed or not runs_by_seed[0]:
        raise ValueError("기록이 없음")
    by_case: dict[str, list[dict]] = {}
    for records in runs_by_seed:
        for r in records:
            by_case.setdefault(r["case_id"], []).append(r)
    per = [consistency_case(v) for v in by_case.values()]
    n = len(per)
    return {
        "consistency_cases": {"value": n, "unit": "건"},
        "consistency_k": {"value": len(runs_by_seed), "unit": "회"},
        "verdict_agree_pct": _m(100 * sum(p["verdict_agree"] for p in per) / n, "%"),
        "facts_kept_pct": _m(100 * sum(p["facts_kept"] for p in per) / n, "%"),
        "similarity_mean": _m(sum(p["similarity"] for p in per) / n, "", 4),
    }


def run_consistency(cases: list[dict], generate: Callable, *, seeds: list[int], lexicon: Lexicon,
                    clock: Callable[[], float] = time.perf_counter) -> tuple[dict, list[list[dict]]]:
    """같은 사례를 시드마다 한 번씩 돌려 (일관성 지표, 시드별 기록)을 돌려준다. E-07-06: 앞 20건, 시드 1~5."""
    runs = [run_eval(cases, generate, seed=s, lexicon=lexicon, clock=clock) for s in seeds]
    return consistency_metrics(runs), runs
