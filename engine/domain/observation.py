"""관측 투영·믿음 갱신·결정 기록(A-06-02). 기획서 v1.1 5.1(네 층 분리)·5.2 끝·8.2, events.md 3절.

처리 순서: **관측 투영**(① 진실 → ③ 개인 지식은 이미 `state.npcs[npc].private`에 투영돼 있고, ② 공개 기록과 함께
복사) → **믿음 갱신**(④: 알리바이 확정 명제와 타인 목격 주장 근거) → **행동 선택**(정책은 `ObservationSnapshot`만 받는다).

이 모듈의 함수는 `GameTruth`를 받지 않는다. 입력은 `GameState`(공개 기록과 관찰자 자기 상태)뿐이고, 스냅샷에는
관찰자 자신의 ③·④만 들어간다(domain.md 7절). 진실을 읽는 `truth_revealed` 본문은 엔진(`engine.py`)이 판 종료 때만 만든다.

계층: 도메인. 표준 라이브러리와 engine 패키지만 사용한다.
"""
from __future__ import annotations

import copy
import enum
from dataclasses import dataclass, field
from typing import Any, Union

from . import serde
from .model import (
    PLAYER_ID, ClaimType, Command, CommandKind, Beliefs, EvidenceGroup, EvidenceRef, EvidenceSource, EvidenceType,
    GameState, NightPlan, ObservationSnapshot, PrivateKnowledge, Proposition, PublicRecord, PUBLIC_PLACES,
    ReactionIntent, Reason, alibi_proposition_kind, alibi_propositions,
)


# ---------------------------------------------------------------- 이유 코드와 결정
class ReasonCode(str, enum.Enum):
    """NPC 결정의 선택 이유 코드(기획서 v1.1 5.2 끝). 복기와 학습에 쓴다. 새 정책은 코드를 여기에 더한다."""
    BASELINE_SILENCE = "baseline_silence"          # 임시 규칙: 말 차례 침묵
    BASELINE_EXPLAIN = "baseline_explain"          # 임시 규칙: 캐묻기 답은 해명
    MAX_SUSPICION = "max_suspicion"                # 후보 중 의심도 최댓값
    TIE_SEEDED = "tie_seeded"                      # 최댓값 동률을 시드로 고름
    SKIP_CONFESSED = "skip_confessed"              # 고백자를 후보에서 뺌
    AVOID_PARTNER = "avoid_partner"                # 씐 자: 짝을 피함
    FALLBACK_ALL_LEGAL = "fallback_all_legal"      # 뺄 후보를 빼니 남는 후보가 없어 합법 후보 전체로
    SEEDED_EXECUTOR = "seeded_executor"            # 밤: 실행자를 시드로 고름
    SEEDED_VICTIM = "seeded_victim"                # 밤: 피해자를 시드로 고름
    SCRIPTED = "scripted"                          # 테스트·재현용으로 정해 준 결정
    WITNESS_OTHERS_CLAIM = "witness_others_claim"  # 근거: 타인의 공개 목격 주장(알리바이 근거, 확정 아님)
    WITNESS_MUTUAL_VOUCH = "witness_mutual_vouch"  # 근거: 서로 본 두 사람의 상호 보증(담합일 수 있음)


# 근거 기록이 없어도 되는 이유 코드(조건 4 "근거 ID"는 근거가 있을 때만 요구, 11/6 PM 결정).
# 이 코드만으로 된 결정은 evidence=[]일 수 있다. 그 밖의 결정은 근거 ID를 1개 이상 싣는다.
NO_EVIDENCE_CODES: frozenset[ReasonCode] = frozenset({
    ReasonCode.BASELINE_SILENCE, ReasonCode.BASELINE_EXPLAIN, ReasonCode.MAX_SUSPICION, ReasonCode.TIE_SEEDED,
    ReasonCode.SKIP_CONFESSED, ReasonCode.AVOID_PARTNER, ReasonCode.FALLBACK_ALL_LEGAL,
    ReasonCode.SEEDED_EXECUTOR, ReasonCode.SEEDED_VICTIM, ReasonCode.SCRIPTED,
})
SOURCE_CODE: dict[EvidenceSource, ReasonCode] = {
    EvidenceSource.OTHERS_CLAIM: ReasonCode.WITNESS_OTHERS_CLAIM,
    EvidenceSource.MUTUAL_VOUCH: ReasonCode.WITNESS_MUTUAL_VOUCH,
}


DecisionValue = Union[Command, str, ReactionIntent, NightPlan]


@dataclass
class Decision:
    """정책이 돌려주는 결정. value는 행동(말 명령·투표 대상·답변 의도·밤 명령), 이유 코드는 1개 이상.

    evidence는 결정의 근거 기록 ID(공개 기록·관찰자 자기 목격). 근거가 없는 결정(침묵 등)은 빈 목록이다.
    모든 결정은 `decision` 이벤트에서 같은 `snapshot_id`의 관측 스냅샷을 가리킨다.
    """
    value: DecisionValue
    reason_codes: list[ReasonCode]
    evidence: list[EvidenceRef] = field(default_factory=list)


