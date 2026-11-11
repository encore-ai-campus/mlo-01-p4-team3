"""판 밖 진척 규칙: 원한 단서 획득과 한풀이 결말 해금(E-09-08, domain.md 9절, 기획서 "원한 단서").

- 단서는 비밀 고백(`confession` 이벤트의 `grudge_clue_id`)으로 얻는다. 고백은 캐묻기(추궁) 답변으로만 나온다.
- 같은 단서를 다시 얻으면 무시한다(`PlayerProgress.add_clue`·저장소 `add_clue`가 False).
- 같은 `hypothesis_id`의 세 부분(identity, death, target)이 모두 모이면 `haewon:<hypothesis_id>`가 열린다.
  서로 다른 가설의 부분은 합치지 않는다. 퇴마(`exorcism`)는 항상 고를 수 있다.
- 판 안 상태(NPC 믿음·기억, 공개 기록)는 진척에 넣지 않는다. 진척은 단서·결말·판 ID만 남긴다.

멱등 키(`source_event_id`): 고백은 판마다 NPC당 한 번뿐이라 `<game_id>:confess:<npc_id>`로 정한다.
저장소가 매기는 이벤트 ID(`<game_id>:<seq>`)는 저장 뒤에야 정해져, 명령 처리 직후 반영과 아웃박스 재처리가
같은 키를 쓰도록 이벤트 내용에서 키를 만든다. 결말 해금 키는 플레이어당 결말마다 하나(`unlock:<ending>`)다.

계층: 도메인. 저장소·이벤트 저장 위치를 모른다. 반영 순서는 응용 계층(`server/app/progress.py`)이 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .content import GrudgeClue, Secret
from .events import EventEnvelope, EventType
from .model import ClueRecord, ConfessionRecord, PlayerProgress

EXORCISM = "exorcism"
HAEWON_PREFIX = "haewon:"
CLUE_PARTS: tuple[str, ...] = ("identity", "death", "target")
PROGRESS_EVENT_TYPES: frozenset[str] = frozenset({EventType.CONFESSION.value, EventType.GAME_ENDED.value})


@dataclass(frozen=True)
class ClueGain:
    """고백 하나에서 나온 단서 획득 후보. 저장소가 처음인지 판단한다."""
    clue_id: str
    game_id: str
    day: int
    source_event_id: str


@dataclass(frozen=True)
class EndingUnlock:
    """모인 단서로 새로 열 결말."""
    ending: str
    source_event_id: str


def haewon_ending(hypothesis_id: str) -> str:
    """가설 ID → 한풀이 결말 ID(`haewon:<hypothesis_id>`)."""
    return f"{HAEWON_PREFIX}{hypothesis_id}"


def confession_source_id(game_id: str, npc_id: str) -> str:
    """고백 이벤트의 멱등 키. 판마다 NPC는 한 번만 고백한다."""
    return f"{game_id}:confess:{npc_id}"


def clue_gains(events: Iterable[EventEnvelope], grudge_clues: Iterable[GrudgeClue]) -> list[ClueGain]:
    """이벤트 목록에서 고백으로 얻는 단서를 고른다. 콘텐츠에 없는 단서 ID와 단서 없는 비밀은 건너뛴다.

    같은 목록 안에 같은 고백이 두 번 있어도 한 번만 낸다(재처리 입력이 겹쳐도 같은 결과).
    """
    known = {c.clue_id for c in grudge_clues}
    out: list[ClueGain] = []
    seen: set[str] = set()
    for e in events:
        if e.event_type != EventType.CONFESSION.value:
            continue
        clue_id = e.payload.get("grudge_clue_id")
        npc_id = e.payload.get("npc_id")
        if not clue_id or clue_id not in known or not npc_id:
            continue
        key = confession_source_id(e.game_id, npc_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ClueGain(clue_id, e.game_id, e.day, key))
    return out


def clue_gains_from_confessions(game_id: str, confessions: Iterable[ConfessionRecord], secrets: Iterable[Secret],
                                grudge_clues: Iterable[GrudgeClue]) -> list[ClueGain]:
    """판의 공개 고백 기록(`PublicRecord.confessions`)에서 단서를 고른다. 이벤트판과 같은 멱등 키를 쓴다.

    저장된 판 상태만으로 다시 계산할 수 있어, 명령 직후 반영이 실패해도 판 종료·같은 명령 재요청·복기 때 따라잡는다.
    """
    known = {c.clue_id for c in grudge_clues}
    clue_of = {s.secret_id: s.grudge_clue_id for s in secrets}
    out: list[ClueGain] = []
    seen: set[str] = set()
    for rec in confessions:
        clue_id = clue_of.get(rec.secret_id)
        key = confession_source_id(game_id, rec.npc_id)
        if not clue_id or clue_id not in known or key in seen:
            continue
        seen.add(key)
        out.append(ClueGain(clue_id, game_id, rec.day, key))
    return out


def completed_hypotheses(clues: Iterable[str] | Mapping[str, ClueRecord],
                         grudge_clues: Iterable[GrudgeClue]) -> list[str]:
    """세 부분이 모두 모인 가설 ID(정렬). 한 가설의 같은 부분 단서가 여럿이면 하나만 있어도 그 부분은 채운다."""
    have = set(clues)
    parts: dict[str, set[str]] = {}
    for c in grudge_clues:
        if c.clue_id in have:
            parts.setdefault(c.hypothesis_id, set()).add(c.part)
    return sorted(h for h, got in parts.items() if set(CLUE_PARTS) <= got)


def ending_unlocks(progress: PlayerProgress, grudge_clues: Iterable[GrudgeClue]) -> list[EndingUnlock]:
    """지금 진척으로 새로 열어야 할 결말. 이미 열린 결말은 내지 않는다."""
    out = []
    for h in completed_hypotheses(progress.clues, grudge_clues):
        ending = haewon_ending(h)
        if ending not in progress.endings_unlocked:
            out.append(EndingUnlock(ending, f"unlock:{ending}"))
    return out


def ending_available(progress: PlayerProgress, ending_choice: str) -> bool:
    """판 시작 때 고른 결말을 쓸 수 있는가. 퇴마는 진척 문서와 무관하게 항상 된다."""
    return ending_choice == EXORCISM or ending_choice in progress.endings_unlocked


def progress_events(events: Iterable[EventEnvelope]) -> list[EventEnvelope]:
    """재처리 대상 이벤트(`confession`, `game_ended`)만 순서대로 고른다(domain.md 9절)."""
    return [e for e in events if e.event_type in PROGRESS_EVENT_TYPES]
