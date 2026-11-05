"""E-07-07 지표 함수(E-07-06이 같은 함수를 쓴다): run_eval·성공률·사유 분포·지연·일관성·백분위. 가짜 생성기로 돈다."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from llm import mvp_metrics as mm
from llm import validator as v
from llm.prompt import GEN_SETTINGS, build_messages

ROOT = Path(__file__).resolve().parents[2]
EVAL = json.loads((ROOT / "fixtures" / "llm" / "interrogation_eval.json").read_text(encoding="utf-8"))
LEX = v.Lexicon.from_json(EVAL["lexicon"])
CASES = EVAL["cases"]


class FakeClock:
    """호출마다 step초씩 가는 시계."""

    def __init__(self, step: float = 0.001):
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def fake_generate(texts: dict, prompt_ms=1000.0, gen_ms=500.0, gen_tokens=10, fail=()):
    """사례 ID(마지막 user 메시지의 사실 문장으로 찾음) → 생성문. fail에 든 사실 문장은 예외."""
    by_fact: dict[str, str] = {}
    for c in CASES:   # 같은 사실 문장이면 첫 사례(unique()와 같은 기준)
        by_fact.setdefault(c["request"]["fact_sentence"], c["case_id"])

    def gen(messages, *, seed):
        fact = messages[-1]["content"].removeprefix("사실 문장: ")
        cid = by_fact[fact]
        if cid in fail:
            raise RuntimeError("생성 실패")
        text = texts.get(cid, fact)
        if callable(text):
            text = text(seed)
        return {"text": text, "prompt_ms": prompt_ms, "gen_ms": gen_ms, "prompt_tokens": 100, "gen_tokens": gen_tokens}

    return gen


def unique(cases):
    """사실 문장이 겹치지 않는 사례만(가짜 생성기가 사실 문장으로 사례를 찾으므로)."""
    seen, out = set(), []
    for c in cases:
        if c["request"]["fact_sentence"] not in seen:
            seen.add(c["request"]["fact_sentence"])
            out.append(c)
    return out


SAMPLE = unique(CASES)[:10]


class Percentile(unittest.TestCase):
    def test_nearest_rank_no_interpolation(self):
        vals = list(range(1, 21))          # 1..20
        self.assertEqual(mm.percentile(vals, 50), 10)
        self.assertEqual(mm.percentile(vals, 95), 19)
        self.assertEqual(mm.percentile(vals, 100), 20)
        self.assertEqual(mm.percentile(vals, 0), 1)
        self.assertEqual(mm.percentile([1.0, 2.0], 50), 1.0)     # 보간했다면 1.5
        self.assertEqual(mm.percentile(list(range(1, 101)), 95), 95)
        self.assertEqual(mm.percentile([3.0, 1.0, 2.0], 95), 3.0)

    def test_errors(self):
        with self.assertRaises(ValueError):
            mm.percentile([], 50)
        with self.assertRaises(ValueError):
            mm.percentile([1], -1)


class RunEval(unittest.TestCase):
    def test_success_and_fallback(self):
        c0, c1, c2, c3 = (c["case_id"] for c in SAMPLE[:4])
        texts = {c1: "그날 새벽엔 어딘가에 있었소.", c2: "", c3: "Hello"}
        recs = mm.run_eval(SAMPLE, fake_generate(texts, fail={SAMPLE[4]["case_id"]}), seed=0, lexicon=LEX,
                           clock=FakeClock())
        st = {r["case_id"]: r for r in recs}
        self.assertEqual(st[c0]["status"], mm.DONE)
        self.assertFalse(st[c0]["fallback"])
        self.assertEqual(st[c0]["text"], SAMPLE[0]["request"]["fact_sentence"])
        self.assertEqual(st[c1]["status"], mm.FAILED)
        self.assertEqual(st[c2]["reasons"][0], v.FORMAT_LENGTH)   # 빈 문장: 형식 + 사실 요소 빠짐
        self.assertEqual(st[SAMPLE[4]["case_id"]]["status"], mm.ERROR)
        self.assertEqual(st[SAMPLE[4]["case_id"]]["error_type"], "RuntimeError")
        for r in recs:
            if r["fallback"]:
                self.assertTrue(r["fallback_has_fact"])
                self.assertIn(SAMPLE[[c["case_id"] for c in SAMPLE].index(r["case_id"])]["request"]["fact_sentence"],
                              r["text"])
        m = mm.success_metrics(recs)
        self.assertEqual(m["n_requests"]["value"], 10)
        self.assertEqual(m["success_count"]["value"], 6)
        self.assertEqual(m["success_rate_pct"], {"value": 60.0, "unit": "%"})
        self.assertEqual(m["fallback_rate_pct"]["value"], 40.0)
        self.assertEqual(m["error_count"]["value"], 1)
        self.assertEqual(m["validation_fail_count"]["value"], 3)
        self.assertEqual(m["reason_format_length_count"]["value"], 2)
        self.assertEqual(m["reason_fact_changed_count"]["value"], 1)
        self.assertEqual(m["reason_fact_changed_pct"]["value"], 10.0)
        self.assertEqual(m["fallback_has_fact_pct"]["value"], 100.0)
        self.assertEqual(sum(m[f"reason_{c}_count"]["value"] for c in v.REASONS), 3)

    def test_timeout_is_fallback(self):
        case = json.loads(json.dumps(SAMPLE[0]))
        case["request"]["deadline_ms"] = 100
        rec = mm.run_case(case, fake_generate({}), seed=0, lexicon=LEX, clock=FakeClock(step=0.2))
        self.assertTrue(rec["valid"])                 # 검증은 통과했지만
        self.assertEqual(rec["status"], mm.TIMEOUT)   # 합계가 마감을 넘어 폴백
        self.assertTrue(rec["fallback"])
        self.assertEqual(mm.success_metrics([rec])["timeout_count"]["value"], 1)

    def test_total_includes_queue_and_wall(self):
        def gen(messages, *, seed):
            return {"text": "x", "prompt_ms": 1.0, "gen_ms": 1.0, "prompt_tokens": 1, "gen_tokens": 1, "queue_ms": 500.0}
        rec = mm.run_case(SAMPLE[0], gen, seed=0, lexicon=LEX, clock=FakeClock(step=0.01))
        self.assertAlmostEqual(rec["total_ms"], 500.0 + 30.0)   # 시계 4번 호출: 시작·검증 전·검증 후·끝 → 3구간

    def test_latency(self):
        recs = mm.run_eval(SAMPLE, fake_generate({}, gen_ms=2000.0, gen_tokens=20), seed=0, lexicon=LEX,
                           clock=FakeClock())
        m = mm.latency_metrics(recs)
        self.assertEqual(m["prompt_p95_ms"], {"value": 1000.0, "unit": "ms"})
        self.assertEqual(m["gen_p50_ms"]["value"], 2000.0)
        self.assertEqual(m["gen_tps_mean"], {"value": 10.0, "unit": "tok/s"})
        self.assertIn("total_p95_ms", m)

    def test_metrics_follow_result_format(self):
        from tools.evidence_result import build_result
        recs = mm.run_eval(SAMPLE, fake_generate({}), seed=0, lexicon=LEX, clock=FakeClock())
        metrics = {**mm.success_metrics(recs), **mm.latency_metrics(recs)}
        build_result("E-07-07", "eval_x", metrics, "cmd", "0" * 40, "2026-11-07T00:00:00Z")   # 형식 오류면 예외

    def test_prompt_carries_fact(self):
        for c in CASES:
            msgs = build_messages(c["request"])
            self.assertEqual(msgs[0]["role"], "system")
            self.assertIn(c["request"]["fact_sentence"], msgs[-1]["content"])
        self.assertEqual(set(GEN_SETTINGS), {"temperature", "top_p", "top_k", "max_tokens", "n_ctx", "n_threads"})


class Consistency(unittest.TestCase):
    def test_similarity(self):
        self.assertEqual(mm.similarity(["가나다", "가나다", "가나다"]), 1.0)
        self.assertEqual(mm.similarity(["가"]), 1.0)
        self.assertAlmostEqual(mm.similarity(["가나", "다라"]), 0.0)

    def test_k5(self):
        cases = SAMPLE[:3]
        a, b, c = (x["case_id"] for x in cases)
        fa, fb = cases[0]["request"]["fact_sentence"], cases[1]["request"]["fact_sentence"]
        texts = {
            a: lambda s: fa,                                       # 5회 모두 같고 통과
            b: lambda s: fb if s % 2 else "그날 새벽엔 어딘가에 있었소.",   # 통과·실패가 섞임, 사실 바뀜
            c: lambda s: "",                                        # 5회 모두 실패(형식), 판정은 일치
        }
        m, runs = mm.run_consistency(cases, fake_generate(texts), seeds=[1, 2, 3, 4, 5], lexicon=LEX,
                                     clock=FakeClock())
        self.assertEqual(len(runs), 5)
        self.assertEqual(m["consistency_k"]["value"], 5)
        self.assertEqual(m["consistency_cases"]["value"], 3)
        self.assertAlmostEqual(m["verdict_agree_pct"]["value"], 66.67)   # a, c 일치 / b 불일치
        self.assertAlmostEqual(m["facts_kept_pct"]["value"], 33.33)      # a만. c는 빈 문장이라 사실 요소가 빠짐
        per_a = mm.consistency_case([r[0] for r in runs])
        self.assertEqual(per_a["similarity"], 1.0)

    def test_error_run_breaks_facts(self):
        recs = [{"valid": True, "status": mm.DONE, "generated": "가", "reasons": []},
                {"valid": False, "status": mm.ERROR, "generated": None, "reasons": []}]
        p = mm.consistency_case(recs)
        self.assertFalse(p["verdict_agree"])
        self.assertFalse(p["facts_kept"])


if __name__ == "__main__":
    unittest.main()
