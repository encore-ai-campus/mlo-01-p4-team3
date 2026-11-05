"""게임 엔진(`GameEngine` 포트 구현). A-05-01: 판 생성·배정·아침 진술(`new_game`).

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
순수 함수처럼 동작한다: 같은 메타(시드 포함)·콘텐츠면 같은 판을 만든다. 난수는 `random.Random`
인스턴스만 쓰고, 순서는 콘텐츠와 시드로만 정해진다.
근거: 기획서 4.1·4.2·4.6·5.1, docs/contracts/domain.md 2·5·6-1·10절.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from . import params
from .content import Assignment, Character, ContentBundle, Secret, required_counts, validate_assignment
from .events import DomainEvent, EventType
from .model import (
    PLAYER_ID, Claim, ClaimTruth, ClaimType, DisappearanceRecord, GameMeta, GameState, GameTruth,
    NightPlan, NpcState, ObservationSnapshot, Phase, Place, Preset, PrivateKnowledge, PUBLIC_PLACES,
    PublicRecord, Role, Sighting, Slot,
)
from ..ports import EngineDeps, PlayerView, TransitionResult

NIGHT0 = 0
FIRST_DAY = 1
SLOTS = (Slot.EVENING, Slot.MIDNIGHT)


class AssignmentError(ValueError):
    """콘텐츠가 프리셋을 채우지 못하거나, 재시도 한도 안에 검증기를 통과하는 배정을 못 만들었다."""


@dataclass
class Setup:
    """판 생성 중간 결과. 배정과 그 배정에서 파생된 목격·거짓 행적."""
    assignment: Assignment
    attempts: int                                   # 검증기 통과까지 뽑은 횟수(1 = 첫 시도)
    sightings: dict[str, list[Sighting]]            # npc -> 0일째 밤 자기 목격
    lies: dict[str, dict[Slot, Place]]              # npc -> 거짓으로 말할 시간대 -> 말할 장소


# ---------------------------------------------------------------- 배정
def make_setup(preset: Preset, content: ContentBundle, seed: int, game_id: str) -> Setup:
    """시드로 배정을 만들고 검증기를 통과할 때까지 파생 시드로 다시 뽑는다(domain.md 10절)."""
    _check_content(preset, content)
    for attempt in range(params.ASSIGNMENT_MAX_ATTEMPTS):
        rng = random.Random(f"{seed}:{attempt}")
        a = _draw_assignment(rng, preset, content, game_id)
        if a is None:
            continue
        sightings = _draw_sightings(rng, a)
        plan = a.night0_plan
        alibi = executor_alibi(rng, a.night0_whereabouts[plan.executor][Slot.MIDNIGHT], sightings[plan.executor])
        if preset.n_possessed == 2:                       # 2명 판은 실행자의 거짓 알리바이를 합의한다
            plan.agreed_alibis = {plan.executor: alibi.value}
        if validate_assignment(preset, content, a):
            continue
        return Setup(a, attempt + 1, sightings, _lies_of(a, content, alibi))
    raise AssignmentError(f"{preset.preset_id}: 시드 {seed}에서 {params.ASSIGNMENT_MAX_ATTEMPTS}회 안에 유효한 배정 없음")


def _check_content(preset: Preset, content: ContentBundle) -> None:
    need = required_counts(preset)
    have = {"characters": len(content.characters), "secrets": len(content.secrets),
            "temperaments": len(content.temperaments)}
    short = {k: (have[k], v) for k, v in need.items() if have[k] < v}
    if short:
        raise AssignmentError(f"{preset.preset_id}: 콘텐츠 부족 {short}")


def _draw_assignment(rng: random.Random, preset: Preset, content: ContentBundle, game_id: str) -> Optional[Assignment]:
    chars = {c.npc_id: c for c in content.characters}
    residents = rng.sample(sorted(chars), preset.n_resident_pool_needed)
    possessed = rng.sample(residents, preset.n_possessed)
    roles = {r: (Role.POSSESSED if r in possessed else Role.VILLAGER) for r in residents}
    villagers = [r for r in residents if r not in possessed]
    victim = rng.choice(villagers)
    secrets = _draw_secrets(rng, [v for v in villagers if v != victim], content.secrets, preset.n_secret)
    if secrets is None:
        return None
    by_id = {s.secret_id: s for s in content.secrets}

    where: dict[str, dict[Slot, Place]] = {r: {s: _routine_place(rng, chars[r], s) for s in SLOTS}
                                           for r in residents}
    for npc, sid in secrets.items():                      # 비밀 시간대는 비밀의 실제 장소
        s = by_id[sid]
        where[npc][s.slot] = s.actual_place
    executor = rng.choice(sorted(possessed))
    crime = where[victim][Slot.MIDNIGHT]
    where[executor][Slot.MIDNIGHT] = crime                # 실행자는 한밤에 피해자의 장소로 간다
    if crime in PUBLIC_PLACES:                            # 범행 장소 동석 금지: 다른 마을 주민은 비킨다
        fixed = {npc for npc, sid in secrets.items() if by_id[sid].slot is Slot.MIDNIGHT}
        for r in villagers:                               # 한밤 비밀 장소와 겹치면 검증기가 걸러 다시 뽑는다
            if r != victim and r not in fixed and where[r][Slot.MIDNIGHT] is crime:
                where[r][Slot.MIDNIGHT] = _routine_place(rng, chars[r], Slot.MIDNIGHT, exclude=crime)

    plan = NightPlan(executor=executor, victim=victim)
    return Assignment(game_id=game_id, preset_id=preset.preset_id, residents=residents,
                      night0_victim=victim, roles=roles, secrets=secrets,
                      night0_whereabouts=where, night0_plan=plan)


def _draw_secrets(rng: random.Random, holders_pool: list[str], secrets: list[Secret],
                  n: int) -> Optional[dict[str, str]]:
    """자격(eligible_npcs)·충돌(conflicts_with)을 지키는 비밀 n개를 무작위 백트래킹으로 고른다."""
    order = list(secrets)
    rng.shuffle(order)
    pool = list(holders_pool)
    rng.shuffle(pool)

    by_id = {s.secret_id: s for s in secrets}

    def conflicts(sid: str, chosen: dict[str, str]) -> bool:   # 어느 쪽에 적혀 있어도 충돌로 본다
        return any(c in by_id[sid].conflicts_with or sid in by_id[c].conflicts_with for c in chosen.values())

    def go(i: int, chosen: dict[str, str]) -> Optional[dict[str, str]]:
        if len(chosen) == n:
            return dict(chosen)
        if i >= len(order):
            return None
        s = order[i]
        if not conflicts(s.secret_id, chosen):
            for npc in pool:
                if npc not in chosen and npc in s.eligible_npcs:
                    chosen[npc] = s.secret_id
                    got = go(i + 1, chosen)
                    if got is not None:
                        return got
                    del chosen[npc]
        return go(i + 1, chosen)

    return go(0, {})


def _routine_place(rng: random.Random, ch: Character, slot: Slot, exclude: Optional[Place] = None) -> Place:
    """인물 동선 가중치로 장소를 뽑는다. 후보가 없으면 집."""
    prefs = [(p, w) for p, w in ch.routine.get(slot, {}).items() if p is not exclude and w > 0]
    if not prefs:
        return Place.HOME
    places, weights = zip(*prefs)
    return rng.choices(places, weights=weights)[0]


def executor_alibi(rng: random.Random, crime: Place, own_sightings: list[Sighting]) -> Place:
    """실행자가 한밤에 있었다고 말할 장소(기획서 5.2 씐 자 전략의 단순형, 정교화는 A-07-01).

    자기가 아는 목격 기록으로 반박되지 않는 공공장소 중에서 시드로 고른다: 범행 장소와
    자기가 한밤에 누군가를 본 장소(= 그 시각 실제로 있던 곳)는 뺀다.
    """
    known = {s.place for s in own_sightings if s.slot is Slot.MIDNIGHT} | {crime}
    return rng.choice([p for p in PUBLIC_PLACES if p not in known])


# ---------------------------------------------------------------- 목격과 거짓 행적
def _draw_sightings(rng: random.Random, a: Assignment) -> dict[str, list[Sighting]]:
    """같은 시간대·같은 공공장소의 두 사람은 WITNESS_PROB로 서로를 본다(기획서 4.2)."""
    seen: dict[str, dict[Slot, list[str]]] = {r: {s: [] for s in SLOTS} for r in a.residents}
    for slot in SLOTS:
        for place in PUBLIC_PLACES:
            here = [r for r in a.residents if a.night0_whereabouts[r][slot] is place]
            for i, x in enumerate(here):
                for y in here[i + 1:]:
                    if rng.random() < params.WITNESS_PROB:
                        seen[x][slot].append(y)
                        seen[y][slot].append(x)
    return {r: [Sighting(NIGHT0, s, a.night0_whereabouts[r][s], sorted(seen[r][s]))
                for s in SLOTS if seen[r][s]]
            for r in a.residents}


def _lies_of(a: Assignment, content: ContentBundle, alibi: Place) -> dict[str, dict[Slot, Place]]:
    """거짓으로 진술할 행적. 비밀 보유자는 비밀 시간대, 실행자는 한밤(기획서 4.1·4.6)."""
    by_id = {s.secret_id: s for s in content.secrets}
    lies: dict[str, dict[Slot, Place]] = {}
    for npc, sid in a.secrets.items():
        s = by_id[sid]
        lies[npc] = {s.slot: s.false_place}
    lies[a.night0_plan.executor] = {Slot.MIDNIGHT: alibi}
    return lies


# ---------------------------------------------------------------- 아침 진술
def morning_claims(speakers: list[str], a: Assignment, sightings: dict[str, list[Sighting]],
                   lies: dict[str, dict[Slot, Place]], first_id: int = 1) -> tuple[list[Claim], dict[str, ClaimTruth]]:
    """0일째 밤에 대한 1일째 아침 진술(기획서 4.2·5.1 주장 스키마).

    화자마다 시간대 순으로 자기 행적 주장 1개, 그 시간대에 사실대로 말하는 목격이 있으면 목격 주장 1개.
    거짓으로 말하는 시간대(비밀·홀림)는 거짓 장소를 말하고 그 시간대의 목격은 말하지 않는다.
    씐 자는 범행 장소·시간대의 목격(실행자·피해자가 보임)을 말하지 않는다.
    """
    claims: list[Claim] = []
    truths: dict[str, ClaimTruth] = {}
    crime = (a.night0_whereabouts[a.night0_victim][Slot.MIDNIGHT], Slot.MIDNIGHT)
    n = first_id
    for sp in speakers:
        mine = {s.slot: s for s in sightings.get(sp, [])}
        for slot in SLOTS:
            lie = lies.get(sp, {}).get(slot)
            place = lie if lie is not None else a.night0_whereabouts[sp][slot]
            cid = f"cl{n:02d}"
            n += 1
            claims.append(Claim(cid, sp, ClaimType.WHEREABOUTS, sp, NIGHT0, slot, place, day=FIRST_DAY))
            truths[cid] = ClaimTruth(cid, is_true=lie is None, deliberate_lie=lie is not None)
            s = mine.get(slot)
            hide = a.roles[sp] is Role.POSSESSED and (s is not None and (s.place, slot) == crime)
            if lie is None and s is not None and not hide:
                cid = f"cl{n:02d}"
                n += 1
                claims.append(Claim(cid, sp, ClaimType.SIGHTING, sp, NIGHT0, slot, s.place,
                                    seen=list(s.seen), day=FIRST_DAY))
                truths[cid] = ClaimTruth(cid, is_true=True, deliberate_lie=False)
    return claims, truths


# ---------------------------------------------------------------- 엔진
class PaldoEngine:
    """`GameEngine` 포트 구현. A-05-01은 `new_game`, 나머지는 A-05-02 이후 태스크가 채운다."""

    def new_game(self, meta: GameMeta, deps: EngineDeps) -> TransitionResult:
        """판을 만든다: 배정(검증기 통과) → 0일째 밤 행적·목격·홀림 → 1일째 아침 실종 발표와 진술."""
        content = deps.content
        preset = meta.preset
        setup = make_setup(preset, content, meta.seed, meta.game_id)
        a = setup.assignment
        victim = a.night0_victim
        crime = a.night0_whereabouts[victim][Slot.MIDNIGHT]
        speakers = [r for r in a.residents if r != victim]
        claims, claim_truths = morning_claims(speakers, a, setup.sightings, setup.lies)
        temperaments = {c.npc_id: c.temperament_id for c in content.characters}
        secrets = {s.secret_id: s for s in content.secrets}
        possessed = [r for r in a.residents if a.roles[r] is Role.POSSESSED]

        npcs: dict[str, NpcState] = {}
        for r in speakers:
            pk = PrivateKnowledge(npc_id=r, role=a.roles[r], secret_id=a.secrets.get(r),
                                  whereabouts={NIGHT0: dict(a.night0_whereabouts[r])},
                                  witnessed=list(setup.sightings[r]))
            if a.roles[r] is Role.POSSESSED:
                partners = [p for p in possessed if p != r]
                pk.partner_id = partners[0] if partners else None
                pk.night_plans = {NIGHT0: _copy_plan(a.night0_plan)}
            if r in a.secrets:
                s = secrets[a.secrets[r]]
                pk.hidden_whereabouts = [{"night": str(NIGHT0), "slot": s.slot.value}]
                # 고백(confession_threshold 등)은 A-06-01·A-07-01 범위. 여기서는 비밀 사건과 그 시간대 거짓말만 만든다.
            pk.my_false_claims = [c.claim_id for c in claims
                                  if c.speaker == r and claim_truths[c.claim_id].deliberate_lie]
            npcs[r] = NpcState(npc_id=r, temperament_id=temperaments[r], private=pk)

        public = PublicRecord(claims=claims,
                              disappearances=[DisappearanceRecord(NIGHT0, victim, crime, Slot.MIDNIGHT)])
        state = GameState(meta=meta, state_version=0, day=FIRST_DAY, phase=Phase.MORNING,
                          active=[PLAYER_ID] + speakers, missing=[victim], public=public, npcs=npcs)
        truth = GameTruth(game_id=meta.game_id,
                          roles={PLAYER_ID: Role.MUDANG, **a.roles},
                          secrets=dict(a.secrets),
                          whereabouts={NIGHT0: {r: dict(w) for r, w in a.night0_whereabouts.items()}},
                          night_plans={NIGHT0: _copy_plan(a.night0_plan)},
                          claim_truths=claim_truths)
        events = [
            DomainEvent(EventType.GAME_STARTED, FIRST_DAY, Phase.MORNING, {"preset_id": preset.preset_id}),
            DomainEvent(EventType.MORNING_ANNOUNCED, FIRST_DAY, Phase.MORNING,
                        {"missing": [victim], "crime_scene": {"place": crime.value, "slot": Slot.MIDNIGHT.value},
                         "claims": [c.claim_id for c in claims]}),
        ]
        return TransitionResult(True, state, truth, events)

    def apply(self, state, truth, env, deps):  # pragma: no cover - A-05-02
        """명령 처리. A-05-02에서 구현한다."""
        raise NotImplementedError("A-05-02")

    def player_view(self, state: GameState, content: ContentBundle) -> PlayerView:  # pragma: no cover
        """플레이어 화면 정보. A-05-02에서 구현한다."""
        raise NotImplementedError("A-05-02")

    def observe(self, state: GameState, truth: GameTruth, npc_id: str) -> ObservationSnapshot:  # pragma: no cover
        """관측 스냅샷. A-06-02에서 구현한다."""
        raise NotImplementedError("A-06-02")


def _copy_plan(p: NightPlan) -> NightPlan:
    return NightPlan(p.executor, p.victim, dict(p.agreed_alibis))


def assignment_of(state: GameState, truth: GameTruth) -> Assignment:
    """판 상태·진실에서 0일째 배정을 다시 꺼낸다(검증기 재검사·테스트용)."""
    victim = state.public.disappearances[0].victim
    residents = [r for r in state.active if r != PLAYER_ID] + [victim]
    return Assignment(game_id=state.meta.game_id, preset_id=state.meta.preset.preset_id,
                      residents=residents, night0_victim=victim,
                      roles={r: truth.roles[r] for r in residents}, secrets=dict(truth.secrets),
                      night0_whereabouts=dict(truth.whereabouts[NIGHT0]),
                      night0_plan=truth.night_plans[NIGHT0])
