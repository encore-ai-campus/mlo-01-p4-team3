"""판 모델 버전 고정·카나리아 배정·업데이트 내역의 B 응용(B-09-01, ml_lifecycle.md 4절).

계층: 응용. engine(도메인)과 표준 라이브러리만 import한다. 레지스트리 구현(C-09-01 MLflow 등)은 인프라가
`ModelRegistry` 포트로 주입한다.

규칙
- 모델 버전은 판 시작 때 한 번만 고르고 `GameMeta.model_versions`에 고정한다. 이미 버전이 있는 판(저장된 판,
  재시작 요청)은 다시 고르지 않는다. 레지스트리가 롤백돼도 진행 중인 판은 메타의 버전을 그대로 쓴다.
- 카나리아는 회차 없는 일반 판에서만, 설정 비율(시작값 0, 운영에서 정함)로 배정한다. 사용자 테스트 회차 판은
  항상 끈다. 배정은 판 ID 해시로 정해 같은 판의 재전송에도 같은 결과가 나온다.
- 해석 결과의 모델 버전이 판 메타의 의도 해석기 버전과 다르면 경보 로그(`level=warning`)를 남긴다. 해석은 막지 않는다.
- 씐 자 전략 밴딧(11/12): 스위치가 켜져 있으면 회차 없는 일반 사람 판(부하 시험 아님)에만 밴딧 버전과 팔을
  `model_versions`의 `bandit`·`bandit_arm`에 고정한다. 고르기에 실패하면 판 시작을 막지 않고 기록 없이(균등 비율) 시작한다.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from typing import Callable, Optional, Protocol

from engine.domain.model import DataSource, GameMeta

LOGGER = logging.getLogger("paldo.web.model_versions")

INTENT_KEY = "intent"          # model_versions 안의 의도 해석기 버전 키(FakeModelSelector와 같은 이름)
SUSPICION_KEY = "suspicion"    # 의심 모델 버전 키
BANDIT_KEY = "bandit"          # 씐 자 전략 밴딧 버전 키(D-09-02, 11/12 운영 연결)
BANDIT_ARM_KEY = "bandit_arm"  # 그 판에 고른 씐 자 전략 팔 ID(`ml/bandit_arms.json`)
CANARY_BUCKETS = 10_000


@dataclass(frozen=True)
class PromotionRecord:
    """레지스트리 승격 기록 한 줄. 화면에는 이 값만 보인다."""

    model: str          # INTENT_KEY·SUSPICION_KEY·BANDIT_KEY 중 하나
    version: str
    stage: str          # "production" | "canary" | "previous"
    at: str             # ISO 날짜·시각


class ModelRegistry(Protocol):
    """모델 레지스트리 조회 포트. 실제 구현은 C-09-01 레지스트리 조회(인프라)."""

    def production_versions(self) -> dict[str, str]:
        """지금 운영(`production`) 버전. 키는 모델 이름."""
        ...

    def canary_versions(self) -> dict[str, str]:
        """카나리아(`canary`) 버전. 없으면 빈 dict."""
        ...

    def promotions(self) -> list[PromotionRecord]:
        """승격 기록(순서 무관)."""
        ...


def canary_bucket(game_id: str) -> float:
    """판 ID를 0 이상 1 미만 값으로 바꾼다. 같은 판 ID면 항상 같은 값이다."""
    digest = hashlib.sha256(f"paldo:canary:{game_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % CANARY_BUCKETS / CANARY_BUCKETS


class VersionPinning:
    """`GameService.pin_versions`로 쓰는 판 시작 버전 고정 정책."""

    def __init__(self, registry: ModelRegistry, canary_ratio: Callable[[], float] = lambda: 0.0):
        self.registry = registry
        self.canary_ratio = canary_ratio

    def canary_allowed(self, meta: GameMeta) -> bool:
        """사용자 테스트 회차 판은 카나리아를 끈다(ml_lifecycle.md 4절)."""
        return meta.session_id is None

    def __call__(self, meta: GameMeta) -> GameMeta:
        """새 판 메타에 버전을 고정한다. 이미 버전이 있는 메타는 그대로 돌려준다."""
        if meta.model_versions:
            return meta
        versions = dict(self.registry.production_versions())
        canary = False
        candidate = self.registry.canary_versions() if self.canary_allowed(meta) else {}
        ratio = min(max(float(self.canary_ratio()), 0.0), 1.0)
        if candidate and ratio > 0 and canary_bucket(meta.game_id) < ratio:
            versions.update(candidate)
            canary = True
        return replace(meta, model_versions=versions, canary=canary)


def pinned_dependencies(dependencies: Callable, suspicion_model: Optional[Callable[[str], object]]):
    """판 메타에 고정한 의심 모델 버전으로 엔진 의존성을 만든다.

    `suspicion_model`이 None이거나 메타에 의심 모델 버전이 없으면 원래 의존성을 그대로 쓴다.
    `suspicion_model`에 `for_game(game_id, version)`이 있으면(프로세스 보관소) 판 ID와 함께 빌려 판 수를 세게 한다.
    """
    if suspicion_model is None:
        return dependencies
    for_game = getattr(suspicion_model, "for_game", None)

    def resolve(meta):
        deps = dependencies(meta)
        version = meta.model_versions.get(SUSPICION_KEY)
        if not version:
            return deps
        model = for_game(meta.game_id, version) if for_game is not None else suspicion_model(version)
        return replace(deps, suspicion=model)
    resolve.__doc__ = "판 메타의 의심 모델 버전으로 바꾼 엔진 의존성."
    return resolve


def parse_version_mismatch(meta: GameMeta, parse_versions: dict) -> Optional[dict]:
    """해석 결과 모델 버전과 판 메타의 의도 해석기 버전이 다르면 두 값을, 같거나 비교할 수 없으면 None."""
    pinned = meta.model_versions.get(INTENT_KEY)
    used = (parse_versions or {}).get("model")
    if not pinned or not used or pinned == used:
        return None
    return {"game_id": meta.game_id, "pinned_model": pinned, "parse_model": used}


def warn_parse_version_mismatch(meta: GameMeta, parse_versions: dict) -> bool:
    """불일치면 경보 로그를 남기고 True. 해석 흐름은 그대로 진행한다."""
    mismatch = parse_version_mismatch(meta, parse_versions)
    if mismatch is None:
        return False
    LOGGER.warning("intent_model_version_mismatch",
                   extra={"component": "model_versions", "fields": mismatch})
    return True


def update_history(registry: Optional[ModelRegistry], limit: int = 20) -> list[PromotionRecord]:
    """화면용 승격 기록. 최근 것부터 `limit`개, 레지스트리가 없으면 빈 목록."""
    if registry is None:
        return []
    return sorted(registry.promotions(), key=lambda r: (r.at, r.model, r.version), reverse=True)[:limit]


class BanditChooser(Protocol):
    """씐 자 전략 밴딧 팔 고르기 포트. 실제 구현은 인프라(`server/infra/possessed_bandit.py`, Mongo 게시 상태)."""

    def choose(self, game_id: str) -> tuple[str, str]:
        """(밴딧 버전, 팔 ID). 같은 판 ID와 같은 게시 상태면 같은 값이다."""
        ...


class BanditPinning:
    """`VersionPinning` 뒤에 붙여 새 판 메타에 씐 자 전략 밴딧 버전·팔을 고정한다.

    대상: 스위치 켜짐, 회차 없는 판(`session_id is None`), 사람 판, 부하 시험 아님, 아직 밴딧 기록 없음.
    튜토리얼 판은 이 시작 경로를 쓰지 않는다(출처 SCRIPTED). 고르기 실패는 경고 로그만 남기고 기록 없이 시작한다.
    """

    def __init__(self, inner: Optional[Callable[[GameMeta], GameMeta]], chooser: BanditChooser,
                 enabled: Callable[[], bool] = lambda: True, load_test: Callable[[], bool] = lambda: False):
        self.inner = inner
        self.chooser = chooser
        self.enabled = enabled
        self.load_test = load_test

    def eligible(self, meta: GameMeta) -> bool:
        """밴딧 팔을 고를 판인지."""
        return (bool(self.enabled()) and not self.load_test() and meta.session_id is None
                and meta.source is DataSource.HUMAN and BANDIT_KEY not in meta.model_versions)

    def __call__(self, meta: GameMeta) -> GameMeta:
        """기존 버전 고정 뒤, 대상 판이면 밴딧 버전·팔을 더한다."""
        if self.inner is not None:
            meta = self.inner(meta)
        if not self.eligible(meta):
            return meta
        try:
            version, arm = self.chooser.choose(meta.game_id)
        except Exception as exc:  # 밴딧 상태를 못 읽어도 판 시작은 막지 않는다(균등 비율)
            LOGGER.warning("possessed_bandit_unavailable",
                           extra={"component": "model_versions", "fields": {"error_type": type(exc).__name__}})
            return meta
        return replace(meta, model_versions={**meta.model_versions, BANDIT_KEY: version, BANDIT_ARM_KEY: arm})
