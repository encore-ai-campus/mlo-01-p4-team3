"""E-09-02: test_human 분할·교집합 검사·동결 묶음(`tools/ut_freeze.py`)."""
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools import ut_freeze as f


def row(aid, who, text, pm=False):
    return {"attempt_id": aid, "game_id": "g", "player_pseudonym": who, "pm": pm, "text": text}


def export(games):
    return {"export_version": "ut-export-1", "games": games}


def game(who, attempts, source="human", load_test=False, session="s1"):
    events = []
    for i, (aid, text) in enumerate(attempts):
        events.append({"event_id": f"{who}-{i}", "event_type": "intent_attempt", "player_pseudonym": who,
                       "load_test": load_test, "payload": {"attempt_id": aid, "text": text, "reaction": None}})
        events.append({"event_id": f"{who}-{i}r", "event_type": "intent_attempt", "player_pseudonym": who,
                       "payload": {"attempt_id": aid, "text": text, "reaction": "confirm"}})
    return {"game_id": f"g-{who}", "player_pseudonym": who, "session_id": session, "source": source,
            "events": events}


class NormalizeTests(unittest.TestCase):
    def test_nfc_and_whitespace(self):
        decomposed = "가 나"  # 조합형 '가'
        self.assertEqual(f.normalize("  " + decomposed + "\t\n "), "가 나")
        self.assertEqual(f.normalize(None), "")


class SentencesTests(unittest.TestCase):
    def test_initial_events_only_and_filters(self):
        games = [game("u1", [("a1", "문장 하나"), ("a2", "   ")]), game("pm", [("p1", "PM 문장")]),
                 game("bot", [("b1", "시뮬")], source="sim"), game("lt", [("l1", "부하")], load_test=True),
                 game("u2", [("c1", "다른 회차")], session="s2")]
        rows = f.sentences_of(games, {"pm"}, {"s1"})
        self.assertEqual([(r["attempt_id"], r["pm"]) for r in rows], [("a1", False), ("p1", True)])


class SplitTests(unittest.TestCase):
    def test_reproducible_and_order_independent(self):
        users = [f"u{i}" for i in range(7)]
        first = f.split_users(users, 42)
        self.assertEqual(first, f.split_users(list(reversed(users)) + ["u0"], 42))
        evals, train, ordered = first
        self.assertEqual((len(evals), len(train)), (3, 4))  # 홀수: 평가가 1명 적다
        self.assertFalse(set(evals) & set(train))
        self.assertEqual(sorted(evals + train), ordered)


class PlayersTests(unittest.TestCase):
    def test_players_without_sentences_are_split(self):
        games = [game("u1", [("a1", "말")]), game("u2", []), game("pm", [("p1", "PM")]),
                 game("bot", [], source="sim"), game("u3", [], session="s2")]
        players = f.players_of(games, {"pm"}, {"s1"})
        self.assertEqual(players, ["u1", "u2"])
        out = f.build_freeze(f.sentences_of(games, {"pm"}, {"s1"}), [], 1, players=players)
        self.assertEqual((out["report"]["participants"], out["report"]["eval_users"]), (2, 1))


class BuildFreezeTests(unittest.TestCase):
    def setUp(self):
        self.usertest = [row(f"a{i}", f"u{i}", f"문장 {i}") for i in range(6)]
        self.pretest = [row("p1", "pm", "PM 문장", pm=True)]

    def test_clean_freeze_passes_and_records_seed(self):
        out = f.build_freeze(self.usertest, self.pretest, 7)
        r = out["report"]
        self.assertTrue(r["ok"])
        self.assertEqual((r["participants"], r["eval_users"], r["train_users"]), (6, 3, 3))
        self.assertEqual(r["test_human"], 4)
        self.assertEqual(r["test_human_pm"], 1)
        self.assertEqual(r["freeze_record"], "manifest.json(sha256)")
        self.assertEqual(r["odd_rule"], "eval=floor(n/2)")
        self.assertEqual(out["assignment"]["seed"], 7)
        test_users = {x["player_pseudonym"] for x in out["test_human"]} - {"pm"}
        self.assertEqual(test_users, set(out["assignment"]["eval_users"]))

    def test_privacy_exclusion_counted(self):
        out = f.build_freeze(self.usertest, self.pretest, 7, privacy_excluded={"p1": "name"})
        self.assertEqual(out["report"]["privacy_excluded"], 1)
        self.assertEqual(out["report"]["privacy_reasons"], ["name"])
        self.assertEqual(out["report"]["test_human_pm"], 0)

    def test_privacy_exclusion_applies_to_train_candidates(self):
        train_ids = [r["attempt_id"] for r in f.build_freeze(self.usertest, [], 7)["train_candidates"]]
        out = f.build_freeze(self.usertest, [], 7, privacy_excluded={train_ids[0]: "phone"})
        self.assertNotIn(train_ids[0], [r["attempt_id"] for r in out["train_candidates"]])
        self.assertEqual(out["report"]["privacy_excluded_train"], 1)
        self.assertEqual(out["report"]["privacy_reasons"], ["phone"])

    def test_sentence_conflict_with_external_set_fails_or_drops(self):
        external = [{"text": " PM  문장 "}]
        failed = f.build_freeze(self.usertest, self.pretest, 7, external)
        self.assertFalse(failed["report"]["ok"])
        self.assertEqual(failed["conflicts"], [{"attempt_id": "p1", "with": "external_set"}])
        dropped = f.build_freeze(self.usertest, self.pretest, 7, external, drop_conflicts=True)
        self.assertTrue(dropped["report"]["ok"])
        self.assertEqual(dropped["report"]["conflicts_dropped"], 1)
        self.assertEqual(dropped["report"]["test_human"], 3)

    def test_same_sentence_from_train_user_is_conflict(self):
        usertest = [row("a0", "u0", "같은 말"), row("a1", "u1", "같은 말"), row("a2", "u2", "x"),
                    row("a3", "u3", "y")]
        seed = next(s for s in range(100) if len({"u0", "u1"} & set(f.split_users(["u0", "u1", "u2", "u3"], s)[0])) == 1)
        out = f.build_freeze(usertest, [], seed)  # u0·u1이 평가·학습으로 갈린 시드
        self.assertEqual(out["report"]["sentence_conflicts"], 1)
        self.assertEqual(out["conflicts"][0]["with"], "train_candidate")
        self.assertFalse(out["report"]["ok"])

    def test_external_user_overlap_fails(self):
        evals = f.split_users([r["player_pseudonym"] for r in self.usertest], 7)[0]
        out = f.build_freeze(self.usertest, [], 7, [{"text": "무관", "user": evals[0]}])
        self.assertEqual(out["report"]["user_overlap"], 1)
        self.assertFalse(out["report"]["ok"])

    def test_external_overlap_checked_for_eval_user_without_kept_sentences(self):
        """검수로 문장이 모두 빠진 평가 사용자도 외부 세트 사용자와 겹치면 실패(Codex P2)."""
        evals = f.split_users([r["player_pseudonym"] for r in self.usertest], 7)[0]
        dropped = {r["attempt_id"]: "name" for r in self.usertest if r["player_pseudonym"] == evals[0]}
        out = f.build_freeze(self.usertest, [], 7, [{"text": "무관", "user": evals[0]}], dropped)
        self.assertNotIn(evals[0], {r["player_pseudonym"] for r in out["test_human"]})
        self.assertEqual(out["report"]["user_overlap"], 1)
        self.assertFalse(out["report"]["ok"])


