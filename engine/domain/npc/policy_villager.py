"""마을 주민 NPC 규칙 v0(A-07-01): 말 행동·투표·캐묻기 답(고백 포함). 기획서 v1.1 5.2(v1.3 4.2), 3.4·3.5.

정책은 관찰자의 `ObservationSnapshot`만 받고 `Decision(값, 이유 코드, 근거)`를 돌려준다(A-06-02 시그니처).
믿음은 `beliefs.compute_beliefs`로 스냅샷에서 다시 계산한다. 엔진이 `update_beliefs`를 불러 상태에 저장하면
스냅샷의 믿음과 같은 값이 나온다(같은 입력이면 같은 결과).

- 말: ① 아직 아무도 지적하지 않은 공개 모순 지적 → ② 의심도가 문턱 이상인 후보를 근거와 함께 의심(성향 aggression 확률)
  → ③ 나에 대한 새 의심이 있으면 해명 → ④ 직접 목격으로 알리바이를 아는 사람이 의심받으면 감싸기 → ⑤ 침묵.
- 투표(안전 층 포함): 확정 명제(`not_possessed`)로 무고가 확정된 사람과 고백자는 빼고, 무당은 신뢰도가 아주 낮을 때만 후보.
  남은 후보 중 의심도 + 작은 잡음이 가장 큰 사람. 걸러서 후보가 없으면 합법 후보 전체로 돌아간다.
  투표 3층(성향 가중 확률·동조·잡음)과 의심 모델 연결은 A-07-02가 이 자리를 바꾼다.
- 캐묻기 답: 비밀 보유자는 압박이 고백 문턱값 이상이면 고백, 숨기는 시간대·모순 추궁이면 회피. 그 밖은 성향에 따라 역공 또는 해명.
- 목격 근거(`others_claim`·`mutual_vouch`)를 의심도에 쓴 대상에 대한 결정에만 `observation.witness_basis`를 붙인다.

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
"""
from __future__ import annotations

import copy
import random
from typing import Optional

from ..content import ContentBundle, Temperament
from ..model import (
    PLAYER_ID, Beliefs, Command, CommandKind, EvidenceGroup, EvidenceRef, EvidenceSource, EvidenceType,
    InterrogationKind, ObservationSnapshot, PropositionKind, ReactionIntent,
)
from ..observation import Decision, ReasonCode, witness_basis
from . import beliefs as bl

SUSPECT_MIN = 62.0          # 의심 발언을 고려하는 의심도(잠정)
VOTE_NOISE = 2.0            # 투표 점수 잡음 폭(±, 잠정)
MUDANG_VOTE_TRUST = 20.0    # 무당 신뢰도가 이 값 미만이면 무당도 투표 후보(잠정)
MAX_VOTE_EVIDENCE = 3       # 투표 결정에 싣는 근거 수


class RuleBase:
    """규칙 v0 정책 공통: 성향 조회, 믿음 계산, 근거 고르기, 목격 근거 표시."""

    version = "rule-v0"

    def __init__(self, content: Optional[ContentBundle] = None):
        temps = {t.temperament_id: t for t in content.temperaments} if content else {}
        self._temperament: dict[str, Temperament] = {
            c.npc_id: temps[c.temperament_id] for c in (content.characters if content else [])
            if c.temperament_id in temps}
        self._routine = {c.npc_id: c.routine for c in (content.characters if content else [])}
        self._last: Optional[tuple[ObservationSnapshot, Beliefs]] = None

    def temperament(self, npc_id: str) -> Optional[Temperament]:
        """인물의 성향(콘텐츠 공개 정보). 콘텐츠가 없거나 모르는 인물이면 None(가중치 1.0)."""
        return self._temperament.get(npc_id)

    def update_beliefs(self, obs: ObservationSnapshot) -> Beliefs:
        """믿음 갱신(④). 엔진이 결정 직전에 불러 `state.npcs[npc].beliefs`와 스냅샷에 넣는다.

        결정 1회 안에서 **같은 스냅샷 객체**로 다시 부르면 저장해 둔 결과를 쓴다(엔진은 갱신과 행동 선택에 같은 객체를
        넘긴다). ID가 같아도 다른 객체(분기·재연)면 새로 계산한다.
        """
        if self._last is not None and self._last[0] is obs:
            return copy.deepcopy(self._last[1])
        out = bl.compute_beliefs(obs, self.temperament(obs.observer))
        self._last = (obs, copy.deepcopy(out))
        return out

    @staticmethod
    def best_ref(b: Beliefs, me: str, target: str) -> Optional[EvidenceRef]:
        """대상을 의심하게 만든 근거 중 변화량이 가장 크고 아직 쓰지 않은 반복 의심 키의 근거. 없으면 None."""
        used = set(b.repeat_keys)
        pos = sorted((r for r in b.reasons if r.target == target and r.delta > 0),
                     key=lambda r: (-r.delta, r.evidence.type.value, r.evidence.record_id))
        for r in pos:
            ref = _public_ref(r.evidence)
            if ref is not None and bl.repeat_key(me, target, [ref]) not in used:
                return ref
        return None

    @staticmethod
    def with_witness(obs: ObservationSnapshot, b: Beliefs, target: str, value, codes: list[ReasonCode],
                     evidence: list[EvidenceRef]) -> Decision:
        """결정을 만든다. 대상의 의심도에 타인 목격 근거를 썼으면 witness_* 코드와 주장 근거를 붙인다(안 썼으면 붙이지 않음)."""
        used = any(r.target == target and r.group is EvidenceGroup.WITNESS and r.evidence.type is EvidenceType.CLAIM
                   for r in b.reasons)
        codes, evidence = list(codes), list(evidence)
        if used:
            wc, wr = witness_basis(obs, target)
            codes += [c for c in wc if c not in codes]
            evidence += [r for r in wr if all((r.type, r.record_id) != (e.type, e.record_id) for e in evidence)]
        return Decision(value, codes, evidence)


