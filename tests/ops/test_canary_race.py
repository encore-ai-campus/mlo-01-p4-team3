"""C-10-01 평가한 canary와 실제 승격 대상이 달라지는 경합 회귀."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.mlflow.canary import main
from ops.mlflow.lifecycle import transition


class CanaryRaceTests(unittest.TestCase):
    """쓰기 전의 별칭 변경을 거부하고 평가 버전에만 승격을 제한한다."""

    def test_cli_observation_then_changed_canary_never_writes(self):
        """CLI 최초 관측 직후 canary가 바뀌는 원래 경합을 재현한다."""
        client = Mock()
        client.get_model_version_by_alias.side_effect = [
            SimpleNamespace(version='1'), SimpleNamespace(version='9'),
            SimpleNamespace(version='2', run_id='new-run')]
        report = dict(canary_games=30, control_games=30, canary_win=.7, control_win=.7,
                      fallback=0, p95_ms=20, intent_edit_cancel='unmeasurable', source='bot')
        with TemporaryDirectory() as directory:
            path = Path(directory)/'observations.json'
            path.write_text(json.dumps(report), encoding='utf-8');path.chmod(0o600)
            with patch.dict(sys.modules, {'mlflow': SimpleNamespace(MlflowClient=lambda: client)}):
                with self.assertRaisesRegex(ValueError, 'canary_version_changed'):
                    main(['--report', str(path), '--model', 'suspicion', '--expected-production', '9',
                          '--expected-canary', '1', '--apply'])
        client.set_registered_model_alias.assert_not_called()
        client.set_model_version_tag.assert_not_called()
        client.delete_registered_model_alias.assert_not_called()

    def test_gate_lookup_then_changed_canary_is_rechecked_before_first_write(self):
        """전환 함수 안의 관문 조회 도중 바뀌어도 재확인에서 중단한다."""
        client = Mock()
        client.get_model_version_by_alias.side_effect = [SimpleNamespace(version='9'),
            SimpleNamespace(version='1', run_id='evaluated'), SimpleNamespace(version='2')]
        client.get_run.return_value = SimpleNamespace(info=SimpleNamespace(status='FINISHED'),
            data=SimpleNamespace(tags={'paldo.gate.status': 'passed'}))
        with self.assertRaisesRegex(ValueError, 'canary_version_changed'):
            transition(client, 'suspicion', 'promote', '9', 'cohorts_passed', expected_canary='1')
        client.set_registered_model_alias.assert_not_called()
        client.set_model_version_tag.assert_not_called()

    def test_promote_requires_expected_candidate_and_pins_that_version(self):
        """기대 후보 누락은 거부하고 정상 경로는 평가 버전 번호만 쓴다."""
        client = Mock()
        with self.assertRaisesRegex(ValueError, 'expected_canary_required'):
            transition(client, 'suspicion', 'promote', '9', 'cohorts_passed')
        client.get_model_version_by_alias.side_effect = [SimpleNamespace(version='9'),
            SimpleNamespace(version='1', run_id='evaluated'), SimpleNamespace(version='1'),
            SimpleNamespace(version='9'), SimpleNamespace(version='1')]
        client.get_run.return_value = SimpleNamespace(info=SimpleNamespace(status='FINISHED'),
            data=SimpleNamespace(tags={'paldo.gate.status': 'passed'}))
        with patch.dict(sys.modules, {'mlflow.exceptions': SimpleNamespace(MlflowException=RuntimeError)}):
            result = transition(client, 'suspicion', 'promote', '9', 'cohorts_passed', expected_canary='1')
        self.assertEqual(result['selected_version'], '1')
        client.set_registered_model_alias.assert_any_call('suspicion', 'production', '1')

    def test_changed_production_during_gate_is_rejected_before_writes(self):
        """후보 확인 중 운영 버전이 바뀌면 이전 관측으로 덮어쓰지 않는다."""
        client = Mock()
        client.get_model_version_by_alias.side_effect = [SimpleNamespace(version='9'),
            SimpleNamespace(version='1', run_id='evaluated'), SimpleNamespace(version='1'),
            SimpleNamespace(version='10')]
        client.get_run.return_value = SimpleNamespace(info=SimpleNamespace(status='FINISHED'),
            data=SimpleNamespace(tags={'paldo.gate.status': 'passed'}))
        with self.assertRaisesRegex(ValueError, 'production_version_changed'):
            transition(client, 'suspicion', 'promote', '9', 'cohorts_passed', expected_canary='1')
        client.set_registered_model_alias.assert_not_called()
        client.set_model_version_tag.assert_not_called()
