"""화면용 뷰 모델. 템플릿(Django 템플릿 + htmx + Alpine.js)은 이것만 받는다.

계층: 표현. 응용 DTO(PlayerView 등)를 화면 단위로 바꾼다. 플레이어에게 허용된 정보만 담는다.
상세: docs/contracts/api.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from engine.domain.model import ClaimType, CommandKind
from engine.ports import PlayerView

PLACE_KO = {"well": "우물가", "shrine": "서낭당", "tavern": "주막", "gate": "마을 어귀", "home": "집"}
SLOT_KO = {"evening": "초저녁", "midnight": "한밤"}


@dataclass
class PersonVM:
    npc_id: str
    name: str
    status: str                  # active | locked | missing
    notebook_value: Optional[int] = None


@dataclass
class ClaimCardVM:
    claim_id: str
    speaker_name: str
    text: str                    # "한밤 · 서낭당 · 있었다"
    selectable: bool = True


@dataclass
class SpeechVM:
    speech_id: str
    speaker_name: str
    text: str
    pending_job_id: Optional[str] = None   # 있으면 htmx 폴링 조각을 붙인다


@dataclass
class DisappearanceVM:
    night: int
    victim_name: str
    text: str                    # "1일째 아침: 과부의 짚신이 우물가에서 발견됐다(한밤)"


@dataclass
class MeetingVM:
    game_id: str
    state_version: int
    day: int
    phase: str
    people: list[PersonVM]
    claims: list[ClaimCardVM]
    speeches: list[SpeechVM]
    allowed_actions: list[str]           # CommandKind 값
    my_turn: bool
    interrogations_left: int
    reveals_left_today: int
    runoff_candidates: list[str] = field(default_factory=list)
    ended: bool = False
    disappearances: list[DisappearanceVM] = field(default_factory=list)   # 피해 장소 공개(P-02)


def build_meeting_vm(v: PlayerView) -> MeetingVM:
    def status(n: str) -> str:
        return "locked" if n in v.locked else "missing" if n in v.missing else "active"

    people = [PersonVM(n, v.names.get(n, n), status(n), v.player.notebook.get(n))
              for n in v.names]
    claims = []
    for c in v.public.claims:
        if c.claim_type is ClaimType.DIVINATION_REVEAL:
            text = f"점괘 공개 · {v.names.get(c.subject, c.subject)} · {c.divination_result.value if c.divination_result else ''}"
        else:
            where = PLACE_KO.get(c.place.value, "") if c.place else ""
            when = SLOT_KO.get(c.slot.value, "") if c.slot else ""
            if c.claim_type is ClaimType.SIGHTING:
                seen = "·".join(v.names.get(n, n) for n in c.seen) or "누군가"
                text = f"{when} · {where} · {seen}을(를) 봤다"
            elif c.claim_type is ClaimType.NON_SIGHTING:
                seen = "·".join(v.names.get(n, n) for n in c.seen) or "아무도"
                text = f"{when} · {where} · {seen} 못 봤다"
            else:
                text = f"{when} · {where} · {'있었다' if c.polarity.value == 'affirm' else '없었다'}"
        claims.append(ClaimCardVM(c.claim_id, v.names.get(c.speaker, c.speaker), text))
    disappearances = []
    for d in v.public.disappearances:
        name = v.names.get(d.victim, d.victim)
        where = f"{name}의 집" if d.place.value == "home" else PLACE_KO.get(d.place.value, d.place.value)
        disappearances.append(DisappearanceVM(
            d.night, name, f"{d.night + 1}일째 아침: {name}의 짚신이 {where}에서 발견됐다({SLOT_KO.get(d.slot.value, '')})"))
    speeches = [SpeechVM(s.speech_id, v.names.get(s.speaker, s.speaker), s.line_text or s.fact_sentence or "",
                         s.render_job_id if s.render_job_id in v.pending_render_jobs else None)
                for s in v.public.speeches]
    return MeetingVM(
        game_id=v.game_id, state_version=v.state_version, day=v.day, phase=v.phase.value,
        people=people, claims=claims, speeches=speeches,
        allowed_actions=[k.value for k in v.legal_kinds if k is not CommandKind.NIGHT_PLAN],
        my_turn=v.awaiting == "mudang",
        interrogations_left=v.player.interrogations_left,
        reveals_left_today=v.player.reveals_left_today,
        runoff_candidates=list(v.runoff_candidates), ended=v.result is not None,
        disappearances=disappearances,
    )
