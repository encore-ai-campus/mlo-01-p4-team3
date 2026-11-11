"""복기용 이유 조회 `review_trace`와 템플릿 해설(E-10-10). 기획서 v1.1 5.2 끝(이유 코드)·8장(복기).

입력은 엔진이 행동 시점에 낸 `decision` 이벤트(아웃박스에 남는다)와 근거 ID를 풀 때만 쓰는 종료 시점 공개 기록이다.
현재 믿음·상태에서 이유를 다시 계산하지 않는다(사후 합리화 금지). 엔진 상태·난수는 읽지도 바꾸지도 않는다.

행동과 발언의 연결(종류별 규칙):
- 회의 말(suspect·defend·point_contradiction·explain·silence): 같은 전이(state_version) 안에서 그 결정 뒤에 나온
  첫 `speech_made` 중 화자가 같은 것.
- 캐묻기 답(kind="react"): 같은 전이 안에서 그 결정 뒤의 첫 `interrogation_answered`.
- 투표·밤: 발언이 없다(`speech_id=None`). 결정 자체를 복기 항목으로 둔다.

계층: 도메인. 표준 라이브러리와 engine.domain만 import한다.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .model import (PLAYER_ID, ClaimType, CommandKind, EvidenceRef, EvidenceType, Place, Polarity,
                    PrivateKnowledge, PublicRecord)
from .observation import NO_EVIDENCE_CODES, ReasonCode

TALK_KINDS = frozenset(k.value for k in (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.POINT_CONTRADICTION,
                                          CommandKind.EXPLAIN, CommandKind.SILENCE))
REACT_KIND = "react"
PHRASE_PREFIX = "rv_"
_NO_EVIDENCE = frozenset(c.value for c in NO_EVIDENCE_CODES)


@dataclass(frozen=True)
class TraceEvent:
    """응용이 아웃박스 봉투에서 뽑아 넘기는 필드만. 가명·세션·명령 ID는 넣지 않는다."""
    seq: int
    day: int
    phase: str
    state_version: int
    event_type: str
    payload: dict[str, Any]


@dataclass
class TraceInput:
    """review_trace 입력: 판 ID·규칙 버전·시드와 아웃박스 이벤트."""
    game_id: str
    rule_version: str
    seed: int
    events: list[TraceEvent]
    public: Optional[PublicRecord] = None    # 근거 ID 해소용(종료 시점). 이유·행동 판단에는 쓰지 않는다


@dataclass
class ReviewTraceItem:
    """복기 항목 하나(NPC 결정 하나). 이유 코드·근거·문구 ID와 이어진 발언."""
    item_id: str                             # "d<seq>"
    day: int
    phase: str
    actor: str
    kind: str                                # suspect|defend|point_contradiction|explain|silence|vote|night_plan|react
    reason_codes: list[str]
    evidence: list[EvidenceRef]
    policy_version: str
    phrase_id: str
    target_npc: Optional[str] = None
    reaction: Optional[str] = None           # react일 때 반응 의도
    executor: Optional[str] = None           # night_plan일 때
    victim: Optional[str] = None
    speech_id: Optional[str] = None


@dataclass
class ReviewTrace:
    """한 판의 복기 항목 목록."""
    game_id: str
    rule_version: str
    items: list[ReviewTraceItem] = field(default_factory=list)
    win_prob: Optional[list] = None          # 선택 필드(D-10-03). 지금은 늘 None


def representative_code(codes: list[str]) -> str:
    """한 결정의 대표 이유 코드: 근거가 필요한 코드 중 첫 것, 없으면 첫 코드."""
    if not codes:
        raise ValueError("이유 코드 없는 결정")
    return next((c for c in codes if c not in _NO_EVIDENCE), codes[0])


def phrase_id_for(codes: list[str]) -> str:
    """대표 이유 코드의 템플릿 해설 문구 ID(`rv_<코드>`)."""
    return PHRASE_PREFIX + representative_code(codes)


def _evidence(rows: Any) -> list[EvidenceRef]:
    refs = []
    for row in rows or []:
        try:
            refs.append(EvidenceRef(EvidenceType(row["type"]), str(row.get("record_id", ""))))
        except (KeyError, TypeError, ValueError):
            continue                          # 모르는 근거 형식은 건너뛴다(옛 판 호환)
    return refs


def _item(ev: TraceEvent) -> ReviewTraceItem:
    p = ev.payload
    kind, target = str(p["kind"]), p.get("target")
    codes = [str(c) for c in p["reason_codes"]]
    item = ReviewTraceItem(f"d{ev.seq}", ev.day, ev.phase, str(p["actor"]), kind, codes, _evidence(p.get("evidence")),
                           str(p.get("policy_version", "")), phrase_id_for(codes))
    if kind == REACT_KIND:
        item.reaction = target if isinstance(target, str) else None
    elif kind == CommandKind.NIGHT_PLAN.value:
        if isinstance(target, dict):
            item.executor, item.victim = target.get("executor"), target.get("victim")
    elif isinstance(target, str):
        item.target_npc = target
    return item


def _speakers(public: Optional[PublicRecord]) -> dict[str, str]:
    return {} if public is None else {s.speech_id: s.speaker for s in public.speeches}


def review_trace(inp: TraceInput) -> ReviewTrace:
    """decision 이벤트(seq 순)로 복기 항목을 만든다. 플레이어 결정(actor=mudang)은 넣지 않는다."""
    events = sorted(inp.events, key=lambda e: e.seq)
    speakers = _speakers(inp.public)
    used: set[int] = set()
    items: list[ReviewTraceItem] = []
    for i, ev in enumerate(events):
        if ev.event_type != "decision" or ev.payload.get("actor") in (None, "mudang"):
            continue
        item = _item(ev)
        if item.kind in TALK_KINDS or item.kind == REACT_KIND:
            want = "speech_made" if item.kind in TALK_KINDS else "interrogation_answered"
            for j in range(i + 1, len(events)):
                nxt = events[j]
                if nxt.state_version != ev.state_version:
                    break
                if j in used or nxt.event_type != want:
                    continue
                sid = nxt.payload.get("speech_id")
                if want == "speech_made" and speakers and speakers.get(sid) != item.actor:
                    continue
                item.speech_id = sid
                used.add(j)
                break
        items.append(item)
    return ReviewTrace(inp.game_id, inp.rule_version, items)


def evidence_speaker(ref: EvidenceRef, public: Optional[PublicRecord]) -> Optional[str]:
    """근거 주장(또는 기색·점괘 공개의 주장)의 화자. 모순이면 첫 주장 화자. 못 찾으면 None."""
    if public is None or ref.type in (EvidenceType.VOTE, EvidenceType.MAJORITY):
        return None
    claim_id = ref.record_id.split("|", 1)[0]
    return next((c.speaker for c in public.claims if c.claim_id == claim_id), None)


# ---------------------------------------------------------------- 템플릿 해설
_JOSA = {"은(는)": ("은", "는"), "이(가)": ("이", "가"), "을(를)": ("을", "를"), "과(와)": ("과", "와"),
         "이(라)": ("이라", "라")}
_JOSA_RE = re.compile(r"(\S)(" + "|".join(re.escape(k) for k in _JOSA) + r")")


def _has_batchim(ch: str) -> Optional[bool]:
    code = ord(ch) - 0xAC00
    if 0 <= code <= 11171:
        return code % 28 != 0
    return None


def attach_josa(text: str) -> str:
    """'훈장은(는)'처럼 괄호 조사를 앞 글자 받침에 맞춰 하나로 고른다. 한글이 아니면 앞쪽(받침형)을 쓴다."""
    def pick(m: re.Match) -> str:
        has = _has_batchim(m.group(1))
        first, second = _JOSA[m.group(2)]
        return m.group(1) + (second if has is False else first)
    return _JOSA_RE.sub(pick, text)


SLOT_DEFAULTS = {"actor": "누군가", "target": "다른 사람", "victim": "누군가", "executor": "누군가"}
PLAYER_NAME = "무당"                          # 플레이어는 콘텐츠 인물 목록에 없다(화면 이름표와 같은 이름)


def review_text(item: ReviewTraceItem, lines: dict[str, list[str]], names: dict[str, str], seed: int) -> str:
    """복기 항목의 템플릿 해설 문장. 같은 문구 ID 아래 변형은 판 시드와 항목 ID로 결정적으로 고른다.

    문구가 없으면(새 이유 코드) 빈 문자열을 돌려주고, 화면은 이유 코드 이름표만 보인다.
    """
    variants = lines.get(item.phrase_id) or []
    if not variants:
        return ""
    template = random.Random(f"{seed}:review:{item.item_id}").choice(variants)
    slots = {"actor": item.actor, "target": item.target_npc, "victim": item.victim, "executor": item.executor}
    names = {PLAYER_ID: PLAYER_NAME, **names}
    filled = {k: (names.get(v, v) if v else SLOT_DEFAULTS[k]) for k, v in slots.items()}
    return attach_josa(template.format(**filled))


# ---------------------------------------------------------------- 의심 근거 풀이(11/11 사용자 승인)
# 마을 사람 의심(rule_suspect)의 근거가 왜 수상했는지를 판 종료 뒤 기록으로 다시 따진다. 판정은 `npc.beliefs`의 규칙과 같다.
# 1~3(행적·목격 주장 규칙)이 맞으면 그중 가중치(W) 큰 쪽, 아니면 4~6(근거 종류: 모순·공개된 기색·다른 사람의 의심 발언).
BASIS_OWN_WITNESS_LIE = "own_witness_lie"                    # 의심한 NPC가 그 밤 대상을 다른 곳에서 봄(W 35)
BASIS_CLAIMED_SEEING_ME_FALSELY = "claimed_seeing_me_falsely"  # 대상이 의심한 NPC를 봤다고 했지만 그는 거기 없었음(W 35)
BASIS_AT_CRIME_SCENE = "at_crime_scene"                      # 대상이 실종 장소·시간대에 있었다고 스스로 말함(W 8)
# 아래 셋은 위 셋이 맞지 않을 때만(11/11 사용자 추가 승인). 근거 종류가 모순·기색·다른 사람의 의심 발언일 때
BASIS_CONTRADICTION = "contradiction"                        # 대상의 주장이 다른 사람 주장과 공개 모순(W 20)
BASIS_TELL = "tell"                                          # 대상이 그 주장을 할 때 공개된 기색이 보임(W 12)
BASIS_OTHERS_SUSPECT = "others_suspect"                      # 다른 사람이 이미 대상을 의심하는 말을 함(W 6~8)
BASIS_PREFIX = "rv_basis_"


@dataclass(frozen=True)
class SuspectBasis:
    """의심 근거 풀이 하나: 규칙 ID와 문장 자리(실종자는 ①에서만)."""
    basis: str
    victim: Optional[str] = None


def suspect_basis(item: ReviewTraceItem, public: Optional[PublicRecord],
                  private: Optional[PrivateKnowledge]) -> Optional[SuspectBasis]:
    """의심 항목(이유 코드 rule_suspect)의 근거가 왜 수상했는지(규칙 1~3 우선, 아니면 근거 종류 4~6). 없으면 None.

    `private`는 의심한 NPC의 개인 지식(판 종료 뒤라 복기에서 써도 된다). 근거 주장의 대상이 의심 대상과 같을 때만 본다.
    """
    if (item.kind != CommandKind.SUSPECT.value or ReasonCode.RULE_SUSPECT.value not in item.reason_codes
            or public is None or not item.target_npc):
        return None                       # 마을 사람 의심(rule_suspect)만. 씐 자 누명(frame_*)은 믿음 규칙을 쓰지 않는다
    by_id = {c.claim_id: c for c in public.claims}
    claims = [by_id[r.record_id] for r in item.evidence if r.type is EvidenceType.CLAIM and r.record_id in by_id]
    me, target = item.actor, item.target_npc
    found: list[SuspectBasis] = []
    for c in claims:
        if c.speaker == me or c.polarity is not Polarity.AFFIRM:   # 부정 주장은 문장("있었다"·"봤다")과 맞지 않아 뺀다
            continue
        if c.claim_type is ClaimType.WHEREABOUTS and c.subject == target and private is not None:
            if any(target in s.seen and s.night == c.night and s.slot is c.slot and s.place is not c.place
                   for s in private.witnessed):
                found.append(SuspectBasis(BASIS_OWN_WITNESS_LIE))
        if c.claim_type is ClaimType.SIGHTING and c.speaker == target and me in c.seen and private is not None:
            actual = private.whereabouts.get(c.night, {}).get(c.slot)
            if actual is not None and actual is not c.place:
                found.append(SuspectBasis(BASIS_CLAIMED_SEEING_ME_FALSELY))
        if c.claim_type is ClaimType.WHEREABOUTS and c.subject == target and c.place is not Place.HOME:
            for rec in public.disappearances:
                if rec.night == c.night and rec.slot is c.slot and rec.place is c.place:
                    found.append(SuspectBasis(BASIS_AT_CRIME_SCENE, rec.victim))
                    break
    if not found:
        found = _secondary_basis(item, public, by_id)
    order = (BASIS_OWN_WITNESS_LIE, BASIS_CLAIMED_SEEING_ME_FALSELY, BASIS_AT_CRIME_SCENE,
             BASIS_CONTRADICTION, BASIS_TELL, BASIS_OTHERS_SUSPECT)
    return min(found, key=lambda b: order.index(b.basis)) if found else None


def _secondary_basis(item: ReviewTraceItem, public: PublicRecord, by_id: dict) -> list[SuspectBasis]:
    """근거 종류로 고르는 풀이(모순·기색·다른 사람의 의심). 공개되지 않은 기색은 쓰지 않는다."""
    me, target = item.actor, item.target_npc
    shown = {t.claim_id for t in public.tells if t.shown}
    speeches = {s.speech_id: s for s in public.speeches}
    found: list[SuspectBasis] = []
    for r in item.evidence:
        if r.type is EvidenceType.CONTRADICTION:
            pair = [by_id[i] for i in r.record_id.split("|") if i in by_id]
            if len(pair) == 2 and any(c.speaker == target for c in pair) and any(c.speaker != target for c in pair):
                found.append(SuspectBasis(BASIS_CONTRADICTION))
        elif r.type is EvidenceType.TELL:
            c = by_id.get(r.record_id)
            if c is not None and c.speaker == target and r.record_id in shown:
                found.append(SuspectBasis(BASIS_TELL))
        elif r.type is EvidenceType.MAJORITY:
            sp = speeches.get(r.record_id)
            if sp is not None and sp.kind is CommandKind.SUSPECT and sp.target == target and sp.speaker not in (me, target):
                found.append(SuspectBasis(BASIS_OTHERS_SUSPECT))
    return found


def basis_text(item: ReviewTraceItem, basis: Optional[SuspectBasis], lines: dict[str, list[str]],
               names: dict[str, str]) -> str:
    """근거 풀이 문장(문구가 없거나 풀이가 없으면 ""). 자리: {actor}=의심한 NPC, {target}=대상, {victim}=실종자."""
    if basis is None:
        return ""
    variants = lines.get(BASIS_PREFIX + basis.basis) or []
    if not variants:
        return ""
    names = {PLAYER_ID: PLAYER_NAME, **names}
    slots = {"actor": item.actor, "target": item.target_npc, "victim": basis.victim}
    filled = {k: (names.get(v, v) if v else SLOT_DEFAULTS[k]) for k, v in slots.items()}
    return attach_josa(variants[0].format(**filled))


def missing_phrases(lines: dict[str, list[str]]) -> list[str]:
    """`ReasonCode` 중 문구가 없는 코드(빠진 코드 0건 검사용)."""
    return sorted(c.value for c in ReasonCode if not lines.get(PHRASE_PREFIX + c.value))
