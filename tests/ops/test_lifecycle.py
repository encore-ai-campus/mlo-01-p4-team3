"""C 전환 명령이 거부 후보와 오래된 운영 버전으로 별칭을 바꾸지 않는지 검사한다."""
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from ops.mlflow.canary import verdict
from ops.mlflow.lifecycle import transition
from ops.mlflow.retrain import evaluate
from server.infra.mlflow_registry import MlflowModelRegistry


class LifecycleTests(unittest.TestCase):
    """B 포트의 아티팩트 버전과 C 별칭 전환의 경계를 검증한다."""

    def test_registry_returns_artifact_version_instead_of_mlflow_number(self):
        """모델 버전 번호 7을 실제 모델 로더의 버전 이름으로 잘못 넘기지 않는다."""
        client = Mock()
        client.get_model_version_by_alias.return_value = SimpleNamespace(
            version="7", tags={"paldo.artifact_version": "suspicion-v2"}
        )
        registry = MlflowModelRegistry(client, {"suspicion": "paldo-suspicion"})
        with patch.dict(sys.modules, {"mlflow.exceptions": SimpleNamespace(MlflowException=RuntimeError)}):
            self.assertEqual(registry.production_versions(), {"suspicion": "suspicion-v2"})

    def test_changed_production_and_failed_gate_do_not_write_aliases(self):
        """이전 관측으로 실행한 전환과 관문 거부 후보를 차단한다."""
        client = Mock()
        client.get_model_version_by_alias.side_effect = [
            SimpleNamespace(version="2"), SimpleNamespace(version="2"),
            SimpleNamespace(version="3", run_id="r3"),
        ]
        with self.assertRaisesRegex(ValueError, "production_version_changed"):
            transition(client, "suspicion", "canary", "1", "test")
        client.get_run.return_value = SimpleNamespace(
            info=SimpleNamespace(status="FINISHED"), data=SimpleNamespace(tags={"paldo.gate.status": "rejected"})
        )
        with self.assertRaisesRegex(ValueError, "candidate_gate_not_passed"):
            transition(client, "suspicion", "canary", "2", "test")
        client.set_registered_model_alias.assert_not_called()

    def test_missing_canary_after_rollback_returns_no_candidate(self):
        """MLflow에서 canary 미설정 오류 코드가 달라도 실제 별칭 부재를 확인한다."""
        client = Mock()
        client.get_model_version_by_alias.side_effect = RuntimeError("alias absent")
        client.get_registered_model.return_value = SimpleNamespace(aliases={})
        registry = MlflowModelRegistry(client, {"suspicion": "paldo-suspicion"})
        with patch.dict(sys.modules, {"mlflow.exceptions": SimpleNamespace(MlflowException=RuntimeError)}):
            self.assertEqual(registry.canary_versions(), {})
            client.get_registered_model.return_value = SimpleNamespace(aliases={"canary": "2"})
            with self.assertRaises(RuntimeError):
                registry.canary_versions()

    def test_cohort_minimum_and_adverse_signal_override_promotion(self):
        """표본 부족·즉시 롤백·검토 신호가 있는 관측은 승격하지 않는다."""
        report = dict(canary_games=30, control_games=30, canary_win=.7, control_win=.7,
                      fallback=0, p95_ms=20, intent_edit_cancel="unmeasurable", source="bot")
        self.assertEqual(verdict(report), "promote")
        self.assertEqual(verdict(report | {"control_games": 29}), "wait")
        self.assertEqual(verdict(report | {"fallback": .06}), "rollback")
        self.assertEqual(verdict(report | {"intent_edit_cancel": True}), "hold")
        with self.assertRaisesRegex(ValueError, "human_intent_signal_required"):
            verdict(report | {"source": "human"})

    def test_missing_human_suspicion_metrics_remain_rejected(self):
        """실제 사람 홀드아웃이 없는 학습 보고서를 등록 가능으로 바꾸지 않는다."""
        decision = evaluate("suspicion", {"candidate": {}, "reference": {}})
        self.assertFalse(decision.accepted)
        self.assertIn("human:invalid_or_missing_measurement", decision.reasons)
