"""콘텐츠 계약과 배정 검증기 계약.

계층: 도메인. 콘텐츠 파일(JSON)은 E가 content/에 쓰고, 인프라의 ContentSource가 읽어 이 타입으로 넘긴다.
상세: docs/contracts/content.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import (
    CommandKind, EvidenceGroup, NightPlan, Place, Preset, ReactionIntent, Role, Slot,
)


@dataclass
class PlaceDef:
    place: Place
    name_ko: str
    public: bool


@dataclass
class Temperament:
    temperament_id: str
    name_ko: str
    group_weights: dict[EvidenceGroup, float]   # 모델 기여도에 곱하는 성향 가중치
    aggression: float          # 의심하기 선택 경향 0~1
    stubbornness: float        # 믿음 갱신 속도의 역수 0~1
    conformity: float          # 투표 동조 가중치 0~1
    confession_ease: float     # 고백 문턱값 보정 0~1


@dataclass
class Character:
    npc_id: str
    name_ko: str
    aliases: list[str]          # 별칭 사전(의도 해석기 인물 인식에 사용)
    temperament_id: str
    routine: dict[Slot, dict[Place, float]]   # 시간대별 장소 선호 가중치
    speech_style: str           # LLM 페르소나·템플릿 말투 요약


@dataclass
class Secret:
    secret_id: str
    title_ko: str
    eligible_npcs: list[str]
    slot: Slot                   # 숨기는 시간대. 비밀 사건은 0일째 밤에만 일어난다(그 밤·그 시간대만 거짓 진술)
    actual_place: Place          # 실제 행적
    false_place: Place           # 거짓 진술 장소
    confession_line: str
    confession_threshold: float  # 기본 문턱값(성향으로 보정)
    conflicts_with: list[str] = field(default_factory=list)
    grudge_clue_id: Optional[str] = None


@dataclass
class GrudgeClue:
    clue_id: str
    text_ko: str
    unlock: str      # 획득 조건 설명(예: "secret:gambling 고백")
    part: str        # identity | death | target (원한 가설의 세 부분)
    hypothesis_id: str = "h1"   # 같은 가설의 세 부분이 모두 모여야 그 가설의 한풀이 결말이 열린다


@dataclass
class TemplateLine:
    template_id: str
    kind: CommandKind
    temperament_id: Optional[str]       # None = 공용
    reaction: Optional[ReactionIntent]  # 캐묻기 답변용
    variants: list[str]                 # 자리표시자: {target} {place} {slot} {night} {addressee}


@dataclass
class ContentBundle:
    content_version: str
    places: list[PlaceDef]
    temperaments: list[Temperament]
    characters: list[Character]
    secrets: list[Secret]
    grudge_clues: list[GrudgeClue]
    templates: list[TemplateLine]
    # 확장 인물·비밀(E-08-02, 11/11 사용자 결정). 주민 풀이 핵심 인물(확장 인물을 뺀 나머지) 수 이하인 프리셋은
    # 핵심 인물·비밀에서만 뽑아 기존 판과 같은 배정을 낸다. 비어 있으면 전체가 핵심이다.
    extra_npcs: list[str] = field(default_factory=list)
    extra_secrets: list[str] = field(default_factory=list)


def draw_pool(preset: Preset, content: ContentBundle) -> tuple[list[Character], list[Secret]]:
    """이 프리셋의 배정에 쓸 인물·비밀. 핵심 인물만으로 주민 풀이 차면 확장 인물·비밀을 뺀다(목록 순서 유지)."""
    extra_n, extra_s = set(content.extra_npcs), set(content.extra_secrets)
    core = [c for c in content.characters if c.npc_id not in extra_n]
    if preset.n_resident_pool_needed <= len(core):
        return core, [s for s in content.secrets if s.secret_id not in extra_s]
    return list(content.characters), list(content.secrets)


# 프리셋별 필수 콘텐츠 수(domain.md 판 밖 상태). 주민 풀은 인원 이상, 비밀은 n_secret 이상이며 배정 가능해야 한다.
def required_counts(preset: Preset) -> dict[str, int]:
    return {
        "characters": preset.n_resident_pool_needed,
        "secrets": preset.n_secret,
        "temperaments": 1,
    }


# ---------------------------------------------------------------- 배정 검증기 계약
@dataclass
class Assignment:
    """판 시작 시 엔진이 만든 배정(시드로 재현). 0일째 밤 행적까지 포함."""
    game_id: str
    preset_id: str
    residents: list[str]                 # 무당 외 등장 주민(0일째 실종자 포함)
    night0_victim: str
    roles: dict[str, Role]               # 주민만
    secrets: dict[str, str]              # npc_id -> secret_id
    night0_whereabouts: dict[str, dict[Slot, Place]]
    night0_plan: NightPlan


@dataclass
class Violation:
    code: str
    detail: str


def validate_assignment(preset: Preset, content: ContentBundle, a: Assignment) -> list[Violation]:
    """배정이 계약 제약을 지키는지 검사한다. 빈 목록이면 통과."""
    v: list[Violation] = []
    chars = {c.npc_id for c in content.characters}
    secrets = {s.secret_id: s for s in content.secrets}

    if len(a.residents) != preset.n_resident_pool_needed:
        v.append(Violation("resident_count", f"{len(a.residents)} != {preset.n_resident_pool_needed}"))
    if len(set(a.residents)) != len(a.residents):
        v.append(Violation("resident_duplicate", "주민 중복"))
    for r in a.residents:
        if r not in chars:
            v.append(Violation("unknown_character", r))
    if set(a.roles) != set(a.residents):
        v.append(Violation("role_coverage", "모든 주민에게 역할이 있어야 함"))
    possessed = [n for n, r in a.roles.items() if r is Role.POSSESSED]
    if len(possessed) != preset.n_possessed:
        v.append(Violation("possessed_count", f"{len(possessed)} != {preset.n_possessed}"))
    if any(r is Role.MUDANG for r in a.roles.values()):
        v.append(Violation("mudang_in_residents", "무당은 주민 역할이 아님"))
    if a.night0_victim not in a.residents or a.roles.get(a.night0_victim) is Role.POSSESSED:
        v.append(Violation("night0_victim", "0일째 피해자는 마을 주민이어야 함"))

    # 비밀
    if len(a.secrets) != preset.n_secret:
        v.append(Violation("secret_count", f"{len(a.secrets)} != {preset.n_secret}"))
    for npc, sid in a.secrets.items():
        s = secrets.get(sid)
        if s is None:
            v.append(Violation("unknown_secret", sid))
            continue
        if a.roles.get(npc) is not Role.VILLAGER:
            v.append(Violation("secret_on_non_villager", f"{npc}:{sid}"))
        if npc == a.night0_victim:
            v.append(Violation("secret_on_victim", f"{npc}:{sid}"))
        if npc not in s.eligible_npcs:
            v.append(Violation("secret_not_eligible", f"{npc}:{sid}"))
        if s.actual_place == s.false_place:
            v.append(Violation("secret_false_equals_actual", sid))
        for other in s.conflicts_with:
            if other in a.secrets.values():
                v.append(Violation("secret_conflict", f"{sid}<->{other}"))
        actual = a.night0_whereabouts.get(npc, {}).get(s.slot)
        if actual is not None and actual is not s.actual_place:
            v.append(Violation("secret_whereabouts_mismatch", f"{npc}:{sid}"))

    # 0일째 밤 행적과 홀림
    for npc in a.residents:
        w = a.night0_whereabouts.get(npc)
        if not w or set(w) != set(Slot):
            v.append(Violation("whereabouts_incomplete", npc))
    plan = a.night0_plan
    if plan.executor not in possessed:
        v.append(Violation("executor_not_possessed", plan.executor))
    if plan.victim != a.night0_victim:
        v.append(Violation("plan_victim_mismatch", plan.victim))
    ex_w = a.night0_whereabouts.get(plan.executor, {}).get(Slot.MIDNIGHT)
    vic_w = a.night0_whereabouts.get(plan.victim, {}).get(Slot.MIDNIGHT)
    if ex_w is not None and vic_w is not None and ex_w is not vic_w:
        v.append(Violation("executor_not_at_victim", f"{ex_w.value} != {vic_w.value}"))
    # 목격 조합: 목격은 행적에서 파생된다(같은 시간대·같은 공공장소의 두 사람은 서로 본다).
    # 피해 시각에 피해 장소에 실행자·피해자 외 다른 주민이 있으면 범행 목격자가 생겨 판이 성립하지 않는다.
    if vic_w is not None and vic_w is not Place.HOME:
        for npc in a.residents:
            if (npc not in (plan.executor, plan.victim) and a.roles.get(npc) is not Role.POSSESSED
                    and a.night0_whereabouts.get(npc, {}).get(Slot.MIDNIGHT) is vic_w):
                v.append(Violation("witness_at_crime", npc))
    for npc, place in plan.agreed_alibis.items():
        if npc not in possessed:
            v.append(Violation("alibi_by_non_possessed", npc))
        if npc == plan.executor and place == (ex_w.value if ex_w else None):
            v.append(Violation("executor_alibi_is_truth", "실행자의 합의 알리바이는 실제 위치와 달라야 함"))
    return v
