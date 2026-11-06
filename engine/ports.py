"""엔진과 바깥 계층 사이의 계약(포트). typing.Protocol로 정의한다.

계층: 도메인 경계. 구현은 인프라(server/infra, intent/, ml/, llm/)가 하고,
가짜 구현은 engine/testing/fakes.py에 있다. 상세: docs/contracts/ports.md
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .domain.content import ContentBundle
from .domain.events import DomainEvent, EventEnvelope
from .domain.model import (
    Command, CommandEnvelope, CommandKind, EvidenceGroup, EvidenceRef, GameMeta, GameState,
    GameTruth, ObservationSnapshot, Phase, PlayerProgress, PlayerState, PublicRecord,
    ReactionIntent, RejectCode, GameResult, SpeechGroup,
)


# ================================================================ 의심 모델(D 구현)
@dataclass
class SuspicionEstimate:
    observer: str
    candidates: list[str]                       # 스냅샷의 candidates와 같은 순서
    probs: dict[str, float]                     # 0~1, 합 = n_possessed_remaining
    contributions: dict[str, dict[EvidenceGroup, float]]  # 후보 -> 묶음별 로그 오즈 기여
    model_version: str
    feature_version: str
    pair_probs: Optional[dict[str, float]] = None   # 창귀 2명: "a|b"(정렬) -> 확률, 합 1


class ModelUnavailable(RuntimeError):
    """모델 로드 실패·시간 초과. 엔진은 규칙 v0 의심도로 폴백한다."""


class FeatureVersionMismatch(RuntimeError):
    """모델이 기대하는 특징 버전과 엔진 스냅샷 버전이 다름. 폴백 + 경보."""


@runtime_checkable
class SuspicionModel(Protocol):
    feature_version: str

    def estimate(self, obs: ObservationSnapshot) -> SuspicionEstimate: ...


# ================================================================ 의도 해석기(D 구현)
class UtteranceForm(str, enum.Enum):
    ASSERTION = "assertion"
    QUESTION = "question"
    QUOTE = "quote"
    REQUEST = "request"


class ParseFailure(str, enum.Enum):
    EMPTY_INPUT = "empty_input"
    UNSUPPORTED_INTENT = "unsupported_intent"
    MISSING_TARGET = "missing_target"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    UNPARSEABLE = "unparseable"


@dataclass
class ParseContext:
    game_id: str
    state_version: int
    phase: Phase
    public: PublicRecord
    aliases: dict[str, list[str]]           # npc_id -> 별칭(활동 주민만)
    selected_npc: Optional[str] = None      # 화면에서 선택된 인물(대명사·생략 해소 1순위)
    selected_cards: list[EvidenceRef] = field(default_factory=list)
    last_addressee: Optional[str] = None    # 직전 대화 상대(2순위)


@dataclass
class CommandCandidate:
    command: Command
    intent_score: float                     # 보정된 확률
    form: UtteranceForm
    missing_slots: list[str] = field(default_factory=list)
    slot_scores: dict[str, float] = field(default_factory=dict)
    evidence_alternatives: list[EvidenceRef] = field(default_factory=list)


@dataclass
class ParseResult:
    candidates: list[CommandCandidate]      # 점수 내림차순. 다중 의도면 여러 개
    failure: Optional[ParseFailure]
    needs_confirmation: bool                # MVP 초기에는 항상 True
    versions: dict[str, str]                # model, tokenizer, alias_dict, matcher, threshold


@runtime_checkable
class IntentParser(Protocol):
    def parse(self, text: str, ctx: ParseContext) -> ParseResult: ...


# ================================================================ 대사 표현기(LLM, E 구현) — 비동기
class RenderStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    TIMEOUT = "timeout"          # 마감 초과 → 폴백 표시, 생성 취소
    CANCELLED = "cancelled"
    DISCARDED = "discarded"      # 폴백 확정 뒤 도착한 늦은 결과


@dataclass
class RenderRequest:
    job_id: str
    game_id: str
    state_version: int           # 요청을 만든 전이 후의 버전
    speech_id: str               # 결과가 붙을 발언
    npc_id: str
    persona: str
    reaction: ReactionIntent
    fact_sentence: str           # 엔진이 완성. LLM은 사실 요소 수정·추가 금지, 끝 어미만 말투 허용(ports.md 4절)
    allowed_names: list[str]
    fallback_text: str           # 템플릿 표현. 생성 결과와 같은 정보량
    priority: int = 0            # 0 = 캐묻기(최우선), 1 = 복기, 2 = 운영 분석
    deadline_ms: int = 15000     # 실측 후 조정


@dataclass
class RenderResult:
    job_id: str
    status: RenderStatus
    text: Optional[str]          # DONE이면 생성문, 아니면 None(화면은 fallback_text)
    latency_ms: Optional[int] = None
    queue_ms: Optional[int] = None
    failure_reason: Optional[str] = None
    model_version: str = ""
    prompt_version: str = ""
    quantization: str = ""       # 양자화 종류(예: Q4_K_M). render_job 이벤트용(11/4 PM 결정 #18)
    fallback: bool = False       # 화면에 폴백 문장을 썼는지(DONE이 아니면 True)


@runtime_checkable
class LineRenderer(Protocol):
    def submit(self, req: RenderRequest) -> None: ...
    def poll(self, job_id: str) -> RenderResult: ...
    def cancel(self, job_id: str) -> None: ...


# ================================================================ 저장소(C 구현)
class VersionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int):
        super().__init__(f"expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


@dataclass
class StoredCommand:
    command_id: str
    response_json: str           # 같은 command_id 재요청에 그대로 돌려줄 응답


@runtime_checkable
class GameRepository(Protocol):
    def create(self, state: GameState, truth: GameTruth, outbox: list[EventEnvelope]) -> None: ...

    def load(self, game_id: str) -> tuple[GameState, GameTruth]: ...

    def find_command(self, game_id: str, command_id: str) -> Optional[StoredCommand]: ...

    def commit(self, game_id: str, expected_version: int, state: GameState, truth: GameTruth,
               outbox: list[EventEnvelope], command: StoredCommand) -> None:
        """상태, 진실, 아웃박스, 명령 기록을 판 문서 하나에 원자적으로 쓴다.
        저장된 버전 != expected_version 이면 VersionConflict. 새 상태의 버전은 expected+1.
        outbox의 seq·event_id는 저장소가 판 문서의 next_seq로 다시 매긴다(호출자 값은 무시)."""
        ...

    def append_events(self, game_id: str, outbox: list[EventEnvelope], dedup_key: str) -> bool:
        """상태를 바꾸지 않는 이벤트(해석 시도·카드 반응·수첩·설문·렌더 결과)를 아웃박스에 원자적으로 추가한다.
        상태 버전은 올리지 않는다. 같은 dedup_key가 이미 있으면 False(멱등). seq는 commit과 같은 카운터."""
        ...


@runtime_checkable
class ProgressRepository(Protocol):
    """판 밖 상태. 읽고-고치고-쓰기 대신 원자적·멱등적 연산만 둔다(동시 판의 덮어쓰기 방지).
    source_event_id(게임 이벤트 ID)가 이미 반영됐으면 아무것도 하지 않는다. 아웃박스 재처리로 복구할 수 있다."""
    def load(self, player_pseudonym: str) -> PlayerProgress: ...
    def add_clue(self, player_pseudonym: str, clue_id: str, game_id: str, day: int, source_event_id: str) -> bool: ...
    def unlock_ending(self, player_pseudonym: str, ending: str, source_event_id: str) -> bool: ...
    def add_game(self, player_pseudonym: str, game_id: str) -> None: ...


@runtime_checkable
class ContentSource(Protocol):
    def load(self, content_version: str) -> ContentBundle: ...


@runtime_checkable
class EventSink(Protocol):
    """시뮬레이터·배치 전용 직접 발행 경로. 웹 게임 경로는 아웃박스를 쓰고 이 포트를 쓰지 않는다."""
    def append(self, envelopes: list[EventEnvelope]) -> None: ...


@runtime_checkable
class ModelSelector(Protocol):
    """판 시작 시 쓸 모델 버전을 정한다(카나리아 배정 포함). 판 진행 중에는 바꾸지 않는다."""
    def select(self, game_id: str, player_pseudonym: Optional[str]) -> tuple[dict[str, str], bool]: ...
    def suspicion_model(self, version: str) -> SuspicionModel: ...
    def intent_parser(self, version: str) -> "IntentParser": ...


# ================================================================ 엔진 파사드(A 구현, B가 호출)
@dataclass
class PlayerView:
    """플레이어에게 허용된 정보만. 진실·NPC 믿음·다른 NPC의 개인 지식은 없다."""
    game_id: str
    state_version: int
    day: int
    phase: Phase
    active: list[str]
    locked: list[str]
    missing: list[str]
    public: PublicRecord
    player: PlayerState
    legal_kinds: list[CommandKind]
    awaiting: Optional[str]          # 지금 행동할 차례인 참가자(플레이어면 "mudang")
    runoff_candidates: list[str]
    result: Optional[GameResult]
    names: dict[str, str]            # npc_id -> 표시 이름
    pending_render_jobs: list[str] = field(default_factory=list)
    speech_groups: list[SpeechGroup] = field(default_factory=list)   # 비어 있으면 축약 없음(전부 표시)


@dataclass
class TransitionResult:
    accepted: bool
    state: Optional[GameState]
    truth: Optional[GameTruth]
    events: list[DomainEvent]
    render_requests: list[RenderRequest] = field(default_factory=list)
    reject: Optional[RejectCode] = None
    reject_detail: str = ""


@dataclass
class EngineDeps:
    content: ContentBundle
    suspicion: SuspicionModel


class GameEngine(Protocol):
    """엔진 진입점. apply는 플레이어 명령 1개를 처리한 뒤 다음 플레이어 결정 지점까지 NPC 행동을 자동 진행한다.
    순수 함수처럼 동작한다: 입력 상태를 바꾸지 않고 새 상태를 반환하며, 같은 입력·시드면 같은 결과."""

    def new_game(self, meta: GameMeta, deps: EngineDeps) -> TransitionResult: ...
    def apply(self, state: GameState, truth: GameTruth, env: CommandEnvelope, deps: EngineDeps) -> TransitionResult: ...
    def player_view(self, state: GameState, content: ContentBundle) -> PlayerView: ...
    def observe(self, state: GameState, truth: GameTruth, npc_id: str) -> ObservationSnapshot: ...
