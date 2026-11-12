"""B-08-03 실제 loopback 소켓·Django 중계·실패 격리 회귀."""
from dataclasses import asdict
import json
import socket
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from ads.domain.model import AdContext, AdRequest, AdSlot
from server.accounts.models import AccountProfile
from server.app.ads_port import AdGateway, CircuitBreaker
from server.app.test_sessions import TestSessionService
from server.infra.ad_client import HTTPAdProvider
from server.paldo_web import composition
from .ad_http_host import LocalAdHost
from .session_store import MemorySessionStore
from .support import make_service


class HTTPAdWire(SimpleTestCase):
    """본문·상태·시간·호출 횟수와 회로 차단을 실제 TCP로 검사한다."""

    def setUp(self):
        """시험 서버와 게이트웨이 자원을 분리한다."""
        self.host = LocalAdHost()
        self.addCleanup(self.host.close)
        self.provider = HTTPAdProvider(self.host.url)
        self.gateway = AdGateway(self.provider)
        self.addCleanup(self.gateway._pool.shutdown, wait=True)
        self.addCleanup(self.gateway._relay_pool.shutdown, wait=True)
        self.req = AdRequest('wire-request', AdSlot.LOBBY_BANNER, 'ad-only', AdContext())

    def test_wire_dto_and_all_four_paths(self):
        """네 포트 메서드는 계약 경로·본문·상태와 요청 ID를 사용한다."""
        response, result = self.gateway.fetch(self.req)
        self.assertEqual(result, 'ok')
        self.assertEqual(response.request_id, self.req.request_id)
        self.assertEqual(self.host.calls[-1]['body'], json.loads(json.dumps(asdict(self.req))))
        self.assertEqual(self.gateway.report_impression(response.serve_token, .5, 1000, 7, 'imp'), (True, 'ok'))
        self.assertEqual(self.host.calls[-1]['body'], dict(token=response.serve_token, visible_ratio=.5,
                                                         visible_ms=1000, occurred_at_ms=7))
        self.assertEqual(self.gateway.click(response.serve_token, 'click'), ('/ads/landing/sample', 'ok'))
        self.assertEqual(len(self.host.calls), 3)  # 302는 따라가지 않는다.
        self.assertIn('가상 랜딩', self.gateway.landing('sample', 'landing')[0])
        self.assertEqual([c['headers']['X-Request-ID'] for c in self.host.calls], ['wire-request', 'imp', 'click', 'landing'])
        forbidden = {'cookie', 'authorization', 'x-forwarded-for', 'user-agent', 'referer'}
        for call in self.host.calls:
            self.assertFalse(forbidden.intersection(k.lower() for k in call['headers']))
            self.assertLessEqual({k.lower() for k in call['headers']},
                                 {'host', 'content-length', 'x-request-id', 'content-type'})

    def test_synthetic_creative_click_and_landing(self):
        """합성 소재의 클릭·랜딩은 실제 HTTP에서도 같은 내부 경로로 이어진다."""
        self.host.location = '/ads/landing/syn:cr-a'
        with self.subTest(path='click'):
            self.assertEqual(self.gateway.click('token', 'click'), (self.host.location, 'ok'))
        with self.subTest(path='landing'):
            body, result = self.gateway.landing('syn:cr-a', 'landing')
            self.assertEqual(result, 'ok')
            self.assertIn('가상 랜딩', body)
        self.assertEqual([call['path'] for call in self.host.calls],
                         ['/ads/v1/click?t=token', self.host.location])

    def test_invalid_http_responses_are_empty_and_not_retried(self):
        """형식 오류·잘못된 ID·HTML·리다이렉트·오류 상태는 빈 광고다."""
        cases = [(200, 'text/html', b'<html>bad</html>'), (200, 'application/json', b'{'),
                 (200, 'application/json', b'{"request_id":"wrong","filled":false}'),
                 (200, 'application/json', b'{"request_id":"wire-request","filled":"false"}'),
                 (200, 'application/json', b'{"request_id":"wire-request","filled":true}'),
                 (200, 'application/json', b'{"request_id":"wire-request","filled":false,"price":123}'),
                 (200, 'application/json', b'{"request_id":"wire-request","filled":false,"policy_version":null}'),
                 (302, 'application/json', b'{}'), (503, 'application/json', b'{}')]
        for case in cases:
            self.gateway.breaker = CircuitBreaker()
            self.host.response_override = case
            before = len(self.host.calls)
            response, result = self.gateway.fetch(self.req)
            self.assertFalse(response.filled)
            self.assertEqual(result, 'error')
            self.assertEqual(len(self.host.calls), before + 1)

    def test_empty_response_is_success_without_opening_breaker(self):
        """정상 미제공은 서버 실패로 세지 않는다."""
        self.host.response_override = (200, 'application/json', b'{"request_id":"wire-request","filled":false}')
        for _ in range(6):
            self.assertEqual(self.gateway.fetch(self.req)[1], 'empty')
        self.assertFalse(self.gateway.breaker.is_open)

    def test_total_deadline_includes_delayed_and_trickling_bodies(self):
        """무응답·3초 지연·조금씩 오는 본문도 전체 800ms로 제한한다."""
        for mode in ('delay', 'unresponsive', 'trickle'):
            self.gateway.breaker = CircuitBreaker()
            self.host.mode = mode
            started = time.monotonic()
            self.assertEqual(self.gateway.fetch(self.req)[1], 'timeout')
            self.assertLess(time.monotonic() - started, 1.3)

    def test_connect_deadline_and_no_retry(self):
        """연결 설정은 100ms이며 연결 시간 초과를 재시도하지 않는다."""
        def fail(connection):
            self.assertEqual(connection.timeout, .1)
            raise TimeoutError()
        with patch('http.client.HTTPConnection.connect', autospec=True, side_effect=fail) as connect:
            self.assertEqual(self.gateway.fetch(self.req)[1], 'timeout')
        self.assertEqual(connect.call_count, 1)

    def test_stopped_server_fails_closed(self):
        """listener 중단은 한 번의 연결 실패로 빈 광고를 반환한다."""
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            provider = HTTPAdProvider(f'http://127.0.0.1:{listener.getsockname()[1]}')
            gateway = AdGateway(provider)
            try:
                response, result = gateway.fetch(self.req)
                self.assertFalse(response.filled)
                self.assertIn(result, {'error', 'timeout'})
            finally:
                gateway._pool.shutdown()
                gateway._relay_pool.shutdown()

    def test_shared_circuit_five_failures_thirty_seconds_and_recovery(self):
        """serve·중계의 다섯 실패 후 30초 생략과 반열림 회복을 확인한다."""
        now = [0.]
        self.gateway.breaker = CircuitBreaker(clock_ms=lambda: now[0])
        self.host.mode = 'error'
        for _ in range(4):
            self.assertEqual(self.gateway.fetch(self.req)[1], 'error')
        self.assertEqual(self.gateway.report_impression('token', .5, 1000, 7, 'imp')[1], 'error')
        count = len(self.host.calls)
        for call in (lambda: self.gateway.fetch(self.req), lambda: self.gateway.click('token', 'click'),
                     lambda: self.gateway.landing('sample', 'landing')):
            self.assertEqual(call()[1], 'circuit_open')
        now[0] = 29_999
        self.assertEqual(self.gateway.fetch(self.req)[1], 'circuit_open')
        self.assertEqual(len(self.host.calls), count)
        now[0] = 30_000
        self.host.mode = 'normal'
        self.assertEqual(self.gateway.fetch(self.req)[1], 'ok')
        self.assertFalse(self.gateway.breaker.is_open)

    def test_relay_failures_and_external_redirect_never_followed(self):
        """중계 실패는 실패 값이며 외부·변형 랜딩 Location은 따라가지 않는다."""
        for location in ('https://example.invalid/', '//example.invalid/', '/ads/landing/a?x=1'):
            self.host.location = location
            self.assertEqual(self.gateway.click('token', 'click')[1], 'error')
        self.host.mode = 'error'
        self.gateway.breaker = CircuitBreaker()
        self.assertEqual(self.gateway.report_impression('token', .5, 1000, 7, 'imp'), (False, 'error'))
        self.assertEqual(self.gateway.landing('sample', 'landing'), (None, 'error'))

    def test_invalid_origins_and_ids_cannot_change_destination(self):
        """설정 인증·경로 및 요청 ID 헤더 삽입·소재 경로 변조를 거부한다."""
        for url in ('file:///tmp/a', 'http://user:password@localhost', 'http://localhost/other',
                    'http://localhost?token=secret', 'http://localhost#part'):
            with self.assertRaises(ValueError):
                HTTPAdProvider(url)
        self.assertEqual(self.gateway.landing('../other', 'landing')[1], 'error')
        self.req.request_id = 'bad\r\nCookie: private'
        self.assertEqual(self.gateway.fetch(self.req)[1], 'error')
        self.assertEqual(self.host.calls, [])


