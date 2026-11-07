"""해석기 계측과 설정 선택. 응용은 Django·Prometheus에 의존하지 않는다."""
from prometheus_client import Histogram
from time import perf_counter

PARSE_DURATION = Histogram("paldo_intent_parse_duration_seconds", "Intent parser duration", ["stage", "status"],
                           buckets=(.005, .01, .025, .05, .1, .2, .3, .5, 1, 2, 5))


class ObservedIntentParser:
    """주입된 해석기 호출만 측정하며 원문·계정 식별자는 지표에 넣지 않는다."""

    def __init__(self, parser):
        """실제 해석 포트를 보관한다."""
        self.parser = parser

    def parse(self, text, context):
        """기존 포트 결과를 그대로 반환하고 실패 호출도 지연에 포함한다."""
        started, status = perf_counter(), "error"
        try:
            result = self.parser.parse(text, context)
            status = result.failure.value if result.failure else "ok"
            return result
        finally:
            PARSE_DURATION.labels("parse", status).observe(perf_counter() - started)
