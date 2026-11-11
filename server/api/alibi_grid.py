"""기록 창 행적표: 공개 진술을 (밤, 시간대, 장소) 칸에 모은 읽기 전용 화면 모델(11/10 사용자 결정).

계층: 표현. `PlayerView`의 공개 기록(진술·실종·감금)과 이름만 읽는다. 엔진 규칙·저장소는 부르지 않고
model의 열거형만 쓴다. 결백·모순·알리바이를 판정하지 않고, 칸에 놓인 모든 사람에 "누가 한 말인지"를 남긴다.
목격을 참인 위치로 승격하지 않는다. 출처 "본인 말"은 글로 쓰지 않고 이름만 두며, 목격 출처와 정정 표시만 괄호로 붙인다.
칸 안 이름에는 지금 상태(실종·감금)를 붙이지 않는다(지난 밤 칸이 "실종자가 말했다"로 읽히므로, 11/11 사용자 결정). 부정 진술(없었다·못 봤다)은 사람 목록과 섞지 않고 칸 아래 줄로만 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.domain.model import PLAYER_ID, PUBLIC_PLACES, ClaimType, Place, Polarity, Slot
from engine.ports import PlayerView
from .viewmodels import PLACE_KO, SLOT_KO
from .vn_script import attach_particles

HOME_LABEL = "각자 집"
SOMEONE = "누군가"
GRID_PLACES = (*PUBLIC_PLACES, Place.HOME)
GRID_SLOTS = (Slot.EVENING, Slot.MIDNIGHT)
GRID_CLAIM_TYPES = (ClaimType.WHEREABOUTS, ClaimType.SIGHTING, ClaimType.NON_SIGHTING)
CORRECTED_LABEL = {"before": "고치기 전", "after": "정정한 진술"}


@dataclass(frozen=True)
class GridSourceVM:
    """칸에 그 사람을 놓은 출처 하나. kind: self(본인 말)·seen_by(누가 봄)·sighting_speaker(봤다고 말함)."""

    kind: str
    by_name: str
    claim_id: str        # 동작용 값. 화면 글자로 내지 않는다
    corrected: str       # none·before(고치기 전)·after(정정한 진술)
    text: str            # 화면 글자. 본인 말은 정정 표시만("정정한 진술"·"고치기 전"), 아니면 빈 문자열


@dataclass(frozen=True)
class GridPersonVM:
    """칸 안 있었다 묶음의 한 사람. 같은 사람은 한 줄로 합치고 출처를 이어 적는다."""

    npc_id: str
    name: str
    sources: tuple[GridSourceVM, ...]
    shown: tuple[GridSourceVM, ...]   # 괄호 안에 쓸 출처(글자가 있는 것): 목격 출처와 정정 표시만
    label: str           # shown 글자를 ", "로 이은 것, 없으면 빈 문자열
    faded: bool          # 출처가 모두 고치기 전이면 흐리게


@dataclass(frozen=True)
class GridLineVM:
    """칸 아래 기타 진술 한 줄(없었다·못 봤다)."""

    text: str
    claim_id: str
    corrected: str


@dataclass(frozen=True)
class GridCellVM:
    """한 칸(장소 × 시간대): 있었다 묶음, 기타 진술 줄, 피해 칸 표시."""

    slot: str
    people: tuple[GridPersonVM, ...]
    others: tuple[GridLineVM, ...]
    crime: str           # "실종: {이름}"(피해 칸만), 없으면 빈 문자열


@dataclass(frozen=True)
class GridRowVM:
    """장소 한 행. 집 행의 머리글은 "각자 집"."""

    place_key: str
    place_label: str
    cells: tuple[GridCellVM, ...]


@dataclass(frozen=True)
class GridNightVM:
    """밤 하나의 표(접기 단위)."""

    night: int
    label: str           # "{N}일째 밤"
    caption: str         # 밤 + 실종 정보
    open: bool           # 최신 밤만 연다
    rows: tuple[GridRowVM, ...]
    no_claim: str        # "진술 없음: …", 없거나 참가자 복원이 불확실하면 빈 문자열


@dataclass
class _Person:
    npc_id: str
    name: str
    sources: list[GridSourceVM] = field(default_factory=list)


def _display(view: PlayerView, npc_id: str) -> str:
    name = view.names.get(npc_id, npc_id)
    return "무당" if name == PLAYER_ID else name


def _corrections(claims) -> dict[str, str]:
    """주장 ID → none·before·after. 고쳐진 주장은 모두 고치기 전이고, 고친 주장 중 마지막만 정정한 진술이다."""
    replaced = {c.corrects for c in claims if c.corrects}
    out = {}
    for c in claims:
        if c.claim_id in replaced:
            out[c.claim_id] = "before"
        elif c.corrects:
            out[c.claim_id] = "after"
        else:
            out[c.claim_id] = "none"
    return out


def _with_mark(text: str, corrected: str) -> str:
    mark = CORRECTED_LABEL.get(corrected)
    return f"{text} · {mark}" if mark else text


def _place_label(view: PlayerView, place: Place, victim: str) -> str:
    return f"{_display(view, victim)}의 집" if place is Place.HOME else PLACE_KO.get(place.value, place.value)


def _participants(view: PlayerView, night: int, residents: list[str]) -> list[str]:
    """그 밤의 참가자(다음 날 아침 화자 후보): 그 밤까지의 실종자와 그 밤 전 낮까지 감금된 사람을 뺀다."""
    gone = {d.victim for d in view.public.disappearances if d.night <= night}
    gone |= {lock.locked for lock in view.public.locks if lock.locked is not None and lock.day <= night}
    return [r for r in residents if r not in gone]


def _no_claim_text(view: PlayerView, night: int, claims, residents: list[str]) -> str:
    """행적 주장이 빠진 참가자. 그 밤 행적 주장이 없거나 참가자가 아닌 화자가 있으면 복원이 불확실해 그리지 않는다."""
    told: dict[str, set[Slot]] = {}
    for c in claims:
        if c.claim_type is ClaimType.WHEREABOUTS and c.polarity is Polarity.AFFIRM:
            told.setdefault(c.subject, set()).add(c.slot)
    participants = _participants(view, night, residents)
    if not told or not set(told) <= set(participants):
        return ""
    parts = []
    for npc in participants:
        missing = [s for s in GRID_SLOTS if s not in told.get(npc, set())]
        if len(missing) == len(GRID_SLOTS):
            parts.append(_display(view, npc))
        elif missing:
            parts.append(f"{_display(view, npc)}({SLOT_KO[missing[0].value]})")
    return f"진술 없음: {', '.join(parts)}" if parts else ""


def build_alibi_grid(view: PlayerView) -> list[GridNightVM]:
    """공개 진술과 실종 기록을 밤별 격자로 묶는다. 격자에 둘 공개 기록이 없으면 빈 목록."""
    status = _corrections(view.public.claims)
    claims = [c for c in view.public.claims
              if c.claim_type in GRID_CLAIM_TYPES and c.speaker != PLAYER_ID and c.slot is not None and c.place is not None]
    nights = sorted({c.night for c in claims} | {d.night for d in view.public.disappearances})
    residents = [n for n in dict.fromkeys([*view.names, *view.active, *view.locked, *view.missing]) if n != PLAYER_ID]
    return [_night(view, night, [c for c in claims if c.night == night], status, residents, night == nights[-1])
            for night in nights]


def _night(view, night, claims, status, residents, is_latest) -> GridNightVM:
    cells: dict[tuple[Place, Slot], dict[str, _Person]] = {(p, s): {} for p in GRID_PLACES for s in GRID_SLOTS}
    others: dict[tuple[Place, Slot], list[GridLineVM]] = {key: [] for key in cells}
    crimes: dict[tuple[Place, Slot], list[str]] = {key: [] for key in cells}

    def add(key, npc_id, name, source):
        person = cells[key].setdefault(npc_id, _Person(npc_id, name))
        if all((s.kind, s.claim_id) != (source.kind, source.claim_id) for s in person.sources):
            person.sources.append(source)

    # 행적(있었다)을 먼저 놓아 목격 화자의 "본인 말" 합치기를 판단한다.
    for c in claims:
        key, mark = (c.place, c.slot), status[c.claim_id]
        if c.claim_type is not ClaimType.WHEREABOUTS:
            continue
        if c.polarity is Polarity.DENY:
            others[key].append(GridLineVM(_with_mark(f"{_display(view, c.subject)}: 여기 없었다고 함", mark), c.claim_id, mark))
        else:
            add(key, c.subject, _display(view, c.subject), GridSourceVM("self", "", c.claim_id, mark, CORRECTED_LABEL.get(mark, "")))
    for c in claims:
        key, mark = (c.place, c.slot), status[c.claim_id]
        speaker = _display(view, c.speaker)
        seen = [_display(view, s) for s in c.seen]
        if c.claim_type is ClaimType.NON_SIGHTING:
            text = f"{speaker}: {'·'.join(seen)}을(를) 못 봤다고 함" if seen else f"{speaker}: 아무도 못 봤다고 함"
            others[key].append(GridLineVM(_with_mark(attach_particles(text), mark), c.claim_id, mark))
            continue
        if c.claim_type is not ClaimType.SIGHTING:
            continue
        for npc_id, name in zip(c.seen, seen) if seen else [("", SOMEONE)]:
            add(key, npc_id, name, GridSourceVM("seen_by", speaker, c.claim_id, mark,
                                                _with_mark(attach_particles(f"{speaker}이(가) 봄"), mark)))
        own = cells[key].get(c.speaker)
        # 화자의 유효한(고치기 전이 아닌) 자기 행적이 같은 칸에 있을 때만 이름만으로 합친다. 고쳐진 행적만 있으면 목격 진술을 따로 남긴다.
        if own is None or not any(s.kind == "self" and s.corrected != "before" for s in own.sources):
            targets = "·".join(seen) or SOMEONE
            add(key, c.speaker, speaker, GridSourceVM("sighting_speaker", "", c.claim_id, mark,
                                                      _with_mark(attach_particles(f"{targets}을(를) 봤다고 말함"), mark)))

    captions = [f"{night}일째 밤"]
    for d in view.public.disappearances:
        if d.night != night:
            continue
        name = _display(view, d.victim)
        crimes[(d.place, d.slot)].append(f"실종: {name} ({name}의 집)" if d.place is Place.HOME else f"실종: {name}")
        captions.append(f"실종: {name} · {SLOT_KO[d.slot.value]} {_place_label(view, d.place, d.victim)}")

    rows = tuple(
        GridRowVM(place.value, HOME_LABEL if place is Place.HOME else PLACE_KO[place.value], tuple(
            GridCellVM(slot.value,
                       tuple(GridPersonVM(p.npc_id, p.name, tuple(p.sources), tuple(s for s in p.sources if s.text),
                                          ", ".join(s.text for s in p.sources if s.text),
                                          all(s.corrected == "before" for s in p.sources))
                             for p in cells[(place, slot)].values()),
                       tuple(others[(place, slot)]), " · ".join(crimes[(place, slot)]))
            for slot in GRID_SLOTS))
        for place in GRID_PLACES)
    return GridNightVM(night, f"{night}일째 밤", " · ".join(captions), is_latest, rows,
                       _no_claim_text(view, night, claims, residents))
