"""이벤트 계약. 엔진은 상태 전이 결과로 DomainEvent 목록을 반환하고,
응용 계층이 판 메타를 붙여 EventEnvelope로 만든 뒤 상태와 함께 아웃박스에 원자 저장한다.

계층: 도메인(순수 데이터). 상세: docs/contracts/events.md
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional

from .model import DataSource, Phase


class EventType(str, enum.Enum):
    GAME_STARTED = "game_started"
    MORNING_ANNOUNCED = "morning_announced"
    CLAIM_MADE = "claim_made"
    SPEECH_MADE = "speech_made"
    INTERROGATION_ANSWERED = "interrogation_answered"
    CONFESSION = "confession"
    VOTES_REVEALED = "votes_revealed"
    LOCK_RESOLVED = "lock_resolved"
    NIGHT_RESOLVED = "night_resolved"
    DIVINATION_DONE = "divination_done"       # 무당 개인 정보. 플레이어 화면과 학습에만
    GAME_ENDED = "game_ended"
    DECISION = "decision"                     # NPC·봇·플레이어의 행동 결정(관측 스냅샷 참조)
    OBSERVATION = "observation"               # 관측 스냅샷 본문
    INTENT_ATTEMPT = "intent_attempt"
    RENDER_JOB = "render_job"
    NOTEBOOK_UPDATED = "notebook_updated"
    SURVEY_SUBMITTED = "survey_submitted"
    TRUTH_REVEALED = "truth_revealed"         # 판 종료 후 진실. 별도 토픽
    COMMAND_REJECTED = "command_rejected"


# 토픽 라우팅(events.md 표와 동일)
TOPIC_OF: dict[EventType, str] = {
    EventType.OBSERVATION: "paldo.observations",
    EventType.INTENT_ATTEMPT: "paldo.intent",
    EventType.RENDER_JOB: "paldo.llm",
    EventType.NOTEBOOK_UPDATED: "paldo.player",
    EventType.SURVEY_SUBMITTED: "paldo.player",
    EventType.TRUTH_REVEALED: "paldo.truth",
}
DEFAULT_TOPIC = "paldo.game"


def topic_of(t: EventType) -> str:
    return TOPIC_OF.get(t, DEFAULT_TOPIC)


@dataclass
class DomainEvent:
    """엔진이 반환하는 이벤트. 판 메타·ID는 응용 계층이 붙인다."""
    event_type: EventType
    day: int
    phase: Phase
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventEnvelope:
    event_id: str                 # 전역 고유. 소비 단계 중복 제거 키
    game_id: str
    seq: int                      # 판 내 순번(0부터, 빈틈 없음)
    state_version: int            # 이 이벤트를 만든 전이 후의 상태 버전
    event_type: str               # EventType 값. 소비자는 모르는 타입을 건너뛴다
    topic: str
    occurred_at: str              # ISO 8601 UTC
    day: int
    phase: str
    source: DataSource
    contract_version: str
    preset_id: str
    seed: int
    rule_version: str
    engine_version: str
    content_version: str
    npc_policy_versions: dict[str, str]
    model_versions: dict[str, str]
    payload: dict[str, Any]
    player_pseudonym: Optional[str] = None
    session_id: Optional[str] = None
    command_id: Optional[str] = None
    load_test: bool = False


@dataclass
class OutboxEntry:
    """판 문서 안 outbox 배열의 원소. 상태와 같은 단일 문서 쓰기로 저장된다."""
    envelope: EventEnvelope
    published: bool = False
    attempts: int = 0
