"""튜토리얼 판 시작 정책: 서버 상수로 판 메타를 만든다(튜토리얼 설계 3·4절).

계층: 응용. engine(도메인)만 import한다. Django·저장소 구현을 import하지 않는다.
- 시드·프리셋·규칙 버전은 `engine/domain/tutorial.py` 상수다. 클라이언트 값이나 새 일반 판 규칙 설정을 따르지 않는다.
- 콘텐츠 버전은 운영 활성 버전이 검증 목록(`tutorial.CONTENT_VERSIONS`)에 있을 때만 그 값으로 고정한다. 없으면 시작을 막는다.
- 출처는 `SCRIPTED`, `scenario_id`로 튜토리얼 판을 표시한다. 의심 모델 버전은 넣지 않는다(NPC 판단 모델 없음).
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable

from engine.domain import tutorial
from engine.domain.model import ENGINE_VERSION, PRESETS, DataSource, GameMeta
from .usecases import StartUnavailable


class TutorialUnavailable(StartUnavailable):
    """운영 활성 콘텐츠 버전이 튜토리얼 대본을 확인한 버전이 아니다(503)."""


def tutorial_game_id(player, start_token):
    """같은 플레이어의 같은 시작 요청 UUID는 항상 같은 판 ID가 된다(일반 판과 다른 이름공간)."""
    return str(uuid.uuid5(uuid.UUID(player), f"paldo:tutorial:{uuid.UUID(start_token)}"))


class TutorialStartPolicy:
    """`GameService.start_tutorial`이 쓰는 메타 생성 정책."""

    def __init__(self, active_content_version: Callable[[], str]):
        self.active_content_version = active_content_version

    def __call__(self, data, player):
        content_version = self.active_content_version()
        if content_version not in tutorial.CONTENT_VERSIONS:
            raise TutorialUnavailable(content_version)
        return GameMeta(tutorial_game_id(player, data.start_token), PRESETS[tutorial.PRESET_ID], tutorial.SEED,
                        DataSource.SCRIPTED, tutorial.rule_version(), ENGINE_VERSION, content_version,
                        player_pseudonym=player, session_id=None, mudang_variant=data.mudang_variant,
                        scenario_id=tutorial.SCENARIO_ID)


LOGGER = logging.getLogger("paldo.tutorial")
_REPORTED: set = set()


def report_off_script(game_id: str, state_version: int, day: int, phase: str) -> None:
    """각본 이탈을 서버 로그에 한 번 남긴다(같은 판·상태 버전은 프로세스 안에서 한 번만).

    대본이 어긋나는 원인은 규칙 수치·NPC 정책·콘텐츠·의존성 조립(모델 끼어듦) 변경이다. 로그로 알아채고 시드·대본을 다시 고른다.
    """
    key = (game_id, state_version)
    if key in _REPORTED:
        return
    _REPORTED.add(key)
    LOGGER.warning("tutorial_off_script game=%s state_version=%s day=%s phase=%s", game_id, state_version, day, phase)


def tutorial_instead(service, data, player):
    """일반 판 시작 요청을 튜토리얼 판으로 바꿔 시작한다(11/11 사용자 결정: 튜토리얼 강제). 바꾸지 않으면 None.

    - 같은 시작 UUID로 이미 만든 튜토리얼 판이 있으면 그 판을 돌려준다(시작 뒤 완료 표시가 켜져도 재요청은 같은 판).
    - 판단은 도메인 `tutorial.routes_to_tutorial`(회차 판은 가로채지 않음).
    - 튜토리얼 정책이 없거나 운영 콘텐츠 버전이 튜토리얼 확인 버전이 아니면 일반 판으로 둔다(시작 자체를 막지 않음).
    진행 중인 일반 판은 중단하지 않는다(튜토리얼은 진행 판 규칙과 무관).
    """
    from .dto import StartTutorialInput
    from .usecases import ResourceNotFound
    if data.session_id is not None or not data.start_token or service.prepare_tutorial is None:
        return None
    try:
        replay = service.detail(tutorial_game_id(player, data.start_token), player)
    except ResourceNotFound:
        pass
    else:
        service.progress.mark_tutorial_completed(player)   # 앞 요청이 판 저장 뒤 표시 전에 실패했으면 메운다(멱등)
        return replay
    if not tutorial.routes_to_tutorial(service.progress.load(player).tutorial_completed, data.session_id):
        return None
    try:
        return service.start_tutorial(StartTutorialInput(player, data.start_token, data.mudang_variant), player, True)
    except TutorialUnavailable:
        LOGGER.warning("tutorial_route_skipped reason=content_version")
        return None
