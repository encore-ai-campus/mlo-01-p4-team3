"""B-07-05 출처·인증·광고 실패 격리와 HTTP 중계 회귀 시험."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
import time
from unittest.mock import patch
from uuid import uuid4
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TransactionTestCase, SimpleTestCase
from django.utils import timezone
from prometheus_client import REGISTRY
from ads.testing.fakes import FakeAdProvider
from server.app.ads_port import AdGateway, CircuitBreaker
from server.paldo_web.composition import configure_ad_gateway
from server.paldo_web.asgi import application


def message(**changes):
    """유효한 노출 비콘을 만들고 시험별 오류를 주입한다."""
    data = {"type": "ad.impression", "msg_id": str(uuid4()), "token": str(uuid4()),
            "visible_ratio": 0.5, "visible_ms": 1000, "occurred_at_ms": 1000}
    return dict(data, **changes)


class WebsocketContract(TransactionTestCase):
    """실제 Django 세션을 쓰는 ASGI 연결 시험."""

    def setUp(self):
        """인증 세션과 실패 주입 광고 제공자를 조립한다."""
        self.user = User.objects.create_user(username="ws-pilot")
        self.client.force_login(self.user)
        self.cookie = (settings.SESSION_COOKIE_NAME + "=" + self.client.cookies[settings.SESSION_COOKIE_NAME].value).encode()
        self.provider = FakeAdProvider()
        configure_ad_gateway(AdGateway(self.provider))

    def tearDown(self):
        """시험 광고 제공자를 해제한다."""
        configure_ad_gateway(None)

    def connection(self, origin=b"http://testserver", cookie=True):
        """허용 출처와 실제 세션 쿠키로 연결기를 만든다."""
        headers = [] if origin is None else [(b"origin", origin)]
        if cookie:
            headers.append((b"cookie", self.cookie))
        return WebsocketCommunicator(application, "/ws/game/", headers=headers)

    async def test_origin_exact_and_anonymous_close(self):
        """출처 없음·스킴·포트·호스트 위반은 거부하고 익명은 수락 후 4401이다."""
        for origin in (None, b"https://testserver", b"http://testserver:8000", b"http://other.test"):
            ws = self.connection(origin=origin)
            self.assertFalse((await ws.connect())[0])
            await ws.disconnect()
        ws = self.connection(cookie=False)
        self.assertTrue((await ws.connect())[0])
        self.assertEqual((await ws.receive_output())["code"], 4401)
        labels = {"reason": "queue_full"}
        before = REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", labels)
        await ws.send_json_to({"type": "hello", "dropped": 1,
                               "dropped_by_reason": {"queue_full": 1, "page_exit": 0}})
        self.assertTrue(await ws.receive_nothing(timeout=.01))
        self.assertEqual(REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", labels), before)
        await ws.disconnect()

    async def test_valid_relay_and_no_participant_headers(self):
        """비콘·응답이 계약 필드와 같고 헤더·로그인 식별자는 포트에 전달되지 않는다."""
        ws = self.connection()
        self.assertTrue((await ws.connect())[0])
        await ws.send_json_to({"type": "hello", "dropped": 0,
                               "dropped_by_reason": {"queue_full": 0, "page_exit": 0}})
        event = message()
        await ws.send_json_to(event)
        self.assertEqual(await ws.receive_json_from(), {"type": "ack", "msg_id": event["msg_id"], "relayed": True})
        args = self.provider.relay_calls[0]
        self.assertEqual(args[:5], ("impression", event["token"], 0.5, 1000, 1000))
        self.assertEqual(args[-2:], (100, 300))
        self.assertNotIn(self.cookie.decode(), repr(args))
        self.assertNotIn(self.user.username, repr(args))
        await ws.disconnect()

    async def test_hello_counts_valid_reasons_once(self):
        """잘못된 보고는 집계하지 않고, 같은 연결의 정상 보고는 한 번만 받는다."""
        def dropped_counts():
            return tuple(REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", {"reason": reason})
                         for reason in ("queue_full", "page_exit", "rate_limited"))

        ws = self.connection()
        await ws.connect()
        before = dropped_counts()
        valid = {"type": "hello", "dropped": 3, "dropped_by_reason": {"queue_full": 2, "page_exit": 1}}
        invalid_reports = (
            {"type": "hello", "dropped": 3},
            {**valid, "dropped": -1},
            {**valid, "dropped": 4},
            {**valid, "dropped_by_reason": {"queue_full": -1, "page_exit": 4}},
            {**valid, "dropped_by_reason": {"queue_full": True, "page_exit": 2}},
            {**valid, "dropped_by_reason": {"queue_full": 2.0, "page_exit": 1}},
            {**valid, "dropped_by_reason": {"queue_full": 3}},
            {**valid, "dropped_by_reason": {"queue_full": 2, "page_exit": 1, "rate_limited": 0}},
            {**valid, "dropped": 10**400, "dropped_by_reason": {"queue_full": 10**400, "page_exit": 0}},
        )
        error = {"type": "error", "msg_id": None, "code": "bad_message"}
        for invalid in invalid_reports:
            with self.subTest(hello=invalid):
                await ws.send_json_to(invalid)
                self.assertEqual(await ws.receive_json_from(), error)
                self.assertEqual(dropped_counts(), before)
        await ws.send_json_to(valid)
        await ws.send_json_to(valid)
        self.assertEqual(await ws.receive_json_from(), error)
        self.assertEqual(dropped_counts(), (before[0] + 2, before[1] + 1, before[2]))
        self.assertIsNone(REGISTRY.get_sample_value("paldo_ad_beacon_reported_dropped_total"))
        event = message()
        await ws.send_json_to(event)
        self.assertTrue((await ws.receive_json_from())["relayed"])
        await ws.disconnect()

    async def test_message_errors_size_and_rate(self):
        """오류에는 연결을 유지하고 크기 초과만 1009로 닫는다."""
        ws = self.connection()
        await ws.connect()
        rate_labels = {"reason": "rate_limited"}
        rate_before = REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", rate_labels)
        for raw, code in (("{", "bad_message"), ('[]', "bad_message"), ('{"type":"game.command"}', "unknown_type")):
            await ws.send_to(text_data=raw)
            self.assertEqual((await ws.receive_json_from())["code"], code)
        for _ in range(2):
            await ws.send_to(text_data="{")
            self.assertEqual((await ws.receive_json_from())["code"], "bad_message")
        await ws.send_json_to({"type": "other"})
        self.assertEqual((await ws.receive_json_from())["code"], "rate_limited")
        # A valid hello does not consume the five-message budget.
        await ws.send_json_to({"type": "hello", "dropped": 0,
                               "dropped_by_reason": {"queue_full": 0, "page_exit": 0}})
        self.assertTrue(await ws.receive_nothing(timeout=.01))
        self.assertEqual(REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", rate_labels), rate_before)
        await ws.disconnect()
        ws = self.connection()
        await ws.connect()
        for changes in ({"visible_ratio": True}, {"visible_ratio": float("nan")}, {"visible_ms": -1}, {"token": ""}, {"extra": 1}):
            await ws.send_json_to(message(**changes))
            self.assertEqual((await ws.receive_json_from())["code"], "bad_message")
        await ws.send_json_to(message())
        self.assertEqual((await ws.receive_json_from())["code"], "rate_limited")
        self.assertEqual(REGISTRY.get_sample_value("paldo_ad_beacon_dropped_total", rate_labels), rate_before + 1)
        await ws.send_to(text_data="한" * 1366)
        self.assertEqual((await ws.receive_output())["code"], 1009)
        await ws.disconnect()

    async def test_out_of_range_integer_ratio_keeps_connection_open(self):
        """실수로 변환할 수 없는 큰 정수도 거부하고 다음 비콘은 처리한다."""
        ws = self.connection()
        await ws.connect()
        invalid = message(visible_ratio=10**400)
        await ws.send_json_to(invalid)
        self.assertEqual(await ws.receive_json_from(),
                         {"type": "error", "msg_id": invalid["msg_id"], "code": "bad_message"})
        self.assertEqual(self.provider.relay_calls, [])
        valid = message()
        await ws.send_json_to(valid)
        self.assertEqual(await ws.receive_json_from(),
                         {"type": "ack", "msg_id": valid["msg_id"], "relayed": True})
        await ws.disconnect()

    async def test_logout_expiry_password_reset_close(self):
        """연결 후 세션 삭제·만료·비밀번호 변경은 비콘 전달 전에 막는다."""
        for mode in ("logout", "expire", "password"):
            await sync_to_async(self.client.force_login)(self.user)
            self.cookie = (settings.SESSION_COOKIE_NAME + "=" + self.client.cookies[settings.SESSION_COOKIE_NAME].value).encode()
            ws = self.connection()
            await ws.connect()
            if mode == "logout":
                await sync_to_async(self.client.logout)()
            elif mode == "expire":
                await sync_to_async(Session.objects.update)(expire_date=timezone.now() - timedelta(seconds=1))
            else:
                await sync_to_async(self.user.set_unusable_password)()
                await sync_to_async(self.user.save)()
            await ws.send_json_to(message())
            self.assertEqual((await ws.receive_output())["code"], 4401)
            await ws.disconnect()
        self.assertEqual(self.provider.relay_calls, [])

    async def test_async_boundary_and_failure_ack(self):
        """느린 동기 중계 중에도 이벤트 루프가 계속 실행되고 실패는 ack false다."""
        entered = threading.Event()
        release = threading.Event()
        event_loop_thread = threading.get_ident()
        def block(*args):
            self.assertNotEqual(threading.get_ident(), event_loop_thread)
            entered.set()
            release.wait(2)
            return False, "timeout"
        ws = self.connection()
        await ws.connect()
        with patch("server.api.consumers._relay", side_effect=block):
            await ws.send_json_to(message())
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            self.assertTrue(entered.is_set())
            release.set()
            self.assertFalse((await ws.receive_json_from())["relayed"])
        await ws.disconnect()

    def test_http_click_landing_auth_failure_and_safe_redirect(self):
        """성공·실패·잘못된 외부 Location과 비로그인 처리를 확인한다."""
        self.assertEqual(self.client.get("/ads/click?t=fake")["Location"], "/ads/landing/cr_fake")
        self.assertContains(self.client.get("/ads/landing/cr_fake"), "가상 광고")
        with patch.object(self.provider, "send_click", return_value="/ads/landing/syn:cr_fake"):
            self.assertEqual(self.client.get("/ads/click?t=fake")["Location"], "/ads/landing/syn:cr_fake")
        self.assertEqual(self.client.get("/ads/landing/syn:cr_fake").status_code, 200)
        self.provider.fail = True
        failed = self.client.get("/ads/click?t=fake")
        self.assertEqual(failed.status_code, 200)
        self.assertNotIn("Location", failed)
        self.assertContains(failed, "돌아가기")
        self.assertContains(self.client.get("/ads/landing/cr_fake"), "돌아가기")
        self.provider.fail = False
        with patch.object(self.provider, "send_click", return_value="https://evil.test/"):
            evil = self.client.get("/ads/click?t=fake")
            self.assertEqual(evil.status_code, 200)
            self.assertNotIn("Location", evil)
        self.client.logout()
        self.assertEqual(self.client.get("/ads/click?t=fake").status_code, 302)
        self.assertEqual(self.client.get("/ads/landing/cr_fake").status_code, 302)


class GatewayRelay(SimpleTestCase):
    """중계 상한·회로 공유·시간 제한을 검증한다."""

    def test_shared_breaker_and_timeout_without_retry(self):
        """중계 실패 다섯 번은 serve와 같은 회로를 열고 재시도하지 않는다."""
        provider = FakeAdProvider(fail=True)
        now = [0]
        gateway = AdGateway(provider, CircuitBreaker(clock_ms=lambda: now[0]))
        for _ in range(5):
            self.assertEqual(gateway.click("x", "request"), (None, "error"))
        self.assertEqual(gateway.report_impression("x", 0.5, 1000, 1000, "request"), (False, "circuit_open"))
        self.assertEqual(len(provider.relay_calls), 5)
        now[0] = 30000
        provider.fail = False
        self.assertTrue(gateway.landing("cr_fake", "request")[0])
        slow = FakeAdProvider(latency_ms=80)
        self.assertEqual(AdGateway(slow, total_timeout_ms=10).click("x", "request"), (None, "timeout"))
        self.assertEqual(len(slow.relay_calls), 1)

    def test_relay_limit_is_separate_from_serve(self):
        """중계 슬롯이 가득 차도 serve 슬롯은 비어 있고 다음 중계는 대기하지 않는다."""
        gateway = AdGateway(FakeAdProvider(), max_concurrent=1)
        gateway._relay_slots.acquire()
        try:
            self.assertEqual(gateway.click("x", "request"), (None, "busy"))
            self.assertTrue(gateway._slots.acquire(blocking=False))
            gateway._slots.release()
        finally:
            gateway._relay_slots.release()
