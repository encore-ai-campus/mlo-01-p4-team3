"""도메인 계약: 게임 상태, 주장, 명령, 기록, NPC 상태, 관측, 진실, 판 밖 상태.

계층: 도메인. 표준 라이브러리만 사용하고 다른 계층을 import하지 않는다.
상세 설명은 docs/contracts/domain.md. 규칙 구현은 A가 engine/domain/ 아래에 둔다.
여기 있는 함수(check_end, resolve_vote, ...)는 계약의 기준 구현이며 계약 테스트가 검증한다.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

PLAYER_ID = "mudang"  # 플레이어(무당)의 참가자 ID. 주민 NPC ID와 겹치지 않는다.
CONTRACT_VERSION = "contract-0.2"


# ---------------------------------------------------------------- 열거형
class Faction(str, enum.Enum):
    VILLAGE = "village"
    CHANGGWI = "changgwi"


class Role(str, enum.Enum):
    MUDANG = "mudang"          # 플레이어 고정
    VILLAGER = "villager"      # 주민(비밀 유무는 별도 필드)
    POSSESSED = "possessed"    # 씐 자


class Place(str, enum.Enum):
    WELL = "well"              # 우물가
    SHRINE = "shrine"          # 서낭당
    TAVERN = "tavern"          # 주막
    VILLAGE_GATE = "gate"      # 마을 어귀(산길 입구)
    HOME = "home"              # 각자 집(공공장소 아님)


PUBLIC_PLACES = (Place.WELL, Place.SHRINE, Place.TAVERN, Place.VILLAGE_GATE)


class Slot(str, enum.Enum):
    EVENING = "evening"        # 초저녁
    MIDNIGHT = "midnight"      # 한밤


class Phase(str, enum.Enum):
    MORNING = "morning"            # 실종 발표 + 주민 진술(자동)
    ROUND1 = "round1"
    INTERROGATION = "interrogation"
    ROUND2 = "round2"
    VOTE = "vote"
    RUNOFF = "runoff"
    NIGHT = "night"
    ENDED = "ended"


class ClaimType(str, enum.Enum):
    WHEREABOUTS = "whereabouts"        # 자기 행적
    SIGHTING = "sighting"              # 누구를 봤다
    NON_SIGHTING = "non_sighting"      # 아무도/누구를 못 봤다
    DIVINATION_REVEAL = "divination_reveal"  # 무당의 점괘 공개


class Polarity(str, enum.Enum):
    AFFIRM = "affirm"   # 있었다·봤다
    DENY = "deny"       # 없었다·못 봤다


class DivinationResult(str, enum.Enum):
    CLEAR = "clear"     # 맑음
    MURKY = "murky"     # 탁함


class CommandKind(str, enum.Enum):
    SUSPECT = "suspect"
    DEFEND = "defend"
    POINT_CONTRADICTION = "point_contradiction"
    EXPLAIN = "explain"
    SILENCE = "silence"
    REVEAL_DIVINATION = "reveal_divination"   # 무당 전용
    INTERROGATE = "interrogate"               # 무당 전용
    VOTE = "vote"
    DIVINE = "divine"                         # 무당의 밤 점
    NIGHT_PLAN = "night_plan"                 # 씐 자(들)의 공동 밤 명령. NPC 전용
    ADVANCE = "advance"                       # 자동 단계 진행 요청(아침 → 1라운드 등)


class InterrogationKind(str, enum.Enum):
    WHEREABOUTS = "whereabouts"
    CONTRADICTION = "contradiction"
    SUSPICION_REASON = "suspicion_reason"


class ReactionIntent(str, enum.Enum):
    EXPLAIN = "explain"
    EVADE = "evade"
    COUNTER = "counter"
    CONFESS = "confess"


class EvidenceType(str, enum.Enum):
    CLAIM = "claim"
    CONTRADICTION = "contradiction"   # record_id = "claimA|claimB"
    TELL = "tell"                     # 기색. record_id = 주장 ID
    DIVINATION = "divination"         # record_id = 점괘 공개 주장 ID
    VOTE = "vote"                     # record_id = 투표 기록 ID
    MAJORITY = "majority"             # 다수 의견. record_id = 빈 문자열 허용


class EvidenceGroup(str, enum.Enum):
    """모델 기여도와 성향 가중치의 공통 묶음(9.2)."""
    CONTRADICTION = "contradiction"
    WITNESS = "witness"
    STATEMENT = "statement"
    DIVINATION = "divination"
    TELL = "tell"
    INTERROGATION = "interrogation"
    SOCIAL = "social"
    PROGRESS = "progress"


class PropositionKind(str, enum.Enum):
    WAS_AT = "was_at"                # X는 밤n 시간대s에 장소p에 있었다
    NOT_EXECUTOR = "not_executor"    # X는 밤n의 홀림 실행자가 아니다
    NOT_POSSESSED = "not_possessed"  # X는 씐 자가 아니다(창귀 1명 판, 또는 감금 공개)
    IS_POSSESSED = "is_possessed"    # 감금 공개로 확인


class EvidenceSource(str, enum.Enum):
    OWN_WITNESS = "own_witness"
    OTHERS_CLAIM = "others_claim"
    MUTUAL_VOUCH = "mutual_vouch"
    LOCK_REVEAL = "lock_reveal"
    SELF = "self"


class EndReason(str, enum.Enum):
    ALL_POSSESSED_LOCKED = "all_possessed_locked"   # 1. G = 0
    MUDANG_LOCKED = "mudang_locked"                 # 2. 무당 감금
    POSSESSED_PARITY = "possessed_parity"           # 3. G >= V + M


class DataSource(str, enum.Enum):
    HUMAN = "human"
    BOT = "bot"
    SYNTHETIC = "synthetic"      # tools/gen_synthetic.py. 학습·분석 결론에 쓰지 않는다
    SCRIPTED = "scripted"        # 손으로 만든 픽스처·시나리오


class LoadTestTag(str, enum.Enum):
    NONE = "none"
    LOAD_TEST = "load_test"


# ---------------------------------------------------------------- 프리셋
@dataclass(frozen=True)
class Preset:
    preset_id: str
    n_active: int        # 1일째 활동 인원(무당 포함)
    n_possessed: int     # K
    n_secret: int        # 비밀 있는 주민 수(고정)
    status: str = "candidate"   # candidate | validated | test_default

    def __post_init__(self) -> None:
        if self.n_active < 5:
            raise ValueError("n_active는 5 이상")
        if self.n_possessed not in (1, 2):
            raise ValueError("n_possessed는 1 또는 2")
        if self.n_possessed == 2 and self.n_active not in (9, 11):
            raise ValueError("창귀 2명은 9·11인 판만")
        villagers = self.n_active - 1 - self.n_possessed
        if not 0 <= self.n_secret <= villagers:
            raise ValueError("n_secret은 0~마을 주민 NPC 수")

    @property
    def n_resident_pool_needed(self) -> int:
        """무당 외 등장 주민 수 = (인원 - 1) + 0일째 실종자 1 = 인원."""
        return self.n_active


PRESETS: dict[str, Preset] = {
    p.preset_id: p
    for p in (
        Preset("short5", 5, 1, 1),
        Preset("base6", 6, 1, 2, status="test_default"),
        Preset("base7", 7, 1, 2),
        Preset("large8", 8, 1, 3),
        Preset("duo9", 9, 2, 3),
        Preset("duo11", 11, 2, 4),
    )
}


# ---------------------------------------------------------------- 공개 기록(②)
@dataclass
class Claim:
    claim_id: str
    speaker: str
    claim_type: ClaimType
    subject: str                 # 누구에 대한 주장인가(자기 행적이면 speaker)
    night: int                   # 0 = 1일째 이전 밤
    slot: Optional[Slot] = None  # 점괘 공개면 None
    place: Optional[Place] = None
    polarity: Polarity = Polarity.AFFIRM
    seen: list[str] = field(default_factory=list)   # 목격 대상
    corrects: Optional[str] = None                   # 정정·철회 대상 주장 ID
    divination_result: Optional[DivinationResult] = None  # 점괘 공개 시 공개한 결과
    day: int = 1                                     # 말한 날


@dataclass
class EvidenceRef:
    type: EvidenceType
    record_id: str


@dataclass
class Speech:
    speech_id: str
    day: int
    phase: Phase
    speaker: str
    kind: CommandKind
    target: Optional[str] = None
    addressee: Optional[str] = None
    evidence: list[EvidenceRef] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)  # 이 발언이 만든 주장
    reaction: Optional[ReactionIntent] = None           # 캐묻기 답변이면 엔진이 정한 의도
    fact_sentence: Optional[str] = None                  # 엔진이 완성한 사실 문장
    line_text: Optional[str] = None                      # 표시 문장(템플릿 또는 LLM 표현)
    render_job_id: Optional[str] = None


@dataclass
class VoteRecord:
    vote_id: str
    day: int
    round: str          # "main" | "runoff"
    voter: str
    target: str


@dataclass
class LockRecord:
    day: int
    locked: Optional[str]         # None = 무감금
    revealed_possessed: Optional[bool] = None   # 금줄 반응(감금 공개)


@dataclass
class DisappearanceRecord:
    """아침 실종 발표(공개 기록). 피해 장소와 시간대도 공개한다(11/4 PM 결정 P-02).

    화면 문구 예: "실종자의 짚신이 우물가에서 발견됐다". place=HOME이면 **피해자의 집**이다.
    0일째 사건도 같은 방식으로 공개한다. 모든 주민과 플레이어가 같은 내용을 안다.
    """
    night: int
    victim: str
    place: Place                  # 피해 장소(5곳 중 하나. HOME = 피해자의 집)
    slot: Slot = Slot.MIDNIGHT    # 피해 시간대(홀림은 한밤)


@dataclass
class ConfessionRecord:
    day: int
    npc_id: str
    secret_id: str
    corrected_claims: list[str] = field(default_factory=list)


@dataclass
class TellRecord:
    """기색 판정(공개). 사실 주장이 거짓일 때만 판정하며, 주장 ID당 한 번 정해지면 바뀌지 않는다."""
    claim_id: str
    day: int                      # 판정이 공개된 날(주장을 말한 직후)
    shown: bool                   # True = 기색이 보였다


@dataclass
class SpeechGroup:
    """회의 발언 표시 단위(11인 판 축약 규칙, domain.md). 원본 Speech는 공개 기록에 모두 남는다."""
    speech_ids: list[str]
    summary_text: str = ""        # 비어 있으면 원문 그대로 표시, 있으면 묶음 요약 한 줄


@dataclass
class PublicRecord:
    claims: list[Claim] = field(default_factory=list)
    speeches: list[Speech] = field(default_factory=list)
    votes: list[VoteRecord] = field(default_factory=list)
    locks: list[LockRecord] = field(default_factory=list)
    disappearances: list[DisappearanceRecord] = field(default_factory=list)
    confessions: list[ConfessionRecord] = field(default_factory=list)
    tells: list[TellRecord] = field(default_factory=list)


# ---------------------------------------------------------------- 명령
@dataclass
class DivinationPayload:
    target: str
    night: int
    result: DivinationResult      # 공개하는 결과(거짓 가능)


@dataclass
class NightPlan:
    """씐 자 공동 밤 명령. 창귀 2명이면 둘이 합쳐 하나."""
    executor: str
    victim: str
    agreed_alibis: dict[str, str] = field(default_factory=dict)  # npc_id -> Place 값


@dataclass
class Command:
    kind: CommandKind
    actor: str
    target: Optional[str] = None
    addressee: Optional[str] = None
    evidence: list[EvidenceRef] = field(default_factory=list)
    interrogation_kind: Optional[InterrogationKind] = None
    divination: Optional[DivinationPayload] = None
    night_plan: Optional[NightPlan] = None
    night: Optional[int] = None
    slot: Optional[Slot] = None


@dataclass
class CommandEnvelope:
    """API·응용 계층이 엔진에 넘기는 단위."""
    game_id: str
    command_id: str                 # 클라이언트가 만든 고유 ID(멱등성 키)
    expected_state_version: int
    command: Command
    intent_attempt_id: Optional[str] = None   # 자유 입력에서 왔으면 해석 시도 ID


# 행동별 필수 슬롯(intent.md와 동일). 누락 시 엔진은 MISSING_SLOT으로 거부하고 행동을 소비하지 않는다.
REQUIRED_SLOTS: dict[CommandKind, tuple[str, ...]] = {
    CommandKind.SUSPECT: ("target",),
    CommandKind.DEFEND: ("target",),
    CommandKind.POINT_CONTRADICTION: ("evidence",),  # CONTRADICTION 1개 또는 CLAIM 2개
    CommandKind.EXPLAIN: (),
    CommandKind.SILENCE: (),
    CommandKind.REVEAL_DIVINATION: ("divination",),
    CommandKind.INTERROGATE: ("addressee", "interrogation_kind"),
    CommandKind.VOTE: ("target",),
    CommandKind.DIVINE: ("target",),
    CommandKind.NIGHT_PLAN: ("night_plan",),
    CommandKind.ADVANCE: (),
}

INTERROGATION_REQUIRED: dict[InterrogationKind, tuple[str, ...]] = {
    InterrogationKind.WHEREABOUTS: ("night", "slot"),
    InterrogationKind.CONTRADICTION: ("evidence",),
    InterrogationKind.SUSPICION_REASON: (),
}


def missing_slots(cmd: Command) -> list[str]:
    missing = [s for s in REQUIRED_SLOTS[cmd.kind] if not getattr(cmd, s)]
    if cmd.kind is CommandKind.INTERROGATE and cmd.interrogation_kind:
        missing += [s for s in INTERROGATION_REQUIRED[cmd.interrogation_kind]
                    if getattr(cmd, s) is None or getattr(cmd, s) == []]
    return missing


# ---------------------------------------------------------------- 단계별 합법 명령(domain.md 상태 전이 표)
@dataclass(frozen=True)
class PhaseRule:
    player_kinds: tuple[CommandKind, ...]
    npc_kinds: tuple[CommandKind, ...]


_TALK = (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.POINT_CONTRADICTION,
         CommandKind.EXPLAIN, CommandKind.SILENCE)

PHASE_RULES: dict[Phase, PhaseRule] = {
    Phase.MORNING: PhaseRule((CommandKind.ADVANCE,), ()),
    Phase.ROUND1: PhaseRule(_TALK + (CommandKind.REVEAL_DIVINATION,), _TALK),
    Phase.INTERROGATION: PhaseRule((CommandKind.INTERROGATE, CommandKind.ADVANCE), ()),
    Phase.ROUND2: PhaseRule(_TALK + (CommandKind.REVEAL_DIVINATION,), _TALK),
    Phase.VOTE: PhaseRule((CommandKind.VOTE,), (CommandKind.VOTE,)),
    Phase.RUNOFF: PhaseRule((CommandKind.VOTE,), (CommandKind.VOTE,)),
    Phase.NIGHT: PhaseRule((CommandKind.DIVINE,), (CommandKind.NIGHT_PLAN,)),
    Phase.ENDED: PhaseRule((), ()),
}

LIMITS = {
    "interrogations_per_day": 2,
    "divination_reveals_per_day": 1,
    "talk_actions_per_round": 1,
}


def is_legal_kind(phase: Phase, kind: CommandKind, is_player: bool) -> bool:
    rule = PHASE_RULES[phase]
    return kind in (rule.player_kinds if is_player else rule.npc_kinds)


# ---------------------------------------------------------------- 엔진 거부 코드
class RejectCode(str, enum.Enum):
    STATE_VERSION_CONFLICT = "state_version_conflict"
    ILLEGAL_PHASE = "illegal_phase"
    NOT_YOUR_TURN = "not_your_turn"
    MISSING_SLOT = "missing_slot"
    ILLEGAL_TARGET = "illegal_target"
    LIMIT_EXCEEDED = "limit_exceeded"
    GAME_ENDED = "game_ended"
    UNKNOWN_EVIDENCE = "unknown_evidence"


# 행동 소비 규칙: 거부된 명령은 어떤 횟수도 소비하지 않는다. 수락되어 상태가 바뀐 순간에만 소비한다.


# ---------------------------------------------------------------- 기준 규칙 함수(계약)
def check_end(g: int, v: int, mudang_active: bool, mudang_locked: bool) -> Optional[EndReason]:
    """종료 판정(4.4). 감금 직후와 밤 처리 직후 같은 함수로 호출한다.

    g = 활동 씐 자 수, v = 활동 마을 주민 NPC 수, mudang_active = 무당 활동 여부.
    """
    if g == 0:
        return EndReason.ALL_POSSESSED_LOCKED
    if mudang_locked:
        return EndReason.MUDANG_LOCKED
    if g >= v + (1 if mudang_active else 0):
        return EndReason.POSSESSED_PARITY
    return None


def night_runs(after_lock_end: Optional[EndReason]) -> bool:
    """감금 직후 종료되면 밤을 실행하지 않는다(마지막 씐 자 감금 포함)."""
    return after_lock_end is None


def winner_of(reason: EndReason) -> Faction:
    return Faction.VILLAGE if reason is EndReason.ALL_POSSESSED_LOCKED else Faction.CHANGGWI


@dataclass
class VoteOutcome:
    locked: Optional[str]
    runoff_candidates: list[str] = field(default_factory=list)  # 결선이 필요하면 후보
    counts: dict[str, int] = field(default_factory=dict)


def tally(votes: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for voter, target in votes.items():
        if voter == target:
            raise ValueError("자기 자신에게 투표 불가")
        counts[target] = counts.get(target, 0) + 1
    return counts


def _check_ballots(votes: dict[str, str], voters: Optional[list[str]], name: str) -> None:
    if voters is None:
        return
    if set(votes) != set(voters):
        raise ValueError(f"{name}: 유권자 불일치(누락 {sorted(set(voters) - set(votes))}, 추가 {sorted(set(votes) - set(voters))})")
    bad = [t for t in votes.values() if t not in voters]
    if bad:
        raise ValueError(f"{name}: 활동 인원이 아닌 대상 {bad}")


def resolve_vote(main: dict[str, str], runoff: Optional[dict[str, str]] = None,
                 voters: Optional[list[str]] = None) -> VoteOutcome:
    """동시 비공개 투표 결과. 동점이면 동점자만 후보로 결선, 결선도 동점이면 무감금.

    voters = 활동 인원 전체(무당 포함). 주면 전원이 정확히 한 표씩 냈는지 검사한다.
    엔진은 항상 voters를 넘긴다(본투표·결선 모두 활동 인원 전원 투표).
    """
    _check_ballots(main, voters, "본투표")
    _check_ballots(runoff or {}, voters if runoff is not None else None, "결선")
    counts = tally(main)
    top = _top(counts)
    if len(top) == 1:
        return VoteOutcome(top[0], counts=counts)
    if runoff is None:
        return VoteOutcome(None, runoff_candidates=sorted(top), counts=counts)
    bad = [t for t in runoff.values() if t not in top]
    if bad:
        raise ValueError(f"결선 후보가 아닌 대상: {bad}")
    r_counts = tally(runoff)
    r_top = _top(r_counts)
    return VoteOutcome(r_top[0] if len(r_top) == 1 else None, counts=r_counts)


def _top(counts: dict[str, int]) -> list[str]:
    if not counts:
        return []
    m = max(counts.values())
    return [k for k, c in counts.items() if c == m]


def divination_of(is_possessed: bool, has_unconfessed_secret: bool) -> DivinationResult:
    return DivinationResult.MURKY if is_possessed or has_unconfessed_secret else DivinationResult.CLEAR


def alibi_proposition_kind(n_possessed_total: int) -> PropositionKind:
    """피해 시각에 다른 공공장소에서 직접 목격된 사람에 대해 확정할 수 있는 명제(5.1).

    창귀 1명 판: 씐 자가 아님. 창귀 2명 판: 그날 밤 실행자가 아님까지만.
    """
    return PropositionKind.NOT_POSSESSED if n_possessed_total == 1 else PropositionKind.NOT_EXECUTOR


def crime_scene_of(record: "DisappearanceRecord") -> tuple[Place, Optional[str]]:
    """공개된 피해 장소. HOME이면 (HOME, 피해자 ID) — 피해자의 집이다."""
    return (record.place, record.victim if record.place is Place.HOME else None)


# ---------------------------------------------------------------- NPC 상태(③ 개인 지식, ④ 믿음)
@dataclass
class Sighting:
    night: int
    slot: Slot
    place: Place
    seen: list[str]


@dataclass
class Proposition:
    kind: PropositionKind
    subject: str
    source: EvidenceSource
    night: Optional[int] = None
    slot: Optional[Slot] = None
    place: Optional[Place] = None


def alibi_propositions(record: DisappearanceRecord, own_sightings: list["Sighting"],
                      n_possessed_total: int, observer: str) -> list[Proposition]:
    """공개된 피해 장소(record) + 관찰자 **자신의 목격**으로 확정 알리바이 명제를 만든다(5.1).

    - 피해 시각(record.night, record.slot)에 피해 장소가 아닌 공공장소에서 직접 목격한 사람 → 명제.
      창귀 1명 판: not_possessed, 2명 판: not_executor까지만(후보에서 빼지 않음).
    - 피해자 본인과 관찰자 자신은 대상에서 뺀다.
    - 다른 사람의 공개 목격 주장(공개 기록)은 **확정 명제가 아니다**. 같은 계산을 하되 출처를
      others_claim / mutual_vouch 근거로 믿음 갱신에만 쓴다(기획서 5.1: 확정 명제는 직접 목격만).
    엔진은 진실의 피해 장소를 넣지 않는다. 오직 공개 기록(record)만 쓴다.
    """
    kind = alibi_proposition_kind(n_possessed_total)
    out: list[Proposition] = []
    seen_subjects: set[str] = set()
    for s in own_sightings:
        if s.night != record.night or s.slot is not record.slot or s.place is record.place:
            continue
        if s.place not in PUBLIC_PLACES:  # 계약: "피해 장소가 아닌 **공공장소**"에서의 직접 목격만
            continue
        for p in s.seen:
            if p in (record.victim, observer) or p in seen_subjects:
                continue
            seen_subjects.add(p)
            out.append(Proposition(kind, p, EvidenceSource.OWN_WITNESS, record.night, record.slot, s.place))
    return out


@dataclass
class Reason:
    target: str
    evidence: EvidenceRef
    group: EvidenceGroup
    delta: float


@dataclass
class PrivateKnowledge:
    """③ 그 NPC에게 투영된 진실."""
    npc_id: str
    role: Role
    secret_id: Optional[str] = None
    whereabouts: dict[int, dict[Slot, Place]] = field(default_factory=dict)
    witnessed: list[Sighting] = field(default_factory=list)
    # 씐 자 전용
    partner_id: Optional[str] = None
    night_plans: dict[int, NightPlan] = field(default_factory=dict)
    my_false_claims: list[str] = field(default_factory=list)
    # 비밀 보유자 전용
    hidden_whereabouts: list[dict[str, str]] = field(default_factory=list)  # [{"night":..,"slot":..}]
    confession_threshold: Optional[float] = None


@dataclass
class Beliefs:
    """④ 그 NPC의 생각."""
    suspicion: dict[str, float] = field(default_factory=dict)   # 후보 -> 0~100
    trust_in_mudang: float = 50.0
    propositions: list[Proposition] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)
    repeat_keys: list[str] = field(default_factory=list)       # "발언자|대상|근거" 반복 방지 키


@dataclass
class NpcRuntime:
    """규칙별 필수 상태(5.1)."""
    pressure: float = 0.0
    confessed: bool = False
    confessed_day: Optional[int] = None
    explain_count: int = 0
    tells: dict[str, bool] = field(default_factory=dict)   # 주장 ID -> 기색 판정(1회 고정)


@dataclass
class NpcState:
    npc_id: str
    temperament_id: str
    private: PrivateKnowledge
    beliefs: Beliefs = field(default_factory=Beliefs)
    runtime: NpcRuntime = field(default_factory=NpcRuntime)


# ---------------------------------------------------------------- 플레이어 상태
@dataclass
class PlayerState:
    interrogations_left: int = 2
    reveals_left_today: int = 1
    talk_used_this_round: bool = False
    divinations: dict[int, dict[str, DivinationResult]] = field(default_factory=dict)  # 밤 -> {대상: 실제 결과}
    notebook: dict[str, int] = field(default_factory=dict)  # npc_id -> 0~100(자기 보고)


def check_player_limits(player: "PlayerState", kind: CommandKind) -> Optional["RejectCode"]:
    """플레이어 횟수 제한. 거부되면 아무것도 소비하지 않는다."""
    if kind is CommandKind.INTERROGATE and player.interrogations_left <= 0:
        return RejectCode.LIMIT_EXCEEDED
    if kind is CommandKind.REVEAL_DIVINATION and player.reveals_left_today <= 0:
        return RejectCode.LIMIT_EXCEEDED
    if kind in _TALK + (CommandKind.REVEAL_DIVINATION,) and player.talk_used_this_round:
        return RejectCode.LIMIT_EXCEEDED
    return None


# ---------------------------------------------------------------- 진실(①) — 엔진만, 판 문서 안 별도 필드
@dataclass
class ClaimTruth:
    claim_id: str
    is_true: bool
    deliberate_lie: bool          # 의도적 거짓 보고(기색 판정 대상)


@dataclass
class GameTruth:
    game_id: str
    roles: dict[str, Role]
    secrets: dict[str, str] = field(default_factory=dict)      # npc_id -> secret_id
    whereabouts: dict[int, dict[str, dict[Slot, Place]]] = field(default_factory=dict)  # 밤 -> npc -> slot -> place
    night_plans: dict[int, NightPlan] = field(default_factory=dict)
    claim_truths: dict[str, ClaimTruth] = field(default_factory=dict)


# ---------------------------------------------------------------- 판 메타와 상태
@dataclass
class GameMeta:
    game_id: str
    preset: Preset
    seed: int
    source: DataSource
    rule_version: str
    engine_version: str
    content_version: str
    npc_policy_versions: dict[str, str] = field(default_factory=dict)
    model_versions: dict[str, str] = field(default_factory=dict)   # 판 시작 시 고정(ml_lifecycle.md)
    player_pseudonym: Optional[str] = None
    session_id: Optional[str] = None
    canary: bool = False
    created_at: str = ""
    ending_choice: str = "exorcism"     # 판 시작 시 고른 결말("exorcism" | "haewon:<hypothesis_id>")
    mudang_variant: Optional[str] = None  # 주인공 초상 "f" | "m"(표시 전용, 엔진 규칙은 읽지 않는다). None = 선택 없음(녹화 판·시뮬레이터)


@dataclass
class GameResult:
    winner: Faction
    reason: EndReason
    day: int
    completed: bool = True      # False = 중단


@dataclass
class GameState:
    meta: GameMeta
    state_version: int
    day: int
    phase: Phase
    active: list[str]                       # 활동 인원(무당 포함)
    locked: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    public: PublicRecord = field(default_factory=PublicRecord)
    npcs: dict[str, NpcState] = field(default_factory=dict)
    player: PlayerState = field(default_factory=PlayerState)
    turn_order: list[str] = field(default_factory=list)    # 현재 라운드 발언 순서
    turn_index: int = 0
    pending_votes: dict[str, str] = field(default_factory=dict)  # 공개 전 비공개 투표
    runoff_candidates: list[str] = field(default_factory=list)
    result: Optional[GameResult] = None


# ---------------------------------------------------------------- 관측 스냅샷(ML 입력, 진실 미포함)
@dataclass
class ObservationSnapshot:
    snapshot_id: str
    game_id: str
    observer: str
    day: int
    phase: Phase
    state_version: int
    public: PublicRecord
    private: PrivateKnowledge
    beliefs: Beliefs
    legal_kinds: list[CommandKind]
    candidates: list[str]          # 활동 주민 중 무당·관찰자·고백자·감금 공개로 무고 확정된 사람 제외. ID 정렬이 계약
    n_possessed_remaining: int     # 감금 공개로 알려진 남은 창귀 수
    preset_id: str


# ---------------------------------------------------------------- 판 밖 상태
@dataclass
class ClueRecord:
    clue_id: str
    first_game_id: str
    first_day: int


@dataclass
class PlayerProgress:
    """판을 넘어 남는 것. 한 플레이어(가명 ID)당 하나."""
    player_pseudonym: str
    game_ids: list[str] = field(default_factory=list)
    clues: dict[str, ClueRecord] = field(default_factory=dict)  # clue_id -> 처음 얻은 기록(중복은 무시)
    confessed_secrets_seen: list[str] = field(default_factory=list)
    endings_unlocked: list[str] = field(default_factory=lambda: ["exorcism"])   # 퇴마는 처음부터 열림
    content_version: str = ""

    def add_clue(self, clue_id: str, game_id: str, day: int) -> bool:
        """처음이면 기록하고 True. 이미 있으면 False(중복 무시)."""
        if clue_id in self.clues:
            return False
        self.clues[clue_id] = ClueRecord(clue_id, game_id, day)
        return True
