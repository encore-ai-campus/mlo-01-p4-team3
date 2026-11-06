"""A-06-03 판 실행 도구와 인수 검사 도구 테스트. 실제 엔진으로 판 3개를 돌리고, 위반을 넣어 검사가 잡는지 본다."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from engine.ports import EventSink
from tools import check_handoff_a06 as chk
from tools import run_engine_games as run

GAMES = 3


def _run(out: Path, *extra: str) -> int:
    with redirect_stdout(io.StringIO()):
        return run.main(["--games", str(GAMES), "--seed", "1", "--out", str(out), *extra])


class RunAndCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env = os.environ.get("PALDO_LOG_DIR")
        os.environ["PALDO_LOG_DIR"] = str(Path(cls._tmp.name) / "logs")
        cls.out = Path(cls._tmp.name) / "a06"
        assert _run(cls.out) == 0
        cls.lines = (cls.out / run.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
        cls.manifest = json.loads((cls.out / run.MANIFEST_FILE).read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        if cls._env is None:
            os.environ.pop("PALDO_LOG_DIR", None)
        else:
            os.environ["PALDO_LOG_DIR"] = cls._env
        cls._tmp.cleanup()

    def check(self, lines):
        return chk.check_lines(lines, GAMES)

    def test_clean_output_passes(self):
        rep = self.check(self.lines)
        self.assertTrue(rep.ok, rep.violations)
        self.assertEqual(rep.games, GAMES)
        self.assertEqual(rep.events, self.manifest["events"])
        self.assertEqual(rep.truths, GAMES)
        self.assertGreater(rep.decisions, 0)
        self.assertEqual(rep.observations, rep.decisions)

    def test_manifest_and_policy_versions(self):
        self.assertEqual(self.manifest["games"], GAMES)
        self.assertEqual(self.manifest["npc_policy_version"], "baseline-0")
        self.assertEqual(self.manifest["game_ids"], [f"eng_base6_1_{i:05d}" for i in range(GAMES)])
        first = json.loads(self.lines[0])
        self.assertEqual(first["seed"], run.game_seed(1, 0))
        self.assertEqual(first["source"], "bot")
        self.assertEqual(set(first["npc_policy_versions"].values()), {"baseline-0"})
        self.assertEqual(len(first["npc_policy_versions"]), 6)

    def test_same_command_same_bytes(self):
        """D·C가 같은 명령으로 재생성하면 같은 jsonl이 나온다(고정 시각·시드)."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_run(Path(d)), 0)
            self.assertEqual((Path(d) / run.EVENTS_FILE).read_bytes(), (self.out / run.EVENTS_FILE).read_bytes())

    def test_main_exit_codes(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(chk.main(["--dir", str(self.out)]), 0)
            self.assertEqual(chk.main(["--dir", str(self.out / "none")]), 1)

    # ---- 위반 주입
    def mutate(self, idx_pred, fn):
        out = []
        for line in self.lines:
            d = json.loads(line)
            if idx_pred(d):
                d = fn(d)
                if d is None:
                    continue
            out.append(json.dumps(d, ensure_ascii=False))
        return out

    def assertCaught(self, lines, cond):
        rep = self.check(lines)
        self.assertTrue(rep.violations[cond], f"{cond} 위반을 못 잡음")

    def test_seq_gap(self):
        self.assertCaught(self.mutate(lambda d: d["seq"] == 5 and d["game_id"].endswith("00000"), lambda d: None),
                          "c1_seq")

    def test_unknown_field_and_bad_snapshot(self):
        self.assertCaught(self.mutate(lambda d: d["seq"] == 0, lambda d: {**d, "extra": 1}), "c1_schema")

        def bad_snap(d):
            d["payload"]["snapshot"]["truth"] = {}
            return d
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "observation", bad_snap), "c1_schema")

    def test_double_end_and_truth_missing(self):
        dup = []
        for line in self.lines:
            dup.append(line)
            if json.loads(line)["event_type"] == "game_ended":
                dup.append(line)
        self.assertCaught(dup, "c1_ended_once")
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "truth_revealed", lambda d: None),
                          "h3_truth_after_end")

    def test_decision_without_observation(self):
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "observation", lambda d: None),
                          "h2_observation_before_decision")

    def test_game_without_decisions(self):
        stripped, seqs = [], {}
        for line in self.lines:
            d = json.loads(line)
            if d["game_id"].endswith("00000") and d["event_type"] in ("observation", "decision"):
                continue
            n = seqs.get(d["game_id"], 0)
            seqs[d["game_id"]] = n + 1
            d["seq"], d["event_id"] = n, f"{d['game_id']}:{n:05d}"
            stripped.append(json.dumps(d, ensure_ascii=False))
        self.assertCaught(stripped, "h4_reason_codes")
        rep = self.check(stripped)
        self.assertFalse(rep.violations["c1_seq"])

    def test_command_line_quotes_out(self):
        import argparse
        ns = argparse.Namespace(preset="base6", games=2, seed=1, out="out/a b", policy="min-legal")
        self.assertIn("--out 'out/a b'", run.command_line(ns))

    def test_reason_codes(self):
        def empty(d):
            d["payload"]["reason_codes"] = []
            return d

        def unknown(d):
            d["payload"]["reason_codes"] = ["guess"]
            return d
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "decision", empty), "h4_reason_codes")
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "decision", unknown), "h4_reason_codes")

    def test_malformed_payload_types_reported_not_raised(self):
        def roles_null(d):
            d["payload"]["roles"] = None
            return d

        def codes_unhashable(d):
            d["payload"]["reason_codes"] = [["x"]]
            return d

        def actor_list(d):
            d["payload"]["actor"] = ["a"]
            return d
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "truth_revealed", roles_null), "h3_truth_after_end")
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "decision", codes_unhashable), "h4_reason_codes")
        self.assertCaught(self.mutate(lambda d: d["event_type"] == "decision", actor_list), "h4_reason_codes")

    def test_malformed_truth_fields(self):
        for k, bad in (("secrets", None), ("secrets", {"nobody": "s1"}), ("lies", None), ("whereabouts", {}),
                       ("night_plans", None)):
            def f(d, k=k, bad=bad):
                d["payload"][k] = bad
                return d
            with self.subTest(k=k, bad=bad):
                self.assertCaught(self.mutate(lambda d: d["event_type"] == "truth_revealed", f), "h3_truth_after_end")

    def test_failed_rerun_removes_old_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / run.MANIFEST_FILE).write_text(json.dumps(self.manifest), encoding="utf-8")
            orig = run.run_games
            run.run_games = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                    run.main(["--games", "1", "--out", d])
            finally:
                run.run_games = orig
            self.assertFalse((dd / run.MANIFEST_FILE).exists())

    def test_manifest_without_game_count_fails(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / run.EVENTS_FILE).write_bytes((self.out / run.EVENTS_FILE).read_bytes())
            for games in (None, "3", 0, True):
                (dd / run.MANIFEST_FILE).write_text(json.dumps({**self.manifest, "games": games}), encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(chk.main(["--dir", d]), 1, games)

    def test_not_base6_and_game_count(self):
        self.assertCaught(self.mutate(lambda d: True, lambda d: {**d, "preset_id": "duo9"}), "h1_base6_completed")
        self.assertTrue(chk.check_lines(self.lines, GAMES + 1).violations["c1_game_count"])


class Pieces(unittest.TestCase):
    def test_sink_is_event_sink(self):
        with tempfile.TemporaryDirectory() as d:
            s = run.JsonlEventSink(Path(d) / "e.jsonl")
            try:
                self.assertIsInstance(s, EventSink)
            finally:
                s.close()

    def test_game_seed_distinct_across_runs(self):
        a = {run.game_seed(1, i) for i in range(200)}
        b = {run.game_seed(2, i) for i in range(500)}
        self.assertFalse(a & b)

    def test_occurred_at_format(self):
        self.assertEqual(run.occurred_at(0, 0), "2026-11-06T00:00:00.000Z")
        self.assertEqual(run.occurred_at(2, 1234), "2026-11-06T00:02:01.234Z")


if __name__ == "__main__":
    unittest.main()
