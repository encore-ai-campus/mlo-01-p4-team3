"""PM 검토용 에셋 경계와 실제 복기 화면의 종료·소유권 제한을 검사한다."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from server.api.ui_assets import review_asset_url
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class ProposalAssetBoundary(SimpleTestCase):
    """시안 카탈로그가 있어도 지정 디렉터리 밖 이미지를 조회하지 않는다."""

    def test_game_art_uses_manifest_even_when_proposal_exists(self):
        """실제 게임 원화·파생 초상·마스코트·공통 배경은 시안 후보로 덮어쓰지 않는다."""
        from server.api.assets import asset_url
        for content_id in ('sprite:jumo', 'portrait:jumo', 'mascot:kkachi', 'background:meeting', 'background:night'):
            self.assertEqual(review_asset_url(content_id), asset_url(content_id))

    def test_runtime_does_not_read_proposal_catalog(self):
        """시안 파일 없이도 공식 ID로 이미지를 조회한다."""
        from server.api.assets import asset_url
        self.assertEqual(review_asset_url('sprite:mudang_f'), asset_url('sprite:mudang_f'))


@override_settings(GAME_UI='vn')
class ReviewRepresentation(TestCase):
    """브랜치의 새 HTML도 기존 API의 판 종료·소유권 기준을 그대로 따른다."""

    def setUp(self):
        """동의한 합성 계정과 시험 전용 녹화 게임을 조립한다."""
        self.user = User.objects.create_user(username='ui-review-player')
        self.register = install_test_profiles(self)
        self.register(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        started = self.client.post('/games', {'preset_id': 'base6', 'session_id': 'test-session', 'mudang_variant': 'f'},
                                   content_type='application/json')
        self.url = '/games/' + started.json()['view']['game_id']

    def test_html_review_requires_end_and_owner_and_does_not_invent_reasons(self):
        """진행 중 403·다른 계정 404이며 종료 후 실제 기록과 인수 전 이유를 구분한다."""
        self.assertEqual(self.client.get(self.url + '/review', HTTP_ACCEPT='text/html').status_code, 403)
        steps = json.loads((settings.BASE_DIR / 'fixtures/api/recorded_game_base6.json').read_text(encoding='utf-8'))['steps']
        for step in steps[1:]:
            response = self.client.post(self.url + '/commands',
                {**step['request'], 'game_id': self.url.split('/')[-1], 'command_id': str(uuid4())},
                content_type='application/json')
            self.assertEqual(response.status_code, 200)
        html = self.client.get(self.url + '/review', HTTP_ACCEPT='text/html')
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, '그날의 선택 돌아보기')
        self.assertNotContains(html, '현재 믿음')
        self.assertEqual(self.client.get(self.url + '/review').status_code, 200)
        other = User.objects.create_user(username='ui-review-other')
        self.register(other)
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url + '/review', HTTP_ACCEPT='text/html').status_code, 404)

    def test_pm_index_does_not_open_without_login(self):
        """검토 안내는 인증된 계정으로만 열 수 있다."""
        self.client.logout()
        self.assertEqual(self.client.get('/ui-review').status_code, 302)

    def test_pm_comparison_preserves_manifest_and_distinguishes_source_versions(self):
        """공식 파일·시안 후보를 분리하고 여성 무당 버전 차이와 임시 배경을 안내한다."""
        manifest_before = settings.ASSET_MANIFEST.read_bytes()
        response = self.client.get('/ui-review')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['official_assets']), 18)
        self.assertEqual(response.context['placeholder_count'], 84)
        self.assertEqual(len(response.context['backgrounds']), 2)
        self.assertNotContains(response, 'design-preview')
        self.assertContains(response, 'v5')
        self.assertEqual(settings.ASSET_MANIFEST.read_bytes(), manifest_before)

    def test_classic_vn_switch_preserves_state_and_restores_template(self):
        """모드를 왕복해도 API 상태는 같고 각 모드의 화면·스타일을 선택한다."""
        before = self.client.get(self.url).json()
        for mode in ('vn', 'classic', 'vn'):
            with self.settings(GAME_UI=mode):
                response = self.client.get(self.url, HTTP_ACCEPT='text/html')
                self.assertEqual(response.status_code, 200)
                if mode == 'vn':
                    self.assertContains(response, 'data-vn-show-all')
                    self.assertContains(response, '/static/game.css')
                else:
                    self.assertNotContains(response, 'data-vn-stage')
                    self.assertContains(response, '/static/classic-game.css')
                    self.assertEqual(self.client.get('/ui-review').status_code, 404)
                self.assertEqual(self.client.get(self.url).json(), before)
