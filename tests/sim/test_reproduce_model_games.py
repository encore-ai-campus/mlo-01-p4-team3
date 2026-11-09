"""D-08-04 A 쪽 재현 확인: 같은 모델·시드 두 실행 비교."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.domain.engine import PaldoEngine
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from ml.adapters.reproduce_model_games import RunDigest, compare_runs, digest_events, reproduce
from tools.run_engine_games import EVENTS_FILE, MANIFEST_FILE, POLICIES, JsonlEventSink, run_game


def fake_run(games=3, seed=1, model_version="fake-suspicion-1"):
    """실제 엔진과 가짜 의심 모델로 run_model_games와 같은 출력 파일을 쓴다."""
    def run(out: Path) -> int:
        policy = POLICIES["rule-v0"]()
        model = FakeSuspicionModel()
        model.model_version = model_version
        deps = EngineDeps(FixtureContentSource().load("*"), model)
        sink = JsonlEventSink(out / EVENTS_FILE)
        try:
            runs = [run_game(PaldoEngine(policy), deps, "base6", seed, i, policy.version, sink,
                             model_version=model_version) for i in range(games)]
        finally:
            sink.close()
        manifest = {"games": len(runs), "game_ids": [r.game_id for r in runs], "events": sink.count,
                    "model_versions": {"suspicion": model_version}, "feature_version": model.feature_version,
                    "rule_version": "r", "preset_id": "base6", "seed": seed, "npc_policy_version": policy.version,
                    "code_commit": str(out), "metrics": {"elapsed": {"value": id(out)}}}
        (out / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
        return 0
    return run


class ReproduceTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_same_model_and_seed_reproduce(self):
        report = reproduce(fake_run(), self.root)
        self.assertTrue(report["passed"], report["diffs"])
        self.assertEqual(report["games"], 3)
        self.assertEqual(sum(report["winners"].values()), 3)

    def test_different_results_are_reported(self):
        runs = iter([fake_run(seed=1), fake_run(seed=2)])
        report = reproduce(lambda out: next(runs)(out), self.root)
        self.assertFalse(report["reproduced"])
        self.assertIn("events_sha256", report["diffs"])
        self.assertIn("manifest.game_ids", report["diffs"])

    def test_failed_run_code_fails_even_if_same(self):
        inner = fake_run()
        report = reproduce(lambda out: inner(out) or 1, self.root)
        self.assertTrue(report["reproduced"])
        self.assertFalse(report["passed"])

    def test_digest_and_compare_rules(self):
        events = [{"game_id": "g1", "event_type": "x"},
                  {"game_id": "g1", "event_type": "game_ended", "payload": {"winner": "village", "reason": "r"}}]
        counts, results = digest_events(events)
        self.assertEqual(counts, {"g1": 2})
        self.assertEqual(results["g1"]["winner"], "village")
        a = RunDigest({"games": 1, "code_commit": "a"}, "h", counts, results)
        b = RunDigest({"games": 1, "code_commit": "b"}, "h", counts, {"g1": {"winner": "spirit", "reason": "r"}})
        self.assertEqual(compare_runs(a, a), [])
        self.assertEqual(compare_runs(a, b), ["result.g1"])


if __name__ == "__main__":
    unittest.main()
