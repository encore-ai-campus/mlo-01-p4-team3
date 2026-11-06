"""E-06-01 템플릿 대사 검사(`tools/check_templates.py`)와 `content/bundle_v1.json`의 templates."""
from __future__ import annotations

import copy
import unittest

from engine.domain import serde
from engine.domain.content import ContentBundle
from engine.domain.model import CommandKind, ReactionIntent
from tools.check_content import ROOT, load_bundle
from tools.check_templates import (MIN_VARIANTS, PLACEHOLDERS, combo_table, engine_pick, placeholder_fields,
                                   template_problems, run)

BUNDLE = "content/bundle_v1.json"
TEMPS = ("scholar", "keen", "hothead", "timid")
# E-06-01 전 공용 템플릿(엔진이 쓰는 ID·선택 필드). 지우거나 바꾸면 엔진 호환이 깨진다.
LEGACY = {
    "t_suspect": ("suspect", None, None), "t_defend": ("defend", None, None),
    "t_contra": ("point_contradiction", None, None), "t_explain": ("explain", None, None),
    "t_silence": ("silence", None, None), "t_reveal": ("reveal_divination", None, None),
    "t_whereabouts": ("explain", None, None), "t_ans_explain": ("interrogate", None, "explain"),
    "t_ans_evade": ("interrogate", None, "evade"), "t_ans_counter": ("interrogate", "hothead", "counter"),
    "t_ans_confess": ("interrogate", None, "confess"),
}


def _bundle() -> ContentBundle:
    return load_bundle(ROOT / BUNDLE)


class PlaceholderFields(unittest.TestCase):
    """자리표시자 추출 규칙."""

    def test_names(self):
        self.assertEqual(placeholder_fields("{night}일째 밤 {slot}에 {place}"), (["night", "slot", "place"], []))
        self.assertEqual(placeholder_fields("자리표시자 없음"), ([], []))

    def test_unbalanced_braces(self):
        for text in ("{target 수상하오", "target} 수상하오", "{fact"):
            with self.subTest(text):
                self.assertTrue(placeholder_fields(text)[1])

    def test_positional_attr_spec(self):
        for text in ("{} 수상하오", "{0} 수상하오", "{target.name}", "{target[0]}", "{target!r}", "{target:>5}"):
            with self.subTest(text):
                self.assertTrue(placeholder_fields(text)[1])

    def test_escaped_braces_ok(self):
        self.assertEqual(placeholder_fields("{{중괄호}} {fact}"), (["fact"], []))


class BundleTemplates(unittest.TestCase):
    """완료 조건 1~4를 실제 묶음으로 확인."""

    def test_check_passes(self):
        r = run(BUNDLE)
        self.assertEqual([p.message for p in r.problems], [])
        self.assertGreaterEqual(r.min_variants, MIN_VARIANTS)

    def test_combo_table(self):
        """조건 1: 행동 유형 × 성향 4종(점괘 공개·행적 진술은 공용)."""
        table = run(BUNDLE).combo_table
        for row in ("suspect", "defend", "point_contradiction", "explain", "silence", "interrogate/explain",
                    "interrogate/evade", "interrogate/counter", "interrogate/confess"):
            for col in ("공용",) + TEMPS:
                with self.subTest(row=row, col=col):
                    self.assertEqual(table[row][col], 1)
        self.assertEqual(table["whereabouts"]["공용"], 1)
        self.assertEqual(table["reveal_divination"]["공용"], 1)

    def test_fact_in_every_interrogate_variant(self):
        """조건 2: 캐묻기 반응의 모든 변형에 {fact}."""
        for t in _bundle().templates:
            if t.kind is CommandKind.INTERROGATE:
                for v in t.variants:
                    with self.subTest(t.template_id):
                        self.assertIn("{fact}", v)

    def test_placeholders_in_contract(self):
        """조건 3: 계약 목록 밖 자리표시자·중괄호 오류 0."""
        for t in _bundle().templates:
            for v in t.variants:
                names, errors = placeholder_fields(v)
                with self.subTest(v):
                    self.assertEqual(errors, [])
                    self.assertLessEqual(set(names), PLACEHOLDERS)

    def test_legacy_ids_and_fields_kept(self):
        got = {t.template_id: (t.kind.value, t.temperament_id, t.reaction.value if t.reaction else None)
               for t in _bundle().templates}
        for tid, fields in LEGACY.items():
            with self.subTest(tid):
                self.assertEqual(got.get(tid), fields)

    def test_engine_picks_same_common_templates(self):
        """엔진 `template()` 규칙으로 고르는 공용 템플릿이 E-06-01 전과 같고, 첫 변형이 엔진 인자로 format된다."""
        ts = _bundle().templates
        expect = {(CommandKind.SUSPECT, None): "t_suspect", (CommandKind.DEFEND, None): "t_defend",
                  (CommandKind.POINT_CONTRADICTION, None): "t_contra", (CommandKind.EXPLAIN, None): "t_explain",
                  (CommandKind.SILENCE, None): "t_silence",
                  (CommandKind.INTERROGATE, ReactionIntent.EXPLAIN): "t_ans_explain",
                  (CommandKind.INTERROGATE, ReactionIntent.EVADE): "t_ans_evade",
                  (CommandKind.INTERROGATE, ReactionIntent.COUNTER): "t_ans_counter_common",
                  (CommandKind.INTERROGATE, ReactionIntent.CONFESS): "t_ans_confess"}
        for (k, r), tid in expect.items():
            with self.subTest(tid):
                t = engine_pick(ts, k, r)
                self.assertEqual(t.template_id, tid)
                if k is CommandKind.INTERROGATE:
                    self.assertIn("사실", t.variants[0].format(fact="사실"))
                else:
                    t.variants[0].format(target="아무개")
        self.assertEqual(placeholder_fields(engine_pick(ts, CommandKind.EXPLAIN, None).variants[0])[0], [])

    def test_no_names_in_templates(self):
        b = _bundle()
        names = {c.name_ko for c in b.characters} | {a for c in b.characters for a in c.aliases}
        for t in b.templates:
            for v in t.variants:
                with self.subTest(v):
                    self.assertFalse([n for n in names if n in v])


