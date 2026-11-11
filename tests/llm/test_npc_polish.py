"""회의 발언 다듬기 도메인 규칙(E-11-13): 요청 조립(공개 정보만)·프롬프트·출력 검사·결과 선택."""
import ast
import inspect
import json
import sys
import unittest
from pathlib import Path

from llm import npc_polish as P
from llm.validator import Lexicon

LEX = Lexicon(places={"우물가": ("우물가",), "서낭당": ("서낭당",), "주막": ("주막",), "장터": ("장터",)},
              names={n: (n,) for n in ("주모", "훈장", "과부", "머슴", "약초꾼", "대장장이")})
STYLE = "점잖은 하오체. 옛글 문자를 섞고, 남을 '자네'라 부르며 말끝을 길게 끈다."
ROOT = Path(__file__).resolve().parents[2]


def req(text="훈장이 수상하오.", kind="suspect"):
    return P.build_request("s1", kind, text, STYLE, LEX)


def ok(out, r=None):
    return P.check(out, r or req(), LEX)[1]


class RequestTest(unittest.TestCase):
    def test_request_holds_public_fields_only(self):
        """허용 이름은 원문에 나온 인물뿐이고 요청에는 원문·말투·종류만 들어간다."""
        r = req("훈장이 주모를 감싸는 게 수상하오.")
        self.assertEqual(r.allowed_names, ("훈장", "주모"))
        self.assertEqual(set(r.as_dict()), {"speech_id", "kind", "text", "persona", "allowed_names"})

    def test_same_public_input_gives_identical_bytes_for_any_speaker_role(self):
        """씐 자 화자와 마을 사람 화자에게 같은 공개 원문·말투·종류를 넣으면 요청 바이트가 같다."""
        possessed = P.request_body(P.build_request("s1", "defend", "과부는 그런 사람이 아니오.", STYLE, LEX))
        villager = P.request_body(P.build_request("s1", "defend", "과부는 그런 사람이 아니오.", STYLE, LEX))
        self.assertEqual(json.dumps(possessed, ensure_ascii=False).encode(), json.dumps(villager, ensure_ascii=False).encode())
        self.assertTrue(possessed["cache_prompt"])

    def test_builder_takes_no_truth_objects_and_imports_only_stdlib_or_llm(self):
        """요청 조립은 문자열만 받는다. 모듈은 표준 라이브러리와 같은 llm 도메인만 쓰고 복기 입력 조립과 코드를 나누지 않는다."""
        params = list(inspect.signature(P.build_request).parameters)
        self.assertEqual(params, ["speech_id", "kind", "text", "persona", "lexicon"])
        tree = ast.parse((ROOT / "llm" / "npc_polish.py").read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        for mod in mods:
            top = mod.split(".")[0]
            self.assertTrue(top in sys.stdlib_module_names or top == "__future__" or mod in
                            ("llm.free_answer", "llm.prompt", "llm.validator"), mod)
        source = (ROOT / "llm" / "npc_polish.py").read_text(encoding="utf-8")
        for word in ("GameTruth", "truth", "belief", "secret", "review_narration"):
            self.assertNotIn(word, source.replace("판 진실·믿음·비밀", ""))

    def test_prompt_puts_source_last_and_kind_word(self):
        msgs = P.build_messages(req())
        self.assertEqual(msgs[-1], {"role": "user", "content": "발언: 훈장이 수상하오."})
        self.assertIn("의심", msgs[0]["content"])

    def test_eligible_kinds(self):
        self.assertTrue(P.eligible("suspect", "훈장이 수상하오."))
        self.assertFalse(P.eligible("silence", "……"))
        self.assertFalse(P.eligible("interrogate", "주막에 있었소."))


class CheckTest(unittest.TestCase):
    def test_pass_ending_only_and_same(self):
        self.assertTrue(ok("훈장이 수상하오.").ok)
        self.assertTrue(ok("훈장이 참 수상하구려.").ok)
        self.assertTrue(ok("허, 훈장이 수상하오.").ok)

    def test_codex_counterexamples_added_sentence(self):
        for out in ("훈장이 수상하오. 실은 반대요.", "훈장이 수상하오. 과부가 보았소."):
            with self.subTest(out=out):
                self.assertIn(P.ADDED_SENTENCE, ok(out).reasons)

    def test_role_words(self):
        for out in ("훈장이 창귀요.", "훈장이 범인이오."):
            with self.subTest(out=out):
                self.assertFalse(ok(out).ok)
                self.assertIn("forbidden_entity", ok(out).reasons)

    def test_target_swap(self):
        for out in ("주모가 수상하오.", "과부가 수상하오."):
            with self.subTest(out=out):
                self.assertFalse(ok(out).ok)

    def test_judgment_flip(self):
        defend = req("과부는 그런 사람이 아니오.", "defend")
        for out, r in (("훈장은 믿을 만하오.", req()), ("훈장은 결백하오.", req()),
                       ("과부는 수상한 사람이오.", defend), ("과부가 거짓을 말하오.", defend)):
            with self.subTest(out=out):
                v = ok(out, r)
                self.assertFalse(v.ok)

    def test_new_person_or_place(self):
        for out in ("훈장이 장터에서 수상하오.", "훈장과 머슴이 수상하오."):
            with self.subTest(out=out):
                self.assertIn("forbidden_entity", ok(out).reasons)

    def test_nameless_source_rejects_any_mention(self):
        r = req("두 사람 말이 서로 맞지 않소.", "point_contradiction")
        self.assertTrue(ok("허, 두 사람 말이 서로 맞지 않소이다.", r).ok)
        for out in ("훈장과 주모 말이 서로 맞지 않소.", "두 사람 말이 우물가에서 맞지 않소."):
            with self.subTest(out=out):
                self.assertIn("forbidden_entity", ok(out, r).reasons)

    def test_added_sentences_and_length(self):
        for out in ("훈장이 수상하오. 정말이오.", "훈장이 수상하오, 그리고 어제부터 내내 눈빛이 이상하였소."):
            with self.subTest(out=out):
                self.assertFalse(ok(out).ok)

    def test_new_noun_candidate(self):
        v = ok("훈장 나리 수염이 수상하오.")
        self.assertFalse(v.ok)

    def test_two_sentence_source_allows_two(self):
        r = req("훈장, 수상하다. 넌 말이 안 된다!", "suspect")
        self.assertTrue(ok("훈장, 수상하다. 넌 말이 안 된다!", r).ok)
        self.assertIn(P.ADDED_SENTENCE, ok("훈장, 수상하다. 넌 말이 안 된다! 정말이다.", r).reasons)


class InterjectionTest(unittest.TestCase):
    """원문 앞 감탄사·추임새는 인물 말투 계열별 허용 목록에 있을 때만 통과(11/11 사용자 승인)."""

    def test_allowed_pieces_of_own_family_and_common(self):
        for out in ("에헴, 훈장이 수상하오.", "허허, 훈장이 수상하오.", "거 참, 훈장이 수상하오.",
                    "거, 훈장이 수상하오.", "훈장이 수상하구려."):
            with self.subTest(out=out):
                self.assertTrue(ok(out).ok, ok(out))

    def test_typo_and_other_family_rejected(self):
        """오타("에헝")와 다른 계열 감탄사(대장장이의 "쳇")는 거부한다."""
        for out in ("에헝, 훈장이 수상하오.", "쳇. 훈장이 수상하오.", "어머나, 훈장이 수상하오."):
            with self.subTest(out=out):
                self.assertIn(P.INTERJECTION, ok(out).reasons)
        # 앞말에 원문 첫 어절과 같은 낱말("허")이 있어도 실제 원문 시작 앞을 본다(Codex 지적)
        r = P.build_request("s3", "suspect", "허, 훈장이 수상하오.", STYLE, LEX)
        self.assertEqual(P.added_prefix("허, 에헝, 훈장이 수상하오.", r.text), ("허", "에헝"))
        self.assertIn(P.INTERJECTION, P.check("허, 에헝, 훈장이 수상하오.", r, LEX)[1].reasons)

    def test_stammer_family_and_source_start_kept(self):
        """더듬는 말투는 "예, 예…"를 붙일 수 있고, 원문이 이미 추임새로 시작하면 그 부분은 원문으로 본다."""
        stammer = "더듬는 말투(~습니다요)."
        r = P.build_request("s2", "point_contradiction", "그, 그런데… 말씀이 앞뒤가 다른 것 같습니다요.", stammer, LEX)
        self.assertTrue(P.check("예, 예… 그런데 말씀이 앞뒤가 다른 것 같습니다요.", r, LEX)[1].ok)
        self.assertEqual(P.added_prefix("저, 저기… 훈장이 이상하오.", "저, 저기… 훈장이 이상하오."), ())

    def test_changed_start_is_rejected_and_live_result_combines(self):
        self.assertEqual(P.interjection_problem("그 밤 한때는 집에 있었소.", "그날 한밤엔 집에 있었소.", STYLE),
                         "원문 시작을 찾지 못함")
        base = P.ValidationResult(True)
        self.assertEqual(P.with_interjection_check("에헝. 그날 한밤엔 집에 있었소.", base, "그날 한밤엔 집에 있었소.",
                                                   STYLE).reasons, (P.INTERJECTION,))


class SelectTest(unittest.TestCase):
    def test_shown_text_prefers_final_then_ready_then_template(self):
        self.assertEqual(P.shown_text({"variant": "template", "text": None}, "다듬음", "원문")[0], "template")
        self.assertEqual(P.shown_text(None, "다듬음", "원문"), ("polished", "다듬음"))
        self.assertEqual(P.shown_text(None, None, "원문"), ("template", "원문"))

    def test_fix_variant_falls_back_when_off_or_not_ready(self):
        self.assertEqual(P.fix_variant("polished", True, "다듬음"), "polished")
        self.assertEqual(P.fix_variant("polished", False, "다듬음"), "template")
        self.assertEqual(P.fix_variant("polished", True, None), "template")
        self.assertEqual(P.fix_variant("template", True, "다듬음"), "template")


if __name__ == "__main__":
    unittest.main()
