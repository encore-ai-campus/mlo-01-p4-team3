"""엔진 결과 이벤트에서 세는 Prometheus 지표(B-09-01 5번, 11/5 PM 결정).

계층: 표현(관측). 엔진 도메인의 `fallback_count`로 의심 모델 폴백 결정 수를 세어
`paldo_engine_suspicion_fallback_total` 카운터를 그만큼 올린다. `/metrics`(probes.metrics)가 기본 레지스트리를 내보낸다.
"""
from prometheus_client import Counter

from engine.domain.npc.vote import FALLBACK_METRIC, fallback_count

SUSPICION_FALLBACK = Counter(FALLBACK_METRIC, "의심 모델 대신 규칙 v0로 정한 NPC 투표 결정 수")


def observe_engine_events(events):
    """저장된 엔진 결과 이벤트의 폴백 결정 수만큼 카운터를 올리고 그 수를 돌려준다."""
    count = fallback_count(events)
    if count:
        SUSPICION_FALLBACK.inc(count)
    return count
