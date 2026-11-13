"""모델 레지스트리 조회의 임시 인프라 구현(B-09-01).

계층: 인프라. `server.app.model_versions.ModelRegistry` 포트를 구현한다.
C-09-01(MLflow 레지스트리 조회)이 들어오기 전까지 운영 조립이 배포된 모델 버전을 그대로 운영 버전으로 쓰게 한다.
카나리아 버전과 승격 기록은 없다(빈 값). 실제 레지스트리 조회로 바꿀 때 이 클래스만 교체한다.
"""
from __future__ import annotations

from engine.ports import ModelUnavailable
from server.app.model_versions import PromotionRecord


class StaticModelRegistry:
    """배포 코드가 가진 모델 버전만 아는 레지스트리. 승격 기록은 주입한 값만 돌려준다."""

    def __init__(self, production: dict[str, str], canary: dict[str, str] | None = None,
                 promotions: list[PromotionRecord] | None = None):
        self._production = {k: v for k, v in production.items() if v}
        self._canary = dict(canary or {})
        self._promotions = list(promotions or [])

    def production_versions(self) -> dict[str, str]:
        """운영 버전 복사본."""
        return dict(self._production)

    def canary_versions(self) -> dict[str, str]:
        """카나리아 버전 복사본(없으면 빈 dict)."""
        return dict(self._canary)

    def promotions(self) -> list[PromotionRecord]:
        """주입한 승격 기록 복사본."""
        return list(self._promotions)


class UnavailableSuspicionModel:
    """이 프로세스에 없는 의심 모델 버전. 호출하면 `ModelUnavailable`을 내 엔진이 규칙 v0로 대신한다."""

    def __init__(self, version: str, feature_version: str = "unavailable"):
        self.model_version = version
        self.feature_version = feature_version

    def estimate(self, obs):
        """항상 사용 불가. 폴백 횟수는 엔진 이벤트로 집계된다."""
        raise ModelUnavailable(f"suspicion_model_unavailable:{self.model_version}")


class LoadedSuspicionModels:
    """프로세스가 올린 의심 모델을 버전으로 찾는다. 판이 고정한 버전이 없으면 사용 불가 모델을 돌려준다."""

    def __init__(self, *models):
        self._models = {m.model_version: m for m in models}

    def __call__(self, version: str):
        """판 메타의 버전에 맞는 모델."""
        model = self._models.get(version)
        return model if model is not None else UnavailableSuspicionModel(version)
