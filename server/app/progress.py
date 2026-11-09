"""판 밖 진척 반영 유스케이스(E-09-08). 명령 저장 직후와 아웃박스 재처리(C-09-04)가 같은 함수를 쓴다.

순서: 고백 단서 `add_clue`(멱등) → 진척 다시 읽기 → 세 부분이 모인 가설의 결말 `unlock_ending`(멱등).
단서를 새로 얻지 않은 호출에서도 결말 확인을 하므로, 단서 저장 뒤 해금 전에 멈춘 경우도 재처리로 복구된다.

계층: 응용. 규칙은 `engine.domain.progress`, 저장은 주입한 `ProgressRepository`(engine/ports.py)가 맡는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from engine.domain.content import GrudgeClue
from engine.domain.events import EventEnvelope
from engine.domain.progress import clue_gains, ending_unlocks


@dataclass
class ProgressApplied:
    """이번 호출에서 처음 반영된 단서·결말(이미 있던 것은 넣지 않는다)."""
    clues: list[str] = field(default_factory=list)
    endings: list[str] = field(default_factory=list)


def apply_progress(progress_repo, player_pseudonym: str, events: Iterable[EventEnvelope],
                   grudge_clues: Iterable[GrudgeClue]) -> ProgressApplied:
    """이벤트에서 나온 진척을 저장소에 멱등 반영한다. 같은 이벤트로 두 번 불러도 결과가 같다."""
    clues = list(grudge_clues)
    applied = ProgressApplied()
    for gain in clue_gains(events, clues):
        if progress_repo.add_clue(player_pseudonym, gain.clue_id, gain.game_id, gain.day, gain.source_event_id):
            applied.clues.append(gain.clue_id)
    for unlock in ending_unlocks(progress_repo.load(player_pseudonym), clues):
        if progress_repo.unlock_ending(player_pseudonym, unlock.ending, unlock.source_event_id):
            applied.endings.append(unlock.ending)
    return applied
