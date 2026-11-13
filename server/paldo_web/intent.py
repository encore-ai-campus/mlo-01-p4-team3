"""해석기 계측과 설정 선택. 응용은 Django·Prometheus에 의존하지 않는다."""
from prometheus_client import Histogram
from time import perf_counter


def make_bert_parser():
    """검증된 로컬 D 체크포인트를 기존 무인자 팩토리 설정에 연결한다.

    운영 전환은 성능 판정 후 INTENT_PARSER_FACTORY를 명시적으로 바꿀 때만 한다.
    로드 실패는 설정 오류이며 기준선으로 조용히 대체하지 않는다.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured
    run_dir = getattr(settings, 'INTENT_BERT_RUN_DIR', '')
    if not isinstance(run_dir, str) or not run_dir.strip():
        raise ImproperlyConfigured('BERT 체크포인트 경로 설정이 필요합니다.')
    try:
        from intent.bert import BertIntentParser
        return BertIntentParser(run_dir)
    except (ValueError, OSError, ImportError):
        raise ImproperlyConfigured('BERT 체크포인트를 로드할 수 없습니다.') from None

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
