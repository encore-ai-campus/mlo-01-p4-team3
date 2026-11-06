"""Public-only alias, evidence and time matching for the intent parser."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from engine.domain.model import EvidenceRef, EvidenceType, Place, Slot
from engine.ports import ParseContext

MATCHER_VERSION = "public-longest-v0.1"
_SUFFIX = re.compile(r"^(?:님|씨|선생)?(?:이라고|라고|에게|한테|께|이|가|은|는|을|를|의|과|와|도|만)?(?=$|[\s,.:;!?？…])")
_PLACES = {Place.SHRINE: ("서낭당",), Place.WELL: ("우물",), Place.TAVERN: ("주막",),
           Place.VILLAGE_GATE: ("마을 어귀", "산길 입구"), Place.HOME: ("집",)}


@dataclass(frozen=True)
class PersonMatch:
    """One longest non-overlapping alias span in the input."""
    npc_id: str
    alias: str
    start: int
    end: int


def find_people(text: str, aliases: dict[str, list[str]]) -> list[PersonMatch]:
    """Match full aliases with optional Korean particles; do not match inside another word."""
    possible = []
    for npc_id, names in aliases.items():
        for alias in set(names):
            if not alias:
                continue
            for match in re.finditer(re.escape(alias), text):
                if match.start() and re.match(r"[\w가-힣]", text[match.start() - 1]):
                    continue
                if not _SUFFIX.match(text[match.end():]):
                    continue
                possible.append(PersonMatch(npc_id, alias, match.start(), match.end()))
    result = []
    for match in sorted(possible, key=lambda m: (m.start, -len(m.alias), m.npc_id)):
        if result and match.start < result[-1].end:
            if match.start == result[-1].start and match.end == result[-1].end and match.npc_id != result[-1].npc_id:
                raise ValueError("alias belongs to multiple active NPCs")
            continue
        result.append(match)
    return result


def normalize_people(text: str, aliases: dict[str, list[str]]) -> str:
    """Normalize Unicode, replace only person spans, and preserve surrounding grammar."""
    text = unicodedata.normalize("NFC", text)
    for match in reversed(find_people(text, aliases)):
        text = text[:match.start] + "인물" + text[match.end:]
    return re.sub(r"\s+", " ", re.sub(r"<P\d+>", "인물", text)).strip()


def resolve_time(text: str, ctx: ParseContext) -> tuple[int | None, Slot | None]:
    """Resolve explicit night indexes or relative last night from public records, never truth."""
    slot = Slot.MIDNIGHT if "한밤" in text else Slot.EVENING if "초저녁" in text else None
    explicit = re.search(r"(\d+)\s*(?:번째|일째)?\s*밤|밤\s*(\d+)", text)
    night = int(explicit.group(1) or explicit.group(2)) if explicit else None
    if night is None and any(word in text for word in ("어젯밤", "간밤", "지난밤")):
        nights = [c.night for c in ctx.public.claims] + [d.night for d in ctx.public.disappearances]
        night = max(nights, default=None)
    return night, slot


@dataclass
class EvidenceMatch:
    """Chosen public refs or tied alternatives, with their matching scores."""
    evidence: list[EvidenceRef] = field(default_factory=list)
    alternatives: list[EvidenceRef] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def match_evidence(text: str, ctx: ParseContext) -> EvidenceMatch:
    """Prefer valid selected cards; otherwise score public claims by person/place/time overlap."""
    claims = {claim.claim_id: claim for claim in ctx.public.claims}
    chosen = []
    for ref in ctx.selected_cards:
        ids = ref.record_id.split("|")
        valid = ((ref.type is EvidenceType.CONTRADICTION and len(ids) == 2 and ids[0] != ids[1]) or
                 (ref.type in (EvidenceType.CLAIM, EvidenceType.TELL, EvidenceType.DIVINATION) and len(ids) == 1))
        if valid and all(key in claims for key in ids) and ref not in chosen:
            chosen.append(ref)
    if ctx.selected_cards:
        return EvidenceMatch(chosen, scores={ref.record_id: 1.0 for ref in chosen})
    people = {m.npc_id for m in find_people(text, ctx.aliases)}
    places = {place for place, names in _PLACES.items() if any(name in text for name in names)}
    night, slot = resolve_time(text, ctx)
    scores = {}
    for claim in claims.values():
        score = (3 * bool(people & {claim.speaker, claim.subject, *claim.seen}) +
                 2 * (claim.place in places) + (slot is not None and claim.slot is slot) +
                 (night is not None and claim.night == night))
        if score:
            scores[claim.claim_id] = float(score)
    if not scores:
        return EvidenceMatch()
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    cutoff = scores[ordered[min(1, len(ordered) - 1)]]
    possible = [EvidenceRef(EvidenceType.CLAIM, key) for key in ordered if scores[key] >= cutoff]
    if len(possible) > 2:
        return EvidenceMatch(alternatives=possible, scores=scores)
    return EvidenceMatch(possible, scores=scores)
