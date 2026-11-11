"""MLflow의 신뢰된 가중치를 원자 캐시에 두고 판에 고정된 버전으로 읽는다."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from engine.ports import ModelUnavailable
from server.app.model_versions import INTENT_KEY, SUSPICION_KEY
from server.infra.model_selector import LocalModelSelector


class MlflowArtifacts:
    """별칭 번호와 산출물 버전을 구분하고 이미 고정된 과거 가중치를 유지한다."""

    def __init__(self, client, models, root, feature_version):
        """운영자가 지정한 레지스트리와 프로세스 간 공유 캐시를 사용한다."""
        self.client, self.models = client, dict(models)
        self.root, self.feature_version = Path(root), feature_version
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._parsers = {}

    def _path(self, key, version):
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', version):
            raise ModelUnavailable('invalid artifact version')
        family = {SUSPICION_KEY: 'suspicion-v0', INTENT_KEY: 'intent-v0'}[key]
        return self.root / family / version

    def prepare(self, key, model):
        """별칭을 판에 고정하기 전에 가중치와 특징 메타데이터를 캐시에 게시한다."""
        version = model.tags.get('paldo.artifact_version')
        destination = self._path(key, version)
        with self._lock:
            if destination.is_dir():
                return destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix='.download-', dir=destination.parent))
            try:
                downloaded = Path(self.client.download_artifacts(model.run_id, 'model', str(temporary))).resolve()
                if not downloaded.is_relative_to(temporary.resolve()) or not downloaded.is_dir():
                    raise ModelUnavailable('invalid downloaded artifact directory')
                if any(p.is_symlink() for p in downloaded.rglob('*')):
                    raise ModelUnavailable('artifact symlink')
                if key == SUSPICION_KEY:
                    if not (downloaded / 'models.pkl').is_file():
                        raise ModelUnavailable('missing suspicion weights')
                    if not (downloaded / 'report.json').exists():
                        tags = self.client.get_run(model.run_id).data.tags
                        feature = tags.get('feature_version')
                        if feature != self.feature_version:
                            raise ModelUnavailable('artifact feature version mismatch')
                        (downloaded / 'report.json').write_text(json.dumps({'feature_version': feature}), encoding='utf-8')
                    if json.loads((downloaded / 'report.json').read_text(encoding='utf-8')).get('feature_version') != self.feature_version:
                        raise ModelUnavailable('artifact feature version mismatch')
                elif not ((downloaded / 'model.joblib').is_file() or (downloaded / 'run.json').is_file()):
                    raise ModelUnavailable('missing intent weights')
                # 다른 웹 프로세스가 같은 버전을 먼저 게시했으면 그 불변 캐시를 사용한다.
                try:
                    os.rename(downloaded, destination)
                except OSError:
                    if not destination.is_dir():
                        raise
                return destination
            finally:
                shutil.rmtree(temporary)

    def ensure(self, key, version):
        """재시작 뒤에도 별칭이 아닌 판에 저장된 산출물 버전을 복구한다."""
        destination = self._path(key, version)
        if destination.is_dir():
            return destination
        name = self.models[key]
        for model in self.client.search_model_versions(f"name = '{name}'"):
            if model.tags.get('paldo.artifact_version') == version:
                return self.prepare(key, model)
        raise ModelUnavailable('missing registered pinned artifact')

    def suspicion_model(self, version):
        """고정 버전의 실제 의심 가중치만 읽고 다른 운영 버전으로 바꾸지 않는다."""
        self.ensure(SUSPICION_KEY, version)
        return LocalModelSelector(self.root, suspicion_version=version, intent_version='',
                                  feature_version=self.feature_version).suspicion_model(version, in_process_logistic=True)

    def intent_parser(self, version):
        """고정된 TF-IDF 또는 검수된 BERT 체크포인트 해석기를 한 번만 읽는다."""
        with self._lock:
            if version not in self._parsers:
                path = self.ensure(INTENT_KEY, version)
                if (path / 'run.json').is_file():
                    from intent.bert import BertIntentParser
                    parser = BertIntentParser(path)
                else:
                    parser = LocalModelSelector(self.root, suspicion_version='', intent_version=version,
                                                feature_version=self.feature_version).intent_parser(version)
                self._parsers[version] = parser
            return self._parsers[version]


class PinnedIntentParser:
    """입력 해석도 현재 별칭 대신 해당 판에 저장된 모델 버전을 사용한다."""

    def __init__(self, repository, artifacts, fallback):
        """이미 소유권 검사를 마친 판의 저장 메타와 기본 해석기를 받는다."""
        self.repository, self.artifacts, self.fallback = repository, artifacts, fallback

    def parse(self, text, context):
        """별칭 전환·롤백 이후에도 진행 중 판의 해석 버전을 유지한다."""
        state, _ = self.repository.load(context.game_id)
        version = state.meta.model_versions.get(INTENT_KEY)
        try:
            parser = self.artifacts.intent_parser(version) if version else self.fallback
        except Exception as error:
            from tools.jsonlog import log
            log('model_registry', 'warning', 'pinned_intent_unavailable',
                model_version=version, error_type=type(error).__name__)
            parser = self.fallback
        return parser.parse(text, context)
