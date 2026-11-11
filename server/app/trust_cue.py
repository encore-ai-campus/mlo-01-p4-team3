"""아침 주민 신뢰 단서(11/10 사용자 승인, A-10-01 rule-0.8과 함께). 전날 공개 기록으로 마을 분위기를 한 줄로 알린다.

계층: 응용(순수 함수). 판의 공개 투표·발언 기록만 받아 계산하고, NPC 신뢰도 같은 비공개 상태는 읽지 않는다.

판정: N일째(N≥2) 아침에 N-1일째 본 투표(결선 제외)에서 무당이 받은 표 수 + N-1일째 무당을 의심한 발언 수를 센다.
0이면 안심, 1이면 주의, 2 이상이면 위험 문장이다. 1일째 아침은 전날 기록이 없어 단서를 내지 않는다.
"""
from __future__ import annotations

from typing import Iterable, Optional

from engine.domain.model import PLAYER_ID, CommandKind

TRUST_CALM = "주민들은 아직 무당님을 믿는 눈치예요."
TRUST_WARY = "몇몇 주민이 무당님을 미심쩍게 보고 있어요."
TRUST_DANGER = "주민들 사이에 무당님을 의심하는 말이 돌아요. 이대로면 무당님이 금줄 광에 갇힐지도 몰라요!"


def suspicion_count(day: int, votes: Iterable, speeches: Iterable) -> int:
    """day일째 본 투표에서 무당이 받은 표 수 + day일째 무당을 의심한 발언 수."""
    ballots = sum(1 for v in votes if v.day == day and v.round == "main" and v.target == PLAYER_ID)
    suspects = sum(1 for s in speeches
                   if s.day == day and s.kind is CommandKind.SUSPECT and s.target == PLAYER_ID)
    return ballots + suspects


def trust_cue(day: int, votes: Iterable, speeches: Iterable) -> Optional[str]:
    """day일째 아침에 보일 신뢰 단서 문장. 1일째 아침은 None."""
    if day < 2:
        return None
    count = suspicion_count(day - 1, votes, speeches)
    return TRUST_CALM if count == 0 else TRUST_WARY if count == 1 else TRUST_DANGER
