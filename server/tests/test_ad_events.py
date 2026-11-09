"""광고 중계 응용 규칙: 랜딩 경로 허용, serve_id 추출, 실패 시 홈 대신 None(B-08-03 E 보완)."""
from unittest.mock import patch

from django.test import SimpleTestCase

from ads.domain.model import AdSlot, ServeClaims
from ads.domain.rules import sign_token
from server.api import ad_metrics
from server.app.ad_events import relay_click, serve_id_of


class _Gateway:
    """클릭 결과를 고정해 돌려주는 가짜 게이트웨이."""

    def __init__(self, value):
        self.value = value

    def click(self, token, request_id):
        return self.value


class RelayClickRules(SimpleTestCase):
    """클릭 중계가 허용하는 랜딩 경로."""

    def test_landing_paths_including_synthetic_ids(self):
        """일반·합성("syn:") 소재 랜딩은 그대로, 실패·외부 주소는 None이다."""
        self.assertEqual(relay_click(_Gateway(("/ads/landing/cr_a", "ok")), "t", "r"), ("/ads/landing/cr_a", "ok"))
        self.assertEqual(relay_click(_Gateway(("/ads/landing/syn:cr_a", "ok")), "t", "r"),
                         ("/ads/landing/syn:cr_a", "ok"))
        self.assertEqual(relay_click(_Gateway((None, "timeout")), "t", "r"), (None, "timeout"))
        self.assertEqual(relay_click(_Gateway(("https://evil.test/", "ok")), "t", "r"), (None, "error"))
        self.assertEqual(relay_click(_Gateway(("/ads/landing/../x", "ok")), "t", "r"), (None, "error"))


class ServeIdOf(SimpleTestCase):
    """로그용 serve_id 추출과 중계 로그 필드."""

    def test_reads_serve_id_without_session(self):
        """서명된 토큰에서 serve_id만 꺼내고 세션 가명은 돌려주지 않는다."""
        token = sign_token(ServeClaims("syn:req-1:abc", "session-secret", AdSlot.LOBBY_BANNER, 1, 2), b"k")
        self.assertEqual(serve_id_of(token), "syn:req-1:abc")
        self.assertNotIn("session-secret", serve_id_of(token))

    def test_malformed_tokens(self):
        """형식이 틀린 토큰은 None."""
        for token in (None, "", "abc", "a.b.c", "!!!.sig", "e30.sig", "x" * 5000):
            self.assertIsNone(serve_id_of(token))

    def test_relay_log_carries_serve_id_and_request_id(self):
        """중계 로그에 request_id와 serve_id가 함께 남는다."""
        with patch.object(ad_metrics.LOGGER, "info") as info:
            ad_metrics.relay_result("click", "ok", "req-1", "serve-1")
            ad_metrics.relay_result("landing", "ok", "req-2")
        self.assertEqual(info.call_args_list[0].kwargs["extra"]["fields"],
                         {"request_id": "req-1", "kind": "click", "result": "ok", "serve_id": "serve-1"})
        self.assertNotIn("serve_id", info.call_args_list[1].kwargs["extra"]["fields"])
