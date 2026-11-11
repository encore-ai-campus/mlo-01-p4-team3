"""야간 실행의 산출물 보존과 추적 터널 실패 시 정리를 검증한다."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from ops.mlflow.job import execution_directory, tracking_tunnel
from ops.mlflow.retrain import record_candidate
from pipeline.spark.privacy_filter import load_tombstones


class RetrainJobTests(unittest.TestCase):
    """완료되지 않은 추적 연결이나 D 거부 결과를 성공 등록으로 바꾸지 않는다."""

    def test_repeated_runs_get_different_persistent_directories(self):
        self.assertNotEqual(execution_directory('/work'), execution_directory('/work'))
        self.assertEqual(execution_directory('/work').parent, Path('/work'))

    def test_failed_tunnel_cleans_process_and_preserves_previous_uri(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / 'identity'
            identity.write_text('test-only')
            process = Mock()
            process.poll.return_value = 255
            config = {'mlflow_tunnel': {'identity': str(identity), 'local_port': 15000, 'remote_port': 5000,
                                       'known_hosts': '/protected/hosts', 'target': 'test-host'}}
            with patch.dict(os.environ, {'MLFLOW_TRACKING_URI': 'previous'}), patch(
                    'ops.mlflow.job.subprocess.Popen', return_value=process):
                with self.assertRaisesRegex(RuntimeError, 'tracking_tunnel_failed'):
                    with tracking_tunnel(config):
                        self.fail('training must not start')
                self.assertEqual(os.environ['MLFLOW_TRACKING_URI'], 'previous')
            process.terminate.assert_called_once()
            process.wait.assert_called_once()

    def test_tracking_target_cannot_be_an_ssh_option(self):
        with self.assertRaisesRegex(ValueError, 'invalid_tracking_target'):
            with tracking_tunnel({'mlflow_tunnel': {'target': '-oProxyCommand=command'}}):
                self.fail('must not connect')

    def test_d_effectiveness_rejection_cannot_register_even_if_basic_gate_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            fake = MagicMock()
            fake.MlflowClient.return_value = client
            fake.set_experiment.return_value.experiment_id = 'experiment'
            fake.start_run.return_value.__enter__.return_value.info.run_id = 'run'
            config = {'tombstones': directory, '_tombstones': load_tombstones(directory), 'code_commit': 'a' * 40}
            spec = {'data_version': 'delta:training@1', 'feature_version': 'features-1', 'model': 'suspicion'}
            report = {'data_version': spec['data_version'], 'feature_version': spec['feature_version'],
                      'code_commit': config['code_commit'], 'accepted': False, 'model_version': 'candidate-v1'}
            with patch.dict('sys.modules', {'mlflow': fake}), patch('ops.mlflow.retrain.evaluate',
                    return_value=SimpleNamespace(accepted=True, reasons=[])), patch(
                    'ops.mlflow.gate.check_run', side_effect=lambda c,r,e,**kw:e(None)[0]), patch(
                    'ops.mlflow.gate.register_candidate') as register:
                result = record_candidate('suspicion', spec, report, Path(directory)/'unused.pkl', config)
            self.assertFalse(result['accepted'])
            register.assert_not_called()
            fake.log_artifact.assert_not_called()
