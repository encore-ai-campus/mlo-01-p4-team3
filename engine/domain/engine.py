"""게임 엔진(`GameEngine` 포트 구현). A-05-01: 판 생성·배정·아침 진술(`new_game`).
A-05-02: 명령 처리(`apply`) — 회의 라운드·캐묻기·투표·감금·종료 판정, 최소 밤 처리, `player_view`.
A-06-01: 밤 처리(행적·목격·실종·점 결과)와 다음 날 아침 진술, 점괘 공개의 실제값·공개값 분리, 고백, 기색.

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
순수 함수처럼 동작한다: 같은 메타(시드 포함)·콘텐츠면 같은 판을 만든다. 난수는 `random.Random`
인스턴스만 쓰고, 순서는 콘텐츠와 시드로만 정해진다.
근거: 기획서 4.1~4.6·5.1, docs/contracts/domain.md 2~6-1·10절.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Optional, Protocol

from . import params
from .content import Assignment, Character, ContentBundle, Secret, required_counts, validate_assignment
from .events import DomainEvent, EventType
from .model import (
    LIMITS, PHASE_RULES, PLAYER_ID, Claim, ClaimTruth, ClaimType, Command, CommandEnvelope, CommandKind,
    ConfessionRecord, DisappearanceRecord, DivinationResult, EndReason, EvidenceRef, EvidenceType, GameMeta, GameResult, GameState, GameTruth,
    InterrogationKind, LockRecord, NightPlan, NpcState, ObservationSnapshot, Phase, Place, Preset,
    PrivateKnowledge, PUBLIC_PLACES, PublicRecord, ReactionIntent, RejectCode, Role, Sighting, Slot, Speech,
    TellRecord, VoteRecord, check_end, check_player_limits, divination_of, is_legal_kind, missing_slots, resolve_vote,
    winner_of,
)
from ..ports import EngineDeps, PlayerView, RenderRequest, TransitionResult

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
    """0일째 밤 목격(`draw_sightings`에 배정의 행적을 넣는다)."""
    return draw_sightings(rng, NIGHT0, a.residents, a.night0_whereabouts)


def draw_sightings(rng: random.Random, night: int, people: list[str],
                   where: dict[str, dict[Slot, Place]]) -> dict[str, list[Sighting]]:
    """같은 시간대·같은 공공장소의 두 사람은 WITNESS_PROB로 서로를 본다(기획서 4.2, domain.md 5절 4-3).

    0일째와 이후 밤이 같은 규칙을 쓴다. 쌍마다 한 번 뽑고 서로 본다. 못 봤다는 것은 부재 증명이 아니다.
    """
    seen: dict[str, dict[Slot, list[str]]] = {r: {s: [] for s in SLOTS} for r in people}
    for slot in SLOTS:
        for place in PUBLIC_PLACES:
            here = [r for r in people if where[r][slot] is place]
            for i, x in enumerate(here):
                for y in here[i + 1:]:
                    if rng.random() < params.WITNESS_PROB:
                        seen[x][slot].append(y)
                        seen[y][slot].append(x)
    return {r: [Sighting(night, s, where[r][s], sorted(seen[r][s])) for s in SLOTS if seen[r][s]]
            for r in people}


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
    crime = a.night0_whereabouts[a.night0_victim][Slot.MIDNIGHT]
    return night_claims(speakers, NIGHT0, FIRST_DAY, a.night0_whereabouts, sightings, lies, a.roles, crime,
                        first_id)


def night_claims(speakers: list[str], night: int, day: int, where: dict[str, dict[Slot, Place]],
                 sightings: dict[str, list[Sighting]], lies: dict[str, dict[Slot, Place]],
                 roles: dict[str, Role], crime: Place, first_id: int) -> tuple[list[Claim], dict[str, ClaimTruth]]:
    """밤 `night`에 대한 `day`일째 아침 진술. 0일째(`morning_claims`)와 이후 밤이 같은 규칙을 쓴다.

    화자마다 시간대 순으로 자기 행적 주장 1개, 그 시간대에 사실대로 말하는 목격이 있으면 목격 주장 1개.
    거짓으로 말하는 시간대는 거짓 장소를 말하고 그 시간대의 목격은 말하지 않는다.
    씐 자는 범행 장소·한밤의 목격(실행자·피해자가 보임)을 말하지 않는다.
    """
    claims: list[Claim] = []
    truths: dict[str, ClaimTruth] = {}
    scene = (crime, Slot.MIDNIGHT)
    n = first_id
    for sp in speakers:
        mine = {s.slot: s for s in sightings.get(sp, [])}
        for slot in SLOTS:
            lie = lies.get(sp, {}).get(slot)
            place = lie if lie is not None else where[sp][slot]
            cid = f"cl{n:02d}"
            n += 1
            claims.append(Claim(cid, sp, ClaimType.WHEREABOUTS, sp, night, slot, place, day=day))
            truths[cid] = ClaimTruth(cid, is_true=lie is None, deliberate_lie=lie is not None)
            s = mine.get(slot)
            hide = roles[sp] is Role.POSSESSED and (s is not None and (s.place, slot) == scene)
            if lie is None and s is not None and not hide:
                cid = f"cl{n:02d}"
                n += 1
                claims.append(Claim(cid, sp, ClaimType.SIGHTING, sp, night, slot, s.place,
                                    seen=list(s.seen), day=day))
                truths[cid] = ClaimTruth(cid, is_true=True, deliberate_lie=False)
    return claims, truths


# ---------------------------------------------------------------- 고정 배정으로 판 만들기
def setup_from_assignment(a: Assignment, content: ContentBundle, seed: int) -> Setup:
    """이미 정해진 배정(픽스처·재현용)에서 목격·거짓 행적을 시드로 만든다. 배정 자체는 바꾸지 않는다."""
    rng = random.Random(f"{seed}:fixed")
    sightings = _draw_sightings(rng, a)
    plan = a.night0_plan
    alibi = executor_alibi(rng, a.night0_whereabouts[plan.executor][Slot.MIDNIGHT], sightings[plan.executor])
    return Setup(a, 1, sightings, _lies_of(a, content, alibi))


# ---------------------------------------------------------------- NPC 결정(임시 규칙)
class NpcPolicy(Protocol):
    """엔진이 NPC 차례에 부르는 결정 규칙. 각 NPC는 공개 기록과 자기 상태(`state.npcs[npc]`)만 본다.

    A-05-02는 `BaselinePolicy`(침묵·시드 투표·시드 홀림)만 둔다. 의심·감싸기 같은 실제 결정은
    A-07-01(`npc/policy_*.py`)·A-07-02(`npc/vote.py`)가 같은 자리에 끼운다.
    """

    def talk(self, state: GameState, npc_id: str, rng: random.Random) -> Command:
        """회의 라운드 차례의 말 행동(`PHASE_RULES` NPC 합법 종류)."""
        ...

    def vote(self, state: GameState, npc_id: str, candidates: list[str], rng: random.Random) -> str:
        """투표·결선 대상(후보 중 자기 아닌 1명). vote·runoff 단계에 들어갈 때 한 번 부른다."""
        ...

    def night_plan(self, state: GameState, rng: random.Random) -> NightPlan:
        """씐 자 공동 밤 명령(실행자 = 활동 씐 자, 피해자 = 활동 마을 주민 NPC)."""
        ...

    def react(self, state: GameState, npc_id: str, cmd: Command, rng: random.Random) -> ReactionIntent:
        """캐묻기를 받은 NPC의 답변 의도(해명·회피·역공·고백). 고백은 `can_confess`일 때만 엔진이 받는다.

        압박 누적과 고백 문턱값(성향 보정)은 A-07-01이 이 자리에 끼운다.
        """
        ...


class BaselinePolicy:
    """임시 NPC 규칙. 말 차례는 침묵, 투표는 의심도 최댓값(동률은 시드), 홀림은 시드로, 캐묻기 답은 해명."""

    def react(self, state: GameState, npc_id: str, cmd: Command, rng: random.Random) -> ReactionIntent:
        """캐묻기 답변 의도. 지금은 항상 해명한다(고백 결정은 A-07-01)."""
        return ReactionIntent.EXPLAIN

    def talk(self, state: GameState, npc_id: str, rng: random.Random) -> Command:
        """회의 차례 행동. 지금은 침묵만 한다."""
        return Command(CommandKind.SILENCE, npc_id)

    def vote(self, state: GameState, npc_id: str, candidates: list[str], rng: random.Random) -> str:
        """후보 중 자기 의심도가 가장 높은 사람. 고백자는 빼고(`candidate_pool`), 씐 자는 짝을 피한다. 동률은 시드.

        고백자를 빼면 후보가 남지 않을 때(예: 결선 후보가 모두 고백자)는 합법 후보 전체로 돌아간다.
        """
        me = state.npcs[npc_id]
        legal = [c for c in candidates if c != npc_id]
        allowed = set(candidate_pool(state, npc_id)) | {PLAYER_ID}
        pool = [c for c in legal if c in allowed] or legal
        if me.private.role is Role.POSSESSED:
            pool = [c for c in pool if c != me.private.partner_id] or pool
        sus = me.beliefs.suspicion
        top = max(sus.get(c, 50.0) for c in pool)
        return rng.choice(sorted(c for c in pool if sus.get(c, 50.0) == top))

    def night_plan(self, state: GameState, rng: random.Random) -> NightPlan:
        """활동 씐 자 중 실행자, 활동 마을 주민 NPC 중 피해자를 시드로 고른다."""
        possessed = sorted(n for n in state.active if n in state.npcs
                           and state.npcs[n].private.role is Role.POSSESSED)
        villagers = sorted(n for n in state.active if n in state.npcs
                           and state.npcs[n].private.role is Role.VILLAGER)
        return NightPlan(executor=rng.choice(possessed), victim=rng.choice(villagers))


# ---------------------------------------------------------------- 명령 검증(거부 판정)
SLOT_KO = {Slot.EVENING: "초저녁", Slot.MIDNIGHT: "한밤"}
_TALK_KINDS = (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.POINT_CONTRADICTION,
               CommandKind.EXPLAIN, CommandKind.SILENCE, CommandKind.REVEAL_DIVINATION)
ROUND_PHASES = (Phase.ROUND1, Phase.ROUND2)


def legal_player_kinds(state: GameState) -> list[CommandKind]:
    """지금 단계에서 플레이어가 낼 수 있는 명령 종류(`PHASE_RULES` − 횟수 제한에 걸린 것)."""
    if state.result is not None:
        return []
    return [k for k in PHASE_RULES[state.phase].player_kinds if check_player_limits(state.player, k) is None]


def validate_command(state: GameState, truth: GameTruth, env: CommandEnvelope) -> Optional[tuple[RejectCode, str]]:
    """플레이어 명령을 검사한다. 거부면 (코드, 설명), 수락할 수 있으면 None. 상태는 읽기만 한다.

    순서: 버전 → 종료 → 행동 주체 → 단계 합법(`PHASE_RULES`) → 필수 슬롯 → 횟수(`LIMITS`) → 대상 → 근거.
    """
    cmd = env.command
    if env.expected_state_version != state.state_version:
        return RejectCode.STATE_VERSION_CONFLICT, f"expected {env.expected_state_version}, actual {state.state_version}"
    if state.phase is Phase.ENDED or state.result is not None:
        return RejectCode.GAME_ENDED, ""
    if cmd.actor != PLAYER_ID:
        return RejectCode.NOT_YOUR_TURN, f"플레이어 명령만 받는다: {cmd.actor}"
    if not is_legal_kind(state.phase, cmd.kind, True):
        return RejectCode.ILLEGAL_PHASE, f"{state.phase.value}에서 {cmd.kind.value} 불가"
    if state.phase in ROUND_PHASES and (state.turn_index >= len(state.turn_order)
                                        or state.turn_order[state.turn_index] != PLAYER_ID):
        return RejectCode.NOT_YOUR_TURN, ""
    miss = missing_slots(cmd)
    if miss:
        return RejectCode.MISSING_SLOT, ",".join(miss)
    limit = check_player_limits(state.player, cmd.kind)
    if limit is not None:
        return limit, cmd.kind.value
    bad = _check_target(state, truth, cmd)
    if bad is not None:
        return bad
    return _check_evidence(state, cmd)


def _check_target(state: GameState, truth: GameTruth, cmd: Command) -> Optional[tuple[RejectCode, str]]:
    npcs_active = [n for n in state.active if n != PLAYER_ID]
    k = cmd.kind
    if k in (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.DIVINE) and cmd.target not in npcs_active:
        return RejectCode.ILLEGAL_TARGET, f"활동 주민이 아님: {cmd.target}"
    if k is CommandKind.VOTE:
        pool = state.runoff_candidates if state.phase is Phase.RUNOFF else state.active
        if cmd.target == PLAYER_ID or cmd.target not in pool:
            return RejectCode.ILLEGAL_TARGET, f"투표 대상 아님: {cmd.target}"
    if k is CommandKind.INTERROGATE:
        if cmd.addressee not in npcs_active:
            return RejectCode.ILLEGAL_TARGET, f"활동 주민이 아님: {cmd.addressee}"
        if cmd.interrogation_kind is InterrogationKind.WHEREABOUTS:
            if not (0 <= cmd.night < state.day) or cmd.addressee not in truth.whereabouts.get(cmd.night, {}):
                return RejectCode.ILLEGAL_TARGET, f"지난 밤이 아님: {cmd.night}"
    if k is CommandKind.REVEAL_DIVINATION:
        d = cmd.divination
        if d.target not in state.player.divinations.get(d.night, {}):
            return RejectCode.ILLEGAL_TARGET, f"점친 적 없는 대상·밤: {d.target}/{d.night}"
    return None


def _check_evidence(state: GameState, cmd: Command) -> Optional[tuple[RejectCode, str]]:
    pub = state.public
    claims = {c.claim_id: c for c in pub.claims}
    for ref in cmd.evidence:
        t, rid = ref.type, ref.record_id
        ok = {
            EvidenceType.CLAIM: lambda: rid in claims,
            EvidenceType.CONTRADICTION: lambda: len(rid.split("|")) == 2 and all(x in claims for x in rid.split("|")),
            EvidenceType.TELL: lambda: any(x.claim_id == rid for x in pub.tells),
            EvidenceType.DIVINATION: lambda: rid in claims and claims[rid].claim_type is ClaimType.DIVINATION_REVEAL,
            EvidenceType.VOTE: lambda: any(v.vote_id == rid for v in pub.votes),
            EvidenceType.MAJORITY: lambda: True,
        }[t]()
        if not ok:
            return RejectCode.UNKNOWN_EVIDENCE, f"{t.value}:{rid}"
    needs_pair = (cmd.kind is CommandKind.POINT_CONTRADICTION
                  or (cmd.kind is CommandKind.INTERROGATE
                      and cmd.interrogation_kind is InterrogationKind.CONTRADICTION))
    if needs_pair and not _contradiction_claims(cmd.evidence):
        return RejectCode.MISSING_SLOT, "evidence(모순 1개 또는 주장 2개)"
    return None


def _contradiction_claims(evidence: list[EvidenceRef]) -> list[str]:
    """모순 근거에서 주장 ID 2개를 꺼낸다. 형식이 안 맞으면 빈 목록."""
    for ref in evidence:
        if ref.type is EvidenceType.CONTRADICTION:
            return ref.record_id.split("|")
    ids = [r.record_id for r in evidence if r.type is EvidenceType.CLAIM]
    return ids[:2] if len(ids) >= 2 else []


# ---------------------------------------------------------------- 상태 전이
class _Tx:
    """명령 1개의 전이 작업 공간. 복사본(state, truth)만 고치고 이벤트·렌더 요청을 모은다."""

    def __init__(self, state: GameState, truth: GameTruth, content: ContentBundle, policy: NpcPolicy):
        self.st = copy.deepcopy(state)
        self.tr = copy.deepcopy(truth)
        self.st.state_version = state.state_version + 1
        self.content = content
        self.policy = policy
        self.events: list[DomainEvent] = []
        self.renders: list[RenderRequest] = []
        self.chars = {c.npc_id: c for c in content.characters}
        self.secrets = {s.secret_id: s for s in content.secrets}
        self.place_ko = {p.place: p.name_ko for p in content.places}

    # ---- 공통
    def rng(self, purpose: str) -> random.Random:
        """판 시드·날·목적으로 고정한 난수. 같은 입력이면 같은 결과(재현)."""
        return random.Random(f"{self.st.meta.seed}:{self.st.day}:{purpose}")

    def emit(self, et: EventType, payload: dict) -> None:
        self.events.append(DomainEvent(et, self.st.day, self.st.phase, payload))

    def template(self, kind: CommandKind, reaction: Optional[ReactionIntent] = None) -> str:
        for t in self.content.templates:
            if t.kind is kind and t.reaction is reaction and t.temperament_id is None:
                return t.variants[0]
        return ""

    def name(self, pid: Optional[str]) -> str:
        if pid is None:
            return ""
        return "무당" if pid == PLAYER_ID else self.chars[pid].name_ko if pid in self.chars else pid

    def speak(self, speaker: str, cmd: Command, claim_ids: Optional[list[str]] = None, **kw) -> Speech:
        sp = Speech(f"sp{len(self.st.public.speeches) + 1:02d}", self.st.day, self.st.phase, speaker, cmd.kind,
                    target=cmd.target, addressee=cmd.addressee, evidence=list(cmd.evidence),
                    claim_ids=list(claim_ids or []), **kw)
        self.st.public.speeches.append(sp)
        return sp

    def new_claim(self, **kw) -> Claim:
        c = Claim(claim_id=f"cl{len(self.st.public.claims) + 1:02d}", day=self.st.day, **kw)
        self.st.public.claims.append(c)
        return c

    def record_claim(self, c: Claim, is_true: bool) -> None:
        """새 주장의 진위를 진실에 저장하고 기색을 1회 판정한 뒤 `claim_made`를 낸다."""
        ct = ClaimTruth(c.claim_id, is_true=is_true, deliberate_lie=not is_true)
        self.tr.claim_truths[c.claim_id] = ct
        payload: dict = {"claim_id": c.claim_id}
        if record_tell(self.st, c, ct):
            payload["tell"] = True              # 보인 기색만 싣는다(안 보임은 거짓 여부 누출)
        self.emit(EventType.CLAIM_MADE, payload)

    # ---- 회의 라운드
    def start_round(self, phase: Phase) -> None:
        st = self.st
        st.phase = phase
        st.player.talk_used_this_round = False
        order = list(st.active)
        self.rng(f"order:{phase.value}").shuffle(order)
        st.turn_order, st.turn_index = order, 0
        self.run_npc_turns()

    def run_npc_turns(self) -> None:
        """플레이어 차례가 오거나 라운드가 끝날 때까지 NPC 말 차례를 진행한다."""
        st = self.st
        while st.turn_index < len(st.turn_order) and st.turn_order[st.turn_index] != PLAYER_ID:
            npc = st.turn_order[st.turn_index]
            cmd = self.policy.talk(st, npc, self.rng(f"talk:{st.phase.value}:{npc}"))
            if not is_legal_kind(st.phase, cmd.kind, False):
                raise ValueError(f"NPC 정책이 불법 명령을 냄: {npc} {cmd.kind}")
            sp = self.speak(npc, cmd, line_text=self.template(cmd.kind).format(target=self.name(cmd.target)))
            self.emit(EventType.SPEECH_MADE, {"speech_id": sp.speech_id})
            st.turn_index += 1
        if st.turn_index >= len(st.turn_order):
            self.end_round()

    def end_round(self) -> None:
        st = self.st
        st.turn_order, st.turn_index = [], 0
        if st.phase is Phase.ROUND1:
            st.phase = Phase.INTERROGATION
        else:
            self.start_vote()

    def player_talk(self, cmd: Command) -> None:
        st = self.st
        claim_ids: list[str] = []
        if cmd.kind is CommandKind.REVEAL_DIVINATION:
            # 공개값은 공개 기록(주장)에, 실제값은 무당 개인 정보(player.divinations)에 따로 둔다. 거짓 공개도 합법.
            d = cmd.divination
            c = self.new_claim(speaker=PLAYER_ID, claim_type=ClaimType.DIVINATION_REVEAL, subject=d.target,
                               night=d.night, divination_result=d.result)
            claim_ids.append(c.claim_id)
            self.record_claim(c, is_true=st.player.divinations[d.night][d.target] is d.result)
            st.player.reveals_left_today -= 1
            cmd = Command(cmd.kind, cmd.actor, target=d.target, evidence=list(cmd.evidence))
        sp = self.speak(PLAYER_ID, cmd, claim_ids)
        self.emit(EventType.SPEECH_MADE, {"speech_id": sp.speech_id})
        st.player.talk_used_this_round = True
        st.turn_index += 1
        self.run_npc_turns()

    # ---- 캐묻기
    def interrogate(self, cmd: Command) -> None:
        st = self.st
        npc = cmd.addressee
        st.player.interrogations_left -= 1
        claim_ids: list[str] = []
        n_answers = sum(1 for s in st.public.speeches if s.kind is CommandKind.INTERROGATE)
        reaction = self.policy.react(st, npc, cmd, self.rng(f"react:{n_answers}:{npc}"))
        if reaction is ReactionIntent.CONFESS and not can_confess(st, self.tr, npc):
            raise ValueError(f"NPC 정책이 불가능한 고백을 냄: {npc}")
        if reaction is ReactionIntent.CONFESS:
            claim_ids, fact = self.confess(npc)
        elif cmd.interrogation_kind is InterrogationKind.WHEREABOUTS:
            claim, fact = self.whereabouts_answer(npc, cmd.night, cmd.slot)
            claim_ids.append(claim.claim_id)
        else:
            fact = self.template(CommandKind.EXPLAIN)
        job_id = f"job_{sum(1 for s in st.public.speeches if s.render_job_id) + 1:03d}"
        q = Command(CommandKind.INTERROGATE, npc, addressee=PLAYER_ID, evidence=list(cmd.evidence))
        sp = self.speak(npc, q, claim_ids, reaction=reaction, fact_sentence=fact, render_job_id=job_id)
        fallback = self.template(CommandKind.INTERROGATE, reaction).format(fact=fact) or fact
        names = [self.name(n) for n in st.active + st.locked + st.missing if n != PLAYER_ID]
        self.renders.append(RenderRequest(job_id, st.meta.game_id, st.state_version, sp.speech_id, npc,
                                          self.chars[npc].speech_style if npc in self.chars else "",
                                          reaction, fact, names, fallback))
        self.emit(EventType.INTERROGATION_ANSWERED, {"speech_id": sp.speech_id, "reaction": reaction.value,
                                                     "render_job_id": job_id, "kind": cmd.interrogation_kind.value})

    def whereabouts_answer(self, npc: str, night: int, slot: Slot) -> tuple[Claim, str]:
        """행적 질문의 답. 같은 밤·시간대에 이미 한 주장이 있으면 그 주장을 되풀이하고(같은 주장 ID), 없으면 새 주장을 만든다.

        새 주장은 사실대로 말한다. 그 밤 실행자의 한밤만 거짓으로 답한다: 밤 명령의 합의 알리바이가
        있으면 그 장소, 없으면 `executor_alibi`.
        """
        st, tr = self.st, self.tr
        prior = [c for c in st.public.claims if c.speaker == npc and c.subject == npc
                 and c.claim_type is ClaimType.WHEREABOUTS and c.night == night and c.slot is slot]
        if prior:
            c = prior[-1]
        else:
            actual = tr.whereabouts[night][npc][slot]
            place = actual
            if slot is Slot.MIDNIGHT:
                place = self.executor_lie(night, npc) or actual
            c = self.new_claim(speaker=npc, claim_type=ClaimType.WHEREABOUTS, subject=npc, night=night,
                               slot=slot, place=place)
            if place is not actual:
                st.npcs[npc].private.my_false_claims.append(c.claim_id)
            self.record_claim(c, is_true=place is actual)
        return c, self.whereabouts_sentence(c)

    def whereabouts_sentence(self, c: Claim) -> str:
        """행적 주장의 사실 문장(템플릿 `t_whereabouts`)."""
        fact = next((t.variants[0] for t in self.content.templates if t.template_id == "t_whereabouts"),
                    "{night}일째 밤 {slot}에 {place}에 있었소.")
        return fact.format(night=c.night, slot=SLOT_KO[c.slot], place=self.place_ko.get(c.place, c.place.value))

    def executor_lie(self, night: int, npc: str) -> Optional[Place]:
        """그 밤 실행자면 한밤에 말할 거짓 장소(합의 알리바이, 없으면 `executor_alibi`). 실행자가 아니면 None.

        아침 진술과 캐묻기 답이 같은 난수 키를 써서 같은 장소를 말한다.
        """
        plan = self.tr.night_plans.get(night)
        if plan is None or plan.executor != npc:
            return None
        agreed = plan.agreed_alibis.get(npc)
        if agreed:
            return Place(agreed)
        own = [s for s in self.st.npcs[npc].private.witnessed if s.night == night]
        actual = self.tr.whereabouts[night][npc][Slot.MIDNIGHT]
        return executor_alibi(random.Random(f"{self.st.meta.seed}:{night}:alibi:{npc}"), actual, own)

    # ---- 고백
    def confess(self, npc: str) -> tuple[list[str], str]:
        """비밀 고백(기획서 4.6, domain.md 6-1). 비밀 시간대의 거짓 행적 주장을 `corrects`로 정정한다.

        고백 기록·`confession` 이벤트를 남기고 `runtime.confessed`를 세운다. 다음 점부터 맑음으로 나오고
        (`divination_of`는 밤 시작 상태로 판정), 후보에서 빠진다(`candidate_pool`). 반환: (정정 주장 ID, 사실 문장).
        """
        st, tr = self.st, self.tr
        sec = self.secrets[tr.secrets[npc]]
        wrong = [c for c in st.public.claims if c.speaker == npc and c.subject == npc and c.night == NIGHT0
                 and c.claim_type is ClaimType.WHEREABOUTS and c.slot is sec.slot
                 and not tr.claim_truths[c.claim_id].is_true]
        new_ids: list[str] = []
        for old in wrong or [None]:
            c = self.new_claim(speaker=npc, claim_type=ClaimType.WHEREABOUTS, subject=npc, night=NIGHT0,
                               slot=sec.slot, place=sec.actual_place, corrects=old.claim_id if old else None)
            self.record_claim(c, is_true=True)
            new_ids.append(c.claim_id)
        rt = st.npcs[npc].runtime
        rt.confessed, rt.confessed_day = True, st.day
        corrected = [c.claim_id for c in wrong]
        st.public.confessions.append(ConfessionRecord(st.day, npc, sec.secret_id, corrected))
        self.emit(EventType.CONFESSION, {"npc_id": npc, "secret_id": sec.secret_id, "corrected_claims": corrected,
                                         "claim_ids": new_ids, "grudge_clue_id": sec.grudge_clue_id})
        fact = " ".join(x for x in (sec.confession_line, self.whereabouts_sentence(st.public.claims[-1])) if x)
        return new_ids, fact

    # ---- 투표
    def npc_votes(self, candidates: list[str], round_: str) -> dict[str, str]:
        """vote·runoff 단계에 들어갈 때 NPC 표를 정한다(플레이어 표를 보지 않는다, domain.md 5절 1)."""
        st = self.st
        out = {}
        for npc in st.active:
            if npc == PLAYER_ID:
                continue
            t = self.policy.vote(st, npc, list(candidates), self.rng(f"vote:{round_}:{npc}"))
            if t == npc or t not in candidates:
                raise ValueError(f"NPC 정책이 불법 표를 냄: {npc} -> {t}")
            out[npc] = t
        return out

    def start_vote(self) -> None:
        st = self.st
        st.phase = Phase.VOTE
        st.runoff_candidates = []
        st.pending_votes = self.npc_votes(list(st.active), "main")

    def reveal(self, round_: str) -> dict[str, str]:
        st = self.st
        ballots = {v: st.pending_votes[v] for v in st.active}
        base = sum(1 for v in st.public.votes if v.day == st.day)
        for i, voter in enumerate(st.active):
            st.public.votes.append(VoteRecord(f"v{st.day}_{base + i}", st.day, round_, voter, ballots[voter]))
        st.pending_votes = {}
        return ballots

    def vote(self, cmd: Command) -> None:
        st = self.st
        st.pending_votes[PLAYER_ID] = cmd.target
        voters = list(st.active)
        if st.phase is Phase.VOTE:
            main = self.reveal("main")
            out = resolve_vote(main, voters=voters)
            self.emit(EventType.VOTES_REVEALED, {"round": "main", "votes": main, "counts": out.counts,
                                                 "runoff_candidates": out.runoff_candidates})
            if out.runoff_candidates:
                st.phase = Phase.RUNOFF
                st.runoff_candidates = list(out.runoff_candidates)
                st.pending_votes = self.npc_votes(st.runoff_candidates, "runoff")
                return
        else:
            main = {v.voter: v.target for v in st.public.votes if v.day == st.day and v.round == "main"}
            runoff = self.reveal("runoff")
            out = resolve_vote(main, runoff, voters=voters)
            self.emit(EventType.VOTES_REVEALED, {"round": "runoff", "votes": runoff, "counts": out.counts,
                                                 "runoff_candidates": []})
        self.lock(out.locked)

    def lock(self, locked: Optional[str]) -> None:
        """감금(또는 무감금) → 씐 자 여부 공개 → 종료 판정. 끝나지 않았으면 밤으로 간다."""
        st, tr = self.st, self.tr
        st.runoff_candidates = []
        revealed = None
        if locked is not None:
            st.active.remove(locked)
            st.locked.append(locked)
            revealed = tr.roles[locked] is Role.POSSESSED
        st.public.locks.append(LockRecord(st.day, locked, revealed))
        end = end_reason(st, tr)
        self.emit(EventType.LOCK_RESOLVED, {"locked": locked, "revealed_possessed": revealed,
                                            "end": end.value if end else None})
        if end is not None:
            self.finish(end)
        else:
            self.start_night()

    # ---- 밤
    def start_night(self) -> None:
        st, tr = self.st, self.tr
        st.phase = Phase.NIGHT
        plan = self.policy.night_plan(st, self.rng("night_plan"))
        possessed = [n for n in st.active if tr.roles.get(n) is Role.POSSESSED]
        if plan.executor not in possessed or plan.victim not in st.active or tr.roles.get(plan.victim) is not Role.VILLAGER:
            raise ValueError(f"NPC 정책이 불법 밤 명령을 냄: {plan}")
        tr.night_plans[st.day] = _copy_plan(plan)
        for p in possessed:
            st.npcs[p].private.night_plans[st.day] = _copy_plan(plan)

    def divine(self, cmd: Command) -> None:
        """점 대상을 받아 밤을 일괄 처리한다(domain.md 5절 4).

        1. 상태 고정(점 결과를 밤 시작 상태로 판정) → 2. 행적 → 3. 목격 → 4. 실종 → 5. 종료 판정 →
        6. 점 결과 전달(대상이 그 밤 실종돼도 전달, 확정 결정 ②). 끝나지 않았으면 다음 날 아침 진술.
        """
        st, tr = self.st, self.tr
        night = st.day
        target = cmd.target
        result = night_divination(st, tr, target)                                              # 1
        plan = tr.night_plans[night]
        participants = [n for n in st.active if n != PLAYER_ID]
        where = _night_whereabouts(self.rng("whereabouts"), participants, self.chars, plan)   # 2
        tr.whereabouts[night] = where
        sightings = draw_sightings(self.rng("sightings"), night, participants, where)          # 3
        for n in participants:
            st.npcs[n].private.whereabouts[night] = dict(where[n])
            st.npcs[n].private.witnessed.extend(sightings[n])
        victim = plan.victim                                                                   # 4
        crime = where[victim][Slot.MIDNIGHT]
        st.active.remove(victim)
        st.missing.append(victim)
        st.public.disappearances.append(DisappearanceRecord(night, victim, crime, Slot.MIDNIGHT))
        end = end_reason(st, tr)                                                               # 5
        self.emit(EventType.NIGHT_RESOLVED, {"victim": victim, "end": end.value if end else None})
        st.player.divinations.setdefault(night, {})[target] = result                           # 6
        self.emit(EventType.DIVINATION_DONE, {"target": target, "night": night, "result": result.value})
        if end is not None:
            self.finish(end)
            return
        st.day += 1
        st.phase = Phase.MORNING
        st.player.interrogations_left = LIMITS["interrogations_per_day"]
        st.player.reveals_left_today = LIMITS["divination_reveals_per_day"]
        st.player.talk_used_this_round = False
        claims = self.morning_statements(night, where, sightings, crime)
        self.emit(EventType.MORNING_ANNOUNCED, {"missing": [victim],
                                                "crime_scene": {"place": crime.value, "slot": Slot.MIDNIGHT.value},
                                                "claims": [c.claim_id for c in claims]})

    def morning_statements(self, night: int, where: dict[str, dict[Slot, Place]],
                           sightings: dict[str, list[Sighting]], crime: Place) -> list[Claim]:
        """밤 `night`에 대한 아침 진술(`night_claims`). 실행자는 한밤을 거짓으로 말하고, 거짓 주장은 기색을 판정한다.

        비밀 사건은 0일째 밤에만 있으므로 이후 밤의 비밀 보유자는 사실대로 말한다.
        """
        st, tr = self.st, self.tr
        speakers = [n for n in st.active if n != PLAYER_ID]
        lies = {n: {Slot.MIDNIGHT: p} for n in speakers if (p := self.executor_lie(night, n)) is not None
                and p is not where[n][Slot.MIDNIGHT]}
        claims, truths = night_claims(speakers, night, st.day, where, sightings, lies, tr.roles, crime,
                                      len(st.public.claims) + 1)
        for c in claims:
            st.public.claims.append(c)
            tr.claim_truths[c.claim_id] = truths[c.claim_id]
            if truths[c.claim_id].deliberate_lie:
                st.npcs[c.speaker].private.my_false_claims.append(c.claim_id)
            record_tell(st, c, truths[c.claim_id])
        return claims

    def finish(self, reason: EndReason) -> None:
        st = self.st
        st.phase = Phase.ENDED
        st.turn_order, st.turn_index, st.pending_votes, st.runoff_candidates = [], 0, {}, []
        st.result = GameResult(winner_of(reason), reason, st.day)
        self.emit(EventType.GAME_ENDED, {"winner": st.result.winner.value, "reason": reason.value})


def end_reason(state: GameState, truth: GameTruth) -> Optional[EndReason]:
    """현재 활동 인원으로 `check_end`를 부른다(G = 활동 씐 자, V = 활동 마을 주민 NPC, M = 무당 활동)."""
    g = sum(1 for n in state.active if truth.roles.get(n) is Role.POSSESSED)
    v = sum(1 for n in state.active if truth.roles.get(n) is Role.VILLAGER)
    return check_end(g, v, PLAYER_ID in state.active, PLAYER_ID in state.locked)


def night_divination(state: GameState, truth: GameTruth, target: str) -> DivinationResult:
    """밤 시작 시 대상 상태로 점 결과를 정한다(확정 결정 ②). 탁함 = 씐 자 또는 아직 고백하지 않은 비밀 보유자.

    고백한 주민은 다음 점부터 맑음이다. 밤 처리 중 대상이 실종돼도 이 결과를 그대로 전달한다.
    """
    confessed = target in state.npcs and state.npcs[target].runtime.confessed
    return divination_of(truth.roles[target] is Role.POSSESSED, target in truth.secrets and not confessed)


def record_tell(state: GameState, claim: Claim, claim_truth: ClaimTruth) -> Optional[bool]:
    """주장을 말한 직후 기색을 1회 판정해 화자의 `runtime.tells`에 고정한다. 이미 판정했으면 그 값을 돌려준다.

    공개 기록 `tells`에는 보인 기색(shown=True)만 남긴다. 안 보인 판정을 공개하면 그 주장이 거짓이라는
    진실이 드러나기 때문이다(11/6 PM 결정, domain.md 7절). 판정 대상이 아니면 None.
    난수 키: `{seed}:{말한 날}:tell:{주장 ID}`.
    """
    npc = state.npcs.get(claim.speaker)
    if npc is None:
        return None
    if claim.claim_id in npc.runtime.tells:
        return npc.runtime.tells[claim.claim_id]
    shown = judge_tell(random.Random(f"{state.meta.seed}:{claim.day}:tell:{claim.claim_id}"), claim, claim_truth)
    if shown is None:
        return None
    npc.runtime.tells[claim.claim_id] = shown
    if shown:
        state.public.tells.append(TellRecord(claim.claim_id, claim.day, True))
    return shown


FACT_CLAIMS = (ClaimType.WHEREABOUTS, ClaimType.SIGHTING, ClaimType.NON_SIGHTING)


def judge_tell(rng: random.Random, claim: Claim, claim_truth: ClaimTruth) -> Optional[bool]:
    """기색 판정(기획서 4.2, domain.md 6-1). 사실 주장(행적·목격·비목격)이 거짓일 때만 판정한다.

    판정 대상이 아니면 None, 대상이면 TELL_PROB로 보임(True)/안 보임(False). 엔진은 주장 ID당 한 번만 부른다.
    """
    if claim.claim_type not in FACT_CLAIMS or claim_truth.is_true:
        return None
    return rng.random() < params.TELL_PROB


def can_confess(state: GameState, truth: GameTruth, npc_id: str) -> bool:
    """고백할 수 있는가: 활동 중인 마을 주민이고, 비밀이 있고, 아직 고백하지 않았다. 씐 자는 고백하지 않는다(4.6)."""
    npc = state.npcs.get(npc_id)
    return (npc is not None and npc_id in state.active and truth.roles.get(npc_id) is Role.VILLAGER
            and npc_id in truth.secrets and not npc.runtime.confessed)


def confessed_ids(state: GameState) -> set[str]:
    """고백한 주민(공개 기록 기준). 고백자는 확정 무고로 본다(domain.md 6-1)."""
    return {c.npc_id for c in state.public.confessions}


def candidate_pool(state: GameState, observer: str) -> list[str]:
    """관찰자에게 씐 자 후보인 사람(ID 정렬): 활동 인원 − 무당 − 관찰자 − 고백자(domain.md 6-1).

    공개 기록만 읽는다. 갇힌 사람(감금 공개)·실종자는 활동 인원이 아니므로 이미 빠진다.
    관측 스냅샷 `candidates`(A-06-02)와 투표 안전 층(A-07-02)이 같은 함수를 쓴다.
    """
    out = confessed_ids(state) | {PLAYER_ID, observer}
    return sorted(n for n in state.active if n not in out)


def _night_whereabouts(rng: random.Random, participants: list[str], chars: dict[str, Character],
                       plan: NightPlan) -> dict[str, dict[Slot, Place]]:
    """밤 시작 참가자의 행적. 동선 가중치로 뽑고, 실행자는 한밤에 피해자의 장소로 간다(0일째와 같은 규칙)."""
    where = {r: {s: _routine_place(rng, chars[r], s) for s in SLOTS} for r in participants}
    crime = where[plan.victim][Slot.MIDNIGHT]
    where[plan.executor][Slot.MIDNIGHT] = crime
    if crime in PUBLIC_PLACES:   # 범행 장소 동석 금지
        for r in participants:
            if r not in (plan.victim, plan.executor) and where[r][Slot.MIDNIGHT] is crime:
                where[r][Slot.MIDNIGHT] = _routine_place(rng, chars[r], Slot.MIDNIGHT, exclude=crime)
    return where


# ---------------------------------------------------------------- 엔진
class PaldoEngine:
    """`GameEngine` 포트 구현. `new_game`(A-05-01), `apply`·`player_view`(A-05-02), 밤·고백·기색(A-06-01).

    `policy`는 NPC 결정 규칙이다. 기본은 `BaselinePolicy`이고, 테스트·재현은 정해진 결정을 끼운다.
    """

    def __init__(self, policy: Optional[NpcPolicy] = None):
        self.policy = policy or BaselinePolicy()

    def new_game(self, meta: GameMeta, deps: EngineDeps) -> TransitionResult:
        """판을 만든다: 배정(검증기 통과) → 0일째 밤 행적·목격·홀림 → 1일째 아침 실종 발표와 진술."""
        return self.new_game_from_setup(meta, deps, make_setup(meta.preset, deps.content, meta.seed, meta.game_id))

    def new_game_from_setup(self, meta: GameMeta, deps: EngineDeps, setup: Setup) -> TransitionResult:
        """정해진 배정(`Setup`)으로 1일째 아침 상태를 만든다. `new_game`과 고정 배정 재현이 같이 쓴다."""
        content = deps.content
        preset = meta.preset
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
        for c in claims:                                     # 1일째 아침 진술도 말한 직후 기색 판정(11/6 PM 결정)
            record_tell(state, c, claim_truths[c.claim_id])
        events = [
            DomainEvent(EventType.GAME_STARTED, FIRST_DAY, Phase.MORNING, {"preset_id": preset.preset_id}),
            DomainEvent(EventType.MORNING_ANNOUNCED, FIRST_DAY, Phase.MORNING,
                        {"missing": [victim], "crime_scene": {"place": crime.value, "slot": Slot.MIDNIGHT.value},
                         "claims": [c.claim_id for c in claims]}),
        ]
        return TransitionResult(True, state, truth, events)

    def apply(self, state: GameState, truth: GameTruth, env: CommandEnvelope, deps: EngineDeps) -> TransitionResult:
        """플레이어 명령 1개를 처리하고 다음 플레이어 결정 지점까지 NPC 차례를 진행한다.

        입력 상태·진실은 바꾸지 않는다. 거부면 `accepted=False`, 새 상태 없음, 이벤트 없음, 소비 없음.
        수락이면 `state_version`이 1 오른 새 상태를 돌려준다.
        """
        bad = validate_command(state, truth, env)
        if bad is not None:
            return TransitionResult(False, None, None, [], reject=bad[0], reject_detail=bad[1])
        tx = _Tx(state, truth, deps.content, self.policy)
        cmd = env.command
        k = cmd.kind
        if k is CommandKind.ADVANCE:
            if state.phase is Phase.MORNING:
                tx.start_round(Phase.ROUND1)
            else:                                    # INTERROGATION
                tx.start_round(Phase.ROUND2)
        elif k in _TALK_KINDS:
            tx.player_talk(cmd)
        elif k is CommandKind.INTERROGATE:
            tx.interrogate(cmd)
        elif k is CommandKind.VOTE:
            tx.vote(cmd)
        elif k is CommandKind.DIVINE:
            tx.divine(cmd)
        else:  # pragma: no cover - PHASE_RULES가 막는다
            raise ValueError(k)
        return TransitionResult(True, tx.st, tx.tr, tx.events, render_requests=tx.renders)

    def player_view(self, state: GameState, content: ContentBundle) -> PlayerView:
        """플레이어 화면 정보. 진실·NPC 상태·비공개 표는 넣지 않는다(domain.md 7절)."""
        names = {c.npc_id: c.name_ko for c in content.characters
                 if c.npc_id in set(state.active) | set(state.locked) | set(state.missing)}
        pending = [s.render_job_id for s in state.public.speeches if s.render_job_id and s.line_text is None]
        return PlayerView(
            game_id=state.meta.game_id, state_version=state.state_version, day=state.day, phase=state.phase,
            active=list(state.active), locked=list(state.locked), missing=list(state.missing),
            public=copy.deepcopy(state.public), player=copy.deepcopy(state.player),
            legal_kinds=legal_player_kinds(state),
            awaiting=None if state.result is not None else PLAYER_ID,
            runoff_candidates=list(state.runoff_candidates), result=copy.deepcopy(state.result),
            names=names, pending_render_jobs=pending)

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
