"""판 시작 정책: 테스트 회차 판과 회차 없는 일반 판(랜덤 시드)을 나눈다(E-08-14, 11/8 사용자 결정).

계층: 응용. engine(도메인)만 import한다. Django·저장소 구현을 import하지 않는다.
일반 판은 실제 서비스처럼 로그인·동의한 사용자가 공개 프리셋으로 바로 시작하고, 시드는 서버가 정한다.
회차 판(고정 시드, B-06-03)은 기존 회차 정책에 그대로 맡긴다.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Callable

from engine.domain.model import ENGINE_VERSION, PRESETS, DataSource, GameMeta
from .usecases import StartUnavailable

SEED_LIMIT = 2**63   # 회차 등록 검증(test_sessions.py)과 같은 시드 범위

# 일반 판에 공개하는 프리셋. 공개 프리셋 설정 파일(B-08-02 `server/app/presets_enabled.json`)이 들어오기 전에는
# 엔진 프리셋 상태가 사용자 테스트 기본(`test_default`, 지금 base6)인 것만 연다.
OPEN_PRESET_STATUS = "test_default"


def open_preset_ids():
    """일반 판에서 고를 수 있는 프리셋 ID(정렬)."""
    return sorted(pid for pid, preset in PRESETS.items() if preset.status == OPEN_PRESET_STATUS)


@dataclass(frozen=True)
class StartVersions:
    """일반 판 메타에 넣는 버전 3개. 배포된 코드·설정에서 온다."""

    rule_version: str
    engine_version: str
    content_version: str


def open_game_id(player, start_token):
    """같은 플레이어의 같은 시작 요청 UUID는 항상 같은 판 ID가 된다."""
    return str(uuid.uuid5(uuid.UUID(player), f"paldo:open:{uuid.UUID(start_token)}"))


class StartPolicy:
    """`GameService.prepare_start`로 쓰는 판 시작 정책."""

    def __init__(self, session_prepare: Callable, versions: Callable[[], StartVersions],
                 load_game: Callable, randbelow: Callable[[int], int] = secrets.randbelow):
        self.session_prepare = session_prepare
        self.versions = versions
        self.load_game = load_game
        self.randbelow = randbelow

    def __call__(self, data, player):
        """회차 ID가 있으면 회차 정책, 없으면 일반 판 메타를 만든다."""
        if data.session_id:
            # 없는 회차·미배정 회차는 회차 정책이 거부한다. 일반 판으로 바꾸지 않는다.
            return self.session_prepare(data, player)
        return self.open_meta(data, player)

    def open_meta(self, data, player):
        """공개 프리셋·서버 랜덤 시드·배포 버전으로 일반 판 메타를 만든다.

        같은 시작 UUID로 이미 만든 판이 있으면 그 판 메타를 돌려준다. 응답을 잃은 재전송이 그사이 바뀐
        공개 프리셋·콘텐츠 버전 설정 때문에 거부되지 않게, 새 판에만 필요한 검사보다 먼저 찾는다.
        """
        if data.seed is not None:
            raise ValueError("seed_not_allowed")
        if not data.start_token:
            raise ValueError("start_token_required")
        game_id = open_game_id(player, data.start_token)
        try:
            state, _ = self.load_game(game_id)
        except KeyError:
            pass
        else:
            return state.meta
        if data.preset_id not in open_preset_ids():
            raise ValueError("preset_not_open")
        versions = self.versions()
        return GameMeta(game_id, PRESETS[data.preset_id], self.randbelow(SEED_LIMIT), DataSource.HUMAN,
                        versions.rule_version, versions.engine_version, versions.content_version,
                        player_pseudonym=player, session_id=None,
                        mudang_variant=data.mudang_variant, ending_choice=data.ending_choice)


def deployed_versions(rule_version_source: Callable[[], str], content_version: str):
    """배포 코드의 규칙·엔진 버전과 운영 활성 콘텐츠 버전을 묶는다. 콘텐츠 버전이 없으면 시작을 막는다."""
    if not content_version:
        raise StartUnavailable("content_version_not_configured")
    return StartVersions(rule_version_source(), ENGINE_VERSION, content_version)


def unfinished_open_games(game_ids, load_game):
    """진행 중인 일반 판(회차 없음) ID만 순서대로 고른다. 없는 판은 건너뛴다."""
    found = []
    for game_id in game_ids:
        try:
            state, _ = load_game(game_id)
        except KeyError:
            continue
        if state.meta.session_id is None and state.result is None:
            found.append(game_id)
    return found
