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


@unittest.skipUnless(HAS_ENGINE, "엔진 배정기 병합 전")
class ExtraCast(unittest.TestCase):
    """E-08-02 11/11 사용자 결정: 5·6인 판은 핵심 6명·비밀 6개에서만 뽑아 기존(content-1.0.1) 배정과 같고,
    확장 인물(사냥꾼·스님)과 확장 비밀(rite)은 7·8인 판에만 나온다."""

    @staticmethod
    def _core_only(b: ContentBundle) -> ContentBundle:
        d = serde.to_jsonable(b)
        extra_n, extra_s = set(d["extra_npcs"]), set(d["extra_secrets"])
        d["characters"] = [c for c in d["characters"] if c["npc_id"] not in extra_n]
        d["secrets"] = [s for s in d["secrets"] if s["secret_id"] not in extra_s]
        for s in d["secrets"]:
            s["eligible_npcs"] = [n for n in s["eligible_npcs"] if n not in extra_n]
        d["extra_npcs"], d["extra_secrets"] = [], []
        return serde.from_jsonable(ContentBundle, d)

    def test_bundle_marks_extra_cast(self):
        b = _bundle()
        self.assertEqual(sorted(b.extra_npcs), ["hunter", "monk"])
        self.assertEqual(b.extra_secrets, ["rite"])

    def test_small_presets_match_core_bundle(self):
        """핵심만 남긴 묶음(= 추가 전 묶음)과 같은 시드에서 배정이 한 글자도 다르지 않다."""
        from engine.domain.engine import make_setup
        from engine.domain.model import PRESETS
        full, core = _bundle(), self._core_only(_bundle())
        for pid in ("short5", "base6"):
            for seed in range(40):
                with self.subTest(preset=pid, seed=seed):
                    a = make_setup(PRESETS[pid], full, seed, "g")
                    b = make_setup(PRESETS[pid], core, seed, "g")
                    self.assertEqual(serde.to_jsonable(a), serde.to_jsonable(b))

    def test_extra_cast_only_in_large_presets(self):
        from engine.domain.engine import make_setup
        from engine.domain.model import PRESETS
        b = _bundle()
        extra = set(b.extra_npcs)
        for pid in ("short5", "base6", "base7", "large8"):
            seen_npc = seen_rite = 0
            for seed in range(100):
                a = make_setup(PRESETS[pid], b, seed, "g").assignment
                seen_npc += bool(extra & set(a.residents))
                seen_rite += "rite" in a.secrets.values()
            with self.subTest(pid):
                if pid in ("short5", "base6"):
                    self.assertEqual((seen_npc, seen_rite), (0, 0))
                else:
                    self.assertGreater(seen_npc, 0)

    def test_unknown_extra_ids_are_problems(self):
        d = serde.to_jsonable(_bundle())
        d["extra_npcs"].append("nobody")
        d["extra_secrets"].append("no_secret")
        probs = content_problems(serde.from_jsonable(ContentBundle, d))
        self.assertTrue(any("확장 인물" in p for p in probs))
        self.assertTrue(any("확장 비밀" in p for p in probs))


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
