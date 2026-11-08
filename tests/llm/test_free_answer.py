"""E-08-13 자유 질문 답변 규칙(`llm.free_answer`) 테스트: 반례 기대 판정 표, 스트리밍 파싱, 문장 단위 판정, 폴백."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from llm.free_answer import (EVIDENCE_RANGE, FORMAT_LENGTH, LEAK, MAX_UNKNOWN_NOUNS, NO_FACT_TEXT, PAIR_BINDING,
                             POLARITY, UNGROUNDED, AnswerStream, Fact, FreeQuestion, SentenceGate, Turn,
                             build_messages, check_answer, fallback, noun_candidates, output_regex,
                             parse_evidence_line, polarity, split_clauses, split_sentences)
from llm.validator import lexicon_from_bundle

ROOT = Path(__file__).resolve().parents[2]
LEX = lexicon_from_bundle(json.loads((ROOT / "fixtures" / "content_min" / "bundle.json").read_text(encoding="utf-8")))

# 말하는 NPC 훈장. "갑-우물·을-주막" = 대장장이-우물가(f1)·주모-주막(f2)
ITEM = FreeQuestion(
    npc_name="훈장", persona="훈장. 말투: 점잖은 하오체, 문자를 섞음",
    facts=(Fact("f1", "1일째 밤 한밤엔 우물가에서 대장장이를 봤소."),
           Fact("f2", "1일째 밤 초저녁엔 주막에서 주모를 봤소."),
           Fact("f3", "1일째 밤 초저녁엔 주막에 있었소."),
           Fact("f4", "1일째 밤 한밤엔 집에 있었소."),
           Fact("f5", "1일째 밤 한밤엔 서낭당에서 아무도 못 봤소.")),
    question="훈장, 어째서 대장장이를 의심하오?",
    history=(Turn("훈장, 초저녁엔 뭘 했소?", "1일째 밤 초저녁엔 주막에 있었소."),),
    forbidden_facts=("1일째 밤 한밤엔 서낭당 뒤에서 투전판에 끼었소.",),
)

# 반례 기대 판정 표(E-08-13 2번). (출력, 기대 사유 또는 None=통과, 설명). 사유는 "대표 사유에 포함"으로 본다.
PASS_CASES = [
    ("근거: f1\n허허, 한밤엔 우물가에서 대장장이를 봤소.", "사실 그대로 + 말투"),
    ("근거: f1\n대장장이를 한밤에 우물가에서 봤으니 수상하오.", "의심 이유로 사실 인용"),
    ("근거: f3,f4\n초저녁엔 주막에 있었고, 한밤엔 집에 있었소.", "두 사실 결합(절이 나뉨)"),
    ("근거: f1,f2\n대장장이는 한밤에 우물가에 있었고 주모는 초저녁에 주막에 있었소.", "두 사실 결합(본 사람의 행적)"),
    ("근거: f1\n대장장이를 봤소.", "요약(부분 선택)"),
    ("근거: f5\n한밤엔 서낭당에서 아무도 못 봤소.", "부정이 사실에 들어 있음"),
    ("근거: 없음\n그건 잘 모르겠소.", "근거 없음 + 모름"),
    ("근거: f2, f3\n초저녁엔 주막에서 주모를 봤소.", "근거 목록 공백 허용"),
]

REJECT_CASES = [
    ("근거: f1,f2\n대장장이는 주막에 있었소.", PAIR_BINDING, "바꿔치기 갑-우물·을-주막 → 갑-주막"),
    ("근거: f3,f4\n초저녁엔 집에 있었소.", PAIR_BINDING, "시간-장소 바꿔치기"),
    ("근거: f1\n1일째 밤 초저녁엔 우물가에서 대장장이를 봤소.", PAIR_BINDING, "시간 바꿈(초저녁은 f1에 없음)"),
    ("근거: f1\n대장장이는 우물가에 없었소.", POLARITY, "부정 덧붙임"),
    ("근거: f1\n대장장이가 우물에 없었다는 말은 사실이 아니오.", POLARITY, "이중 부정·전언: 판별 불가로 거부"),
    ("근거: f5\n한밤엔 서낭당에 있었소.", POLARITY, "사실의 부정을 지움(못 봤소 → 있었소)"),
    ("근거: f1\n대장장이는 창귀요.", LEAK, "역할어"),
    ("근거: f1\n한밤엔 우물가에서 대장장이를 봤소. 대장장이가 범인이오.", LEAK, "둘째 문장 역할어"),
    ("근거: f4\n한밤엔 서낭당 뒤 투전판에 있었소.", LEAK, "비공개 사실(쌍·내용어)"),
    ("근거: f1\n피해자는 독살됐소.", UNGROUNDED, "쌍 없는 사건 단정"),
    ("근거: f1\n한밤엔 우물가에서 대장장이가 피투성이였소.", UNGROUNDED, "사건 단정 어휘(쌍은 맞음)"),
    ("근거: f1\n한밤에 우물가에서 대장장이를 봤고 포수도 봤소.", UNGROUNDED, "인용 사실 밖 인물"),
    ("근거: f3\n초저녁엔 주막에 있었소. 그뿐이오.", UNGROUNDED, "인용 사실 요소 없는 문장"),
    ("근거: f1\n한밤엔 우물가에서 대장장이가 도끼와 밧줄과 괭이를 들고 있었소.", UNGROUNDED, "모르는 명사 3개"),
    ("근거: 없음\n대장장이는 모르오.", UNGROUNDED, "근거 없음인데 인물 언급"),
    ("근거: f9\n한밤엔 집에 있었소.", EVIDENCE_RANGE, "허용 목록 밖 ID"),
    ("한밤엔 집에 있었소.", FORMAT_LENGTH, "근거 줄 없음"),
    ("근거: f1\nThe smith was there.", FORMAT_LENGTH, "로마자"),
    ("근거: f1\n2일째 한밤엔 우물가에서 대장장이를 봤소.", FORMAT_LENGTH, "인용 사실에 없는 숫자(날짜 바꿈)"),
    ("근거: f3,f4\n초저녁엔 주막에 있었소. 한밤엔 집에 있었소. 1일째 밤이었소.", FORMAT_LENGTH, "문장 3개"),
    ("근거: f1\n**한밤엔** 우물가에서 대장장이를 봤소.", FORMAT_LENGTH, "마크다운"),
    ("근거: f3\n초저녁엔 주막에 있었소. 주모가 그러는데 대장장이가 수상하답디다.", UNGROUNDED, "직전 대화·질문 따라 새 주장"),
]

# 판별 방법의 허용 한계(통과하지만 규칙으로 못 잡는 것. 사람 판정으로 보완, docs/modules/llm.md 한계)
LIMIT_CASES = [
    ("근거: f1\n한밤엔 우물가에서 대장장이가 도끼와 밧줄을 들고 있었소.", "모르는 명사 2개까지는 통과"),
    ("근거: f1\n대장장이를 봤는데 수상하오.", "평가어(수상하다)는 명사가 아니라 통과"),
]
# 판별 방법의 허용 한계(뜻은 맞지만 거부하는 것. 보수적으로 둔다)
LIMIT_REJECT_CASES = [
    ("근거: f1\n대장장이가 우물에 없었다는 말은 사실이 아니오.", POLARITY, "이중 부정"),
    ("근거: f5\n한밤엔 서낭당에 있었소.", POLARITY, "부정이 걸린 서술어를 구분 못 함"),
    ("근거: f3,f4\n초저녁과 한밤에 주막과 집에 있었소.", PAIR_BINDING, "한 절에 시간 둘·장소 둘"),
]


class TableTest(unittest.TestCase):
    def test_pass_cases(self):
        for raw, why in PASS_CASES:
            with self.subTest(why=why):
                r = check_answer(raw, ITEM, LEX)
                self.assertTrue(r.ok, f"{why}: {r.details}")

    def test_reject_cases_all_rejected(self):
        self.assertGreaterEqual(len(REJECT_CASES), 15)
        for raw, reason, why in REJECT_CASES:
            with self.subTest(why=why):
                r = check_answer(raw, ITEM, LEX)
                self.assertFalse(r.ok, why)
                self.assertIn(reason, r.reasons, f"{why}: {r.details}")

    def test_swap_reason_is_pair_binding_first(self):
        r = check_answer("근거: f1,f2\n대장장이는 주막에 있었소.", ITEM, LEX)
        self.assertEqual(r.reason, PAIR_BINDING)

    def test_limit_cases_documented_as_passing(self):
        for raw, why in LIMIT_CASES:
            with self.subTest(why=why):
                self.assertTrue(check_answer(raw, ITEM, LEX).ok, why)
        for raw, reason, why in LIMIT_REJECT_CASES:
            with self.subTest(why=why):
                self.assertIn(reason, check_answer(raw, ITEM, LEX).reasons, why)


class ToolsTest(unittest.TestCase):
    def test_parse_evidence(self):
        self.assertEqual(parse_evidence_line("근거: f1,f3"), ("f1", "f3"))
        self.assertEqual(parse_evidence_line("근거：없음"), ())
        self.assertEqual(parse_evidence_line("근거: f1,f1"), ("f1",))
        self.assertIsNone(parse_evidence_line("근거:"))
        self.assertIsNone(parse_evidence_line("답: f1"))
        self.assertIsNone(parse_evidence_line("근거: 우물가"))

    def test_split(self):
        self.assertEqual(split_sentences("가. 나?  다!"), ["가.", "나?", "다!"])
        self.assertEqual(split_sentences("…주막 뒷방에서 투전판에 끼었소."), ["…주막 뒷방에서 투전판에 끼었소."])
        self.assertEqual(split_clauses("초저녁엔 주막에 있었고 한밤엔 집에 있었소"), ["초저녁엔 주막에 있었고", "한밤엔 집에 있었소"])
        self.assertEqual(len(split_clauses("창고 앞, 집에 있었소")), 3)   # 명사 끝 '고'도 끊는다(쌍을 줄이는 쪽이라 거부는 안 늚)

    def test_polarity(self):
        self.assertEqual(polarity("주막에 있었소"), "pos")
        self.assertEqual(polarity("주막에 없었소"), "neg")
        self.assertEqual(polarity("아무도 못 봤소"), "neg")
        self.assertEqual(polarity("없었다는 말은 사실이 아니오"), "unknown")
        self.assertEqual(polarity("없었다고 하더이다"), "unknown")

    def test_noun_candidates(self):
        self.assertEqual(noun_candidates("피해자는 독살됐소."), ["피해자"])
        self.assertEqual(noun_candidates("식구들한테만은 말하지 마시오."), ["식구"])
        self.assertEqual(noun_candidates("주막에 있었는데 그날은 혼자였소."), ["주막"])

    def test_prompt_has_facts_history_and_format(self):
        msgs = build_messages(ITEM)
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertIn("f1: 1일째 밤 한밤엔 우물가에서 대장장이를 봤소.", msgs[0]["content"])
        self.assertIn("근거: 없음", msgs[0]["content"])
        self.assertIn("직전 대화(사실 근거 아님)", msgs[1]["content"])
        self.assertTrue(msgs[1]["content"].endswith(ITEM.question))

    def test_output_regex(self):
        import re
        pat = re.compile(output_regex(["f1", "f2"]))
        self.assertTrue(pat.fullmatch("근거: f1,f2\n주막에 있었소."))
        self.assertTrue(pat.fullmatch("근거: 없음\n모르오."))
        self.assertFalse(pat.fullmatch("근거: f3\n주막에 있었소."))


class StreamTest(unittest.TestCase):
    def feed_all(self, chunks):
        s, events = AnswerStream(), []
        for c in chunks:
            events += s.feed(c)
        return events + s.close()

    def test_chunks_split_anywhere(self):
        text = "근거: f3,f4\n초저녁엔 주막에 있었소. 한밤엔 집에 있었소."
        whole = self.feed_all([text])
        for n in (1, 2, 3, 7):
            self.assertEqual(self.feed_all([text[i:i + n] for i in range(0, len(text), n)]), whole)
        self.assertEqual(whole, [("evidence", ("f3", "f4")), ("sentence", "초저녁엔 주막에 있었소."),
                                 ("sentence", "한밤엔 집에 있었소.")])

    def test_sentence_emitted_before_stream_end(self):
        s = AnswerStream()
        self.assertEqual(s.feed("근거: f3\n초저녁엔 주막에 있었소."), [("evidence", ("f3",))])
        self.assertEqual(s.feed(" 그"), [("sentence", "초저녁엔 주막에 있었소.")])

    def test_bad_header(self):
        self.assertEqual(self.feed_all(["초저녁엔 주막에 있었소.\n"])[0][0], "bad_header")
        self.assertEqual(self.feed_all(["가" * 100])[0][0], "bad_header")
        self.assertEqual(self.feed_all(["근거: f1"]), [("evidence", ("f1",))])   # 답 없이 끝남 → 판정에서 실패


class GateTest(unittest.TestCase):
    def run_gate(self, text):
        g, s, acts = SentenceGate(ITEM, LEX), AnswerStream(), []
        for e in s.feed(text) + s.close():
            acts.append(g.on_event(e))
        return g, acts, g.finish()

    def test_ok(self):
        g, acts, res = self.run_gate("근거: f3,f4\n초저녁엔 주막에 있었소. 한밤엔 집에 있었소.")
        self.assertEqual((acts, res), ([None, "publish", "publish"], "ok"))

    def test_first_sentence_fail(self):
        _, acts, res = self.run_gate("근거: f1,f2\n대장장이는 주막에 있었소. 한밤엔 집에 있었소.")
        self.assertEqual((acts[1], res), ("fail", "fail"))

    def test_second_sentence_fail_is_partial(self):
        g, acts, res = self.run_gate("근거: f1\n한밤엔 우물가에서 대장장이를 봤소. 대장장이가 범인이오.")
        self.assertEqual((acts, res, g.published), ([None, "publish", "drop"], "partial",
                                                     ["한밤엔 우물가에서 대장장이를 봤소."]))

    def test_third_sentence_dropped(self):
        _, acts, res = self.run_gate("근거: f3,f4\n초저녁엔 주막에 있었소. 한밤엔 집에 있었소. 1일째 밤 한밤엔 집에 있었소.")
        self.assertEqual((acts[-1], res), ("drop", "partial"))

    def test_range_and_header_fail(self):
        self.assertEqual(self.run_gate("근거: f9\n한밤엔 집에 있었소.")[2], "fail")
        self.assertEqual(self.run_gate("근거: f1")[2], "fail")


class FallbackTest(unittest.TestCase):
    def test_most_overlap(self):
        item = FreeQuestion("훈장", ITEM.persona, ITEM.facts, "훈장, 초저녁에 주막에서 누굴 봤소?")
        self.assertEqual(fallback(item, LEX), (("f2",), ITEM.facts[1].sentence))   # f2·f3 동률(초저녁·주막) → 앞의 f2

    def test_tie_by_order_and_deterministic(self):
        item = FreeQuestion("훈장", ITEM.persona, ITEM.facts, "왜 그러오?")
        self.assertEqual(fallback(item, LEX), (("f1",), ITEM.facts[0].sentence))
        self.assertEqual(fallback(item, LEX), fallback(item, LEX))

    def test_no_facts(self):
        self.assertEqual(fallback(FreeQuestion("훈장", ITEM.persona, (), "어디 있었소?"), LEX), ((), NO_FACT_TEXT))

    def test_fallback_passes_checks(self):
        ids, text = fallback(ITEM, LEX)
        self.assertTrue(check_answer(f"근거: {','.join(ids)}\n{text}", ITEM, LEX).ok)


class EvalSetTest(unittest.TestCase):
    """평가 세트 생성기(11/8 사용자 확인 완료). 픽스처는 같은 시드면 같은 바이트."""

    @classmethod
    def setUpClass(cls):
        from llm import free_answer_eval as fe
        from llm.eval_set import dumps
        bundle = json.loads(fe.BUNDLE.read_text(encoding="utf-8"))
        cls.data = fe.build_eval(bundle, fe.load_questions(fe.EXAMPLES), 1)
        cls.text = dumps(cls.data)

    def test_fixture_bytes_match_seed_1(self):
        fixture = (ROOT / "fixtures" / "llm" / "free_answer_eval.json").read_text(encoding="utf-8")
        self.assertEqual(fixture, self.text)

    def test_status_pending_when_sentences_differ(self):
        from llm import free_answer_eval as fe
        bundle = json.loads(fe.BUNDLE.read_text(encoding="utf-8"))
        other = fe.build_eval(bundle, fe.load_questions(fe.EXAMPLES), 2)
        self.assertNotEqual(other["new_sentences"], self.data["new_sentences"])
        self.assertIn("사용자 확인 전", other["status"])
        changed = [dict(x) for x in self.data["new_sentences"]]
        changed[0]["text"] += " 바뀜"
        self.assertEqual(fe.status_for(changed), fe.STATUS_PENDING)

    def test_counts_and_status(self):
        items = self.data["items"]
        self.assertIn("사용자 확인 완료", self.data["status"])
        normal = [i for i in items if i["kind"] == "normal"]
        self.assertGreaterEqual(len(normal), 60)
        self.assertEqual({(i["qtype"], i["turns"]) for i in normal},
                         {(q, t) for q in ("whereabouts", "contradiction", "suspicion") for t in (0, 2, 4)})
        kinds = [i["kind"] for i in items]
        self.assertEqual((kinds.count("false_premise"), kinds.count("wrong_history")), (6, 6))
        self.assertEqual(len(self.data["new_sentences"]), 12)

    def test_item_fields(self):
        for it in self.data["items"]:
            ids = {f["fact_id"] for f in it["facts"]}
            self.assertTrue(set(it["relevant_fact_ids"]) <= ids, it["item_id"])
            self.assertEqual(it["answerable"], bool(it["relevant_fact_ids"]))
            self.assertEqual(len(it["history"]), it["turns"] // 2)
            self.assertTrue(it["question_source"] == "new" or it["question_source"].startswith("E-06-02:"))
            self.assertNotIn("<P", it["question"])

    def test_facts_and_fallback_pass_checks(self):
        from llm.validator import Lexicon
        lex = Lexicon.from_json(self.data["lexicon"])
        for it in self.data["items"]:
            q = FreeQuestion.from_json(it)
            for f in q.facts:
                self.assertTrue(check_answer(f"근거: {f.fact_id}\n{f.sentence}", q, lex).ok, (it["item_id"], f))
            ids, text = fallback(q, lex)
            self.assertTrue(check_answer(f"근거: {','.join(ids) or '없음'}\n{text}", q, lex).ok, it["item_id"])

    def test_wrong_history_answer_is_rejected_when_repeated(self):
        from llm.validator import Lexicon
        lex = Lexicon.from_json(self.data["lexicon"])
        for it in (i for i in self.data["items"] if i["kind"] == "wrong_history"):
            q = FreeQuestion.from_json(it)
            wrong = it["history"][0]["answer"]
            r = check_answer(f"근거: {it['relevant_fact_ids'][0]}\n{wrong}", q, lex)
            self.assertFalse(r.ok, it["item_id"])


class ConstantsTest(unittest.TestCase):
    def test_noun_cap(self):
        self.assertEqual(MAX_UNKNOWN_NOUNS, 2)


if __name__ == "__main__":
    unittest.main()
