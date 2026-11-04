"""응용 계층 유스케이스의 입력·출력 DTO. API 계약의 기준이다(docs/contracts/api.md).

계층: 응용. engine(도메인·포트)만 import한다. Django를 import하지 않는다.
표현 계층(server/api)은 HTTP 요청을 이 입력 DTO로 바꾸고, 출력 DTO를 화면용 뷰 모델(viewmodels.py)
또는 JSON으로 바꾼다.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from engine.domain.model import (
    Command, EvidenceRef, GameResult, PlayerProgress, RejectCode, Role,
)
from engine.ports import CommandCandidate, ParseFailure, PlayerView, RenderStatus


# ---------------------------------------------------------------- 판 시작·조회
@dataclass
class StartGameInput:
    player_pseudonym: str
    preset_id: str
    session_id: str
    seed: Optional[int] = None          # None이면 서버가 정한다(기록됨)
    ending_choice: str = "exorcism"     # 열린 결말 중 선택(PlayerProgress.endings_unlocked)


@dataclass
class GameOutput:
    view: PlayerView
    pending_render_jobs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 명령 실행
@dataclass
class SubmitCommandInput:
    game_id: str
    command_id: str
    expected_state_version: int
    command: Command
    intent_attempt_id: Optional[str] = None


@dataclass
class CommandOutput:
    accepted: bool
    current_state_version: int
    view: Optional[PlayerView]                 # 수락 시 새 화면, 거부 시 현재 화면
    reject: Optional[RejectCode] = None
    reject_detail: str = ""
    replayed: bool = False                     # 같은 command_id 재요청으로 저장된 응답을 돌려줌
    pending_render_jobs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 자유 입력
class UtteranceReaction(str, enum.Enum):
    CONFIRM = "confirm"                # 카드 그대로 실행
    EDIT = "edit"                      # 카드 수정 후 실행
    RETRY = "retry"                    # 다시 입력(행동 소비 없음)
    CANCEL = "cancel"                  # 취소(행동 소비 없음)
    SWITCH_TO_BUTTONS = "switch_to_buttons"
    NONE_OF_THESE = "none_of_these"    # "해당 없음"


@dataclass
class ParseUtteranceInput:
    game_id: str
    expected_state_version: int
    text: str
    selected_npc: Optional[str] = None
    selected_cards: list[EvidenceRef] = field(default_factory=list)
    last_addressee: Optional[str] = None


@dataclass
class ParseUtteranceOutput:
    attempt_id: str
    state_version: int
    cards: list[CommandCandidate]              # 다중 의도면 여러 장. 플레이어가 하나만 고른다
    failure: Optional[ParseFailure]
    needs_confirmation: bool
    suggestions: list[str] = field(default_factory=list)   # 실패 시 제안할 행동 종류(CommandKind 값)


@dataclass
class ResolveUtteranceInput:
    game_id: str
    attempt_id: str
    reaction: UtteranceReaction
    command_id: Optional[str] = None           # CONFIRM·EDIT일 때 필수
    expected_state_version: Optional[int] = None
    chosen_index: Optional[int] = None         # CONFIRM일 때 고른 카드
    edited_command: Optional[Command] = None   # EDIT일 때
    retry_text: Optional[str] = None           # RETRY일 때 새 문장. 새 attempt_id를 발급하고 이전 시도와 연결


# ---------------------------------------------------------------- LLM 답변 폴링
@dataclass
class PollRenderInput:
    game_id: str
    job_id: str


@dataclass
class RenderOutput:
    job_id: str
    speech_id: str
    status: RenderStatus
    text: str                    # DONE이면 생성문, 그 외에는 폴백 문장(같은 정보량)
    final: bool                  # True면 폴링 중단(DONE·TIMEOUT·FAILED·CANCELLED·DISCARDED)


# ---------------------------------------------------------------- 수첩·설문·복기·진척
@dataclass
class SaveNotebookInput:
    game_id: str
    entries: dict[str, int]                   # npc_id -> 0~100
    vote_confidence: Optional[int] = None     # 투표 직전 확신도 0~100


@dataclass
class SubmitSurveyInput:
    game_id: str
    fun: int                     # 1~5
    unfair: int                  # 1~5
    npc_plausible: int           # 1~5
    comment: str = ""


@dataclass
class ReviewItem:
    speech_id: str
    actor: str
    reason_codes: list[str]
    evidence: list[EvidenceRef]


@dataclass
class ReviewOutput:
    game_id: str
    result: GameResult
    roles: dict[str, Role]                    # 판 종료 후에만 공개
    secrets: dict[str, str]
    timeline: list[ReviewItem]
    explanation_job_id: Optional[str] = None  # LLM 복기 해설(확장)
    clues_gained: list[str] = field(default_factory=list)


@dataclass
class ProgressOutput:
    progress: PlayerProgress
