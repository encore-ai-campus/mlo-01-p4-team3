"""B-07-03 지면·가명 분리·실패 격리·게임 처리의 광고 미호출 시험."""
import json
import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from ads.domain.model import AdCreativeView, AdSlot, SIZE_SHAPES, SLOT_SIZES
from ads.testing.fakes import FakeAdProvider
from server.api.ad_slots import FETCH_TOTAL
from server.accounts.models import AccountProfile
from server.app.ads_port import AdGateway, CircuitBreaker
from server.app.test_sessions import TestSessionService
from server.paldo_web.composition import configure_ad_gateway, configure_game_service, configure_session_service
from server.tests.session_store import MemorySessionStore
from server.tests.support import make_service


class AdSlots(TestCase):
    """실제 Django 요청과 가짜 광고 포트의 경계를 확인한다."""

    def setUp(self):
        """동의한 계정과 광고 가명 저장소·녹화 판을 분리한다."""
        self.user = User.objects.create_user(username="slot-private-login")
        self.profile = AccountProfile.objects.create(user=self.user, consented_at=timezone.now())
        self.client.force_login(self.user)
        self.sessions = TestSessionService(MemorySessionStore())
        configure_session_service(self.sessions)
        self.addCleanup(configure_session_service, None)
        self.game = make_service()
        configure_game_service(self.game)
        self.addCleanup(configure_game_service, None)
        self.provider = FakeAdProvider()
        self.gateway = AdGateway(self.provider)
        self.addCleanup(self.gateway._pool.shutdown, wait=True)
        self.addCleanup(self.gateway._relay_pool.shutdown, wait=True)
        configure_ad_gateway(self.gateway)
        self.addCleanup(configure_ad_gateway, None)

    def test_three_slots_dimensions_same_origin_and_pseudonym(self):
        """세 지면의 규격·문구·이미지 경로와 별도 광고 가명을 확인한다."""
        for slot in AdSlot:
            response = self.client.get(f"/ads/slot/{slot.value}")
            self.assertContains(response, "광고")
            width, height = SLOT_SIZES[slot]
            self.assertContains(response, f'width="{width}" height="{height}"')
            self.assertContains(response, f'/static/promo/fake__{SIZE_SHAPES[(width, height)]}.png')
            self.assertContains(response, '/ads/click?t=fake.')
            self.assertNotContains(response, '/ads/v1/')
            self.assertNotContains(response, self.user.username)
            self.assertEqual(self.provider.calls[-1].request_id, response["X-Request-ID"])
        identities = {call.session_pseudonym for call in self.provider.calls}
        self.assertEqual(len(identities), 1)
        self.assertNotIn(str(self.profile.player_pseudonym), identities)
        uuid.UUID(identities.pop())
        self.assertEqual(self.client.get('/ads/slot/llm_wait').status_code, 404)

    def test_identity_authentication_and_external_image_block(self):
        """로그인·동의 경계와 광고 서버 출처 이미지 차단을 확인한다."""
        self.assertEqual(Client().get('/ads/slot/lobby_banner', HTTP_ACCEPT='application/json').status_code, 401)
        self.profile.consented_at = None
        self.profile.save()
        self.assertEqual(self.client.get('/ads/slot/lobby_banner').status_code, 403)
        self.profile.consented_at = timezone.now()
        self.profile.save()
        self.provider.creative = AdCreativeView('external', 'title', 'body', 'https://ads.example.invalid/p.png')
        response = self.client.get('/ads/slot/lobby_banner')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.strip(), b'')
        self.assertNotContains(response, 'ads.example.invalid')
        configure_ad_gateway(None)
        response = self.client.get('/ads/slot/lobby_banner')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.strip(), b'')

    def test_fetch_result_metrics_and_safe_empty_responses(self):
        """광고 결과 6종을 지표로 구분하고 실패도 빈 HTML을 반환한다."""
        def check(result):
            counter = FETCH_TOTAL.labels(result)._value
            before = counter.get()
            response = self.client.get('/ads/slot/lobby_banner')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(counter.get(), before + 1)
            if result == 'ok':
                self.assertContains(response, '<small>광고</small>')
                self.assertContains(response, 'data-ad-token=')
            else:
                self.assertEqual(response.content.strip(), b'')

        check('ok')
        self.provider.empty = True
        check('empty')
        self.provider.empty = False
        self.provider.fail = True
        check('error')
        self.provider.fail = False
        self.provider.latency_ms = 40
        self.gateway.total_timeout_ms = 1
        check('timeout')
        self.provider.latency_ms = 0
        self.gateway._pool.shutdown(wait=True)
        # 슬롯 포화와 열린 회로를 시계 대기 없이 주입한다.
        other = AdGateway(self.provider, max_concurrent=1)
        self.addCleanup(other._pool.shutdown, wait=True)
        self.addCleanup(other._relay_pool.shutdown, wait=True)
        configure_ad_gateway(other)
        other._slots.acquire()
        check('busy')
        other._slots.release()
        other.breaker = CircuitBreaker(clock_ms=lambda: 0, is_open=True, open_until_ms=30000)
        check('circuit_open')
        metrics = self.client.get('/metrics').content.decode()
        for result in ('ok', 'empty', 'error', 'timeout', 'busy', 'circuit_open'):
            self.assertIn(f'paldo_ad_fetch_total{{result="{result}"}}', metrics)

    def test_lazy_placements_and_game_commands_never_call_ads(self):
        """회의·투표·밤에는 광고가 없고 판 종료 응답도 호출 없이 자리만 만든다."""
        response = self.client.get('/')
        self.assertContains(response, 'hx-get="/ads/slot/lobby_banner"')
        recording = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/api/recorded_game_base6.json').read_text(encoding='utf-8'))
        gid = self.client.post('/games', json.dumps({'preset_id': 'base6', 'session_id': 'test-session', 'mudang_variant': 'f'}), content_type='application/json').json()['view']['game_id']
        for index, step in enumerate(recording['steps']):
            if index:
                payload = dict(step['request'], game_id=gid, command_id=str(uuid.uuid4()))
                self.assertEqual(self.client.post(f'/games/{gid}/commands', json.dumps(payload), content_type='application/json').status_code, 200)
            response = self.client.get(f'/games/{gid}', HTTP_ACCEPT='text/html')
            if index == len(recording['steps']) - 1:
                self.assertContains(response, 'hx-get="/ads/slot/result_banner"')
            else:
                self.assertNotContains(response, 'data-ad-placeholder')
        self.assertEqual(self.provider.calls, [])

    def test_rendered_click_uses_merged_relay(self):
        """세 지면의 실제 링크가 같은 출처 클릭·랜딩 중계로 이어진다."""
        for slot in AdSlot:
            response = self.client.get(f'/ads/slot/{slot.value}')
            click = self.client.get(response.context['click_path'])
            self.assertRedirects(click, '/ads/landing/cr_fake', fetch_redirect_response=False)
            self.assertContains(self.client.get(click['Location']), '가상 광고')
        self.assertEqual([call[0] for call in self.provider.relay_calls], ['click', 'landing'] * 3)
