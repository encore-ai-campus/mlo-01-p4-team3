"""E-05-03 콘텐츠 묶음 검사(`tools/check_content.py`)와 `content/bundle_v1.json`."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from engine.domain import serde
from engine.domain.content import ContentBundle
from tools.check_content import ROOT, ContentLoadError, content_problems, load_bundle, run

BUNDLE = "content/bundle_v1.json"
HAS_ENGINE = importlib.util.find_spec("engine.domain.engine") is not None


def _bundle() -> ContentBundle:
    return load_bundle(ROOT / BUNDLE)


class BundleV1(unittest.TestCase):
    """완료 조건 1·2: 인물 8(첫 플레이 6 + 추가 2, E-08-02)·성향 4·비밀 7, 엄격 검증, 별칭 고유."""

    def test_counts_and_required_people(self):
        b = _bundle()
        self.assertEqual(len(b.characters), 8)
        self.assertEqual(len({c.temperament_id for c in b.characters}), 4)
        self.assertEqual(len(b.secrets), 7)
        ids = {c.npc_id for c in b.characters}
        self.assertTrue({"hunjang", "jumo", "blacksmith", "meosum"} <= ids)
        self.assertTrue({"hunter", "monk"} <= ids)  # E-08-02 추가 주민

    def test_secret_fields(self):
        for s in _bundle().secrets:
            with self.subTest(s.secret_id):
                self.assertNotEqual(s.actual_place, s.false_place)
                self.assertTrue(s.confession_line.strip())
                self.assertGreater(s.confession_threshold, 0)
                self.assertTrue(s.eligible_npcs)

    def test_structure_passes(self):
        r = run(BUNDLE)
        self.assertEqual(r.problems, [])
        self.assertIn("base6", r.presets)

    def test_hypothesis_parts_complete(self):
        parts = {c.part for c in _bundle().grudge_clues if c.hypothesis_id == "h1"}
        self.assertEqual(parts, {"identity", "death", "target"})

    def test_expression_targets_cover_bundle(self):
        """완료 조건 5: 첫 플레이 인물 전원이 규격표 9절 표정 대상에 있다."""
        from tools.assets import sources
        spec = sources.load_spec(sources.SPEC_PATH)
        extra = {"hunter", "monk"}  # 7·8인 판 추가 주민(E-08-02)은 기본 그림 1장만 둔다(11/5 PM 결정)
        first_play = {c.npc_id for c in _bundle().characters} - extra
        self.assertEqual(len(first_play), 6)
        self.assertTrue(first_play <= set(spec.expression_targets))
        self.assertFalse(extra & set(spec.expression_targets))

    @unittest.skipUnless(HAS_ENGINE, "엔진 배정기(A-05-01 engine/domain/engine.py) 병합 전")
    def test_base6_assignment_200_seeds(self):
        """완료 조건 3: 엔진 배정기로 6인 판 시드 200개 위반 0건."""
        r = run(BUNDLE, "base6", 200)
        self.assertEqual(sum(r.violations.values()), 0, dict(r.violations))

    @unittest.skipUnless(HAS_ENGINE, "엔진 배정기(A-05-01 engine/domain/engine.py) 병합 전")
    def test_required_presets_assignment_200_seeds(self):
        """E-08-02: 필수 프리셋(short5·base6·base7·large8) 모두 시드 200개 위반 0건."""
        for preset in ("short5", "base6", "base7", "large8"):
            with self.subTest(preset):
                r = run(BUNDLE, preset, 200)
                self.assertEqual(r.problems, [])
                self.assertEqual(sum(r.violations.values()), 0, dict(r.violations))


class Problems(unittest.TestCase):
    """검사 규칙이 위반을 잡는지."""

    def setUp(self):
        self.data = serde.to_jsonable(_bundle())

    def _problems(self, data) -> list[str]:
        return content_problems(serde.from_jsonable(ContentBundle, data))

    def test_alias_substring_of_other(self):
        d = copy.deepcopy(self.data)
        d["characters"][1]["aliases"].append("훈")
        self.assertTrue(any("별칭 겹침" in p for p in self._problems(d)))

    def test_alias_duplicate(self):
        d = copy.deepcopy(self.data)
        d["characters"][1]["aliases"].append(d["characters"][0]["aliases"][0])
        self.assertTrue(any("별칭 중복" in p for p in self._problems(d)))

    def test_one_sided_conflict(self):
        d = copy.deepcopy(self.data)
        for s in d["secrets"]:
            if s["secret_id"] == "taboo_herb":
                s["conflicts_with"] = []
        self.assertTrue(any("한쪽만" in p for p in self._problems(d)))

    def test_same_slot_place_needs_conflict(self):
        d = copy.deepcopy(self.data)
        for s in d["secrets"]:
            if s["secret_id"] in ("grain", "taboo_herb"):
                s["conflicts_with"] = []
        self.assertTrue(any("충돌로 적지 않음" in p for p in self._problems(d)))

    def test_unknown_refs_and_same_place(self):
        d = copy.deepcopy(self.data)
        s = d["secrets"][0]
        s["eligible_npcs"].append("nobody")
        s["grudge_clue_id"] = "clue_none"
        s["false_place"] = s["actual_place"]
        probs = self._problems(d)
        for key in ("없는 인물", "없는 단서", "실제 장소 = 거짓 장소"):
            self.assertTrue(any(key in p for p in probs), key)

    def test_strict_load_rejects_unknown_field(self):
        d = copy.deepcopy(self.data)
        d["characters"][0]["age"] = 50
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "b.json"
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ContentLoadError):
                load_bundle(p)

    def test_unsupported_preset_is_problem(self):
        r = run(BUNDLE, "duo11", 1)  # 주민 11명이 필요한 확장 프리셋(E-08-02 뒤 묶음은 8명)
        self.assertTrue(any("duo11" in p for p in r.problems))
        self.assertFalse(r.ok)


class ResultTask(unittest.TestCase):
    """--result --task: 결과 JSON을 지정한 태스크 증빙 폴더에 쓴다(기본은 E-05-03)."""

    def _written_task(self, argv: list[str]) -> str:
        from unittest import mock
        from tools import check_content
        with mock.patch("tools.evidence_result.write_result") as w, mock.patch("builtins.print"),                 mock.patch.object(check_content, "log"):
            self.assertEqual(check_content.main(argv), 0)
        w.assert_called_once()
        return w.call_args.args[0]

    def test_default_task(self):
        self.assertEqual(self._written_task(["--preset", "base6", "--seeds", "2", "--result"]), "E-05-03")

    def test_task_option(self):
        self.assertEqual(self._written_task(["--preset", "large8", "--seeds", "2", "--result", "--task", "E-08-02"]),
                         "E-08-02")


if __name__ == "__main__":
    unittest.main()
