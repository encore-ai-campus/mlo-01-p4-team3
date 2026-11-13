"""별칭 변경·재시작·불완전 가중치가 판의 고정 버전을 바꾸지 않는지 검사한다."""
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from engine.ports import ModelUnavailable
from server.app.model_versions import INTENT_KEY, SUSPICION_KEY
from server.infra.mlflow_artifacts import MlflowArtifacts, PinnedIntentParser


class ArtifactTests(unittest.TestCase):
    """외부 서버 없이 실제 디렉터리 게시와 고정 버전 복구를 검사한다."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = Mock()
        self.client.get_run.return_value = SimpleNamespace(data=SimpleNamespace(tags={'feature_version': 'features-1'}))
        self.artifacts = MlflowArtifacts(self.client, {SUSPICION_KEY: 'suspicion', INTENT_KEY: 'intent'},
                                        self.root / 'cache', 'features-1')

    def tearDown(self):
        self.temporary.cleanup()

    def download(self, name, *, weights=True, feature=None):
        """MLflow 다운로드 결과처럼 새 임시 경로 안에 모델을 만든다."""
        def copy(run_id, artifact, directory):
            p = Path(directory) / 'model'
            p.mkdir()
            if weights:
                (p / 'models.pkl').write_bytes(name.encode())
            if feature:
                (p / 'report.json').write_text(json.dumps({'feature_version': feature}), encoding='utf-8')
            return str(p)
        self.client.download_artifacts.side_effect = copy
        return SimpleNamespace(run_id=name, tags={'paldo.artifact_version': name})

    def test_completed_weights_published_once_with_provenance(self):
        model = self.download('v1')
        p = self.artifacts.prepare(SUSPICION_KEY, model)
        self.assertEqual((p / 'models.pkl').read_bytes(), b'v1')
        self.assertEqual(json.loads((p / 'report.json').read_text())['feature_version'], 'features-1')
        self.artifacts.prepare(SUSPICION_KEY, model)
        self.client.download_artifacts.assert_called_once()

    def test_missing_weights_never_leave_a_published_cache(self):
        model = self.download('v1', weights=False)
        with self.assertRaises(ModelUnavailable):
            self.artifacts.prepare(SUSPICION_KEY, model)
        self.assertFalse((self.root / 'cache/suspicion-v0/v1').exists())
        self.assertEqual(list((self.root / 'cache/suspicion-v0').iterdir()), [])

    def test_feature_mismatch_is_rejected_before_publication(self):
        model = self.download('v1', feature='other-feature')
        with self.assertRaises(ModelUnavailable):
            self.artifacts.prepare(SUSPICION_KEY, model)
        self.assertFalse((self.root / 'cache/suspicion-v0/v1').exists())

    def test_restart_recovers_old_pinned_version_without_using_current_alias(self):
        model = self.download('old-v1')
        self.client.search_model_versions.return_value = [model]
        p = self.artifacts.ensure(SUSPICION_KEY, 'old-v1')
        self.assertEqual((p / 'models.pkl').read_bytes(), b'old-v1')
        self.client.get_model_version_by_alias.assert_not_called()

    def test_path_version_cannot_escape_cache(self):
        with self.assertRaises(ModelUnavailable):
            self.artifacts.ensure(SUSPICION_KEY, '../outside')
        self.client.search_model_versions.assert_not_called()

    def test_intent_uses_game_version_after_registry_rollback(self):
        repository = Mock()
        repository.load.return_value = (SimpleNamespace(meta=SimpleNamespace(model_versions={INTENT_KEY: 'old-v1'})), None)
        artifacts = Mock()
        artifacts.intent_parser.return_value.parse.return_value = 'old-result'
        parser = PinnedIntentParser(repository, artifacts, Mock())
        self.assertEqual(parser.parse('text', SimpleNamespace(game_id='game')), 'old-result')
        artifacts.intent_parser.assert_called_once_with('old-v1')

    def test_missing_pinned_intent_uses_baseline_without_selecting_new_alias(self):
        repository = Mock()
        repository.load.return_value = (SimpleNamespace(meta=SimpleNamespace(model_versions={INTENT_KEY: 'old-v1'})), None)
        artifacts, fallback = Mock(), Mock()
        artifacts.intent_parser.side_effect = ModelUnavailable('missing')
        fallback.parse.return_value = 'fallback'
        self.assertEqual(PinnedIntentParser(repository, artifacts, fallback).parse('text', SimpleNamespace(game_id='g')), 'fallback')