# ---------------------------------------------------------------- 공개 기록에서 읽는 판정
def confessed_ids(state: GameState) -> set[str]:
    """고백한 주민(공개 기록 기준). 고백자는 확정 무고로 본다(domain.md 6-1)."""
    return {c.npc_id for c in state.public.confessions}


def candidate_pool(state: GameState, observer: str) -> list[str]:
    """관찰자에게 씐 자 후보인 사람(ID 정렬): 활동 인원 − 무당 − 관찰자 − 고백자(domain.md 6-1).

    공개 기록만 읽는다. 갇힌 사람(감금 공개)·실종자는 활동 인원이 아니므로 이미 빠진다.
    관측 스냅샷 `candidates`와 투표 안전 층(A-07-02)이 같은 함수를 쓴다.
    """
    out = confessed_ids(state) | {PLAYER_ID, observer}
    return sorted(n for n in state.active if n not in out)


def possessed_remaining(state: GameState) -> int:
    """감금 공개로 알려진 남은 창귀 수 = 프리셋 창귀 수 − 감금 공개에서 씐 자로 드러난 수."""
    return state.meta.preset.n_possessed - sum(1 for lk in state.public.locks if lk.revealed_possessed)


# ---------------------------------------------------------------- 믿음 갱신(④)
def witness_evidence(public: PublicRecord, observer: str, n_possessed_total: int) -> list[tuple[Proposition, str]]:
    """타인의 공개 목격 주장으로 만든 알리바이 **근거**(확정 명제 아님, 기획서 v1.1 5.1). (근거 명제, 주장 ID) 목록.

    `alibi_propositions`와 같은 계산(공개 피해 장소·시간대에 다른 공공장소에서 목격됨)을 공개 목격 주장에 한다.
    두 사람이 같은 밤·시간대·장소에서 서로를 봤다고 주장하면 `mutual_vouch`(공범 담합일 수 있음), 아니면 `others_claim`.
    관찰자 자신의 주장, 피해자·관찰자·화자 자신을 대상으로 한 목격은 뺀다. 반환은 실종 기록·주장 순서.
    """
    kind = alibi_proposition_kind(n_possessed_total)
    sightings = [c for c in public.claims if c.claim_type is ClaimType.SIGHTING]
    out: list[tuple[Proposition, str]] = []
    for rec in public.disappearances:
        for c in sightings:
            if c.speaker == observer or c.night != rec.night or c.slot is not rec.slot:
                continue
            if c.place is rec.place or c.place not in PUBLIC_PLACES:
                continue
            for y in c.seen:
                if y in (rec.victim, observer, c.speaker):
                    continue
                vouch = any(o.speaker == y and o.night == c.night and o.slot is c.slot and o.place is c.place
                            and c.speaker in o.seen for o in sightings)
                src = EvidenceSource.MUTUAL_VOUCH if vouch else EvidenceSource.OTHERS_CLAIM
                out.append((Proposition(kind, y, src, rec.night, rec.slot, c.place), c.claim_id))
    return out


def witness_basis(snap: ObservationSnapshot, target: str) -> tuple[list[ReasonCode], list[EvidenceRef]]:
    """대상에 대한 타인 목격 근거의 이유 코드(`witness_others_claim`·`witness_mutual_vouch`)와 주장 ID 근거.

    목격 근거를 실제로 쓴 정책(A-07-01 규칙 v0 의심도 등)만 결정에 붙인다. 쓰지 않은 근거를 붙이면
    복기·학습 기록이 사실과 달라진다. 출처·주장만 보므로 명제 종류(창귀 수)는 쓰지 않는다.
    """
    codes: list[ReasonCode] = []
    refs: list[EvidenceRef] = []
    for p, cid in witness_evidence(snap.public, snap.observer, 1):
        if p.subject != target:
            continue
        if SOURCE_CODE[p.source] not in codes:
            codes.append(SOURCE_CODE[p.source])
        if all(r.record_id != cid for r in refs):
            refs.append(EvidenceRef(EvidenceType.CLAIM, cid))
    return codes, refs


