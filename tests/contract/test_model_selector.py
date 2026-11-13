"""Local artifact selection and compatibility at the engine port boundary."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.domain.engine import PaldoEngine
from engine.ports import EngineDeps, FeatureVersionMismatch, ModelUnavailable
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.perf.npc_rule_check import check_events
from tools.run_engine_games import JsonlEventSink, POLICIES, run_game


class LocalModelSelectorTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "mlruns"

    def selector(self, loader=None, intent_version="tfidf-lr-v0-example"):
        from server.infra.model_selector import LocalModelSelector

        return LocalModelSelector(self.root, suspicion_version="fake-suspicion-1",
                                  intent_version=intent_version, feature_version="fake-1",
                                  suspicion_loader=loader or self.load_fixture)

    def register(self, version="fake-suspicion-1", *, feature="fake-1", artifact_version=None):
        folder = self.root / "suspicion-v0" / version
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps({"feature_version": feature}), encoding="utf-8")
        (folder / "models.pkl").write_text(artifact_version or version, encoding="utf-8")
        return folder

    @staticmethod
    def load_fixture(path):
        model = FakeSuspicionModel()
        model.model_version = path.read_text(encoding="utf-8")
        return model

    def test_lookup_uses_requested_version(self):
        self.register()
        self.register("fake-suspicion-previous")
        selector = self.selector()
        old_model = selector.suspicion_model("fake-suspicion-previous")
        self.assertEqual(selector.suspicion_model("fake-suspicion-1").model_version, "fake-suspicion-1")
        self.assertEqual(old_model.model_version, "fake-suspicion-previous")

    def test_web_selects_direct_logistic_and_keeps_worker_for_other_loads(self):
        from server.infra.model_selector import LocalModelSelector

        logistic = 'suspicion-logistic-0123456789ab'
        other = 'suspicion-heavy-0123456789ab'
        self.register(logistic)
        self.register(other)
        selector = LocalModelSelector(self.root, suspicion_version=logistic,
                                      intent_version='', feature_version='fake-1')
        with patch('ml.adapters.suspicion_model.InProcessSuspicionModelAdapter', side_effect=self.load_fixture) as direct, \
             patch('ml.adapters.suspicion_model.load_model', side_effect=self.load_fixture) as worker:
            self.assertEqual(selector.suspicion_model(logistic, in_process_logistic=True).model_version, logistic)
            self.assertEqual(selector.suspicion_model(other, in_process_logistic=True).model_version, other)
            self.assertEqual(selector.suspicion_model(logistic).model_version, logistic)
        direct.assert_called_once_with(self.root / 'suspicion-v0' / logistic / 'models.pkl')
        self.assertEqual(worker.call_count, 2)

    def test_wrong_feature_is_rejected_before_loading(self):
        self.register(feature="other")

        def never_load(_path):
            self.fail("incompatible artifact reached the adapter")

        with self.assertRaises(FeatureVersionMismatch):
            self.selector(never_load).suspicion_model("fake-suspicion-1")

    def test_loaded_identity_and_feature_are_checked(self):
        self.register(artifact_version="different-version")
        with self.assertRaises(ModelUnavailable):
            self.selector().suspicion_model("fake-suspicion-1")

        self.register()

        def wrong_feature(path):
            model = self.load_fixture(path)
            model.feature_version = "other"
            return model

        with self.assertRaises(FeatureVersionMismatch):
            self.selector(wrong_feature).suspicion_model("fake-suspicion-1")

    def test_missing_or_invalid_registration_is_unavailable(self):
        selector = self.selector()
        for version in ("missing", "", "../escape", "a/b", "a\\b", "latest"):
            with self.subTest(version=version), self.assertRaises(ModelUnavailable):
                selector.suspicion_model(version)
        folder = self.register()
        (folder / "report.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ModelUnavailable):
            selector.suspicion_model("fake-suspicion-1")
        self.register()
        (folder / "models.pkl").unlink()
        with self.assertRaises(ModelUnavailable):
            selector.suspicion_model("fake-suspicion-1")

    def test_loader_failure_is_unavailable(self):
        self.register()

        def broken_loader(_path):
            raise ValueError("broken model")

        with self.assertRaises(ModelUnavailable):
            self.selector(broken_loader).suspicion_model("fake-suspicion-1")

    def test_benchmark_rejects_missing_selected_artifact(self):
        from ml.adapters.run_model_games import main

        out = self.root.parent / "benchmark"
        out.mkdir()
        manifest = out / "manifest.json"
        manifest.write_text('{"status": "passed"}', encoding="utf-8")
        result = main(["--mlruns", str(self.root), "--suspicion-version", "missing",
                       "--intent-version", "tfidf-lr-v0-example", "--out", str(out)])
        self.assertEqual(result, 1)
        self.assertFalse(manifest.exists())

    def test_select_and_parse_with_saved_intent_baseline(self):
        from intent.baseline import IntentBaseline, load_examples
        from tests.intent.test_matcher import context

        self.register()
        model = IntentBaseline().fit(load_examples())
        version = model.model_version
        folder = self.root / "intent-v0" / version
        folder.mkdir(parents=True)
        model.save(folder / "model.joblib")
        selector = self.selector(intent_version=version)

        self.assertEqual(selector.select("game-1", None), ({"suspicion": "fake-suspicion-1", "intent": version}, False))
        result = selector.intent_parser(version).parse("훈장이 수상하다", context())
        self.assertEqual(result.versions["model"], version)
        self.assertEqual(result.candidates[0].command.target, "hunjang")

        self.register(feature="other")
        with self.assertRaises(FeatureVersionMismatch):
            selector.select("game-1", None)
        wrong_folder = self.root / "intent-v0" / "different-version"
        wrong_folder.mkdir()
        model.save(wrong_folder / "model.joblib")
        with self.assertRaises(ModelUnavailable):
            selector.intent_parser("different-version")
        with self.assertRaises(ModelUnavailable):
            selector.intent_parser("missing")

    def test_fixture_model_reproduces_50_fixed_seed_games(self):
        self.register()
        content = FixtureContentSource().load("*")
        outputs = []
        for attempt in range(2):
            model = self.selector().suspicion_model("fake-suspicion-1")
            deps = EngineDeps(content, model)
            policy = POLICIES["rule-v0"]()
            engine = PaldoEngine(policy)
            path = self.root / f"attempt-{attempt}.jsonl"
            sink = JsonlEventSink(path)
            try:
                games = [run_game(engine, deps, "base6", 1, i, policy.version, sink) for i in range(50)]
            finally:
                sink.close()
            self.assertEqual(len({game.game_id for game in games if game.winner is not None}), 50)
            checks = check_events(path)
            for key in ("legal_violations", "inference_violations", "suspicion_fallbacks"):
                self.assertEqual(checks[key], 0, key)
            outputs.append(path.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
