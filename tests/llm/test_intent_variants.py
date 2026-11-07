"""E-06-02 2단계: 의도 예시 변형의 프롬프트·파싱·보존 검사(도메인)와 선별 흐름(가짜 후보) 테스트."""
import unittest

from llm import intent_variants as iv
from llm.bench.gen_intent_variants import select_variants
from tools.intent_examples import normalize_text

SUS = {"example_id": "ex1_000", "text": "<P1>이 영 수상하오", "label": "suspect", "form": "assertion",
       "slots": {"target": "<P1>"}, "label_source": "authored", "split": "train",
       "split_key": {"user": None, "game_id": None, "time_bucket": "2026-11-06", "lineage_root": "ex1_000"},
       "contrast_group": "cg_1", "notes": ""}
ASK = dict(SUS, example_id="ex1_133", text="<P1>, 어젯밤에 어디 있었소?", label="interrogate_whereabouts",
           form="question", slots={"addressee": "<P1>", "night": -1})
NEG = dict(SUS, example_id="ex1_022", text="<P1>은 하나도 수상하지 않소", label="defend")
DIV = dict(SUS, example_id="ex1_111", text="<P1>의 점괘는 맑소", label="reveal_divination",
           slots={"target": "<P1>", "result": "clear"})
MODERN = dict(SUS, example_id="ex1_010", text="<P1> 좀 수상해", notes="register=modern")
ENTITIES = ["주막", "우물가", "훈장", "창귀"]


class PromptTests(unittest.TestCase):
    def test_messages_shape_and_rules(self):
        msgs = iv.build_messages(SUS)
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user"])
        self.assertIn(SUS["text"], msgs[-1]["content"])
        self.assertIn("하오체", msgs[0]["content"])
        self.assertNotIn("?", msgs[1]["content"].splitlines()[0])   # 단언 원문에는 단언 예시
        self.assertIn("?", iv.build_messages(ASK)[1]["content"])
        self.assertIn("요즘 말투", iv.build_messages(MODERN)[0]["content"])
        self.assertIn("점괘", iv.build_messages(DIV)[0]["content"])

    def test_parse_output_strips_numbering_and_quotes(self):
        raw = '1. <P1>이 수상하오\n\n- "<P1> 참 수상하오"\n③ 수상하오 <P1>\n넷째 줄'
        self.assertEqual(iv.parse_output(raw), ["<P1>이 수상하오", "<P1> 참 수상하오", "수상하오 <P1>"])
        self.assertEqual(iv.parse_output(""), [])


class CheckTests(unittest.TestCase):
    def test_passes_simple_rewrite(self):
        self.assertIsNone(iv.check_variant(SUS, "<P1>이 아무래도 수상하오", ENTITIES))
        self.assertIsNone(iv.check_variant(ASK, "<P1>, 지난밤엔 어디 있었소?", ENTITIES))
        self.assertIsNone(iv.check_variant(NEG, "<P1>은 조금도 수상하지 않소", ENTITIES))
        self.assertIsNone(iv.check_variant(DIV, "<P1>의 점괘가 맑게 나왔소", ENTITIES))
        self.assertIsNone(iv.check_variant(MODERN, "<P1> 진짜 수상해요", ENTITIES))   # 현대어 행은 요 허용

    def test_rejects_with_reason(self):
        cases = [
            (SUS, "", "empty"), (SUS, "<P1>이 영 수상하오", "same_as_root"),
            (SUS, "<P1>이 " + "아주 " * 20 + "수상하오", "too_long"),
            (SUS, "<P> 자네가 수상하오", "placeholder"), (SUS, "<P1>과 <P2>가 수상하오", "placeholder"),
            (SUS, "이 사람 수상하오", "placeholder"), (SUS, "<P1>이 수상하오?", "form"),
            (ASK, "<P1>, 어젯밤에 어디 있었소", "form"), (SUS, "<P1>은 수상하지 않소", "negation"),
            (NEG, "<P1>은 수상하오", "negation"), (SUS, "<P1> OK 수상하오", "foreign"),
            (SUS, "<P1>이 수상해요", "register"), (SUS, "<P1>이 주막에서 수상하오", "new_entity"),
            (SUS, "<P1>이 창귀 같소", "new_entity"), (DIV, "<P1>의 점괘는 탁하오", "divination"),
            (DIV, "<P1>이 맑소", "divination"), (DIV, "<P1>의 기운이 좋소", "divination"),
            (SUS, "<P1>이 아무래도 수상해 보이오", "prompt_copy"),
            (dict(SUS, text="이번엔 지켜보겠소", label="silence", slots={}), "이번엔 수상한 자를 지켜보겠소", "new_suspicion"),
        ]
        for row, text, reason in cases:
            self.assertEqual(iv.check_variant(row, text, ENTITIES), reason, text)
        self.assertTrue(set(r for _, _, r in cases) <= set(iv.REJECT_REASONS))

    def test_variant_row_follows_lineage(self):
        v = iv.variant_row(SUS, 2, "<P1>이 아무래도 수상하오")
        self.assertEqual(v["example_id"], "ex1_000_v2")
        self.assertEqual(v["label_source"], "generated")
        self.assertEqual(v["split_key"]["lineage_root"], "ex1_000")
        for key in ("label", "form", "slots", "contrast_group", "split", "notes"):
            self.assertEqual(v[key], SUS[key])


class SelectTests(unittest.TestCase):
    """가짜 후보(모델 없이)로 선별 흐름을 확인한다."""

    def test_select_counts_and_dedup(self):
        candidates = {"ex1_000": ["<P1>이 아무래도 수상하오", "<P1>이 아무래도 수상하오!", "<P1>이 수상해요",
                                  "<P1> 참 수상하오", "<P1> 영 수상쩍소", "<P1> 너무 수상하오"],
                      "ex1_133": ["<P1>, 지난밤엔 어디 있었소?", "기존 문장이오"]}
        accepted, rejected = select_variants([SUS, ASK], candidates, ["기존 문장이오"], ENTITIES,
                                             normalize_text, lambda t: "010" not in t)
        self.assertEqual(accepted["ex1_000"], ["<P1>이 아무래도 수상하오", "<P1> 참 수상하오", "<P1> 영 수상쩍소"])
        self.assertEqual(accepted["ex1_133"], ["<P1>, 지난밤엔 어디 있었소?"])
        self.assertEqual((rejected["duplicate"], rejected["register"], rejected["over_limit"]), (1, 1, 1))
        self.assertEqual(rejected["placeholder"], 1)   # "기존 문장이오"는 자리표시자 없음이 먼저

    def test_reviewed_variants_count_against_limit(self):
        cands = {"ex1_000": ["<P1> 참 수상하오", "<P1> 영 수상쩍소", "<P1> 너무 수상하오"]}
        accepted, rejected = select_variants([SUS], cands, [], ENTITIES, normalize_text, lambda t: True,
                                             {"ex1_000": 2})
        self.assertEqual(accepted["ex1_000"], ["<P1> 참 수상하오"])
        self.assertEqual(rejected["over_limit"], 2)

    def test_masked_text_rejected(self):
        accepted, rejected = select_variants([SUS], {"ex1_000": ["<P1> 010 수상하오"]}, [], [], normalize_text,
                                             lambda t: "010" not in t)
        self.assertEqual(accepted["ex1_000"], [])
        self.assertEqual(rejected["foreign"] + rejected["masked"], 1)


if __name__ == "__main__":
    unittest.main()
