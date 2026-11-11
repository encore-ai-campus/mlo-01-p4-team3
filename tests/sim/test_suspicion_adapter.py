"""D-08-01 계약 경계 테스트. 학습 모델의 성능 증거가 아니다."""
from dataclasses import replace
from functools import partial
from pathlib import Path
import tempfile
import io
import json
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
import unittest
from unittest import mock

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
        if snapshot.game_id == 'slow':
            time.sleep(.25)
        if self.mode == 'error':
            raise ValueError('inference failed')
        if snapshot.game_id == 'bad-feature':
            raise FeatureVersionMismatch('input feature mismatch')
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


def recovering_loader(marker, mode='error'):
    path = Path(marker)
    first = not path.exists()
    path.touch()
    return Backend(mode if first else 'ok')


def changed_identity_loader(marker):
    path = Path(marker)
    first = not path.exists()
    path.touch()
    model = Backend('error' if first else 'ok')
    if not first:
        model.model_version = 'different-model'
    return model


class AdapterContract(unittest.TestCase):
    def adapter(self, loader=Backend, **kwargs):
        from ml.adapters.suspicion_model import SuspicionModelAdapter
        model = SuspicionModelAdapter(loader, 'fake-1', **kwargs)
        self.addCleanup(model.close)
        return model

    def wait_ready(self, model):
        self.assertTrue(model._ready.wait(10), 'worker did not recover')

    def test_failed_call_recovers_same_version_for_next_game(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.adapter(partial(recovering_loader, str(Path(directory) / 'loaded')))
            version = model.model_version
            self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.UNAVAILABLE)
            self.wait_ready(model)
            self.assertIsNone(estimate(model, replace(obs(['a', 'b'], 1), game_id='next-game'))[1])
            self.assertEqual(model.model_version, version)
            self.assertEqual(model._failures, 0)

    def test_soft_timeout_keeps_pid_and_discards_the_late_reply(self):
        model = self.adapter(timeout_s=.06)
        pid = model._process.pid
        with self.assertRaises(ModelUnavailable):
            model.estimate(replace(obs(['a', 'b'], 1), game_id='slow'))
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'c'], 1))
        self.assertTrue(model._idle.wait(3))
        result = model.estimate(obs(['b', 'c'], 1))
        self.assertEqual(result.candidates, ['b', 'c'])
        self.assertEqual(model._process.pid, pid)
        self.assertEqual(model._failures, 0)

    def test_hard_timeout_restarts_without_another_call(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.adapter(partial(recovering_loader, str(Path(directory) / 'loaded'), 'hang'), timeout_s=.03)
            process = model._process
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
            self.assertTrue(process.is_alive())
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            self.wait_ready(model)
            self.assertIsNone(estimate(model, obs(['a', 'b'], 1))[1])
            self.assertEqual(len(model.recovery_ms), 1)

    def test_dead_worker_restarts_same_version(self):
        model = self.adapter()
        process = model._process
        process.terminate()
        process.join(timeout=2)
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'b'], 1))
        self.wait_ready(model)
        self.assertNotEqual(model._process.pid, process.pid)
        self.assertEqual(model.estimate(obs(['a', 'b'], 1)).model_version, 'fake-suspicion-1')

    def test_input_feature_error_keeps_healthy_worker(self):
        model = self.adapter(partial(Backend, 'version'))
        pid = model._process.pid
        self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.VERSION)
        self.assertEqual(estimate(model, obs(['a', 'b'], 1))[1], FallbackReason.VERSION)
        self.assertEqual(model._process.pid, pid)
        self.assertTrue(model._process.is_alive())
        self.assertEqual(model._failures, 0)

    def test_busy_and_restarting_calls_do_not_count_as_worker_failures(self):
        model = self.adapter(timeout_s=.01)
        with model._lock:
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        model._ready.clear()
        self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        self.assertEqual(model._failures, 0)
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(model.latencies_ms), 2)

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
        process = model._process
        started = time.monotonic()
        self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        self.assertLess(time.monotonic() - started, 1)
        process.join(timeout=2)
        self.assertFalse(process.is_alive())
        started = time.monotonic()
        with self.assertRaises(ModelUnavailable):
            model.estimate(obs(['a', 'b'], 1))
        self.assertLess(time.monotonic() - started, .1)

    def test_worker_input_feature_exception_does_not_end_worker_loop(self):
        model = self.adapter()
        pid = model._process.pid
        self.assertEqual(estimate(model, replace(obs(['a', 'b'], 1), game_id='bad-feature'))[1],
                         FallbackReason.VERSION)
        self.assertIsNone(estimate(model, obs(['a', 'b'], 1))[1])
        self.assertEqual(model._process.pid, pid)

    def test_three_actual_failures_open_30_second_circuit_and_close_cancels_restart(self):
        model = self.adapter(partial(Backend, 'error'))
        for _ in range(2):
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
            self.wait_ready(model)
        self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        deadline = time.monotonic() + 5
        while not model._cooldown_until and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertGreater(model._cooldown_until - time.monotonic(), 29)
        for _ in range(5):
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
        self.assertEqual(model._failures, 3)
        model.close()
        model._recovery_thread.join(timeout=2)
        self.assertFalse(model._recovery_thread.is_alive())
        self.assertFalse(model._process.is_alive())

    def test_shared_store_lends_one_worker_and_only_owner_closes_it(self):
        from ml.adapters.suspicion_model import SharedModelStore
        model = self.adapter()
        store = SharedModelStore()
        self.addCleanup(store.close)
        loader = mock.Mock(return_value=model)
        first = store.get(model.model_version, loader)
        second = store.get(model.model_version, loader)
        first.close()
        self.assertIsNone(estimate(second, obs(['a', 'b'], 1))[1])
        loader.assert_called_once()
        store.close()
        self.assertFalse(model._process.is_alive())
        with self.assertRaises(ModelUnavailable):
            store.get(model.model_version, loader)

    def test_restart_load_failures_also_open_the_circuit(self):
        model = self.adapter(broken_loader)
        deadline = time.monotonic() + 5
        while not model._cooldown_until and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertGreater(model._cooldown_until - time.monotonic(), 29)
        self.assertEqual(model._failures, 3)
        model.close()

    def test_restart_cannot_change_the_pinned_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.adapter(partial(changed_identity_loader, str(Path(directory) / 'loaded')))
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])
            model._recovery_thread.join(timeout=5)
            self.assertTrue(model._permanent)
            self.assertEqual(model.model_version, 'fake-suspicion-1')
            self.assertFalse(model._process.is_alive())
            self.assertIsNotNone(estimate(model, obs(['a', 'b'], 1))[1])

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
            self.assertTrue(model._permanent)
            self.assertIsNone(model._recovery_thread)


