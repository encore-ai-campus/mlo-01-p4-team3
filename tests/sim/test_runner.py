"""D-06-01: 실제 엔진의 플레이어 자리 4종 실행과 포트 경계."""
import ast
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import PRESETS, DataSource, GameMeta, Faction, EndReason, GameResult, Phase, CommandKind
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from engine.sim.bots import BOT_TYPES
from engine.sim.runner import run_game, SimulationError
from engine.sim.report import summarize, markdown_table


class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = FixtureContentSource().load("*")
        cls.deps = EngineDeps(cls.content, FakeSuspicionModel())

    def run_bot(self, name, seed=4):
        meta = GameMeta(f"test-{name}-{seed}", PRESETS["base6"], seed, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "paldo-engine", self.content.content_version)
        return run_game(PaldoEngine(), self.deps, meta, BOT_TYPES[name]())

    def test_all_four_complete_real_games_and_repeat_exactly(self):
        for name in BOT_TYPES:
            for seed in range(3):
                with self.subTest(bot=name, seed=seed):
                    a, b = self.run_bot(name, seed), self.run_bot(name, seed)
                    self.assertEqual(asdict(a), asdict(b))
                    self.assertEqual(a.illegal_commands, 0)
                    self.assertGreater(a.steps, 0)
                    self.assertIn(a.winner, ("village", "changgwi"))
                    self.assertEqual(len(a.trace_sha256), 64)

    def test_runner_imports_only_port_and_model_not_engine_implementation(self):
        path = Path(__file__).resolve().parents[2] / "engine/sim/runner.py"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("engine.domain.engine", source)
        self.assertNotIn(".truth.", source)
        self.assertNotIn(".npcs", source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "engine":
                self.assertIn(node.attr, {"new_game", "apply", "player_view"})

    def test_report_rates_denominators_and_table(self):
        run = self.run_bot("logic")
        rows = summarize([run])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["village_win_rate"] + rows[0]["changgwi_win_rate"], 1.)
        self.assertEqual(rows[0]["confession_opportunities"], PRESETS["base6"].n_secret)
        self.assertIn("고백률", markdown_table(rows))
        self.assertIn("의미 있는 선택", markdown_table(rows))
        other = replace(run, winner="changgwi", vote_days=3, no_lock_days=1,
                        confessions=1, confession_opportunities=2, meaningful_choices=4)
        first = replace(run, winner="village", vote_days=1, no_lock_days=1,
                        confessions=0, confession_opportunities=2, meaningful_choices=2)
        row = summarize([first, other])[0]
        self.assertEqual(row["no_lock_rate"], .5)
        self.assertEqual(row["confession_rate"], .25)
        self.assertEqual(row["village_win_rate"], .5)
        self.assertEqual(row["meaningful_choices_mean"], 3)
        no_opportunity = replace(run, vote_days=0, confession_opportunities=0)
        self.assertIsNone(summarize([no_opportunity])[0]["confession_rate"])
        self.assertIsNone(summarize([no_opportunity])[0]["no_lock_rate"])

    def test_benchmark_cli_records_all_policies_without_writing_evidence(self):
        from tools.perf.sim_bench import main
        with tempfile.TemporaryDirectory() as folder:
            old = os.environ.get("PALDO_LOG_DIR")
            os.environ["PALDO_LOG_DIR"] = folder
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--games", "1", "--seed", "7", "--out", folder,
                                           "--no-write"]), 0)
            finally:
                if old is None:
                    os.environ.pop("PALDO_LOG_DIR", None)
                else:
                    os.environ["PALDO_LOG_DIR"] = old
            manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["metrics"]["completed_games"]["value"], 4)
            self.assertEqual(manifest["metrics"]["replay_mismatches"]["value"], 0)
            self.assertIn("마을 승률", (Path(folder) / "report.md").read_text(encoding="utf-8"))

    def test_rejected_command_and_step_limit_fail_instead_of_hiding(self):
        class BadBot:
            version = "bad-test"
            def choose(self, view, rng):
                from engine.domain.model import Command, CommandKind
                return Command(CommandKind.VOTE, "mudang", target="mudang")
        meta = GameMeta("bad", PRESETS["base6"], 1, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "paldo-engine", self.content.content_version)
        with self.assertRaises(SimulationError):
            run_game(PaldoEngine(), self.deps, meta, BadBot())
        with self.assertRaises(SimulationError):
            run_game(PaldoEngine(), self.deps, meta, BOT_TYPES["random"](), max_steps=1)

    def test_forced_advance_and_single_target_are_not_meaningful_choices(self):
        from types import SimpleNamespace
        from tests.sim.test_bots import view
        opaque_truth = object()
        class Port:
            def new_game(self, meta, deps):
                return SimpleNamespace(accepted=True, state=0, truth=opaque_truth, events=[])
            def apply(self, state, truth, envelope, deps):
                self.assert_truth(truth)
                return SimpleNamespace(accepted=True, state=state + 1, truth=opaque_truth, events=[])
            def assert_truth(self, truth):
                if truth is not opaque_truth:
                    raise AssertionError("opaque truth changed")
            def player_view(self, state, content):
                v = view(Phase.MORNING, [CommandKind.ADVANCE])
                if state == 1:
                    v.phase, v.legal_kinds, v.runoff_candidates = Phase.RUNOFF, [CommandKind.VOTE], ["b"]
                if state == 2:
                    v.result = GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 1)
                v.state_version = state
                return v
        meta = GameMeta("forced", PRESETS["base6"], 1, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "port-test", self.content.content_version)
        self.assertEqual(run_game(Port(), self.deps, meta, BOT_TYPES["random"]()).meaningful_choices, 0)
