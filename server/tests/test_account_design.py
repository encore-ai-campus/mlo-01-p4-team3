"""B-10-08의 판 시작 전 브랜딩 경계와 기존 게임 경로를 검증한다."""
from uuid import uuid4
import json
import re
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class AccountDesign(TestCase):
    """호랑이는 계정 화면에만 있고 명령·인증 계약은 유지한다."""

    def test_account_pages_use_official_logo_night_and_title(self):
        """원격 시안이나 별도 그림 경로 없이 manifest 자원을 사용한다."""
        for path in ('/', '/accounts/login', '/accounts/signup'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<title>창귀</title>')
            self.assertContains(response, '/static/assets/branding/tiger_emblem.png')
            self.assertContains(response, '/static/assets/background/night.png')
            self.assertNotContains(response, '시안용 ID')
            self.assertNotContains(response, 'data:image/png;base64')
            self.assertNotContains(response, '임시 배경')
            self.assertNotContains(response, '/static/images/account-night-temporary.png')

    def test_game_morning_meeting_and_night_do_not_request_logo(self):
        """계정에서 공유 헤더를 써도 실제 게임 모든 확인 단계에는 로고 URL이 없다."""
        user = User.objects.create_user('branding-boundary')
        profile = install_test_profiles(self)(user)
        self.client.force_login(user)
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        player = str(profile.player_pseudonym)
        game = service.start(StartGameInput(player, 'base6', 'test-session', 'f'), player, True)
        import json
        from django.conf import settings
        recording = json.loads((settings.BASE_DIR / 'fixtures/api/recorded_game_base6.json').read_text(encoding='utf-8'))
        for index, step in enumerate(recording['steps']):
            if index:
                body = dict(step['request'], game_id=game.view.game_id, command_id=str(uuid4()))
                self.assertEqual(self.client.post('/games/' + game.view.game_id + '/commands',
                    json.dumps(body), content_type='application/json').status_code, 200)
            response = self.client.get('/games/' + game.view.game_id, HTTP_ACCEPT='text/html')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<title>창귀</title>')
            self.assertNotContains(response, '/static/assets/branding/')

    def test_public_home_explains_start_without_authentication_form(self):
        """처음 온 사람에게 순서를 안내하고 실제 가입·로그인 경로로 연결한다."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="게임 시작 방법"')
        self.assertContains(response, '가입하고 시작하기')
        self.assertContains(response, 'href="/accounts/signup"')
        self.assertContains(response, 'href="/accounts/login"')
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'name="start_token"')

    def test_start_modes_prioritize_assignment_and_preserve_both_forms(self):
        """배정 전에는 일반 게임 하나, 배정 후에는 테스트만 열며 원래 POST 값은 유지한다."""
        from functools import partial
        from server.app.open_start import StartPolicy, StartVersions
        from server.app.test_sessions import TestSessionService
        from server.paldo_web.composition import configure_session_service
        from .session_store import MemorySessionStore
        user = User.objects.create_user('start-mode-review')
        profile = install_test_profiles(self)(user)
        self.client.force_login(user)
        service = make_service()
        sessions = TestSessionService(MemorySessionStore())
        service.prepare_start = StartPolicy(partial(sessions.prepare, load_game=service.repository.load),
            lambda: StartVersions('rule', 'engine', 'content'), service.repository.load)
        configure_game_service(service)
        configure_session_service(sessions)
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        ordinary = self.client.get('/')
        modes = re.findall(r'<details[^>]*data-start-mode[^>]*>', ordinary.content.decode())
        self.assertEqual(len(modes), 1)
        self.assertIn(' open', modes[0])
        sessions.create('assigned-review', [{'preset_id': 'base6', 'seed': 7}],
            [str(profile.player_pseudonym)], {'rule_version': 'rule', 'engine_version': 'engine', 'content_version': 'content'})
        assigned = self.client.get('/')
        modes = re.findall(r'<details[^>]*data-start-mode[^>]*>', assigned.content.decode())
        self.assertEqual(len(modes), 2)
        self.assertNotIn(' open', modes[0])
        self.assertIn(' open', modes[1])
        self.assertContains(assigned, 'name="start_token"')
        self.assertContains(assigned, 'name="session_id" value="assigned-review"')
        self.assertContains(assigned, 'name="seed" value="7"')
