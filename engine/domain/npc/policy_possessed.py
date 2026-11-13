"""씐 자 NPC 전략 규칙 v0(A-07-01): 거짓 행적, 누명 대상, 밤 대상 혼합, 무당 의심 유도. 기획서 v1.1 5.2(v1.3 4.2).

마을 추론 절차를 쓰지 않고 생존과 누명을 목표로 한다. 입력은 씐 자 관찰자의 `ObservationSnapshot`뿐이다.
- **비밀 보유 정보를 읽지 않는다**: 누가 비밀을 가졌는지(진실)는 스냅샷에 없고, 이 정책은 개인 지식의 비밀 필드
  (`secret_id`·`hidden_whereabouts`·`confession_threshold`)도 읽지 않는다. 누명 대상은 공개 모순·공개 의심으로만 고른다.
- **전략 혼합 비율**(`StrategyMix`: 거짓말 유형·누명 대상·대사 변형·밤 대상)은 판마다 처음 결정할 때 한 번
  `MixSource.mix_for(game_id)`(D-09-02 밴딧이 구현)로 받아 그 판 동안 바꾸지 않는다. 주입이 없으면 균등 비율.
- 거짓 행적은 밤 명령의 `agreed_alibis`로 엔진에 넘긴다: `routine` = 평소 한밤 동선 중 가장 잦은 공공장소,
  `unrefuted` = 비워 두면 엔진이 밤 뒤 자기 목격으로 반박되지 않는 공공장소를 고른다(`engine.executor_alibi`).
  범행 장소는 밤 명령 뒤에 정해지므로, routine 장소가 실제 범행 장소와 같아지면 엔진이 unrefuted 방식으로 대신한다.
  집(HOME)은 쓰지 않는다: 피해 장소가 피해자의 집이면 "집에 있었다"가 공개 기록상 같은 장소가 되어 거짓 판정이 어긋난다.
- 대사 변형(`answer_intent`)은 캐묻기 답의 의도(해명·회피·역공) 비율이다(11/6 PM 결정: 템플릿 변형은 1개라 팔이 될 수
  없고, 표현 다양성은 E-07-07 LLM이 맡는다). 씐 자는 고백하지 않는다(기획서 3.5).
- 창귀 2명 팀 전략(짝 감싸기 등)은 11/5 PM 결정으로 밴딧 대상으로 대체했다. v0는 짝을 투표·누명·밤 대상에서 피하기만 한다.

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..content import ContentBundle
from ..model import (
    PLAYER_ID, PUBLIC_PLACES, Beliefs, ClaimType, Command, CommandKind, EvidenceRef, EvidenceType, NightPlan,
    ObservationSnapshot, ReactionIntent, Slot,
)
from ..observation import Decision, ReasonCode
from . import beliefs as bl
from .policy_villager import RuleBase, vote_refs

FRAME_TALK_PROB = 0.75   # 말 차례에 누명 발언을 시도할 확률(잠정)
EXPLAIN_PROB = 0.6       # 나에 대한 새 의심이 있을 때 해명할 확률(잠정)

LIE_TYPES = ("unrefuted", "routine")
FRAME_TARGETS = ("contradicted", "suspected", "mudang")
ANSWER_INTENTS = ("explain", "evade", "counter")
NIGHT_TARGETS = ("accuser", "witness", "trusts_mudang")


def _uniform(keys: tuple[str, ...]) -> dict[str, float]:
    return {k: 1.0 for k in keys}


@dataclass(frozen=True)
class StrategyMix:
    """씐 자 전략 혼합 비율(가중치, 합이 1일 필요 없음). 판 시작 때 한 번 정해 그 판 동안 고정한다.

    mix_id는 밴딧 팔(arm) 식별자다. 결정의 이유 코드(LIE_*·FRAME_*·REACT_*·NIGHT_*)로 어느 전략을 썼는지 남는다.
    """
    lie_type: dict[str, float] = field(default_factory=lambda: _uniform(LIE_TYPES))
    frame_target: dict[str, float] = field(default_factory=lambda: _uniform(FRAME_TARGETS))
    answer_intent: dict[str, float] = field(default_factory=lambda: _uniform(ANSWER_INTENTS))
    night_target: dict[str, float] = field(default_factory=lambda: _uniform(NIGHT_TARGETS))
    mix_id: str = "uniform"

    def __post_init__(self) -> None:
        for name, keys in (("lie_type", LIE_TYPES), ("frame_target", FRAME_TARGETS),
                           ("answer_intent", ANSWER_INTENTS), ("night_target", NIGHT_TARGETS)):
            w = getattr(self, name)
            bad = set(w) - set(keys)
            if bad or any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
                raise ValueError(f"StrategyMix.{name} 잘못됨: {w}")


class MixSource(Protocol):
    """판별 전략 혼합 비율 공급자(D-09-02 밴딧이 구현). 판마다 한 번만 불린다.

    비율 키(`StrategyMix` 필드 → 팔 이름, 기본값은 모두 1.0 = 균등):
    - lie_type: unrefuted(엔진이 반박 안 되는 공공장소), routine(평소 한밤 공공장소)
    - frame_target: contradicted(공개 모순 주민), suspected(의심도 높은 주민), mudang(무당 의심 유도)
    - answer_intent: explain(해명), evade(회피), counter(역공) — 캐묻기 답 의도(대사 변형)
    - night_target: accuser(지목한 사람), witness(거짓을 봤을 수 있는 사람), trusts_mudang(무당을 따르는 사람)
    """

    def mix_for(self, game_id: str) -> StrategyMix:
        """그 판에 쓸 혼합 비율."""
        ...


class UniformMix:
    """주입이 없을 때의 기본 공급자: 모든 판에 균등 비율."""

    def mix_for(self, game_id: str) -> StrategyMix:
        """균등 비율을 돌려준다."""
        return StrategyMix()


def weighted_pick(weights: dict[str, float], available: list[str], rng: random.Random) -> Optional[str]:
    """가능한 선택지 중 가중치로 하나를 고른다(키 순서 고정). 가능한 것이 없거나 가중치 합이 0이면 None."""
    opts = [k for k in sorted(available) if weights.get(k, 0.0) > 0]
    if not opts:
        return None
    return rng.choices(opts, weights=[weights[k] for k in opts])[0]


class PossessedPolicy(RuleBase):
    """씐 자 규칙 v0. talk·vote·night_plan·react를 구현한다. 고백하지 않는다."""

    def __init__(self, content: Optional[ContentBundle] = None, mix_source: Optional[MixSource] = None):
        super().__init__(content)
        self.mix_source: MixSource = mix_source or UniformMix()
        self._mixes: dict[str, StrategyMix] = {}

    def mix(self, game_id: str) -> StrategyMix:
        """그 판의 혼합 비율. 처음 부를 때 한 번 주입받아 저장하고, 이후에는 공급자를 다시 부르지 않는다."""
        if game_id not in self._mixes:
            m = self.mix_source.mix_for(game_id)
            if not isinstance(m, StrategyMix):
                raise ValueError(f"혼합 비율이 StrategyMix가 아님: {m!r}")
            self._mixes[game_id] = m
        return self._mixes[game_id]

    # ---- 말
    def talk(self, obs: ObservationSnapshot, rng: random.Random) -> Decision:
        """해명(나에 대한 새 의심) → 누명 발언(혼합 비율로 팔 선택) → 침묵."""
        me, legal, pub = obs.observer, set(obs.legal_kinds), obs.public
        mix = self.mix(obs.game_id)
        b = self.update_beliefs(obs)
        if CommandKind.EXPLAIN in legal:
            n_sus = len(bl.suspicion_speeches(pub, me))
            n_exp = sum(1 for s in pub.speeches if s.kind is CommandKind.EXPLAIN and s.speaker == me)
            if n_sus > n_exp and rng.random() < EXPLAIN_PROB:
                return Decision(Command(CommandKind.EXPLAIN, me), [ReasonCode.RULE_EXPLAIN])
        if rng.random() < FRAME_TALK_PROB:
            arms = self._frame_arms(obs, b, legal)
            arm = weighted_pick(mix.frame_target, list(arms), rng)
            if arm is not None:
                return arms[arm]
        return Decision(Command(CommandKind.SILENCE, me), [ReasonCode.RULE_SILENCE])

    def _frame_arms(self, obs: ObservationSnapshot, b: Beliefs, legal: set[CommandKind]) -> dict[str, Decision]:
        """지금 쓸 수 있는 누명 팔과 그 결정. 짝과 자신은 대상이 아니다. 반복 의심 키는 피한다."""
        me, pub = obs.observer, obs.public
        partner = obs.private.partner_id
        used = set(b.repeat_keys)
        pool = {c: v for c, v in b.suspicion.items() if c != partner}
        arms: dict[str, Decision] = {}
        pointed = {pair for _s, _sp, pair in bl.pointed_pairs(pub)}
        cons = [c for c in bl.contradictions(pub) if c.subject in pool]
        for con in sorted(cons, key=lambda c: (-pool[c.subject], c.record_id)):
            ref = EvidenceRef(EvidenceType.CONTRADICTION, con.record_id)
            if CommandKind.POINT_CONTRADICTION in legal and (con.claim_a, con.claim_b) not in pointed:
                cmd = Command(CommandKind.POINT_CONTRADICTION, me, target=con.subject, evidence=[ref])
            elif CommandKind.SUSPECT in legal and bl.repeat_key(me, con.subject, [ref]) not in used:
                cmd = Command(CommandKind.SUSPECT, me, target=con.subject, evidence=[ref])
            else:
                continue
            arms["contradicted"] = Decision(cmd, [ReasonCode.FRAME_CONTRADICTED], [ref])
            break
        if CommandKind.SUSPECT in legal and pool:
            for target in sorted(pool, key=lambda c: (-pool[c], c)):
                if pool[target] <= bl.BASE_SUSPICION:
                    break
                ref = self.best_ref(b, me, target)
                if ref is not None:
                    arms["suspected"] = Decision(Command(CommandKind.SUSPECT, me, target=target, evidence=[ref]),
                                                 [ReasonCode.FRAME_SUSPECTED], [ref])
                    break
            ref = _mudang_ref(obs, used)
            if ref is not None:
                arms["mudang"] = Decision(Command(CommandKind.SUSPECT, me, target=PLAYER_ID, evidence=[ref]),
                                          [ReasonCode.FRAME_MUDANG], [ref])
        return arms

    # ---- 투표
    def vote(self, obs: ObservationSnapshot, candidates: list[str], rng: random.Random) -> Decision:
        """짝을 피하고, 혼합 비율이 무당 팔을 고르고 무당 의심 근거가 있으면 무당, 아니면 의심도(공개 기록 기준) 최댓값."""
        me = obs.observer
        b = self.update_beliefs(obs)
        mix = self.mix(obs.game_id)
        partner = obs.private.partner_id
        legal = [c for c in candidates if c != me]
        codes: list[ReasonCode] = []
        pool = list(legal)
        if partner in pool and len(pool) > 1:
            pool = [c for c in pool if c != partner]
            codes.append(ReasonCode.AVOID_PARTNER)
        ref = _mudang_ref(obs, set())
        avail = ["suspected"] + (["mudang"] if PLAYER_ID in pool and ref is not None else [])
        if weighted_pick(mix.frame_target, avail, rng) == "mudang":
            return Decision(PLAYER_ID, [ReasonCode.FRAME_MUDANG] + codes, [ref])
        npcs = [c for c in pool if c != PLAYER_ID] or pool
        scores = {c: b.suspicion.get(c, bl.BASE_SUSPICION) for c in npcs}
        best = max(scores.values())
        tied = sorted(c for c, v in scores.items() if v == best)
        target = rng.choice(tied) if len(tied) > 1 else tied[0]
        refs = vote_refs(b, target)
        lead = [ReasonCode.FRAME_SUSPECTED] if refs else [ReasonCode.MAX_SUSPICION]
        if len(tied) > 1:
            codes.append(ReasonCode.TIE_SEEDED)
        return Decision(target, lead + codes, refs)

    # ---- 밤
    def night_plan(self, obs: ObservationSnapshot, executors: list[str], victims: list[str],
                   rng: random.Random) -> Decision:
        """실행자(시드), 피해자(밤 대상 묶음을 혼합 비율로 골라 그 안에서 시드), 거짓 행적 유형(혼합 비율)."""
        mix = self.mix(obs.game_id)
        executor = rng.choice(sorted(executors))
        groups = night_groups(obs, victims)
        arm = weighted_pick(mix.night_target, [k for k, v in groups.items() if v], rng)
        codes = [ReasonCode.SEEDED_EXECUTOR]
        if arm is None:
            victim = rng.choice(sorted(victims))
            codes.append(ReasonCode.NIGHT_ANY)
        else:
            victim = rng.choice(sorted(groups[arm]))
            codes.append({"accuser": ReasonCode.NIGHT_ACCUSER, "witness": ReasonCode.NIGHT_WITNESS,
                          "trusts_mudang": ReasonCode.NIGHT_TRUSTS_MUDANG}[arm])
        plan = NightPlan(executor=executor, victim=victim)
        lie = weighted_pick(mix.lie_type, list(LIE_TYPES), rng)
        place = self.routine_place(executor) if lie == "routine" else None
        if place is not None:
            plan.agreed_alibis = {executor: place.value}
            codes.append(ReasonCode.LIE_ROUTINE)
        else:
            codes.append(ReasonCode.LIE_UNREFUTED)
        return Decision(plan, codes)

    def routine_place(self, npc_id: str):
        """인물의 평소 한밤 동선(콘텐츠 공개 정보) 중 가중치가 가장 큰 공공장소. 없으면 None."""
        prefs = self._routine.get(npc_id, {}).get(Slot.MIDNIGHT, {})
        pubs = [(w, p.value, p) for p, w in prefs.items() if p in PUBLIC_PLACES and w > 0]
        return max(pubs)[2] if pubs else None

    # ---- 캐묻기 답
    def react(self, obs: ObservationSnapshot, cmd: Command, rng: random.Random) -> Decision:
        """대사 변형(`answer_intent`) 비율로 해명·회피·역공 중 하나. 고백하지 않는다."""
        mix = self.mix(obs.game_id)
        arm = weighted_pick(mix.answer_intent, list(ANSWER_INTENTS), rng) or "explain"
        intent = {"explain": ReactionIntent.EXPLAIN, "evade": ReactionIntent.EVADE,
                  "counter": ReactionIntent.COUNTER}[arm]
        code = {"explain": ReasonCode.REACT_EXPLAIN, "evade": ReasonCode.REACT_EVADE,
                "counter": ReasonCode.REACT_COUNTER}[arm]
        return Decision(intent, [code])


def _mudang_ref(obs: ObservationSnapshot, used: set[str]) -> Optional[EvidenceRef]:
    """무당을 의심할 공개 근거(무당의 점괘 공개, 무당의 투표 기록) 중 아직 쓰지 않은 것. 없으면 None."""
    pub, me = obs.public, obs.observer
    refs = [EvidenceRef(EvidenceType.DIVINATION, c.claim_id) for c in pub.claims
            if c.claim_type is ClaimType.DIVINATION_REVEAL and c.speaker == PLAYER_ID]
    refs += [EvidenceRef(EvidenceType.VOTE, v.vote_id) for v in pub.votes
             if v.voter == PLAYER_ID and v.target is not None]   # 기권 기록(rule-0.9~)은 근거로 쓰지 않는다
    for ref in refs:
        if bl.repeat_key(me, PLAYER_ID, [ref]) not in used:
            return ref
    return None


def night_groups(obs: ObservationSnapshot, victims: list[str]) -> dict[str, set[str]]:
    """밤 대상 후보 묶음(공개 기록 + 자기 개인 지식만). 각 묶음은 victims 안에서만.

    - accuser: 나·짝을 의심했거나, 나·짝의 주장이 낀 모순을 지적한 사람
    - witness: 내 거짓 주장과 같은 밤·시간대·공공장소에 있었다고 말한 사람(나를 못 봤다는 것을 알 수 있다)
    - trusts_mudang: 같은 날 같은 투표 회차에서 무당과 같은 대상에 투표한 사람
    """
    pub = obs.public
    ours = {obs.observer} | ({obs.private.partner_id} if obs.private.partner_id else set())
    vs = set(victims)
    our_claims = {c.claim_id for c in pub.claims if c.speaker in ours}
    accuser = {s.speaker for s in pub.speeches if s.kind is CommandKind.SUSPECT and s.target in ours}
    for _sid, speaker, (a, b) in bl.pointed_pairs(pub):
        if a in our_claims or b in our_claims:
            accuser.add(speaker)
    claims = {c.claim_id: c for c in pub.claims}
    witness: set[str] = set()
    for cid in obs.private.my_false_claims:
        lie = claims.get(cid)
        if lie is None or lie.place not in PUBLIC_PLACES:
            continue
        witness |= {c.speaker for c in bl.live_claims(pub) if c.claim_type is ClaimType.WHEREABOUTS
                    and c.night == lie.night and c.slot is lie.slot and c.place is lie.place and c.speaker != obs.observer}
    mudang_votes = {(v.day, v.round): v.target for v in pub.votes if v.voter == PLAYER_ID and v.target is not None}
    trusts = {v.voter for v in pub.votes if v.voter != PLAYER_ID and v.target is not None
              and mudang_votes.get((v.day, v.round)) == v.target}
    return {"accuser": accuser & vs, "witness": witness & vs, "trusts_mudang": trusts & vs}