class InProcessAdapterContract(unittest.TestCase):
    def test_shared_direct_model_allows_concurrent_calls(self):
        from ml.adapters.suspicion_model import InProcessSuspicionModelAdapter, SharedModelStore

        ready = Barrier(2, timeout=5)

        class ConcurrentBackend(Backend):
            def estimate(self, snapshot):
                ready.wait()
                return super().estimate(snapshot)

        with mock.patch('ml.adapters.suspicion_model._ArtifactModel', return_value=ConcurrentBackend()):
            adapter = InProcessSuspicionModelAdapter(Path('trusted-model.pkl'))
        store = SharedModelStore()
        self.addCleanup(store.close)
        borrowed = store.get(adapter.model_version, lambda: adapter)
        snapshot = obs(['a', 'b'], 1)
        borrowed.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(borrowed.estimate, [snapshot, snapshot]))
        self.assertEqual([result.model_version for result in results], [adapter.model_version] * 2)
        self.assertEqual(store.worker_pids(), {})

    def test_direct_model_uses_existing_deadline_and_fallback_reasons(self):
        from ml.adapters.suspicion_model import InProcessSuspicionModelAdapter

        backend = Backend()
        with mock.patch('ml.adapters.suspicion_model._ArtifactModel', return_value=backend):
            adapter = InProcessSuspicionModelAdapter(Path('trusted-model.pkl'))
        snapshot = obs(['a', 'b'], 1)
        self.assertEqual(estimate(adapter, snapshot, iter((0, .101)).__next__), (None, FallbackReason.TIMEOUT))
        self.assertEqual(adapter.estimate(snapshot).model_version, adapter.model_version)
        backend.mode = 'version'
        self.assertEqual(estimate(adapter, snapshot), (None, FallbackReason.VERSION))
        backend.mode = 'error'
        self.assertEqual(estimate(adapter, snapshot), (None, FallbackReason.UNAVAILABLE))
        adapter.close()
        self.assertEqual(estimate(adapter, snapshot), (None, FallbackReason.UNAVAILABLE))


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
