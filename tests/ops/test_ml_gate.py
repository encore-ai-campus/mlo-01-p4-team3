"""필수 태그와 평가 결과가 후보 등록 앞에 오는지 검사한다."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.mlflow import gate


class GateTests(unittest.TestCase):
    """가짜 레지스트리에서 두 실패 경로와 통과 재시도를 검증한다."""

    def setUp(self):
        self.tags = {"data_version": "delta:bronze:42", "feature_version": "feature-1", "code_commit": "a" * 40}
        self.client = Mock()
        self.client.get_run.return_value = SimpleNamespace(info=SimpleNamespace(status="FINISHED"),
                                                           data=SimpleNamespace(tags=self.tags))
        self.client.search_model_versions.return_value = []
        self.register = Mock(return_value=SimpleNamespace(version="1"))
        self.mlflow = SimpleNamespace(MlflowClient=lambda: self.client, register_model=self.register)
        self.args = ["--run-id", "r1", "--model", "suspicion", "--artifact", "model",
                     "--evaluator", "fake_evaluator:run"]

    def invoke(self, evaluate):
        """실제 MLflow 연결 없이 명령을 호출한다."""
        with patch.dict(sys.modules, {"mlflow": self.mlflow, "fake_evaluator": SimpleNamespace(run=evaluate)}):
            return gate.main(self.args)

    def test_missing_delta_metadata_never_evaluates_or_registers(self):
        del self.tags["data_version"]
        evaluate = Mock()
        self.assertEqual(self.invoke(evaluate), 1)
        evaluate.assert_not_called()
        self.register.assert_not_called()
        self.client.set_tag.assert_any_call("r1", "paldo.gate.reason", "required_run_metadata")

    def test_rejected_metrics_never_register(self):
        self.assertEqual(self.invoke(lambda run: (False, "ece")), 1)
        self.register.assert_not_called()
        self.client.set_tag.assert_any_call("r1", "paldo.gate.reason", "ece")

    def test_accepted_retry_reuses_candidate_version(self):
        self.client.search_model_versions.return_value = [
            SimpleNamespace(run_id="r1", source="mlflow-artifacts:/1/r1/artifacts/model", version="3")
        ]
        self.assertEqual(self.invoke(lambda run: (True, "ok")), 0)
        self.register.assert_not_called()
        self.client.set_registered_model_alias.assert_called_once_with("suspicion", "candidate", "3")
