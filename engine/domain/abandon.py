"""판 중단 규칙(11/10 사용자 결정). 순수 함수: 입력 상태를 바꾸지 않고 중단된 새 상태와 종료 이벤트를 돌려준다.

플레이어가 진행 중인 일반 판을 두고 새 판을 시작하면 기존 판을 지우지 않고 중단 처리한다.
중단 결과는 `GameResult(winner=None, reason=ABORTED, completed=False)`이고, 종료 이벤트 `game_ended`에
`completed=false`를 싣는다. 라벨 조인·학습 입력은 `completed=false` 판을 쓰지 않는다(events.md 3절).
진실(`truth_revealed`)은 발행하지 않는다. 중단 판은 라벨이 없으므로 진실을 내보낼 이유가 없다.
"""
from __future__ import annotations

import copy

from .events import DomainEvent, EventType
from .model import EndReason, GameResult, GameState, Phase


class AlreadyEnded(ValueError):
    """이미 끝난 판(완주·중단)은 다시 중단하지 않는다."""


def abandon(state: GameState) -> tuple[GameState, list[DomainEvent]]:
    """진행 중인 판을 중단한 새 상태(버전 +1)와 `game_ended`(completed=false) 이벤트를 만든다."""
    if state.result is not None:
        raise AlreadyEnded("game_already_ended")
    st = copy.deepcopy(state)
    st.state_version = state.state_version + 1
    st.phase = Phase.ENDED
    st.turn_order, st.turn_index, st.pending_votes, st.runoff_candidates = [], 0, {}, []
    st.result = GameResult(None, EndReason.ABORTED, st.day, completed=False)
    payload = {"winner": None, "reason": EndReason.ABORTED.value, "completed": False}
    return st, [DomainEvent(EventType.GAME_ENDED, st.day, Phase.ENDED, payload)]
