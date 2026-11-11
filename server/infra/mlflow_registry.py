"""B의 판 시작 버전 고정 포트에서 MLflow 별칭을 읽는다."""
from server.app.model_versions import PromotionRecord
import threading


class MlflowModelRegistry:
    """레지스트리 번호와 실제 어댑터의 모델 버전을 구분해 반환한다."""

    def __init__(self, client, models, prepare=None):
        """모델별 MLflow 이름을 받고 매 판에서 현재 별칭을 조회한다."""
        self.client = client
        self.models = dict(models)
        self.prepare = prepare
        self._aliases = {}
        self._lock = threading.Lock()

    def _versions(self, alias):
        from mlflow.exceptions import MlflowException

        versions = {}
        for key, name in self.models.items():
            try:
                model = self.client.get_model_version_by_alias(name, alias)
            except MlflowException as error:
                if alias == "canary" and alias not in self.client.get_registered_model(name).aliases:
                    continue
                raise
            version = model.tags.get("paldo.artifact_version")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("artifact_version_tag_required")
            if self.prepare is not None:
                self.prepare(key, model)
            versions[key] = version
        with self._lock:
            self._aliases[alias] = dict(versions)
        return versions

    def cached_versions(self):
        """종료 판 회수 때 외부 서버를 부르지 않고 마지막 확인한 활성 버전을 쓴다."""
        with self._lock:
            return {version for values in self._aliases.values() for version in values.values()}

    def production_versions(self):
        """새 판에 고정할 운영 아티팩트 버전을 반환한다."""
        return self._versions("production")

    def canary_versions(self):
        """카나리아가 설정된 모델만 반환한다."""
        return self._versions("canary")

    def promotions(self):
        """실제 기록된 별칭 전환 시각만 B의 조회 형식으로 반환한다."""
        from mlflow.exceptions import MlflowException

        records = []
        for key, name in self.models.items():
            for alias in ("production", "previous", "canary"):
                try:
                    model = self.client.get_model_version_by_alias(name, alias)
                except MlflowException as error:
                    if alias not in self.client.get_registered_model(name).aliases:
                        continue
                    raise
                at = model.tags.get("paldo.lifecycle.at")
                version = model.tags.get("paldo.artifact_version")
                if at and version:
                    records.append(PromotionRecord(key, version, alias, at))
        return records
