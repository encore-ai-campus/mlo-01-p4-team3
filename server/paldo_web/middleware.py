"""본문·쿼리·인증 정보를 제외한 HTTP 로그와 Prometheus 계측."""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from prometheus_client import Counter, Histogram
from engine.domain.model import RejectCode

REQUESTS = Counter("paldo_http_requests_total", "HTTP requests", ["route", "method", "status"])
DURATION = Histogram("paldo_http_request_duration_seconds", "HTTP latency", ["route", "method", "status"])
LOGIN_RESULTS = Counter("paldo_auth_login_total", "Authentication results", ["result"])
LOGGER = logging.getLogger("paldo.web")


class JsonFormatter(logging.Formatter):
    """허용된 구조화 필드만 기록하고 예외의 원문·로컬 변수는 제외한다."""

    def format(self, record):
        """JSON 한 줄을 만든다."""
        data = {"ts": datetime.now(timezone.utc).isoformat(), "level": record.levelname.lower(),
                "service": "game-web", "component": getattr(record, "component", "http"),
                "msg": record.getMessage()}
        data.update(getattr(record, "fields", {}))
        return json.dumps(data, ensure_ascii=False)


class RequestTelemetryMiddleware:
    """요청마다 새 ID를 발급하고 정규화 경로로 집계한다."""

    def __init__(self, get_response):
        """다음 Django 처리기를 보관한다."""
        self.get_response = get_response

    def __call__(self, request):
        """응답 ID를 설정하고 요청 결과를 한 번 기록한다."""
        started = time.perf_counter()
        request.request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        match = getattr(request, "resolver_match", None)
        route = "/" + match.route if match else "unmatched"
        method = request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        seconds = time.perf_counter() - started
        labels = (route, method, str(response.status_code))
        REQUESTS.labels(*labels).inc()
        DURATION.labels(*labels).observe(seconds)
        fields = {"request_id": request.request_id, "path": route, "status": response.status_code,
                  "duration_ms": round(seconds * 1000, 3), "game_id": getattr(request, "game_id", None)}
        reject = getattr(request, 'command_reject', None)
        if response.status_code == 422 and isinstance(reject, RejectCode):
            fields['reject'] = reject.value
        if response.status_code >= 500:
            fields["error_type"] = "http_server_error"
        LOGGER.log(logging.ERROR if response.status_code >= 500 else logging.INFO,
                   "http_request", extra={"fields": fields})
        return response
