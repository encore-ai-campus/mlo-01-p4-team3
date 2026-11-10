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

from .model import CommandKind, EvidenceRef, EvidenceType, PublicRecord
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
    game_id: str
    rule_version: str
    seed: int
    events: list[TraceEvent]
    public: Optional[PublicRecord] = None    # 근거 ID 해소용(종료 시점). 이유·행동 판단에는 쓰지 않는다


@dataclass
class ReviewTraceItem:
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


def review_text(item: ReviewTraceItem, lines: dict[str, list[str]], names: dict[str, str], seed: int) -> str:
    """복기 항목의 템플릿 해설 문장. 같은 문구 ID 아래 변형은 판 시드와 항목 ID로 결정적으로 고른다.

    문구가 없으면(새 이유 코드) 빈 문자열을 돌려주고, 화면은 이유 코드 이름표만 보인다.
    """
    variants = lines.get(item.phrase_id) or []
    if not variants:
        return ""
    template = random.Random(f"{seed}:review:{item.item_id}").choice(variants)
    slots = {"actor": item.actor, "target": item.target_npc, "victim": item.victim, "executor": item.executor}
    filled = {k: (names.get(v, v) if v else SLOT_DEFAULTS[k]) for k, v in slots.items()}
    return attach_josa(template.format(**filled))


def missing_phrases(lines: dict[str, list[str]]) -> list[str]:
    """`ReasonCode` 중 문구가 없는 코드(빠진 코드 0건 검사용)."""
    return sorted(c.value for c in ReasonCode if not lines.get(PHRASE_PREFIX + c.value))
