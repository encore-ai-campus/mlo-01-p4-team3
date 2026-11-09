"""E-11-12 사람 자유 대화 터미널 클라이언트(`llm.bench.free_chat`) 테스트. 가짜 스트리밍 클라이언트로 돌린다."""
from __future__ import annotations

import io
import json
import unittest

from llm.bench import free_chat as fc
from llm.free_answer import LEAK
from llm.free_answer_eval import BUNDLE
from llm.validator import lexicon_from_bundle

BUNDLE_DATA = json.loads(BUNDLE.read_text(encoding="utf-8"))
LEX = lexicon_from_bundle(BUNDLE_DATA)


class FakeClient:
    """요청마다 정해 둔 답 원문을 몇 조각으로 나눠 내놓는다. 받은 메시지는 `seen`에 남긴다."""

    def __init__(self, answers: list[str]):
        self.answers, self.seen = list(answers), []

    def stream(self, messages, timeout=None):
        self.seen.append(messages)
        text = self.answers.pop(0)
        for i in range(0, len(text), 5):
            yield text[i:i + 5]


def inputs(*lines):
    it = iter(lines)

    def read(_prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return read


class ChatWorldTest(unittest.TestCase):
    def test_self_facts_first_and_deterministic(self):
        a = fc.chat_world(BUNDLE_DATA, "jumo", 1)
        self.assertEqual(a, fc.chat_world(BUNDLE_DATA, "jumo", 1))
        self.assertEqual([f["fact_id"] for f in a["facts"][:2]], ["s1", "s2"])
        self.assertEqual(a["facts"][0]["sentence"], "나는 주모요.")
        self.assertTrue(all(f["fact_id"].startswith("f") for f in a["facts"][2:]))

    def test_unknown_npc(self):
        with self.assertRaises(ValueError):
            fc.chat_world(BUNDLE_DATA, "nobody", 1)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.base = fc.chat_world(BUNDLE_DATA, "hunjang", 1)

    def test_turns_summary_and_history_cap(self):
        ok = "근거: s1\n나는 훈장이오."
        bad = "근거: s1\n나는 창귀요."
        # 1턴 통과, 2턴 첫 시도 누출 → 재생성 통과, 3턴 두 번 다 누출 → 폴백, 나머지 통과
        client = FakeClient([ok, bad, ok, bad, bad] + [ok] * 6)
        session = fc.ChatSession(client, self.base, LEX)
        out, log = io.StringIO(), io.StringIO()
        qs = [f"훈장, {i}번째로 묻소. 자네는 누구요?" for i in range(9)]
        fc.run_terminal(session, inputs(*qs), out, log, minutes=10)
        lines = [json.loads(x) for x in log.getvalue().splitlines()]
        self.assertEqual(len(lines), 9)
        self.assertEqual([x["outcome"] for x in lines[:3]], ["ok", "regen_ok", "fallback"])
        s = fc.summarize_chat(session.records, session.leaks, 1.0)
        self.assertEqual(s["turns"], 9)
        self.assertAlmostEqual(s["pass_rate"], 8 / 9)
        self.assertAlmostEqual(s["fallback_rate"], 1 / 9)
        self.assertEqual(s["leak_blocked_turns"], 2)
        self.assertEqual(s["leak_published"], 0)
        self.assertIn(LEAK, s["blocked_reasons"])
        # 마지막 요청의 직전 대화는 최근 8턴(질문·답 4쌍)까지
        user = client.seen[-1][-1]["content"]
        self.assertIn(qs[7], json.dumps(client.seen[-1], ensure_ascii=False))
        self.assertNotIn(qs[3], json.dumps(client.seen[-1], ensure_ascii=False))
        self.assertIn(qs[8], user)
        self.assertNotIn("창귀", out.getvalue())

    def test_end_word_and_time_limit(self):
        session = fc.ChatSession(FakeClient(["근거: 없음\n잘 모르겠소."] * 3), self.base, LEX)
        out = io.StringIO()
        fc.run_terminal(session, inputs("밥은 먹었소?", "/끝", "안 읽힘"), out, io.StringIO(), minutes=10)
        self.assertEqual(len(session.records), 1)
        ticks = iter([0.0, 601.0, 601.0])
        session2 = fc.ChatSession(FakeClient(["근거: 없음\n잘 모르겠소."] * 3), self.base, LEX)
        fc.run_terminal(session2, inputs("밥은 먹었소?", "또 묻소"), io.StringIO(), io.StringIO(), minutes=10,
                        clock=lambda: next(ticks))
        self.assertEqual(len(session2.records), 1)

    def test_published_leak_recheck_counts_role_word(self):
        from llm.free_answer import FreeQuestion
        item = FreeQuestion.from_json({**self.base, "question": "누구요?"})
        self.assertEqual(fc.published_leaks(item, LEX, ["나는 훈장이오."]), 0)
        self.assertEqual(fc.published_leaks(item, LEX, ["나는 훈장이오. 나는 창귀요."]), 1)


if __name__ == "__main__":
    unittest.main()
