"""D-08-01 계약 경계 테스트. 학습 모델의 성능 증거가 아니다."""
from dataclasses import replace
from functools import partial
from pathlib import Path
import tempfile
import io
import json
from contextlib import redirect_stdout
import time
import unittest

from engine.domain.npc.vote import FallbackReason, estimate
from engine.ports import FeatureVersionMismatch, ModelUnavailable, SuspicionModel
from engine.testing.fakes import FakeSuspicionModel
from tests.contract.test_fakes import obs


class Backend(FakeSuspicionModel):
    def __init__(self, mode='ok'):
        super().__init__()
        self.mode = mode

    def estimate(self, snapshot):
        if self.mode == 'hang':
            time.sleep(60)
        if self.mode == 'error':
            raise ValueError('inference failed')
        result = super().estimate(snapshot)
        if self.mode == 'invalid':
            result.probs['a'] = float('nan')
        if self.mode == 'version':
            result.feature_version = 'other'
        return result


def slow_loader():
    time.sleep(60)
    return Backend()

def broken_loader():
    raise OSError('model missing')


class AdapterContract(unittest.TestCase):
    def adapter(self, loader=Backend, **kwargs):
        from ml.adapters.suspicion_model import SuspicionModelAdapter
        model = SuspicionModelAdapter(loader, 'fake-1', **kwargs)
        self.addCleanup(model.close)
        return model

    def test_port_preserves_output_and_reuses_worker(self):
        model = self.adapter()
        self.assertIsInstance(model, SuspicionModel)
        for _ in range(2):
            result = model.estimate(obs(['a', 'b', 'c'], 1))
            self.assertEqual(result.candidates, ['a', 'b', 'c'])
            self.assertAlmostEqual(sum(result.probs.values()), 1)
            self.assertEqual(result.model_version, 'fake-suspicion-1')

    def test_load_failure_becomes_engine_fallback(self):
        model = self.adapter(broken_loader)
        self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.UNAVAILABLE)

    def test_inference_error_becomes_engine_fallback(self):
        model = self.adapter(partial(Backend, 'error'))
        self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.UNAVAILABLE)

    def test_declared_version_mismatch_rejected(self):
        from ml.adapters.suspicion_model import SuspicionModelAdapter
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(FeatureVersionMismatch):
            SuspicionModelAdapter(Backend, 'different')
        self.assertTrue(output.getvalue(), '특징 불일치 경보 로그 없음')
        self.assertEqual(json.loads(output.getvalue())['error_type'], 'FeatureVersionMismatch')

    def test_output_version_mismatch_becomes_engine_fallback(self):
        model = self.adapter(partial(Backend, 'version'))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.VERSION)
        self.assertTrue(output.getvalue(), '특징 불일치 경보 로그 없음')
        self.assertEqual(json.loads(output.getvalue())['error_type'], 'FeatureVersionMismatch')

    def test_hanging_inference_is_killed_and_no_late_result_is_reused(self):
        model = self.adapter(partial(Backend, 'hang'), timeout_s=.03)
        started = time.monotonic()
        self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(model._process.is_alive())
        started = time.monotonic()
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'b'], 1))
        self.assertLess(time.monotonic() - started, .1)

    def test_invalid_output_becomes_fallback(self):
        model = self.adapter(partial(Backend, 'invalid'))
        self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])

    def test_load_timeout_stops_worker(self):
        model = self.adapter(partial(slow_loader), load_timeout_s=.03)
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'b'], 1))
        self.assertFalse(model._process.is_alive())

    def test_closed_model_does_not_restart(self):
        model = self.adapter()
        model.close()
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'b'], 1))

    def test_missing_artifact_raises_model_unavailable(self):
        from ml.adapters.suspicion_model import load_model
        with tempfile.TemporaryDirectory() as directory:
            model = load_model(Path(directory) / 'missing.pkl')
            self.addCleanup(model.close)
            with self.assertRaises(ModelUnavailable):
                model.estimate(obs(['a', 'b'], 1))


class ModelGames(unittest.TestCase):
    def test_runner_pins_actual_model_version(self):
        from engine.domain.engine import PaldoEngine
        from engine.domain.npc import RuleV0Policy
        from engine.ports import EngineDeps
        from engine.testing.fakes import FixtureContentSource, ListEventSink
        from tools.run_engine_games import run_game
        content = FixtureContentSource().load('*')
        sink = ListEventSink()
        run_game(PaldoEngine(RuleV0Policy(content)), EngineDeps(content, FakeSuspicionModel()),
                 'base6', 1, 0, 'rule-v0', sink, model_version='test-pinned')
        self.assertTrue(sink.items)
        self.assertEqual({e.model_versions['suspicion'] for e in sink.items}, {'test-pinned'})

    def test_adapter_load_failure_keeps_game_running_and_marks_fallback(self):
        from engine.domain.engine import PaldoEngine
        from engine.domain.npc import RuleV0Policy
        from engine.ports import EngineDeps
        from engine.testing.fakes import FixtureContentSource, ListEventSink
        from ml.adapters.suspicion_model import SuspicionModelAdapter
        from tools.run_engine_games import run_game
        content = FixtureContentSource().load('*')
        sink = ListEventSink()
        model = SuspicionModelAdapter(broken_loader, 'fake-1')
        self.addCleanup(model.close)
        with redirect_stdout(io.StringIO()):
            run = run_game(PaldoEngine(RuleV0Policy(content)), EngineDeps(content, model),
                           'base6', 1, 0, 'rule-v0', sink)
        self.assertIsNotNone(run.winner)
        self.assertTrue(any(e.payload.get('policy_version', '').endswith('-fallback')
                            for e in sink.items if e.event_type == 'decision'))

    def test_missing_model_cli_fails_without_claiming_success(self):
        from ml.adapters.run_model_games import main
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'output'
            self.assertEqual(main(['--model', str(Path(directory) / 'missing.pkl'),
                                   '--games', '1', '--out', str(out)]), 1)
            self.assertFalse((out / 'manifest.json').exists())

    def test_failed_rerun_removes_previous_success_manifest(self):
        from ml.adapters.run_model_games import main
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'manifest.json').write_text('{"status":"passed"}', encoding='utf8')
            self.assertEqual(main(['--model', str(out / 'missing.pkl'), '--out', str(out)]), 1)
            self.assertFalse((out / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
