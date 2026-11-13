"""B-05-01 운영 상태와 요청 계측을 검증한다."""
import json
from unittest.mock import patch
from uuid import UUID

from django.db import OperationalError
from django.test import TestCase

from server.paldo_web.middleware import JsonFormatter


class ProbeTests(TestCase):
    """골격만 있는 상태에서 HTTP 요청과 DB 장애를 재현한다."""

    def test_health_readiness_and_telemetry(self):
        """성공한 요청마다 ID와 JSON 로그가 생성되고 지표가 노출된다."""
        with self.assertLogs("paldo.web", "INFO") as captured:
            health = self.client.get("/healthz?example=ignored")
            ready = self.client.get("/readyz")
            metrics = self.client.get("/metrics")

        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(ready.json(), {"status": "ready"})
        ids = set()
        for response, record, path in zip((health, ready, metrics), captured.records,
                                          ("/healthz", "/readyz", "/metrics"), strict=True):
            self.assertEqual(response.status_code, 200)
            request_id = response["X-Request-ID"]
            self.assertEqual(UUID(request_id).version, 4)
            ids.add(request_id)
            data = json.loads(JsonFormatter().format(record))
            self.assertEqual(data["request_id"], request_id)
            self.assertEqual(data["path"], path)
            self.assertEqual(data["status"], 200)
            self.assertGreaterEqual(data["duration_ms"], 0)
            self.assertIsNone(data["game_id"])
        self.assertEqual(len(ids), 3)
        self.assertContains(metrics, "# TYPE paldo_http_request_duration_seconds histogram")
        self.assertContains(metrics, '# TYPE paldo_http_requests_total counter')
        self.assertContains(metrics, 'method="GET",route="/healthz",status="200"')

    def test_database_unavailable(self):
        """DB가 연결되지 않으면 두 상태 확인 경로가 503을 반환한다."""
        with patch("server.paldo_web.probes.connection.cursor", side_effect=OperationalError("test error")):
            health = self.client.get("/healthz")
            ready = self.client.get("/readyz")
        self.assertEqual(health.status_code, 503)
        self.assertEqual(health.json(), {"status": "unavailable"})
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json(), {"status": "not_ready"})
