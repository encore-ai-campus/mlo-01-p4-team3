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
    """판 시작 입력. `session_id`가 없으면 일반 판, 있으면 테스트 회차 판(api.md 2-3절)."""

    player_pseudonym: str  # 서버가 로그인 계정의 가명 ID로 채운다. 요청 본문에 있으면 400(api.md 2-1절)
    preset_id: str
    session_id: Optional[str]           # 테스트 회차 ID. 생략·null·빈 문자열은 회차 없는 일반 판(E-08-14, 표현 계층이 None으로 맞춘다)
    mudang_variant: str                 # 주인공 초상 "f"(여) | "m"(남). 표시 전용, 규칙과 NPC 대사에 영향 없음(11/4 PM 결정)
    seed: Optional[int] = None          # 회차 판만. None이면 배정된 고정 시드. 일반 판은 서버가 랜덤으로 정하고 이 값을 받지 않는다
    ending_choice: str = "exorcism"     # 열린 결말 중 선택(PlayerProgress.endings_unlocked)
    start_token: Optional[str] = None   # 일반 판 시작 요청 UUID. 같은 값을 다시 보내면 같은 판을 돌려준다(중복 시작 방지, E-08-14)
    replace_open: bool = False          # 진행 중인 일반 판을 중단하고 새로 시작하는 데 동의(11/10 사용자 결정). 없으면 진행 판이 있을 때 409


@dataclass
class StartTutorialInput:
    """튜토리얼 판 시작 입력(`POST /tutorial`). 시드·프리셋·회차는 받지 않는다(서버 상수, engine/domain/tutorial.py)."""

    player_pseudonym: str               # 서버가 로그인 계정의 가명 ID로 채운다
    start_token: str                    # 시작 요청 UUID. 같은 값을 다시 보내면 같은 판
    mudang_variant: str = "f"           # 주인공 초상 "f" | "m"(표시 전용)


@dataclass
class GameOutput:
    """판 화면 응답: 플레이어 시점 상태와 아직 끝나지 않은 대사 생성 작업 ID."""
    view: PlayerView
    pending_render_jobs: list[str] = field(default_factory=list)
    mudang_variant: Optional[str] = None    # 판 메타 값. None(녹화 판·시뮬레이터)이면 화면은 기본 이미지
    scenario_id: Optional[str] = None       # 판 메타 값. 튜토리얼 판이면 "tutorial-v1", 일반·회차 판은 None


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
    """판 종료 설문의 응답 또는 전체 건너뛰기 입력."""

    game_id: str
    fun: Optional[int]           # 응답이면 1~5, 건너뛰면 null
    unfair: Optional[int]
    npc_plausible: Optional[int]
    comment: str = ""
    skipped: bool = False


@dataclass
class ReviewItem:
    """복기 항목 1개 = NPC 결정 1개(E-10-10 `review_trace`). 회의 말·캐묻기 답은 발언과 연결되고 투표·밤은 발언이 없다."""
    item_id: str                              # "d<seq>"
    actor: str
    day: int
    phase: str
    kind: str                                 # suspect|defend|point_contradiction|explain|silence|vote|night_plan|react
    reason_codes: list[str]
    evidence: list[EvidenceRef]
    phrase_id: str                            # 템플릿 해설 문구 ID rv_<대표 이유 코드>
    text: str = ""                            # 템플릿 해설 문장(문구가 없으면 빈 문자열)
    speech_id: Optional[str] = None
    target_npc: Optional[str] = None
    reaction: Optional[str] = None
    executor: Optional[str] = None
    victim: Optional[str] = None
    policy_version: str = ""
    vote_explain: Optional[list] = None       # 모델 추정 기여(선택). 지금은 늘 None


@dataclass
class ReviewOutput:
    """종료된 자기 판의 복기와 선택 날짜별 마을 승리 확률."""
    game_id: str
    result: GameResult
    roles: dict[str, Role]                    # 판 종료 후에만 공개
    secrets: dict[str, str]
    timeline: list[ReviewItem]
    explanation_job_id: Optional[str] = None  # LLM 복기 해설(확장)
    clues_gained: list[str] = field(default_factory=list)
    win_prob: Optional[list[dict]] = None  # D-10-03, 그래프는 D3 결정에 따라 숨김


@dataclass
class NarrationSentence:
    """복기 해설 한 문장과 인용한 복기 항목 ID."""
    cite: str                                 # 인용한 복기 항목 ID("d<seq>")
    text: str


@dataclass
class NarrationNpc:
    """NPC 한 명의 해설 문장 묶음. source는 "llm" 또는 "template"."""
    npc: str
    sentences: list[NarrationSentence]
    source: str                               # "llm" | "template"


@dataclass
class ReviewNarrationOutput:
    """복기 해설 LLM 요약 조회(E-10-01, `GET /games/{id}/review/narration`). final이면 다시 바뀌지 않는다."""
    status: str                               # off|queued|running|done|failed|timeout
    final: bool
    npcs: list[NarrationNpc] = field(default_factory=list)


@dataclass
class ProgressOutput:
    progress: PlayerProgress