class CliTests(unittest.TestCase):
    def test_writes_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ut.json").write_text(json.dumps(export([game(f"u{i}", [(f"a{i}", f"말 {i}")]) for i in range(4)])),
                                         encoding="utf-8")
            (tmp / "pre.json").write_text(json.dumps(export([game("pm", [("p1", "PM 말")])])), encoding="utf-8")
            (tmp / "train.jsonl").write_text(json.dumps({"text": "학습 문장"}, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
            (tmp / "review.json").write_text(json.dumps({"exclude": [{"attempt_id": "p1", "reason": "name"}]}),
                                             encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                f.main(["--games", str(tmp / "ut.json"), "--pretest-games", str(tmp / "pre.json"),
                        "--pm-pseudonym", "pm", "--seed", "3", "--exclude-set", str(tmp / "train.jsonl"),
                        "--privacy-review", str(tmp / "review.json"), "--out-dir", str(tmp / "freeze")])
            names = sorted(p.name for p in (tmp / "freeze").iterdir())
            report = json.loads((tmp / "freeze" / "report.json").read_text(encoding="utf-8"))
            lines = (tmp / "freeze" / "test_human.jsonl").read_text(encoding="utf-8").splitlines()
            manifest = json.loads((tmp / "freeze" / "manifest.json").read_text(encoding="utf-8"))
            th_bytes = (tmp / "freeze" / "test_human.jsonl").read_bytes()
        self.assertEqual(names, ["assignment.json", "manifest.json", "report.json", "test_human.jsonl",
                                 "train_candidates.jsonl"])
        # 11/8 사용자 결정: Delta 버전 대신 동결 묶음 파일 목록·sha256을 동결 기록으로 쓴다
        listed = {x["name"]: x for x in manifest["files"]}
        self.assertEqual(sorted(listed), ["assignment.json", "report.json", "test_human.jsonl", "train_candidates.jsonl"])
        self.assertEqual(listed["test_human.jsonl"]["sha256"], hashlib.sha256(th_bytes).hexdigest())
        self.assertEqual(listed["test_human.jsonl"]["bytes"], len(th_bytes))
        self.assertEqual(manifest["freeze_version"], f.FREEZE_VERSION)
        self.assertEqual((report["participants"], report["privacy_excluded"], report["test_human"]), (4, 1, 2))
        self.assertEqual(len(lines), 2)

    def test_failed_check_writes_no_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ut.json").write_text(json.dumps(export([game(f"u{i}", [(f"a{i}", "같은 말")]) for i in range(4)])),
                                         encoding="utf-8")
            (tmp / "freeze").mkdir()
            (tmp / "freeze" / "test_human.jsonl").write_text("previous\n", encoding="utf-8")
            (tmp / "empty.jsonl").write_text("", encoding="utf-8")
            (tmp / "review.json").write_text('{"exclude": []}', encoding="utf-8")
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stop:
                f.main(["--games", str(tmp / "ut.json"), "--seed", "3", "--exclude-set", str(tmp / "empty.jsonl"),
                        "--privacy-review", str(tmp / "review.json"), "--out-dir", str(tmp / "freeze")])
            names = sorted(p.name for p in (tmp / "freeze").iterdir())
            kept = (tmp / "freeze" / "test_human.jsonl").read_text(encoding="utf-8")
        self.assertEqual(stop.exception.code, 1)
        self.assertEqual(names, ["report_failed.json", "test_human.jsonl"])
        self.assertEqual(kept, "previous\n")

    def test_review_and_exclude_set_are_required(self):
        for argv in (["--games", "g.json", "--seed", "1", "--exclude-set", "t.jsonl", "--out-dir", "o"],
                     ["--games", "g.json", "--seed", "1", "--privacy-review", "r.json", "--out-dir", "o"]):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                f.main(argv)
            self.assertEqual(stop.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
