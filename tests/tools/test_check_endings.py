"""E-07-05 결말 문안 검사(`tools/check_endings.py`)와 `content/endings_v1.json`·원한 단서."""
from __future__ import annotations

import copy
import json
import unittest

from tools.check_endings import (ROOT, EndingsLoadError, ending_metrics, ending_problems, expected_ending_ids,
                                 load_json, run)

H = {"h1"}
V = "content-1.0"


def _good() -> dict:
    lines = [f"문장 {i}이다." for i in range(6)]
    return {"content_version": V, "endings": [
        {"ending_id": "exorcism", "title_ko": "퇴마", "requires": None, "lines_ko": list(lines)},
        {"ending_id": "haewon:h1", "title_ko": "한풀이", "requires": "hypothesis:h1", "lines_ko": list(lines)},
    ]}


class EndingRules(unittest.TestCase):
    """규칙 함수 단독 테스트(파일 없이)."""

    def test_good_passes(self):
        self.assertEqual(ending_problems(_good(), H, V), [])

    def test_expected_ids(self):
        self.assertEqual(expected_ending_ids({"h1"}), {"exorcism", "haewon:h1"})
        self.assertEqual(expected_ending_ids(set()), {"exorcism"})

    def test_missing_or_extra_ending(self):
        d = _good()
        d["endings"].pop()
        self.assertTrue(any("집합" in p for p in ending_problems(d, H, V)))
        d = _good()
        d["endings"].append(copy.deepcopy(d["endings"][0]) | {"ending_id": "haewon:h2", "requires": "hypothesis:h2"})
        ps = ending_problems(d, H, V)
        self.assertTrue(any("없는 가설" in p for p in ps))
        self.assertTrue(any("집합" in p for p in ps))

    def test_duplicate_id(self):
        d = _good()
        d["endings"].append(copy.deepcopy(d["endings"][0]))
        self.assertTrue(any("중복" in p for p in ending_problems(d, H, V)))

    def test_requires(self):
        d = _good()
        d["endings"][0]["requires"] = "hypothesis:h1"
        self.assertTrue(any("null" in p for p in ending_problems(d, H, V)))
        d = _good()
        d["endings"][1]["requires"] = None
        self.assertTrue(any("requires" in p for p in ending_problems(d, H, V)))

    def test_line_count_bounds(self):
        for n, bad in ((4, True), (5, False), (8, False), (9, True)):
            with self.subTest(n=n):
                d = _good()
                d["endings"][0]["lines_ko"] = [f"줄 {i}" for i in range(n)]
                self.assertEqual(any("줄(기준" in p for p in ending_problems(d, H, V)), bad)

    def test_blank_line_and_title(self):
        d = _good()
        d["endings"][1]["lines_ko"][2] = "   "
        d["endings"][1]["title_ko"] = ""
        ps = ending_problems(d, H, V)
        self.assertTrue(any("lines_ko[2]" in p for p in ps))
        self.assertTrue(any("title_ko" in p for p in ps))

    def test_keys_and_version(self):
        d = _good()
        d["extra"] = 1
        d["endings"][0]["note"] = "x"
        ps = ending_problems(d, H, V)
        self.assertTrue(any("최상위 키" in p for p in ps))
        self.assertTrue(any("exorcism: 키" in p for p in ps))
        self.assertTrue(any("content_version" in p for p in ending_problems(_good(), H, "content-1.1")))

    def test_wrong_shapes(self):
        self.assertEqual(ending_problems([], H, V), ["최상위가 객체가 아님"])
        self.assertTrue(any("목록이 아님" in p for p in ending_problems({"content_version": V, "endings": {}}, H, V)))

    def test_metrics(self):
        m = ending_metrics(_good(), ["identity", "identity", "death", "target"], [])
        self.assertEqual(m["clues_identity"]["value"], 2)
        self.assertEqual(m["clues_death"]["value"], 1)
        self.assertEqual(m["endings"]["value"], 2)
        self.assertEqual(m["lines_haewon_h1"], {"value": 6, "unit": "줄"})
        self.assertEqual(m["violations"]["value"], 0)


class EndingsV1(unittest.TestCase):
    """완료 조건: 실제 파일이 검사를 통과하고, 결말 2개·단서 4개(신원 2·죽음 1·대상 1)."""

    def test_repo_files_pass(self):
        r = run()
        self.assertEqual(r.problems, [])
        m = r.metrics
        self.assertEqual((m["clues_identity"]["value"], m["clues_death"]["value"], m["clues_target"]["value"]),
                         (2, 1, 1))
        self.assertEqual(m["endings"]["value"], 2)

    def test_clue_ids_and_links_kept(self):
        """clue_id·part·unlock과 비밀의 grudge_clue_id 연결은 E-05-03 그대로(계약: clue_id 유지)."""
        b = load_json(ROOT / "content/bundle_v1.json")
        got = {c["clue_id"]: (c["part"], c["unlock"], c["hypothesis_id"]) for c in b["grudge_clues"]}
        self.assertEqual(got, {
            "clue_famine": ("identity", "secret:grain 고백", "h1"),
            "clue_valley": ("identity", "secret:taboo_herb 고백", "h1"),
            "clue_debt": ("death", "secret:gambling 고백", "h1"),
            "clue_gate": ("target", "secret:drinking 고백", "h1"),
        })
        links = {s["secret_id"]: s["grudge_clue_id"] for s in b["secrets"] if s["grudge_clue_id"]}
        self.assertEqual(links, {"grain": "clue_famine", "taboo_herb": "clue_valley",
                                 "gambling": "clue_debt", "drinking": "clue_gate"})

    def test_missing_file(self):
        with self.assertRaises(EndingsLoadError):
            run("content/no_such_endings.json")


if __name__ == "__main__":
    unittest.main()
