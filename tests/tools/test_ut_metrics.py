"""E-08-10·E-09-01: 사용자 테스트 지표 집계(`tools/ut_metrics.py`) 도메인 규칙."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import ut_metrics as m

T0 = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 13:00 KST


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def ev(event_type, minute, seq, command_id=None, phase="day", payload=None, **extra):
    return {"event_id": f"e{seq}", "seq": seq, "event_type": event_type, "occurred_at": at(minute),
            "phase": phase, "command_id": command_id, "payload": payload or {}, **extra}


def attempt(minute, seq, aid, text="수상한 사람을 추궁해", reaction=None, superseded_by=None):
    return ev("intent_attempt", minute, seq, payload={"attempt_id": aid, "text": text, "reaction": reaction,
                                                     "superseded_by": superseded_by})


def game(gid, who, events, commands=(), result=None, source="human", session="s1", preset="base6"):
    return {"game_id": gid, "player_pseudonym": who, "session_id": session, "source": source, "preset_id": preset,
            "created_at": at(0), "result": result, "events": list(events), "commands": list(commands)}


def cmd(cid, phase, accepted=True, replayed=False):
    return {"command_id": cid, "accepted": accepted, "replayed": replayed, "phase": phase}


class AttemptChainTests(unittest.TestCase):
    def test_retry_links_into_one_chain_with_final_reaction(self):
        events = [attempt(1, 1, "a1"), attempt(2, 2, "a1", reaction="retry", superseded_by="a2"),
                  attempt(2, 3, "a2", text="다시"), attempt(3, 4, "a2", reaction="edit")]
        chains, attempts = m.attempt_chains(events)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["attempts"], ["a1", "a2"])
        self.assertEqual(chains[0]["final_reaction"], "edit")
        self.assertEqual(chains[0]["retries"], 1)
        self.assertEqual(len(attempts), 2)

    def test_duplicate_event_id_counted_once_and_unresolved_kept(self):
        first = attempt(1, 1, "a1")
        chains, attempts = m.attempt_chains([first, dict(first), attempt(2, 2, "b1")])
        self.assertEqual([c["final_reaction"] for c in chains], [None, None])
        self.assertEqual(len(attempts), 2)


class GameSummaryTests(unittest.TestCase):
    def test_cutoff_turns_late_end_into_incomplete(self):
        g = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 70, 9)], result={"completed": True})
        self.assertTrue(m.game_summary(g)["completed"])
        cutoff = T0 + timedelta(minutes=60)
        self.assertFalse(m.game_summary(g, cutoff)["completed"])
        self.assertIsNone(m.game_summary(g, cutoff)["minutes"])

    def test_result_completed_false_is_not_completion(self):
        g = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 5, 1)], result={"completed": False})
        self.assertFalse(m.game_summary(g)["completed"])

    def test_interrogation_dwell_from_command_phases(self):
        events = [ev("game_started", 0, 0, command_id="c0"), ev("phase_changed", 2, 1, command_id="c1"),
                  ev("interrogation_answered", 3, 2, command_id="c2", phase="interrogation"),
                  ev("interrogation_answered", 4, 3, command_id="c3", phase="interrogation"),
                  ev("votes_revealed", 6, 4, command_id="c4"), ev("game_ended", 8, 5, command_id="c5")]
        commands = [cmd("c0", "morning"), cmd("c1", "interrogation"), cmd("c2", "interrogation"),
                    cmd("c3", "interrogation"), cmd("c4", "vote"), cmd("c5", "ended"),
                    cmd("c2", "interrogation", replayed=True)]
        s = m.game_summary(game("g1", "p1", events, commands))
        self.assertEqual(s["interrogations"], 2)
        self.assertEqual(s["interrogation_seconds"], 4 * 60)  # 2분 → 6분
        self.assertEqual(s["accepted_commands"], 6)
        cut = m.game_summary(game("g1", "p1", events, commands), T0 + timedelta(minutes=3, seconds=30))
        self.assertEqual(cut["interrogations"], 1)
        self.assertEqual(cut["interrogation_seconds"], 90)  # 2분 → 3분 30초에서 자름(뒤 명령 구간 제외)
        self.assertEqual(cut["accepted_commands"], 3)  # c0·c1·c2만 마감 전
        untimed = m.game_summary(game("g1", "p1", events, commands + [cmd("c9", "day")]), T0 + timedelta(minutes=30))
        self.assertEqual(untimed["accepted_commands"], 6)  # 이벤트가 없어 시각을 모르는 c9는 마감 기준에서 뺀다

    def test_person_minutes_ignore_endings_after_cutoff(self):
        g1 = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 10, 1)], result={"completed": True})
        g2 = game("g2", "p1", [ev("game_started", 20, 0), ev("game_ended", 70, 1)], result={"completed": True})
        cutoff = T0 + timedelta(minutes=60)
        out = m.aggregate([m.game_summary(g, cutoff) for g in (g1, g2)])
        self.assertEqual(out["person_total_minutes"]["median"], 10.0)

    def test_cutoff_drops_later_attempts_and_survey(self):
        """ut-metrics-1.1(11/8 사용자 결정): 18:00 클로즈가 스냅숏이라 마감 뒤 설문도 세지 않는다."""
        events = [ev("game_started", 0, 0), attempt(1, 1, "a1"), ev("game_ended", 5, 2),
                  attempt(7, 3, "late"), ev("survey_submitted", 9, 4, payload={"skipped": False})]
        s = m.game_summary(game("g1", "p1", events, result={"completed": True}), T0 + timedelta(minutes=6))
        self.assertEqual(s["input_sentences"], 1)
        self.assertEqual(len(s["chains"]), 1)
        self.assertIsNone(s["survey"])
        self.assertTrue(s["completed"])
        kept = m.game_summary(game("g1", "p1", events, result={"completed": True}), T0 + timedelta(minutes=9))
        self.assertIsNotNone(kept["survey"])  # 경계 포함

    def test_game_ended_after_cutoff_is_unfinished(self):
        g = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 70, 9)], result={"completed": True})
        s = m.game_summary(g, T0 + timedelta(minutes=60))
        self.assertEqual((s["unfinished"], s["ended_after_cutoff"], s["ended_at"]), (True, True, None))
        self.assertFalse(m.game_summary(g)["unfinished"])


class CommandTimeTests(unittest.TestCase):
    def test_first_event_at_marks_command_whose_events_were_not_exported(self):
        """추궁 → 투표 전환 명령이 observation·decision만 내 이벤트가 안 보여도 명령 요지 시각으로 끊는다(Codex P1)."""
        events = [ev("game_started", 0, 0, command_id="c1"), ev("game_ended", 30, 9, command_id="c3")]
        to_vote = dict(cmd("c2", "vote"), first_event_at=at(10))
        g = game("g1", "p1", events, [cmd("c1", "interrogation"), to_vote, cmd("c3", "ended")],
                 result={"completed": True})
        self.assertEqual(m.phase_dwell(g, "interrogation", T0 + timedelta(minutes=30)), 600.0)
        without = game("g1", "p1", events, [cmd("c1", "interrogation"), cmd("c2", "vote"), cmd("c3", "ended")])
        self.assertEqual(m.phase_dwell(without, "interrogation", T0 + timedelta(minutes=30)), 1800.0)


class AggregateTests(unittest.TestCase):
    def setUp(self):
        survey_yes = {"skipped": False, "fun": 4}
        self.games = [
            game("g1", "p1", [ev("game_started", 0, 0), attempt(1, 1, "a1"), attempt(1, 2, "a1", reaction="confirm"),
                              ev("game_ended", 8, 3), ev("survey_submitted", 9, 4, payload=survey_yes)],
                 result={"completed": True}),
            game("g2", "p2", [ev("game_started", 0, 0), attempt(1, 1, "b1"),
                              attempt(1, 2, "b1", reaction="cancel"), attempt(2, 3, "b2", text="  "),
                              ev("game_ended", 12, 4), ev("survey_submitted", 13, 5, payload={"skipped": True})],
                 result={"completed": True}),
            game("g3", "p2", [ev("game_started", 20, 0), attempt(21, 1, "c1"),
                              attempt(21, 2, "c1", reaction="switch_to_buttons")]),
            game("g4", "pm", [ev("game_started", 0, 0), ev("game_ended", 5, 1)], result={"completed": True}),
            game("g5", "p3", [ev("game_started", 0, 0, load_test=True)]),
            game("g6", "bot", [ev("game_started", 0, 0)], source="sim"),
        ]

    def test_scope_excludes_pm_load_test_and_non_human(self):
        scoped = m.scope_games(self.games, exclude={"pm"})
        self.assertEqual([g["game_id"] for g in scoped], ["g1", "g2", "g3"])
        self.assertEqual(len(m.scope_games(self.games, include_load_test=True, exclude={"pm"})), 4)
        self.assertEqual([g["game_id"] for g in m.scope_games(self.games, pseudonyms={"p2"})], ["g2", "g3"])

    def test_metrics_numbers(self):
        scoped = m.scope_games(self.games, exclude={"pm"})
        out = m.aggregate([m.game_summary(g) for g in scoped])
        self.assertEqual(out["participants"], 2)
        self.assertEqual(out["games_started"], 3)
        self.assertEqual(out["games_completed"], 2)
        self.assertEqual(out["completion_rate"], round(2 / 3, 4))
        self.assertEqual(out["game_minutes"]["median"], 10.0)
        self.assertEqual(out["survey"], {"events": 2, "answered": 1, "skipped": 1, "games_ended": 2,
                                         "ended_without_survey": 0, "response_rate": 0.5,
                                         "response_rate_of_started": round(1 / 3, 4)})
        self.assertEqual(out["input_sentences"], 3)  # 빈 문장 b2 제외
        fi = out["free_input"]
        self.assertEqual((fi["chains"], fi["executed"], fi["unresolved"]), (4, 1, 1))
        self.assertEqual(fi["desired_action_rate"], 0.25)
        self.assertEqual(fi["cancel_rate"], 0.25)
        self.assertEqual(fi["switch_to_buttons_rate"], 0.25)
        self.assertEqual(fi["give_up_rate"], 0.5)  # cancel 1 + switch 1 (none_of_these 0) / 4

    def test_give_up_rate_includes_none_of_these(self):
        g = game("g1", "p1", [ev("game_started", 0, 0), attempt(1, 1, "a1"),
                              attempt(1, 2, "a1", reaction="none_of_these"), attempt(2, 3, "b1"),
                              attempt(2, 4, "b1", reaction="confirm")])
        fi = m.aggregate([m.game_summary(g)])["free_input"]
        self.assertEqual((fi["none_of_these"], fi["give_up_rate"], fi["cancel_rate"]), (1, 0.5, 0.0))

    def test_snapshot_cuts_endings_and_surveys_and_counts_unfinished(self):
        """ut-metrics-1.1(11/8 사용자 결정): 마감 뒤 종료·설문은 세지 않고, 그때 진행 중이던 판은 미종료로 따로 센다."""
        on_time = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 50, 1),
                                    ev("survey_submitted", 51, 2, payload={"skipped": False})],
                       result={"completed": True})
        late = game("g2", "p2", [ev("game_started", 30, 0), ev("game_ended", 70, 1),
                                 ev("survey_submitted", 71, 2, payload={"skipped": False})],
                    result={"completed": True})
        open_game = game("g3", "p3", [ev("game_started", 40, 0)])
        cutoff = T0 + timedelta(minutes=60)
        out = m.aggregate([m.game_summary(g, cutoff) for g in (on_time, late, open_game)])
        self.assertEqual(out["games_completed"], 1)  # 마감 뒤 종료는 미완주
        self.assertEqual(out["games_unfinished"], 2)  # late(마감 뒤 종료) + open_game
        self.assertEqual(out["games_ended_after_cutoff"], 1)
        self.assertEqual(out["survey"]["games_ended"], 1)
        self.assertEqual(out["survey"]["answered"], 1)
        self.assertEqual(out["survey"]["response_rate"], 1.0)
        self.assertEqual(out["survey"]["response_rate_of_started"], round(1 / 3, 4))

    def test_preset_filter(self):
        games = [game("g1", "p1", [ev("game_started", 0, 0)]),
                 game("g2", "p2", [ev("game_started", 0, 0)], preset="base7")]
        self.assertEqual([g["game_id"] for g in m.scope_games(games, preset_ids={"base6"})], ["g1"])
        self.assertEqual(len(m.scope_games(games)), 2)

    def test_version_counts(self):
        rows = [dict(game("g1", "p1", []), rule_version="r1", engine_version="e", content_version="c1"),
                dict(game("g2", "p2", []), rule_version="r1", engine_version="e", content_version="c1"),
                dict(game("g3", "p3", []), rule_version="r1", engine_version="e", content_version="c2")]
        self.assertEqual(m.version_counts(rows),
                         [{"rule_version": "r1", "engine_version": "e", "content_version": "c1", "games": 2},
                          {"rule_version": "r1", "engine_version": "e", "content_version": "c2", "games": 1}])

    def test_empty_scope_has_no_division_error(self):
        out = m.aggregate([])
        self.assertIsNone(out["completion_rate"])
        self.assertIsNone(out["interrogations"]["lambda_30_phase"])


class ParticipantTests(unittest.TestCase):
    KST = timezone(timedelta(hours=9))

    def accounts(self):
        def a(pid, hh, mm, **kw):
            row = {"player_pseudonym": pid, "date_joined": datetime(2026, 11, 9, hh, mm, tzinfo=self.KST)
                   .astimezone(timezone.utc).isoformat(), "consented_at": "x", "is_staff": False, "is_active": True}
            row.update(kw)
            return row
        return [a("old", 11, 59), a("start", 12, 0), a("mid", 12, 30), a("end", 12, 50), a("late", 12, 51),
                a("staff", 12, 10, is_staff=True), a("off", 12, 10, is_active=False),
                a("noconsent", 12, 10, consented_at=None)]

    def test_window_is_inclusive_and_filters_staff_inactive_unconsented(self):
        start = datetime(2026, 11, 9, 12, 0, tzinfo=self.KST)
        end = datetime(2026, 11, 9, 12, 50, tzinfo=self.KST)
        self.assertEqual(m.select_participants(self.accounts(), start, end), ["start", "mid", "end"])
        self.assertEqual(m.select_participants(self.accounts(), start, end, {"mid"}), ["start", "end"])

    def test_cli_reads_naive_times_as_kst(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": self.accounts()}), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                m.main(["participants", "--accounts", str(path), "--from", "2026-11-09T12:00", "--to",
                        "2026-11-09T12:50"])
        self.assertEqual(out.getvalue().split(), ["start", "mid", "end"])
        self.assertEqual(json.loads(err.getvalue())["count"], 3)


class MetricsCliTests(unittest.TestCase):
    def test_cli_records_confirmed_definition_version(self):
        g = game("g1", "p1", [ev("game_started", 0, 0), ev("game_ended", 70, 1)], result={"completed": True})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "games.json"
            path.write_text(json.dumps({"export_version": "ut-export-1", "games": [g]}), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                m.main(["metrics", "--games", str(path), "--cutoff", "2026-11-09T14:00"])
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["definition_version"], m.DEFINITION_VERSION)
        self.assertEqual(doc["metrics"]["games_completed"], 0)  # 13:00 시작, 14:10 종료 > 14:00 마감
        self.assertEqual(doc["definition_version"], "ut-metrics-1.1")
        self.assertNotIn("needs_confirmation", doc)
        self.assertIn("give_up_rate", doc["definitions"])

    def test_cutoff_is_default_upper_bound_for_game_start(self):
        early = game("g1", "p1", [ev("game_started", 0, 0)])
        late = game("g2", "p2", [ev("game_started", 70, 0)])  # 14:10 KST 시작
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "games.json"
            path.write_text(json.dumps({"games": [early, late]}), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                m.main(["metrics", "--games", str(path), "--cutoff", "2026-11-09T14:00"])
        self.assertEqual(json.loads(out.getvalue())["metrics"]["games_started"], 1)

    def test_default_cutoff_is_usertest_close_and_preset_flag(self):
        """기본 마감 = 11/10 18:00 KST(스냅숏). none이면 자르지 않는다. --preset은 그 프리셋 판만."""
        close = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)  # 18:00 KST
        started = (close - timedelta(minutes=20)).isoformat()
        ended = (close + timedelta(minutes=5)).isoformat()
        before = {"game_id": "g1", "player_pseudonym": "p1", "source": "human", "preset_id": "base6",
                  "events": [{"event_id": "a", "event_type": "game_started", "occurred_at": started},
                             {"event_id": "b", "event_type": "game_ended", "occurred_at": ended}],
                  "commands": [], "result": {"completed": True}}
        other = dict(before, game_id="g2", player_pseudonym="p2", preset_id="base7")
        runs = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "games.json"
            path.write_text(json.dumps({"engine_version": "paldo-engine", "games": [before, other]}), encoding="utf-8")
            for name, extra in (("default", []), ("none", ["--cutoff", "none"]), ("preset", ["--preset", "base6"])):
                out = io.StringIO()
                with redirect_stdout(out):
                    m.main(["metrics", "--games", str(path)] + extra)
                runs[name] = json.loads(out.getvalue())
        self.assertEqual(runs["default"]["cutoff"], "2026-11-10T18:00:00+09:00")
        self.assertEqual(runs["default"]["metrics"]["games_unfinished"], 2)
        self.assertEqual(runs["default"]["metrics"]["games_completed"], 0)
        self.assertIsNone(runs["none"]["cutoff"])
        self.assertEqual(runs["none"]["metrics"]["games_completed"], 2)
        self.assertEqual(runs["preset"]["metrics"]["games_started"], 1)
        self.assertEqual(runs["preset"]["filters"]["presets"], ["base6"])
        self.assertEqual(runs["default"]["export_engine_version"], "paldo-engine")


if __name__ == "__main__":
    unittest.main()
