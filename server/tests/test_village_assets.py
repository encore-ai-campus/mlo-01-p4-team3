"""지도 좌표·출처 갱신·주민 전체 초상과 게임 상태 비변경을 검증한다."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase

from server.api.village_assets import image_source, village_assets
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class VillageAssets(TestCase):
    """실제 게임 HTML과 에셋 메타데이터를 공개 정보 범위에서 검사한다."""

    def setUp(self):
        """격리 계정과 녹화 게임만 사용한다."""
        self.user = User.objects.create_user(username='village-screen')
        install_test_profiles(self)(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post('/games', dict(preset_id='base6', session_id='test-session',
            mudang_variant='f'), content_type='application/json')
        self.url = '/games/' + response.json()['view']['game_id']

    def test_full_pool_map_labels_and_source_in_full_and_partial_html(self):
        """전체 주민 초상·무당 두 모습·고정 장소·출처가 전체/htmx에서 같다."""
        manifest = json.loads(settings.ASSET_MANIFEST.read_text(encoding='utf-8'))
        expected = [r for r in manifest if r['kind'] == 'portrait' and '__' not in r['id']]
        self.assertTrue(all(p['name'] != p['asset_id'].split(':')[1] for p in village_assets()['portraits']))
        for headers in ({'HTTP_ACCEPT': 'text/html'}, {'HTTP_HX_REQUEST': 'true'}):
            page = self.client.get(self.url, **headers)
            self.assertContains(page, '마을 안내')
            self.assertContains(page, '주민 전체 초상 보기')
            for row in expected:
                self.assertContains(page, f'data-portrait-id="portrait:{row["id"]}"', count=1)
                self.assertContains(page, row['url'])
            for place in ('우물가', '서낭당', '주막', '마을 어귀', '집'):
                self.assertContains(page, place)
            self.assertContains(page, 'Codex 내장 image_gen')
            self.assertContains(page, 'SOURCES.md')
            self.assertGreater(page.content.decode().rfind('asset-credits'), page.content.decode().rfind('</form>'))
            self.assertContains(page, '주민의 현재 위치나 이동을 나타내지 않습니다.')

    def test_map_coordinates_come_from_manifest_not_code(self):
        """같은 지도 ID의 좌표만 바꾸면 화면 배치가 바뀐다."""
        manifest = json.loads(settings.ASSET_MANIFEST.read_text(encoding='utf-8'))
        original = village_assets()['village']['places'][0]['left']
        next(r for r in manifest if r['kind'] == 'map')['regions']['well'][0] += 16
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.settings(ASSET_MANIFEST=path):
                updated = village_assets()['village']['places'][0]['left']
                self.assertNotEqual(original, updated)
                page = self.client.get(self.url, HTTP_HX_REQUEST='true')
                self.assertContains(page, f'left:{updated}%')

    def test_source_is_read_from_file_escaped_and_refreshed(self):
        """출처는 파일을 읽고 HTML을 실행하지 않으며 수정 후 새 요청에 반영한다."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for row in json.loads(settings.ASSET_MANIFEST.read_text(encoding='utf-8')):
                if row['kind'] == 'map':
                    target = root / row['path']
                    target.parent.mkdir(parents=True)
                    target.write_bytes((settings.BASE_DIR / row['path']).read_bytes())
            source = root / 'content/images/SOURCES.md'
            source.write_text('<script>unexpected()</script> · 도구 A\n## 비공개 생성 메모', encoding='utf-8')
            with self.settings(BASE_DIR=root):
                html = render_to_string('asset_credits.html', {'asset_source': image_source()})
                self.assertIn('&lt;script&gt;', html)
                self.assertNotIn('<script>', html)
                self.assertNotIn('비공개 생성 메모', html)
                source.write_text('도구 B · 변경된 출처', encoding='utf-8')
                self.assertEqual(image_source(), '도구 B · 변경된 출처')

    def test_read_only_guide_does_not_change_state_or_reveal_hidden_keys(self):
        """지도 보기와 전체 초상은 상태·역할·실제 이동을 바꾸거나 노출하지 않는다."""
        before = self.client.get(self.url).json()
        page = self.client.get(self.url, HTTP_HX_REQUEST='true')
        after = self.client.get(self.url).json()
        self.assertEqual(before, after)
        for key in ('truth', 'beliefs', 'private_knowledge', 'night_plan'):
            self.assertNotIn(key, page.content.decode())

    def test_night_does_not_add_the_map_or_full_roster_gallery(self):
        """밤에는 새 안내 지도·전체 초상 연출을 넣지 않는다."""
        page = self.client.get(self.url, HTTP_HX_REQUEST='true')
        html = render_to_string('meeting.html', dict(vm=replace(page.context['vm'], phase='night'),
                                                    mudang_portrait_id='portrait:mudang_f'))
        self.assertNotIn('village-guide-title', html)