def update_beliefs(beliefs: Beliefs, public: PublicRecord, private: PrivateKnowledge,
                   n_possessed_total: int, observer: str) -> Beliefs:
    """믿음을 갱신한 **새** `Beliefs`를 돌려준다(입력은 바꾸지 않는다).

    - 확정 명제: 공개된 실종 기록마다 `alibi_propositions`(공개 피해 장소 + 자기 목격). 2명 판은 not_executor까지만.
    - 근거: 타인의 공개 목격 주장(`witness_evidence`)을 `Reason(group=WITNESS, delta=0.0)`으로 기록만 한다.
      delta=0.0은 자리표시다. 의심도에 주는 가중치는 규칙 v0 의심도(A-07-01)가 정한다(11/6 PM 결정).
      others_claim·mutual_vouch 구분은 `Reason`에 없고, 결정의 이유 코드(`witness_basis`)로 남긴다.
    같은 명제·근거는 한 번만 넣는다. 진실(①)은 입력에 없다.
    """
    out = copy.deepcopy(beliefs)
    have = {(p.kind, p.subject, p.night, p.source) for p in out.propositions}
    for rec in public.disappearances:
        for p in alibi_propositions(rec, private.witnessed, n_possessed_total, observer):
            key = (p.kind, p.subject, p.night, p.source)
            if key not in have:
                have.add(key)
                out.propositions.append(p)
    seen = {(r.target, r.evidence.record_id) for r in out.reasons if r.evidence.type is EvidenceType.CLAIM}
    for p, cid in witness_evidence(public, observer, n_possessed_total):
        target = p.subject
        if (target, cid) not in seen:
            seen.add((target, cid))
            out.reasons.append(Reason(target, EvidenceRef(EvidenceType.CLAIM, cid), EvidenceGroup.WITNESS, 0.0))
    return out


# ---------------------------------------------------------------- 관측 투영과 스냅샷
def observe_npc(state: GameState, npc_id: str, legal_kinds: list[CommandKind],
                snapshot_id: str) -> tuple[ObservationSnapshot, Beliefs]:
    """행동 직전 관측: 투영(②+③ 복사) → 믿음 갱신(④) → 스냅샷. (스냅샷, 갱신된 믿음)을 돌려준다.

    입력 상태는 바꾸지 않는다. 엔진은 갱신된 믿음을 `state.npcs[npc_id].beliefs`에 쓰고, 스냅샷을 정책에 넘긴다.
    스냅샷의 private·beliefs는 관찰자 자신의 것뿐이고, 다른 NPC 상태와 진실은 들어가지 않는다.
    """
    npc = state.npcs[npc_id]
    public = copy.deepcopy(state.public)
    private = copy.deepcopy(npc.private)
    beliefs = update_beliefs(npc.beliefs, public, private, state.meta.preset.n_possessed, npc_id)
    snap = ObservationSnapshot(
        snapshot_id=snapshot_id, game_id=state.meta.game_id, observer=npc_id, day=state.day, phase=state.phase,
        state_version=state.state_version, public=public, private=private, beliefs=copy.deepcopy(beliefs),
        legal_kinds=list(legal_kinds), candidates=candidate_pool(state, npc_id),
        n_possessed_remaining=possessed_remaining(state), preset_id=state.meta.preset.preset_id)
    return snap, beliefs


def snapshot_id_of(game_id: str, state_version: int, n: int, observer: str, origin: str = "d") -> str:
    """스냅샷 ID. 같은 판·상태 버전 안에서 n(전이 안 관측 순번)으로 구분한다. 같은 입력이면 같은 ID(재현).

    origin: "d" = 결정 직전 관측(엔진 전이 안), "q" = 포트 `observe` 조회. 접두가 달라 두 ID가 겹치지 않는다.
    """
    return f"snap:{game_id}:v{state_version}:{origin}{n:03d}:{observer}"


# ---------------------------------------------------------------- 이벤트 본문
def observation_payload(snap: ObservationSnapshot) -> dict[str, Any]:
    """`observation` 이벤트 본문: 스냅샷 ID·관찰자와 스냅샷 전체(events.md 3절)."""
    return {"snapshot_id": snap.snapshot_id, "observer": snap.observer, "snapshot": serde.to_jsonable(snap)}


def decision_payload(actor: str, snapshot_id: str, kind: str, target: Any, d: Decision,
                     policy_version: str) -> dict[str, Any]:
    """`decision` 이벤트 본문. 관측과 같은 snapshot_id, 이유 코드(1개 이상)와 근거 ID를 싣는다.

    kind는 행동 종류 문자열: 말·투표·밤은 `CommandKind` 값, 캐묻기 답변 의도는 `"react"`.
    근거가 없으면 이유 코드가 모두 `NO_EVIDENCE_CODES` 안에 있어야 한다.
    """
    if not d.reason_codes:
        raise ValueError(f"이유 코드 없는 결정: {actor} {kind}")
    if not d.evidence and not set(d.reason_codes) <= NO_EVIDENCE_CODES:
        raise ValueError(f"근거가 필요한 이유 코드인데 근거 없음: {actor} {[r.value for r in d.reason_codes]}")
    return {"actor": actor, "snapshot_id": snapshot_id, "kind": kind, "target": serde.to_jsonable(target),
            "reason_codes": [r.value for r in d.reason_codes], "evidence": serde.to_jsonable(d.evidence),
            "policy_version": policy_version}
