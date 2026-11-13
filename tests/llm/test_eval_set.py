"""E-07-07 완료 조건 2: 평가 세트. 같은 명령 = 바이트까지 같은 파일, 100건, 계약 필드 그대로, 허용 목록 포함."""
from __future__ import annotations

import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from engine.ports import RenderRequest
from llm import eval_set
from llm import validator as v

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "fixtures" / "llm" / "interrogation_eval.json"
CONTRACT_FIELDS = [f.name for f in dataclasses.fields(RenderRequest)]


def generate(seed: int) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "e.json"
        with redirect_stdout(io.StringIO()):
            import os
            old = os.environ.get("PALDO_LOG_DIR")
            os.environ["PALDO_LOG_DIR"] = d
            try:
                eval_set.main(["--seed", str(seed), "--out", str(out)])
            finally:
                if old is None:
                    os.environ.pop("PALDO_LOG_DIR", None)
                else:
                    os.environ["PALDO_LOG_DIR"] = old
        return out.read_bytes()


class EvalSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = generate(1)
        cls.data = json.loads(cls.raw.decode("utf-8"))

    def test_same_command_same_bytes(self):
        self.assertEqual(generate(1), self.raw)

    def test_committed_file_matches_seed1(self):
        self.assertEqual(COMMITTED.read_bytes(), self.raw, "python -m llm.eval_set --seed 1 로 다시 만든다")

    def test_fixed_serialization(self):
        text = self.raw.decode("utf-8")
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))
        self.assertNotIn("\\u", text)     # ensure_ascii=False
        self.assertNotIn("\r", text)
        self.assertEqual(text, eval_set.dumps(self.data))

    def test_other_seed_differs(self):
        self.assertNotEqual(generate(2), self.raw)

    def test_100_cases_with_contract_request(self):
        cases = self.data["cases"]
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({c["case_id"] for c in cases}), 100)
        for c in cases:
            req = c["request"]
            self.assertEqual(sorted(req), sorted(CONTRACT_FIELDS), c["case_id"])   # 계약 필드 그대로
            RenderRequest(**req)   # 계약 타입으로 만들 수 있다
            self.assertTrue(req["fact_sentence"])
            self.assertTrue(req["allowed_names"])
            self.assertIn("allowed_places", c)
            self.assertIn(req["fact_sentence"], req["fallback_text"])   # 폴백 동등성

    def test_kinds_and_reactions(self):
        kinds = {}
        for c in self.data["cases"]:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        self.assertEqual(kinds, dict(eval_set.KIND_COUNTS))
        self.assertEqual({c["request"]["reaction"] for c in self.data["cases"]}, {"explain", "evade", "counter", "confess"})

    def test_facts_and_fallbacks_pass_validator(self):
        lex = v.Lexicon.from_json(self.data["lexicon"])
        for c in self.data["cases"]:
            req = c["request"]
            el = v.fact_elements(req["fact_sentence"], lex)
            self.assertEqual(sorted(el.places), c["allowed_places"])
            self.assertTrue(el.names <= set(req["allowed_names"]), c["case_id"])
            for text in (req["fact_sentence"], req["fallback_text"]):
                r = v.validate(text, req, lex, c["allowed_places"])
                self.assertTrue(r.ok, f"{c['case_id']}: {r.details}")

    def test_contract_examples_untouched(self):
        ex = json.loads((ROOT / "fixtures" / "llm" / "interrogation_requests.json").read_text(encoding="utf-8"))
        self.assertEqual([r["job_id"] for r in ex], ["job_001", "job_002", "job_003"])

    def test_object_particle(self):
        self.assertEqual(eval_set.object_particle("주모"), "를")
        self.assertEqual(eval_set.object_particle("약초꾼"), "을")


if __name__ == "__main__":
    unittest.main()