class Problems(unittest.TestCase):
    """검사 규칙이 위반을 잡는지."""

    def setUp(self):
        self.data = serde.to_jsonable(_bundle())

    def _codes(self, data) -> set[str]:
        return {p.code for p in template_problems(serde.from_jsonable(ContentBundle, data))}

    def _t(self, d, tid):
        return next(t for t in d["templates"] if t["template_id"] == tid)

    def test_fact_missing(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_ans_evade_keen")["variants"][1] = "더 묻지 말게."
        self.assertIn("fact_missing", self._codes(d))

    def test_placeholder_typo(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_suspect_keen")["variants"][0] = "{taget}, 수상한데?"
        self._t(d, "t_defend_keen")["variants"][0] = "{target 은(는) 아니야."
        self.assertIn("placeholder_typo", self._codes(d))
        self.assertEqual(sum(p.code == "placeholder_typo"
                             for p in template_problems(serde.from_jsonable(ContentBundle, d))), 2)

    def test_not_allowed_and_engine_format(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_suspect_scholar")["variants"][0] = "{fact} 수상하오."
        codes = self._codes(d)
        self.assertIn("placeholder_not_allowed", codes)
        self.assertIn("engine_format_error", codes)

    def test_explain_sentence_no_placeholder(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_explain")["variants"][0] = "{target}, 나는 상관없소."
        self.assertIn("placeholder_not_allowed", self._codes(d))

    def test_whereabouts_needs_all_fields(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_whereabouts")["variants"][1] = "{night}일째 밤엔 {place}에 있었소."
        self.assertIn("placeholder_missing", self._codes(d))

    def test_whereabouts_before_explain_breaks_engine_order(self):
        d = copy.deepcopy(self.data)
        ts = d["templates"]
        i, j = (next(n for n, t in enumerate(ts) if t["template_id"] == x) for x in ("t_explain", "t_whereabouts"))
        ts[i], ts[j] = ts[j], ts[i]
        self.assertIn("engine_order", self._codes(d))

    def test_missing_and_duplicate_combo(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_silence_timid")["temperament_id"] = "keen"
        codes = self._codes(d)
        self.assertIn("missing_combo", codes)
        self.assertIn("duplicate_combo", codes)

    def test_duplicate_id_unknown_temperament_reaction(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_contra_keen")["template_id"] = "t_contra"
        self._t(d, "t_explain_timid")["temperament_id"] = "brave"
        self._t(d, "t_silence_keen")["reaction"] = "evade"
        codes = self._codes(d)
        for c in ("duplicate_id", "unknown_temperament", "reaction_mismatch"):
            self.assertIn(c, codes)
        table = combo_table(serde.from_jsonable(ContentBundle, d))   # 없는 성향이 있어도 표를 만든다
        self.assertEqual(table["explain"]["brave"], 1)

    def test_too_few_and_empty_variants(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_defend_hothead")["variants"] = ["{target}은(는) 아니다!"]
        self._t(d, "t_defend_timid")["variants"][1] = "  "
        codes = self._codes(d)
        self.assertIn("too_few_variants", codes)
        self.assertIn("empty_variant", codes)

    def test_name_in_template(self):
        d = copy.deepcopy(self.data)
        self._t(d, "t_defend_scholar")["variants"][1] = "{target}은(는) 주모와 함께 있었소."
        self.assertIn("name_in_template", self._codes(d))


if __name__ == "__main__":
    unittest.main()