class AdFaultCriteria(SimpleTestCase):
    """광고 장애 시험의 합격 기준을 별도 경계값으로 확인한다."""

    def test_p95_error_rate_and_relay_boundaries(self):
        """50ms는 허용하고 초과·게임 오류·중계 단절은 실패로 판정한다."""
        from .ad_fault_bench import assess_case
        baseline = dict(requests=100, errors=0, finished_games=1, ad_errors=0, beacon_acks=10,
                        beacon_errors=0, click_requests=10, click_errors=0, beacon_relayed=10,
                        beacon_failed=0, p95_ms=200)
        self.assertTrue(assess_case(baseline))
        fault = dict(baseline, p95_ms=250, beacon_relayed=0, beacon_failed=10)
        self.assertTrue(assess_case(fault, baseline))
        for change in ({'p95_ms': 250.001}, {'errors': 1}, {'beacon_errors': 1}, {'click_errors': 1},
                       {'finished_games': 0}, {'beacon_failed': 0}):
            self.assertFalse(assess_case(dict(fault, **change), baseline))


class AdHTTPRoutes(TestCase):
    """게임 Django 경로에서 내부 HTTP로 나가는 요청과 정보 경계를 검사한다."""

    def setUp(self):
        """메모리 회차와 시험 계정을 사용하며 외부 DB에 연결하지 않는다."""
        self.host = LocalAdHost()
        self.addCleanup(self.host.close)
        self.user = User.objects.create_user(username='http-private-login')
        self.profile = AccountProfile.objects.create(user=self.user, consented_at=timezone.now())
        self.client.force_login(self.user)
        composition.configure_session_service(TestSessionService(MemorySessionStore()))
        composition.configure_game_service(make_service())
        composition.configure_ad_gateway(None)
        self.addCleanup(composition.configure_session_service, None)
        self.addCleanup(composition.configure_game_service, None)
        self.addCleanup(composition.configure_ad_gateway, None)
        self.override = override_settings(ADS_INTERNAL_URL=self.host.url)
        self.override.enable()
        self.addCleanup(self.override.disable)
        gateway = composition.get_ad_gateway()
        self.addCleanup(gateway._pool.shutdown, wait=True)
        self.addCleanup(gateway._relay_pool.shutdown, wait=True)

    def test_three_slots_click_landing_and_private_headers(self):
        """세 지면과 중계는 같은 출처이며 실제 내부 HTTP에 계정 정보가 없다."""
        for slot in AdSlot:
            response = self.client.get('/ads/slot/' + slot.value, HTTP_AUTHORIZATION='private-auth',
                HTTP_X_FORWARDED_FOR='private-address', HTTP_USER_AGENT='private-browser', HTTP_REFERER='private-referrer')
            self.assertContains(response, '가상 광고')
            self.assertNotContains(response, self.host.url)
            call = self.host.calls[-1]
            self.assertEqual(call['headers']['X-Request-ID'], response['X-Request-ID'])
            self.assertNotEqual(call['body']['session_pseudonym'], str(self.profile.player_pseudonym))
        click = self.client.get('/ads/click', {'t': 'b0803.test-token'})
        self.assertEqual(click['Location'], '/ads/landing/sample')
        self.assertContains(self.client.get(click['Location']), '가상 랜딩')
        serialized = json.dumps(self.host.calls)
        for secret in (self.user.username, str(self.profile.player_pseudonym), 'private-auth', 'private-address',
                       'private-browser', 'private-referrer', self.client.session.session_key):
            self.assertNotIn(secret, serialized)

    def test_failed_server_has_empty_slot_safe_click_and_landing(self):
        """장애는 빈 지면·홈으로 보내지 않는 안내·돌아가기이며 게임 진척은 계속 응답한다."""
        self.host.mode = 'error'
        self.assertEqual(self.client.get('/ads/slot/lobby_banner').content.strip(), b'')
        click = self.client.get('/ads/click', {'t': 'token'})
        self.assertEqual(click.status_code, 200)
        self.assertNotIn('Location', click)
        self.assertContains(click, '광고를 불러오지 못했습니다')
        self.assertContains(self.client.get('/ads/landing/sample'), '돌아가기')
        self.assertEqual(self.client.get('/players/me/progress').status_code, 200)

    def test_failure_home_reuses_progress_and_game_only_within_request(self):
        """실패 후 홈에서도 공개 선택·회차 표시를 유지하고 중복 조회만 줄인다."""
        game = composition.get_game_service()
        response = self.client.post('/games', dict(preset_id='base6', session_id='test-session',
                                    mudang_variant='f'), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        gid = response.json()['view']['game_id']
        sessions = composition.get_session_service()
        def assigned(player, load_game):
            load_game(gid)
            return []
        with patch.object(sessions, 'assigned', side_effect=assigned), \
             patch.object(game.progress, 'load', wraps=game.progress.load) as progress, \
             patch.object(game.repository, 'load', wraps=game.repository.load) as load:
            for expected in (1, 2):
                self.assertEqual(self.client.get('/').status_code, 200)
                self.assertEqual(progress.call_count, expected)
                self.assertEqual(load.call_count, expected)

    @override_settings(ADS_INTERNAL_URL='')
    def test_unconfigured_and_explicit_injection(self):
        """환경 미설정은 미연결이며 명시적 시험 조립은 환경보다 우선한다."""
        composition.configure_ad_gateway(None)
        with self.assertRaises(composition.GameServiceUnavailable):
            composition.get_ad_gateway()
        marker = object()
        composition.configure_ad_gateway(marker)
        self.assertIs(composition.get_ad_gateway(), marker)

    @override_settings(ADS_INTERNAL_URL='http://invalid:bad-port')
    def test_invalid_configuration_still_returns_an_empty_slot(self):
        """접속 설정 오류도 게임 응답으로 번지지 않는다."""
        composition.configure_ad_gateway(None)
        self.assertEqual(self.client.get('/ads/slot/lobby_banner').content.strip(), b'')
        self.assertEqual(self.client.get('/players/me/progress').status_code, 200)
