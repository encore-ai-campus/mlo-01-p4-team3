"""필수 태그와 평가 결과가 후보 등록 앞에 오는지 검사한다."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.mlflow import gate
from server.infra.model_selector import lookup_model


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

    def test_checkpoint_artifact_registers_with_its_run_id(self):
        self.client.create_model_version.return_value = SimpleNamespace(version="4")
        source = "file:///private/artifacts/model"
        with patch.dict(sys.modules, {"mlflow.exceptions": SimpleNamespace(MlflowException=ValueError)}):
            version = gate.register_candidate(self.client, "r1", "intent", "model", source_uri=source)
        self.assertEqual(version, "4")
        self.client.create_model_version.assert_called_once_with("intent", source, run_id="r1")
        self.client.set_registered_model_alias.assert_called_once_with("intent", "candidate", "4")

    def test_selector_resolves_an_alias_to_an_exact_version(self):
        self.client.get_model_version_by_alias.return_value = SimpleNamespace(
            version=3, source="mlflow-artifacts:/1/r1/artifacts/model", run_id="r1"
        )
        self.assertEqual(lookup_model(self.client, "suspicion", "candidate"), {
            "version": "3", "source": "mlflow-artifacts:/1/r1/artifacts/model", "run_id": "r1"
        })
        self.client.get_model_version_by_alias.assert_called_once_with("suspicion", "candidate")
