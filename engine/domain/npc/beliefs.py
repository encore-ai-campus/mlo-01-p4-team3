"""NPC 규칙 v0의 믿음 계산(④): 대상별 의심도, 무당 신뢰도, 확정 명제, 압박도(A-07-01). 기획서 v1.1 5.1·5.2(v1.3 4.1·4.2), 3.4.

입력은 관찰자의 `ObservationSnapshot`(공개 기록 ② + 자기 개인 지식 ③ + 직전 믿음 ④)뿐이다. 진실(①)과 다른 NPC 상태는 없다.
의심도는 **공개 기록 전체에서 매번 다시 계산**한다(같은 스냅샷이면 같은 결과, 갱신을 여러 번 해도 결과가 같다).
그래서 반복 의심 키·해명 횟수·모순 지적 결과는 공개 발언 기록에서 세고, 결과를 `Beliefs.repeat_keys`·`reasons`에 남긴다.

- 확정 명제는 **직접 목격**으로만 만든다: 알리바이(`observation.update_beliefs`가 이미 넣음)와 "X는 밤n 시간대s에 장소p에
  있었다"(`was_at`, 자기 목격). 타인 목격 주장은 `others_claim`·`mutual_vouch` 근거(`Reason`, WITNESS 묶음)로만 쓴다.
- "못 봤다"(비목격 주장, 목격 기록 없음)는 부재 증명이 아니다. 명제·모순·의심도 어디에도 쓰지 않는다.
- 같은 (발언자, 대상, 근거) 의심은 한 번만 효과가 있다. 해명은 반복할수록 효과가 줄어든다(k번째 해명 = 첫 효과/k).
  모순 지적이 실제 모순이 아니면 지적한 사람의 신뢰가 떨어진다(무당이면 무당 신뢰도, 주민이면 그 사람 의심도 상승).

가중치는 규칙 v0 잠정값이다(근거 문서 없음, A-08-03 시뮬레이션에서 조정). 성향(`Temperament.group_weights`)을 묶음별로 곱한다.

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..content import Temperament
from ..model import (
    PLAYER_ID, PRESETS, Beliefs, Claim, ClaimType, CommandKind, DivinationResult, EvidenceGroup, EvidenceRef,
    EvidenceSource, EvidenceType, InterrogationKind, ObservationSnapshot, Proposition, PropositionKind,
    PublicRecord, Reason, ReactionIntent,
)
from ..observation import witness_evidence

BASE_SUSPICION = 50.0
BASE_TRUST = 50.0

# 의심도 변화량(잠정, A-08-03에서 조정). 양수 = 더 의심. 묶음별 성향 가중치를 곱한다.
W = {
    "contradiction_subject": 20.0,     # 공개 모순: 행적을 말한 쪽
    "contradiction_witness": 6.0,      # 공개 모순: 목격을 말한 쪽(그쪽이 거짓일 수도 있다)
    "pointed_bonus": 8.0,              # 실제 모순이 지적됨(같은 모순은 한 번만)
    "pointed_failed": 10.0,            # 주민이 모순 아닌 것을 모순이라 지적함 → 지적한 사람
    "own_witness_lie": 35.0,           # 내가 직접 본 것과 다른 행적 주장(그 사람은 거짓을 말했다)
    "claimed_seeing_me_falsely": 35.0, # 내가 없던 곳에서 나를 봤다고 주장
    "at_crime_scene": 8.0,             # 공개 피해 장소·시간대에 있었다고 스스로 말함
    "tell": 12.0,                      # 보인 기색
    "divination_murky": 24.0,          # 무당의 탁함 공개(무당 신뢰도 50 기준, 신뢰도에 비례)
    "divination_clear": -16.0,         # 무당의 맑음 공개(같음)
    "others_claim": -12.0,             # 타인 목격 주장으로 알리바이 근거
    "mutual_vouch": -5.0,              # 상호 보증(담합 가능)
    "not_executor": -15.0,             # 2명 판: 그 밤 실행자가 아님(확정, 후보에서는 빼지 않음)
    "suspected_by_npc": 6.0,           # 주민의 의심 발언(키당 한 번)
    "suspected_by_mudang": 8.0,        # 무당의 의심 발언(무당 신뢰도 50 기준, 신뢰도에 비례)
    "defended": -6.0,                  # 감싸기(키당 한 번)
    "explain_first": -8.0,             # 첫 해명. k번째는 이 값/k
    "react_evade": 8.0,                # 캐묻기에 회피
    "react_counter": 4.0,              # 캐묻기에 역공
    "react_explain": -2.0,             # 캐묻기에 해명
    "defended_revealed": 15.0,         # 감금 공개로 씐 자로 드러난 사람을 감쌌음
    "voted_revealed": -5.0,            # 감금 공개로 씐 자로 드러난 사람에게 투표했음
}
# 무당 신뢰도 변화량(잠정).
TRUST_POINT_FAILED = -15.0       # 무당의 모순 지적이 실제 모순이 아님
TRUST_POINT_OK = 5.0             # 무당의 모순 지적이 실제 모순
TRUST_CLEAR_ON_LIAR = -25.0      # 내가 거짓을 직접 본 사람에게 맑음 공개(거짓말한 사람은 그 밤 점이 탁함이다)
TRUST_SUSPECTED_BY_NPC = -4.0    # 다른 주민의 무당 의심 발언(반복 의심 키당 한 번)

# 압박도(고백 판정, 잠정). 문턱값은 비밀의 `confession_threshold`(콘텐츠)에 성향 confession_ease로 보정한다.
P = {
    "interrogated": 15.0,          # 지난 캐묻기 1회
    "now": 15.0,                   # 지금 받는 캐묻기
    "now_contradiction": 10.0,     # 지금 질문이 모순 추궁
    "now_hidden_slot": 15.0,       # 지금 질문이 내가 숨기는 밤·시간대
    "suspected": 8.0,              # 나에 대한 의심 발언(키당 한 번)
    "suspected_by_mudang": 12.0,
    "contradiction": 15.0,         # 내 주장이 낀 공개 모순
    "tell": 10.0,                  # 내 주장에 보인 기색
    "murky_on_me": 20.0,           # 무당이 나를 탁함으로 공개
}
DEFAULT_CONFESSION_THRESHOLD = 60.0


# ---------------------------------------------------------------- 공개 기록 판정(누구나 같은 결과)
def live_claims(public: PublicRecord) -> list[Claim]:
    """정정되지 않은 주장(뒤 주장의 `corrects`가 가리키는 주장은 뺀다). 공개 기록 순서."""
    corrected = {c.corrects for c in public.claims if c.corrects}
    return [c for c in public.claims if c.claim_id not in corrected]


def _conflict(a: Claim, b: Claim) -> Optional[str]:
    """두 주장이 모순이면 거짓말 의심을 받는 쪽(행적을 말한 사람), 아니면 None. 순서 무관.

    - 행적(X, 밤n, s, P) ↔ 목격(Y가 밤n s에 Q에서 X를 봤다), P≠Q.
    - 같은 사람의 같은 밤·시간대 행적이 장소가 다름(정정 없이).
    "못 봤다"(비목격)와 "목격 기록 없음"은 모순을 만들지 않는다(부재 증명 아님).
    """
    for w, s in ((a, b), (b, a)):
        if w.claim_type is ClaimType.WHEREABOUTS and s.claim_type is ClaimType.SIGHTING:
            if (w.subject in s.seen and w.night == s.night and w.slot is s.slot and w.place is not s.place):
                return w.subject
    if (a.claim_type is ClaimType.WHEREABOUTS and b.claim_type is ClaimType.WHEREABOUTS and a.subject == b.subject
            and a.night == b.night and a.slot is b.slot and a.place is not b.place):
        return a.subject
    return None


def is_contradiction(public: PublicRecord, id_a: str, id_b: str) -> bool:
    """공개 주장 두 개가 실제로 모순인가(정정된 주장은 모순으로 보지 않는다). 모순 지적 성공·실패 판정."""
    live = {c.claim_id: c for c in live_claims(public)}
    if id_a not in live or id_b not in live or id_a == id_b:
        return False
    return _conflict(live[id_a], live[id_b]) is not None


def pointing_succeeded(public: PublicRecord, id_a: str, id_b: str) -> bool:
    """모순 지적의 성공 판정: 두 주장이 **원래** 모순인가(뒤에 고백으로 정정돼도 지적 당시 판정은 바뀌지 않는다)."""
    claims = {c.claim_id: c for c in public.claims}
    if id_a not in claims or id_b not in claims or id_a == id_b:
        return False
    return _conflict(claims[id_a], claims[id_b]) is not None


@dataclass(frozen=True)
class Contradiction:
    """공개 모순 한 쌍. subject는 행적을 말한 사람(거짓말 의심), other는 목격을 말한 사람(같은 사람이면 subject)."""
    claim_a: str
    claim_b: str
    subject: str
    other: str

    @property
    def record_id(self) -> str:
        """`EvidenceType.CONTRADICTION`의 record_id("주장A|주장B", 주장 ID 순)."""
        return f"{self.claim_a}|{self.claim_b}"


def contradictions(public: PublicRecord) -> list[Contradiction]:
    """공개 기록의 모든 모순 쌍(주장 ID 순, 정정된 주장 제외)."""
    live = live_claims(public)
    out: list[Contradiction] = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            subj = _conflict(a, b)
            if subj is None:
                continue
            other = b.speaker if a.subject == subj and a.claim_type is ClaimType.WHEREABOUTS else a.speaker
            x, y = sorted((a.claim_id, b.claim_id))
            out.append(Contradiction(x, y, subj, other))
    return out


def pointed_pairs(public: PublicRecord) -> list[tuple[str, str, tuple[str, str]]]:
    """모순 지적 발언 목록: (speech_id, 발언자, (주장A, 주장B) ID 순). 근거 형식이 안 맞는 발언은 뺀다."""
    out = []
    for sp in public.speeches:
        if sp.kind is not CommandKind.POINT_CONTRADICTION:
            continue
        pair = contradiction_pair(sp.evidence)
        if pair:
            out.append((sp.speech_id, sp.speaker, pair))
    return out


def contradiction_pair(evidence: list[EvidenceRef]) -> Optional[tuple[str, str]]:
    """근거에서 주장 ID 두 개(ID 순). 모순 근거 1개 또는 주장 근거 2개. 형식이 안 맞으면 None."""
    for ref in evidence:
        if ref.type is EvidenceType.CONTRADICTION:
            ids = ref.record_id.split("|")
            return tuple(sorted(ids)) if len(ids) == 2 else None   # type: ignore[return-value]
    ids = [r.record_id for r in evidence if r.type is EvidenceType.CLAIM]
    return tuple(sorted(ids[:2])) if len(ids) >= 2 else None       # type: ignore[return-value]


def repeat_key(speaker: str, target: Optional[str], evidence: list[EvidenceRef]) -> str:
    """반복 의심 키 "발언자|대상|근거"(근거는 "종류:ID"를 정렬해 쉼표로, 없으면 "-"). 기획서 3.4."""
    ev = ",".join(sorted(f"{r.type.value}:{r.record_id}" for r in evidence)) or "-"
    return f"{speaker}|{target or '-'}|{ev}"


def suspicion_speeches(public: PublicRecord, target: str) -> list[tuple[str, str]]:
    """대상에 대한 **효과 있는** 의심 발언 (speech_id, 발언자). 같은 반복 의심 키의 두 번째부터는 뺀다."""
    seen: set[str] = set()
    out = []
    for sp in public.speeches:
        if sp.kind is not CommandKind.SUSPECT or sp.target != target:
            continue
        k = repeat_key(sp.speaker, sp.target, sp.evidence)
        if k in seen:
            continue
        seen.add(k)
        out.append((sp.speech_id, sp.speaker))
    return out


def defend_keys(public: PublicRecord) -> set[str]:
    """지금까지 나온 감싸기의 반복 키(의심 키와 따로 센다: 의심과 감싸기는 반대 행동이다)."""
    return {repeat_key(sp.speaker, sp.target, sp.evidence) for sp in public.speeches
            if sp.kind is CommandKind.DEFEND and sp.target}


def explain_effect(k: int) -> float:
    """k번째(1부터) 해명의 의심도 변화량. 반복할수록 효과가 줄어든다(첫 효과/k)."""
    return W["explain_first"] / k if k >= 1 else 0.0


# ---------------------------------------------------------------- 확정 명제(직접 목격만)
def own_was_at(snap: ObservationSnapshot) -> list[Proposition]:
    """자기 목격으로 확정한 행적 명제 "X는 밤n 시간대s에 장소p에 있었다"(출처 own_witness). 피해자·자신 포함 제외 없음."""
    out = []
    for s in snap.private.witnessed:
        for x in s.seen:
            if x != snap.observer:
                out.append(Proposition(PropositionKind.WAS_AT, x, EvidenceSource.OWN_WITNESS, s.night, s.slot, s.place))
    return out


def _merge_props(props: list[Proposition], more: list[Proposition]) -> list[Proposition]:
    key = lambda p: (p.kind, p.subject, p.source, p.night, p.slot, p.place)   # noqa: E731
    have = {key(p) for p in props}
    out = list(props)
    for p in more:
        if key(p) not in have:
            have.add(key(p))
            out.append(p)
    return out


def cleared_by_proposition(beliefs: Beliefs) -> set[str]:
    """확정 명제로 씐 자가 아님이 확정된 사람(`not_possessed`, 직접 목격 또는 감금 공개). 투표 안전 층이 뺀다."""
    return {p.subject for p in beliefs.propositions
            if p.kind is PropositionKind.NOT_POSSESSED and p.source in (EvidenceSource.OWN_WITNESS,
                                                                          EvidenceSource.LOCK_REVEAL)}


def vote_conflicts(beliefs: Beliefs, target: str) -> bool:
    """마을 추론 위반 판정: 투표 대상이 관찰자의 확정 명제(씐 자 아님)와 충돌하는가(A-07-01 조건 5, 기획서 8.3)."""
    return target in cleared_by_proposition(beliefs)


# ---------------------------------------------------------------- 믿음 계산
@dataclass
class _Acc:
    temperament: Optional[Temperament]
    sus: dict[str, float]
    reasons: list[Reason] = field(default_factory=list)

    def add(self, target: str, group: EvidenceGroup, delta: float, ref: EvidenceRef) -> None:
        if target not in self.sus:
            return
        w = self.temperament.group_weights.get(group, 1.0) if self.temperament else 1.0
        d = round(delta * w, 3)
        self.sus[target] += d
        self.reasons.append(Reason(target, ref, group, d))


def compute_beliefs(snap: ObservationSnapshot, temperament: Optional[Temperament] = None) -> Beliefs:
    """스냅샷에서 믿음 ④를 새로 계산한다(입력은 바꾸지 않는다). 같은 스냅샷이면 같은 결과.

    - suspicion: `snap.candidates`(무당·자신·고백자 제외)마다 0~100. 확정 `not_possessed`인 사람은 0.
    - trust_in_mudang: 50에서 무당의 모순 지적 성공·실패, 거짓말한 사람에게 맑음 공개, 다른 주민의 무당 의심으로 오르내린다.
    - propositions: 직전 믿음의 명제(알리바이) + 자기 목격 `was_at`. 타인 주장은 명제에 넣지 않는다.
    - reasons: 의심도에 실제로 더한 근거(변화량 0이면 남기지 않음). 목격 근거는 WITNESS 묶음.
    - repeat_keys: 지금까지 효과가 있었던 의심 발언의 반복 의심 키(감싸기는 종류별로 따로 중복을 거른다).
    """
    pub = snap.public
    me = snap.observer
    n_total = PRESETS[snap.preset_id].n_possessed if snap.preset_id in PRESETS else 1
    props = _merge_props(snap.beliefs.propositions, own_was_at(snap))
    acc = _Acc(temperament, {c: BASE_SUSPICION for c in snap.candidates})
    claims = {c.claim_id: c for c in pub.claims}

    # 무당 신뢰도: 모순 지적 결과, 거짓말한 사람에게 맑음 공개
    trust = BASE_TRUST
    liars = _own_witness_liars(snap)
    for _sid, speaker, (a, b) in pointed_pairs(pub):
        if speaker == PLAYER_ID:
            trust += TRUST_POINT_OK if pointing_succeeded(pub, a, b) else TRUST_POINT_FAILED
    for c in pub.claims:
        if (c.claim_type is ClaimType.DIVINATION_REVEAL and c.divination_result is DivinationResult.CLEAR
                and any(cl == c.subject and night == c.night for cl, night, _cid in liars)):
            trust += TRUST_CLEAR_ON_LIAR
    trust += TRUST_SUSPECTED_BY_NPC * sum(1 for _sid, sp in suspicion_speeches(pub, PLAYER_ID) if sp != me)
    trust = _clamp(trust)
    tscale = trust / BASE_TRUST

    # 모순
    pointed = {pair for _s, _sp, pair in pointed_pairs(pub)}
    for con in contradictions(pub):
        ref = EvidenceRef(EvidenceType.CONTRADICTION, con.record_id)
        acc.add(con.subject, EvidenceGroup.CONTRADICTION, W["contradiction_subject"], ref)
        if con.other != con.subject:
            acc.add(con.other, EvidenceGroup.CONTRADICTION, W["contradiction_witness"], ref)
        if (con.claim_a, con.claim_b) in pointed:
            acc.add(con.subject, EvidenceGroup.CONTRADICTION, W["pointed_bonus"], ref)
    for sid, speaker, (a, b) in pointed_pairs(pub):
        if speaker != PLAYER_ID and not pointing_succeeded(pub, a, b):
            acc.add(speaker, EvidenceGroup.CONTRADICTION, W["pointed_failed"],
                    EvidenceRef(EvidenceType.CONTRADICTION, f"{a}|{b}"))
    for x, _night, cid in liars:
        acc.add(x, EvidenceGroup.CONTRADICTION, W["own_witness_lie"], EvidenceRef(EvidenceType.CLAIM, cid))
    for c in _false_sightings_of_me(snap):
        acc.add(c.speaker, EvidenceGroup.CONTRADICTION, W["claimed_seeing_me_falsely"],
                EvidenceRef(EvidenceType.CLAIM, c.claim_id))

    # 진술: 피해 장소에 있었다고 스스로 말함
    for rec in pub.disappearances:
        for c in live_claims(pub):
            if (c.claim_type is ClaimType.WHEREABOUTS and c.night == rec.night and c.slot is rec.slot
                    and c.place is rec.place and c.place.value != "home"):
                acc.add(c.subject, EvidenceGroup.STATEMENT, W["at_crime_scene"], EvidenceRef(EvidenceType.CLAIM, c.claim_id))

    # 기색(보인 것만 공개됨)
    for t in pub.tells:
        c = claims.get(t.claim_id)
        if t.shown and c is not None:
            acc.add(c.speaker, EvidenceGroup.TELL, W["tell"], EvidenceRef(EvidenceType.TELL, t.claim_id))

    # 점괘 공개(무당 신뢰도에 비례)
    for c in pub.claims:
        if c.claim_type is ClaimType.DIVINATION_REVEAL and c.speaker == PLAYER_ID:
            base = W["divination_murky"] if c.divination_result is DivinationResult.MURKY else W["divination_clear"]
            acc.add(c.subject, EvidenceGroup.DIVINATION, base * tscale, EvidenceRef(EvidenceType.DIVINATION, c.claim_id))

    # 목격 근거(타인 주장, 확정 아님)
    for p, cid in witness_evidence(pub, me, n_total):
        delta = W["mutual_vouch"] if p.source is EvidenceSource.MUTUAL_VOUCH else W["others_claim"]
        acc.add(p.subject, EvidenceGroup.WITNESS, delta, EvidenceRef(EvidenceType.CLAIM, cid))
    for p in props:
        if p.kind is PropositionKind.NOT_EXECUTOR and p.source is EvidenceSource.OWN_WITNESS:
            acc.add(p.subject, EvidenceGroup.WITNESS, W["not_executor"], EvidenceRef(EvidenceType.MAJORITY, ""))

    # 사회: 의심·감싸기(반복 의심 키당 한 번), 해명(반복 감소), 캐묻기 반응, 감금 공개와 묶인 사람
    keys: list[str] = []
    seen_keys: set[tuple[CommandKind, str]] = set()
    explains: dict[str, int] = {}
    revealed = {lk.locked for lk in pub.locks if lk.locked and lk.revealed_possessed}
    for sp in pub.speeches:
        ref = EvidenceRef(EvidenceType.MAJORITY, sp.speech_id)
        if sp.kind in (CommandKind.SUSPECT, CommandKind.DEFEND) and sp.target:
            k = repeat_key(sp.speaker, sp.target, sp.evidence)
            if (sp.kind, k) in seen_keys:
                continue                                   # 같은 종류·발언자·대상·근거의 반복은 효과 없음
            seen_keys.add((sp.kind, k))
            if sp.kind is CommandKind.SUSPECT:
                keys.append(k)                             # repeat_keys는 의심 키만(감싸기는 defend_keys)
            if sp.kind is CommandKind.SUSPECT and sp.speaker != me:
                d = W["suspected_by_mudang"] * tscale if sp.speaker == PLAYER_ID else W["suspected_by_npc"]
                acc.add(sp.target, EvidenceGroup.SOCIAL, d, ref)
            elif sp.kind is CommandKind.DEFEND and sp.speaker != me:
                acc.add(sp.target, EvidenceGroup.SOCIAL, W["defended"], ref)
            if sp.kind is CommandKind.DEFEND and sp.target in revealed:
                acc.add(sp.speaker, EvidenceGroup.SOCIAL, W["defended_revealed"], ref)
        elif sp.kind is CommandKind.EXPLAIN:
            explains[sp.speaker] = explains.get(sp.speaker, 0) + 1
            acc.add(sp.speaker, EvidenceGroup.STATEMENT, explain_effect(explains[sp.speaker]), ref)
        elif sp.kind is CommandKind.INTERROGATE and sp.reaction is not None:
            d = {ReactionIntent.EVADE: W["react_evade"], ReactionIntent.COUNTER: W["react_counter"],
                 ReactionIntent.EXPLAIN: W["react_explain"]}.get(sp.reaction, 0.0)
            acc.add(sp.speaker, EvidenceGroup.INTERROGATION, d, ref)
    for v in pub.votes:
        if v.target in revealed:
            acc.add(v.voter, EvidenceGroup.SOCIAL, W["voted_revealed"], EvidenceRef(EvidenceType.VOTE, v.vote_id))

    cleared = {p.subject for p in props if p.kind is PropositionKind.NOT_POSSESSED
               and p.source in (EvidenceSource.OWN_WITNESS, EvidenceSource.LOCK_REVEAL)}
    sus = {c: (0.0 if c in cleared else _clamp(v)) for c, v in acc.sus.items()}
    reasons = [r for r in acc.reasons if r.delta != 0.0]
    return Beliefs(suspicion=sus, trust_in_mudang=trust, propositions=props, reasons=reasons, repeat_keys=keys)


def _own_witness_liars(snap: ObservationSnapshot) -> list[tuple[str, int, str]]:
    """내가 직접 본 장소와 다른 행적을 말한 사람: (사람, 밤, 그 행적 주장 ID). 정정된 주장은 뺀다."""
    out = []
    for c in live_claims(snap.public):
        if c.claim_type is not ClaimType.WHEREABOUTS or c.speaker == snap.observer:
            continue
        for s in snap.private.witnessed:
            if c.subject in s.seen and s.night == c.night and s.slot is c.slot and s.place is not c.place:
                out.append((c.subject, c.night, c.claim_id))
                break
    return out


def _false_sightings_of_me(snap: ObservationSnapshot) -> list[Claim]:
    """나를 내가 없던 장소에서 봤다고 한 타인의 목격 주장(내 실제 행적 ③과 비교)."""
    mine = snap.private.whereabouts
    out = []
    for c in live_claims(snap.public):
        if c.claim_type is ClaimType.SIGHTING and c.speaker != snap.observer and snap.observer in c.seen:
            actual = mine.get(c.night, {}).get(c.slot)
            if actual is not None and actual is not c.place:
                out.append(c)
    return out


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, round(x, 3)))


# ---------------------------------------------------------------- 압박도와 고백 문턱값
def pressure(snap: ObservationSnapshot, interrogation_kind: Optional[InterrogationKind] = None,
             night: Optional[int] = None, slot=None) -> float:
    """관찰자에게 쌓인 추궁 압박(공개 기록 + 지금 받는 캐묻기). 고백 판정에 쓴다(기획서 3.5).

    지난 캐묻기 답(내 INTERROGATE 발언), 효과 있는 의심 발언, 내 주장이 낀 공개 모순, 내 주장의 보인 기색,
    나에 대한 탁함 공개를 더한다. 지금 질문(`interrogation_kind`)이 주어지면 그 질문 몫을 더한다.
    숨기는 밤·시간대(`private.hidden_whereabouts`)는 관찰자 자기 개인 지식이다.
    """
    pub = snap.public
    me = snap.observer
    my_claims = {c.claim_id for c in pub.claims if c.speaker == me}
    p = 0.0
    p += P["interrogated"] * sum(1 for s in pub.speeches if s.kind is CommandKind.INTERROGATE and s.speaker == me)
    for _sid, speaker in suspicion_speeches(pub, me):
        p += P["suspected_by_mudang"] if speaker == PLAYER_ID else P["suspected"]
    p += P["contradiction"] * sum(1 for con in contradictions(pub) if con.claim_a in my_claims or con.claim_b in my_claims)
    p += P["tell"] * sum(1 for t in pub.tells if t.shown and t.claim_id in my_claims)
    p += P["murky_on_me"] * sum(1 for c in pub.claims if c.claim_type is ClaimType.DIVINATION_REVEAL
                                and c.subject == me and c.divination_result is DivinationResult.MURKY)
    if interrogation_kind is not None:
        p += P["now"]
        if interrogation_kind is InterrogationKind.CONTRADICTION:
            p += P["now_contradiction"]
        if night is not None and slot is not None and any(
                str(h.get("night")) == str(night) and h.get("slot") == getattr(slot, "value", slot)
                for h in snap.private.hidden_whereabouts):
            p += P["now_hidden_slot"]
    return p


def confession_threshold(snap: ObservationSnapshot, temperament: Optional[Temperament] = None) -> float:
    """고백 문턱값 = 비밀의 기본 문턱값(`private.confession_threshold`, 없으면 60) × (1.25 − 0.5 × confession_ease).

    confession_ease 0.5(보통)면 기본값 그대로, 1이면 0.75배(쉽게 고백), 0이면 1.25배.
    """
    base = snap.private.confession_threshold
    base = DEFAULT_CONFESSION_THRESHOLD if base is None else base
    ease = temperament.confession_ease if temperament else 0.5
    return round(base * (1.25 - 0.5 * ease), 3)
