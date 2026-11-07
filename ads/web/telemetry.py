"""토큰·본문을 남기지 않는 광고 구조화 로그와 Prometheus 지표."""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()
SERVES = Counter("paldo_ad_serve_total", "Ad serve HTTP responses", ["filled"], registry=REGISTRY)
DURATION = Histogram("paldo_ad_serve_duration_seconds", "Ad serve latency", ["stage", "status"], registry=REGISTRY)
IMPRESSION = Counter("paldo_ad_impression_total", "Impression judgements", ["status"], registry=REGISTRY)
CLICK = Counter("paldo_ad_click_total", "Click judgements", ["status"], registry=REGISTRY)
for value in ("true", "false"):
    SERVES.labels(value)
for counter in (IMPRESSION, CLICK):
    for value in ("valid", "invalid", "pending"):
        counter.labels(value)
LOGGER = logging.getLogger("paldo.ads")


class JsonFormatter(logging.Formatter):
    """허용된 필드만 JSON 한 줄로 출력한다."""
    def format(self, record):
        """예외 원문·쿼리·접속 헤더를 제외하고 구조화한다."""
        return json.dumps(dict(ts=datetime.now(timezone.utc).isoformat(),
            level=record.levelname.lower(), service="ad-server", component="http",
            msg=record.getMessage(), **getattr(record, "fields", {})), ensure_ascii=False)


class RequestLogMiddleware:
    """게임 웹이 전달한 X-Request-ID를 유지하고 응답·판정을 기록한다."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """본문·토큰 없이 처리 시간과 결과를 기록한다."""
        start = time.perf_counter()
        forwarded = request.headers.get("X-Request-ID", "")
        request.request_id = forwarded if forwarded and len(forwarded) <= 128 else str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        match = getattr(request, "resolver_match", None)
        fields = dict(request_id=request.request_id, route=match.route if match else "unmatched",
            status=response.status_code, duration_ms=round((time.perf_counter() - start) * 1000, 3),
            serve_id=None, charge_id=None)
        fields.update(getattr(request, "ad_fields", {}))
        if response.status_code >= 500:
            fields["error_type"] = "http_server_error"
        LOGGER.log(logging.ERROR if response.status_code >= 500 else logging.INFO,
                   "ad_http", extra={"fields": fields})
        return response
