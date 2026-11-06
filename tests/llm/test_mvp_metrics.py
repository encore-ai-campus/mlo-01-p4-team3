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
        self.assertEqual(m["ending_only_fail_count"]["value"], 0)   # c1은 시간이 바뀐 실제 사실 변경

    def test_ending_only_counted_apart(self):
        case = json.loads(json.dumps(SAMPLE[0]))
        case["request"]["persona"] = "아이. 말투: 어린아이 말투"
        fact = case["request"]["fact_sentence"]
        hage = fact[:-2] + "네."   # "…있었소." → "…있었네.": 사실은 그대로, 어미만 아이 말투 밖
        recs = mm.run_eval([case], fake_generate({case["case_id"]: hage}), seed=0, lexicon=LEX, clock=FakeClock())
        self.assertTrue(recs[0]["fallback"])
        self.assertEqual(recs[0]["reasons"], [v.FACT_CHANGED])
        self.assertEqual(mm.success_metrics(recs)["ending_only_fail_count"]["value"], 1)

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


class StyleExamples(unittest.TestCase):
    """말투 계열별 예시(E-07-07 v2·v3): 콘텐츠 인물 말투가 모두 계열에 맞고, 끝 어미까지 말투로 바꾼 예시 대사가 검증기를 통과한다."""

    def test_every_bundle_style_has_profile(self):
        from llm import prompt
        bundle = json.loads((ROOT / "fixtures" / "content_min" / "bundle.json").read_text(encoding="utf-8"))
        for c in bundle["characters"]:
            self.assertIsNot(prompt.style_profile(c["speech_style"]), prompt.DEFAULT_PROFILE, c["speech_style"])

    def test_real_content_styles_map_to_intended_family(self):
        """실제 콘텐츠(content/bundle_v1.json) 인물의 긴 말투 설명도 의도한 계열로 잡힌다(11/6, 대장장이·과부 오분류 수정)."""
        from llm import prompt
        bundle = json.loads((ROOT / "content" / "bundle_v1.json").read_text(encoding="utf-8"))
        want = {"훈장": "하오체", "주모": "하게체", "대장장이": "거친", "머슴": "더듬", "약초꾼": "노인", "과부": "단정"}
        for c in bundle["characters"]:
            fam = next(p[0][0] for p in prompt.STYLE_PROFILES if prompt.style_profile(c["speech_style"]) == p[1:])
            self.assertEqual(fam, want[c["name_ko"]], c["speech_style"])

    def test_example_lines_pass_validator(self):
        """모든 계열 × 반응 × 앞말 후보 × 뒷말 후보 조합의 예시 대사가 검증기를 통과한다(v4, 11/6)."""
        from llm import prompt
        rows = [(p[0][0], p[1:]) for p in prompt.STYLE_PROFILES] + [("", prompt.DEFAULT_PROFILE)]
        for style, (prefixes, endings, tails) in rows:
            self.assertEqual(set(tails), set(prompt.REACTION_KO))
            persona = f"예시. 말투: {style}" if style else ""
            for reaction in prompt.REACTION_KO:
                for pick in range(len(prefixes) * len(tails[reaction])):
                    fact, line = prompt.example_pair(style, reaction, pick)
                    self.assertNotIn(fact, line) if endings[0] != "소" else self.assertIn(fact, line)
                    r = v.validate(line, {"fact_sentence": fact, "allowed_names": [], "persona": persona}, LEX)
                    with self.subTest(style=style, reaction=reaction, pick=pick):
                        self.assertTrue(r.ok, f"{line} {r.details}")

    def test_pick_covers_every_candidate(self):
        from llm import prompt
        prefixes, _, tails = prompt.style_profile("어린아이 말투")
        lines = {prompt.example_pair("어린아이 말투", "explain", k)[1] for k in range(len(prefixes) * 2)}
        self.assertEqual(len(lines), len(prefixes) * len(tails["explain"]))   # 앞말·뒷말 조합이 모두 다르다
        self.assertTrue(any(not x.startswith("히잉") for x in lines))

    def test_every_example_has_prefix(self):
        """앞말 없는 예시는 모델이 예시 대사를 베끼게 했다(11/6 측정). 모든 계열의 앞말 후보는 비어 있지 않다."""
        from llm import prompt
        for p in (*prompt.STYLE_PROFILES, prompt.DEFAULT_PROFILE):
            self.assertTrue(all(x.strip() for x in p[-3]), p[-3])

    def test_child_examples_vary_by_speech_id(self):
        """같은 발언 ID면 같은 예시(재현), 발언마다는 앞말이 달라진다(아이 말투에 '히잉'만 나오지 않게, 11/6)."""
        from llm.prompt import build_messages as bm
        base = dict(CASES[0]["request"], persona="아이. 말투: 어린아이 말투", reaction="explain")
        ex = {sid: bm(dict(base, speech_id=sid))[2]["content"] for sid in (f"sp_{k}" for k in range(20))}
        self.assertEqual(bm(dict(base, speech_id="sp_3"))[2]["content"], ex["sp_3"])
        self.assertGreaterEqual(len({line.split(" 4일째")[0] for line in ex.values()}), 3)
        self.assertTrue(all("있었어요" in line for line in ex.values()))   # 끝 어미는 계열 어미 그대로


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
