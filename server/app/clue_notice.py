"""판 도중 원한 단서 알림 규칙(11/10 사용자 승인). 고백마다 새 단서·이미 가진 단서·한풀이 열림 중 하나로 고른다.

계층: 응용(순수 함수). 저장소를 부르지 않고, 판의 공개 고백 기록·콘텐츠·진척 값만 받아 계산한다.
규칙 함수(`completed_hypotheses`)는 `engine.domain.progress`의 것을 그대로 쓴다.

판정:
- 진척에 단서가 없으면(명령 직후 반영이 아직 안 됨) 이번에 얻은 새 단서로 본다. 반영은 따라잡기가 맡는다.
- 진척의 처음 얻은 판이 이 판보다 먼저 시작한 판이면 이미 가진 단서, 그 밖(이 판·나중 판)은 새 단서다.
  판 순서는 진척의 `game_ids`(시작 순)로 본다. 이 판 뒤에 시작한 판에서 얻은 단서는 이 판 고백 시점에는 없던 것이다.
- 새 단서가 이 고백으로 가설의 세 부분을 처음 채우면 한풀이 열림이다(이 판에서 이 고백 뒤에 얻은 단서·나중 판 단서는 빼고 본다).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from engine.domain.content import GrudgeClue, Secret
from engine.domain.model import ClueRecord, ConfessionRecord
from engine.domain.progress import completed_hypotheses

NEW = "new"
DUPLICATE = "duplicate"
UNLOCK = "unlock"


@dataclass(frozen=True)
class ClueNotice:
    """고백 하나에 붙는 단서 알림."""

    npc_id: str
    day: int
    clue_id: str
    clue_text: str
    kind: str          # NEW | DUPLICATE | UNLOCK


def clue_notices(game_id: str, confessions: Iterable[ConfessionRecord], secrets: Iterable[Secret],
                 grudge_clues: Iterable[GrudgeClue], progress_clues: Mapping[str, ClueRecord],
                 game_order: Iterable[str] = ()) -> list[ClueNotice]:
    """공개 고백 순서대로 알림을 만든다. 단서가 없는 비밀이나 콘텐츠에 없는 단서 ID는 건너뛴다.

    game_order는 진척의 판 시작 순서(`PlayerProgress.game_ids`). 목록에 없는 판은 이 판보다 먼저로 본다.
    """
    clues = list(grudge_clues)
    text_of = {c.clue_id: c.text_ko for c in clues}
    clue_of = {s.secret_id: s.grudge_clue_id for s in secrets}
    order = {gid: i for i, gid in enumerate(game_order)}
    mine = order.get(game_id, len(order))

    def earlier(record: ClueRecord) -> bool:
        return record.first_game_id != game_id and order.get(record.first_game_id, -1) < mine

    owned = {clue_id for clue_id, record in progress_clues.items() if earlier(record)}
    found = []
    for rec in confessions:
        clue_id = clue_of.get(rec.secret_id)
        if not clue_id or clue_id not in text_of:
            continue
        record = progress_clues.get(clue_id)
        found.append((rec, clue_id, record is None or not earlier(record)))
    # 이 판의 새 단서는 고백 순서대로 하나씩 더해 가며 본다(먼저 시작한 판에서 얻은 단서만 처음부터 있다).
    notices = []
    for rec, clue_id, new in found:
        if not new:
            notices.append(ClueNotice(rec.npc_id, rec.day, clue_id, text_of[clue_id], DUPLICATE))
            continue
        before = set(completed_hypotheses(owned, clues))
        owned = owned | {clue_id}
        opened = set(completed_hypotheses(owned, clues)) - before
        notices.append(ClueNotice(rec.npc_id, rec.day, clue_id, text_of[clue_id], UNLOCK if opened else NEW))
    return notices