def _public_ref(ref: EvidenceRef) -> Optional[EvidenceRef]:
    """말 명령에 실을 수 있는 공개 근거만(빈 ID의 다수 의견 자리표시는 뺀다)."""
    if ref.type is EvidenceType.MAJORITY and not ref.record_id:
        return None
    return ref


def _decision(value, codes: list[ReasonCode], evidence: Optional[list[EvidenceRef]] = None) -> Decision:
    return Decision(value, codes, list(evidence or []))


def _top(scores: dict[str, float], rng: random.Random) -> tuple[str, bool]:
    """점수 최댓값 후보(동률이면 ID 정렬 뒤 시드로). (대상, 동률 여부)."""
    best = max(scores.values())
    tied = sorted(c for c, v in scores.items() if v == best)
    return (rng.choice(tied) if len(tied) > 1 else tied[0]), len(tied) > 1


class VillagerPolicy(RuleBase):
    """마을 주민(비밀 보유자 포함) 규칙 v0. `NpcPolicy`의 talk·vote·react를 구현한다(night_plan 없음)."""

    def talk(self, obs: ObservationSnapshot, rng: random.Random) -> Decision:
        """회의 말 행동: 모순 지적 → 의심 → 해명 → 감싸기 → 침묵 순으로 처음 해당하는 것."""
        me, legal = obs.observer, set(obs.legal_kinds)
        b = self.update_beliefs(obs)
        temp = self.temperament(me)
        pub = obs.public
        if CommandKind.POINT_CONTRADICTION in legal:
            pointed = {pair for _s, _sp, pair in bl.pointed_pairs(pub)}
            cons = [c for c in bl.contradictions(pub) if c.subject in b.suspicion
                    and (c.claim_a, c.claim_b) not in pointed]
            if cons:
                con = max(cons, key=lambda c: (b.suspicion[c.subject], c.record_id))
                ref = EvidenceRef(EvidenceType.CONTRADICTION, con.record_id)
                cmd = Command(CommandKind.POINT_CONTRADICTION, me, target=con.subject, evidence=[ref])
                return _decision(cmd, [ReasonCode.RULE_POINT_CONTRADICTION], [ref])
        if CommandKind.SUSPECT in legal and b.suspicion:
            target, _ = _top(b.suspicion, rng)
            aggression = temp.aggression if temp else 0.5
            if b.suspicion[target] >= SUSPECT_MIN and rng.random() < 0.4 + 0.6 * aggression:
                ref = self.best_ref(b, me, target)
                if ref is not None:
                    cmd = Command(CommandKind.SUSPECT, me, target=target, evidence=[ref])
                    return self.with_witness(obs, b, target, cmd, [ReasonCode.RULE_SUSPECT], [ref])
        if CommandKind.EXPLAIN in legal:
            n_sus = len(bl.suspicion_speeches(pub, me))
            n_exp = sum(1 for s in pub.speeches if s.kind is CommandKind.EXPLAIN and s.speaker == me)
            if n_sus > n_exp:
                return _decision(Command(CommandKind.EXPLAIN, me), [ReasonCode.RULE_EXPLAIN])
        if CommandKind.DEFEND in legal:
            got = self._defend(obs, b)
            if got is not None:
                return got
        return _decision(Command(CommandKind.SILENCE, me), [ReasonCode.RULE_SILENCE])

    def _defend(self, obs: ObservationSnapshot, b: Beliefs) -> Optional[Decision]:
        """직접 목격으로 알리바이(씐 자 아님·실행자 아님)를 아는 후보가 의심받으면, 그를 본 내 공개 목격 주장을 근거로 감싼다."""
        me, pub = obs.observer, obs.public
        alibi = {p.subject: p for p in b.propositions if p.source is EvidenceSource.OWN_WITNESS
                 and p.kind in (PropositionKind.NOT_POSSESSED, PropositionKind.NOT_EXECUTOR)}
        used = bl.defend_keys(pub)
        for x in sorted(alibi):
            if x not in b.suspicion or not bl.suspicion_speeches(pub, x):
                continue
            p = alibi[x]
            mine = [c for c in bl.live_claims(pub) if c.speaker == me and c.claim_type.value == "sighting"
                    and x in c.seen and c.night == p.night and c.slot is p.slot]
            for c in mine:
                ref = EvidenceRef(EvidenceType.CLAIM, c.claim_id)
                if bl.repeat_key(me, x, [ref]) not in used:
                    return _decision(Command(CommandKind.DEFEND, me, target=x, evidence=[ref]),
                                     [ReasonCode.RULE_DEFEND], [ref])
        return None

    def vote(self, obs: ObservationSnapshot, candidates: list[str], rng: random.Random) -> Decision:
        """안전 층(확정 명제·고백자·무당 신뢰도) → 의심도 + 잡음 최댓값. 근거는 대상 의심도를 올린 기록."""
        me = obs.observer
        b = self.update_beliefs(obs)
        legal = [c for c in candidates if c != me]
        codes: list[ReasonCode] = []
        pool = list(legal)
        cleared = bl.cleared_by_proposition(b)
        if any(c in cleared for c in pool):
            rest = [c for c in pool if c not in cleared]
            if rest:
                pool = rest
                codes.append(ReasonCode.EXCLUDE_PROPOSITION)
            else:                                          # 모두 확정 무고(결선 등): 대체 처리(기획서 4.2 안전 층)
                codes.append(ReasonCode.FALLBACK_ALL_LEGAL)
        allowed = set(obs.candidates) | {PLAYER_ID}
        if any(c not in allowed for c in pool) and [c for c in pool if c in allowed]:
            pool = [c for c in pool if c in allowed]
            codes.append(ReasonCode.SKIP_CONFESSED)
        if PLAYER_ID in pool and b.trust_in_mudang >= MUDANG_VOTE_TRUST and len(pool) > 1:
            pool = [c for c in pool if c != PLAYER_ID]
        if not pool:
            pool = list(legal)
            codes.append(ReasonCode.FALLBACK_ALL_LEGAL)
        scores = {c: (100.0 - b.trust_in_mudang if c == PLAYER_ID else b.suspicion.get(c, bl.BASE_SUSPICION))
                  + rng.uniform(-VOTE_NOISE, VOTE_NOISE) for c in pool}
        target, tied = _top(scores, rng)
        codes.insert(0, ReasonCode.MAX_SUSPICION)
        if tied:
            codes.append(ReasonCode.TIE_SEEDED)
        refs = vote_refs(b, target)
        return self.with_witness(obs, b, target, target, codes, refs)

    def react(self, obs: ObservationSnapshot, cmd: Command, rng: random.Random) -> Decision:
        """캐묻기 답: 비밀 보유자는 압박 ≥ 문턱값이면 고백, 숨기는 시간대·모순 추궁이면 회피. 아니면 역공 또는 해명."""
        me = obs.observer
        temp = self.temperament(me)
        confessed = any(c.npc_id == me for c in obs.public.confessions)
        if obs.private.secret_id is not None and not confessed:
            pr = bl.pressure(obs, cmd.interrogation_kind, cmd.night, cmd.slot)
            if pr >= bl.confession_threshold(obs, temp):
                return _decision(ReactionIntent.CONFESS, [ReasonCode.PRESSURE_CONFESS])
            hidden = any(str(h.get("night")) == str(cmd.night) and cmd.slot is not None
                         and h.get("slot") == cmd.slot.value for h in obs.private.hidden_whereabouts)
            if hidden or cmd.interrogation_kind is InterrogationKind.CONTRADICTION:
                return _decision(ReactionIntent.EVADE, [ReasonCode.REACT_EVADE])
        if temp is not None and temp.aggression >= 0.6 and bl.suspicion_speeches(obs.public, me):
            return _decision(ReactionIntent.COUNTER, [ReasonCode.REACT_COUNTER])
        return _decision(ReactionIntent.EXPLAIN, [ReasonCode.REACT_EXPLAIN])


def vote_refs(b: Beliefs, target: str) -> list[EvidenceRef]:
    """투표 대상의 의심도를 올린 근거 상위 몇 개(변화량 큰 순, 같은 근거는 한 번)."""
    out: list[EvidenceRef] = []
    for r in sorted((r for r in b.reasons if r.target == target and r.delta > 0),
                    key=lambda r: (-r.delta, r.evidence.type.value, r.evidence.record_id)):
        ref = _public_ref(r.evidence)
        if ref is not None and all((ref.type, ref.record_id) != (e.type, e.record_id) for e in out):
            out.append(ref)
        if len(out) >= MAX_VOTE_EVIDENCE:
            break
    return out
