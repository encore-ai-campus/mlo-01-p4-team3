"""B-08-01 결과·진척·결말 선택과 비공개 정보 경계의 화면 회귀."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.conf import settings
from django.test import TestCase
from engine.domain.model import EndReason, Faction, GameResult
from server.api.story_view import story_catalog
from server.app.test_sessions import TestSessionService
from server.paldo_web.composition import configure_game_service, configure_session_service
from .session_store import MemorySessionStore
from .support import install_test_profiles, make_service


class StoryScreens(TestCase):
    """가짜 진척 저장소로 화면 계약을 검증한다. 해금 로직 자체는 E 범위다."""

    def setUp(self):
        """독립 계정·진척·고정 회차·녹화 엔진을 조립한다."""
        self.user = User.objects.create_user(username="story-owner")
        self.register = install_test_profiles(self)
        self.player = str(self.register(self.user).player_pseudonym)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        sessions = TestSessionService(MemorySessionStore())
        self.sessions = sessions
        sessions.create('test-session', [{'preset_id': 'base6', 'seed': 7}], [self.player],
                        dict(rule_version='test-rule', engine_version='scripted', content_version='content-1.0'))
        configure_session_service(sessions)
        self.addCleanup(configure_session_service, None)
        self.clues, self.endings = story_catalog()

    def start(self, ending="exorcism", form=False):
        """같은 선택을 JSON·HTML 시작 경로로 전송한다."""
        data = dict(preset_id='base6', session_id='test-session', mudang_variant='f', ending_choice=ending)
        if form:
            return self.client.post('/games', urlencode(data), content_type='application/x-www-form-urlencoded')
        return self.client.post('/games', json.dumps(data), content_type='application/json')

    def test_locked_selection_rejected_and_unlocked_choice_is_fixed(self):
        """잠긴 결말은 400이며 열린 결말은 폼·JSON 모두 메타에 고정된다."""
        for form in (False, True):
            self.assertEqual(self.start('haewon:h1', form).status_code, 400)
        self.assertEqual(self.service.repository.docs, {})
        self.service.progress.unlock_ending(self.player, 'haewon:h1', 'unlock-event')
        for form in (False, True):
            response = self.start('haewon:h1', form)
            self.assertEqual(response.status_code, 302 if form else 200)
            gid = response['Location'].split('/')[-1] if form else response.json()['view']['game_id']
            state, _ = self.service.repository.load(gid)
            self.assertEqual(state.meta.ending_choice, 'haewon:h1')
            self.assertEqual(state.state_version, 0)
            self.assertIsNone(state.result)

    def test_home_shows_locked_and_unlocked_choice_without_story_spoilers(self):
        """시작 폼은 해금 상태만 표시하고 도전은 인수 전 비활성으로 둔다."""
        page = self.client.get('/')
        self.assertContains(page, 'value="haewon:h1" disabled')
        self.assertContains(page, '선택형 도전 · 준비 중')
        self.assertContains(page, '<fieldset disabled>')
        for ending in self.endings:
            self.assertNotContains(page, ending['lines_ko'][0])
        self.service.progress.unlock_ending(self.player, 'haewon:h1', 'unlock-event')
        self.assertNotContains(self.client.get('/'), 'value="haewon:h1" disabled')

    def test_fixed_game_keeps_first_ending_when_start_is_repeated(self):
        """동일 배정 판의 재시작 요청으로 처음 고른 결말을 바꾸지 못한다."""
        self.service.prepare_start = lambda data, player: self.sessions.prepare(
            data, player, self.service.repository.load)
        gid = self.start().json()['view']['game_id']
        self.service.progress.unlock_ending(self.player, 'haewon:h1', 'unlock-event')
        self.assertEqual(self.start('haewon:h1').json()['view']['game_id'], gid)
        self.assertEqual(self.service.repository.load(gid)[0].meta.ending_choice, 'exorcism')

    def test_general_start_preserves_ending_portrait_and_replayed_game(self):
        """일반 판 폼은 결말·초상·시작 UUID를 함께 보존하고 재전송은 첫 선택을 유지한다."""
        from server.app.open_start import StartPolicy, StartVersions
        from engine.domain.model import ENGINE_VERSION
        self.service.prepare_start = StartPolicy(self.service.prepare_start,
            lambda: StartVersions('test-rule', ENGINE_VERSION, 'content-1.0'),
            self.service.repository.load)
        self.service.progress.unlock_ending(self.player, 'haewon:h1', 'unlock-event')
        home = self.client.get('/')
        html = home.content.decode().split('id="open-game"', 1)[1].split('</section>', 1)[0]
        self.assertIn('name="ending_choice"', html)
        self.assertIn('name="start_token"', html)
        self.assertIn('value="m"', html)
        for from_form in (False, True):
            data = dict(preset_id='base6', mudang_variant='m', ending_choice='haewon:h1', start_token=str(uuid4()))
            def send(values):
                if from_form:
                    return self.client.post('/games', urlencode(values), content_type='application/x-www-form-urlencoded')
                return self.client.post('/games', values, content_type='application/json')
            response = send(data)
            self.assertEqual(response.status_code, 302 if from_form else 200)
            gid = response['Location'].split('/')[-1] if from_form else response.json()['view']['game_id']
            state, _ = self.service.repository.load(gid)
            self.assertEqual((state.meta.ending_choice, state.meta.mudang_variant), ('haewon:h1', 'm'))
            with patch('server.app.open_start.open_preset_ids', return_value=[]):
                replay = send(dict(data, ending_choice='exorcism', mudang_variant='f'))
            replay_gid = replay['Location'].split('/')[-1] if from_form else replay.json()['view']['game_id']
            self.assertEqual(replay_gid, gid)
            state, _ = self.service.repository.load(gid)
            self.assertEqual((state.meta.ending_choice, state.meta.mudang_variant), ('haewon:h1', 'm'))
        self.assertEqual(len(self.service.repository.docs), 2)

    def test_general_locked_ending_never_creates_a_game(self):
        """일반 판에서도 잠긴 결말을 폼·JSON 모두 거부하고 판을 만들지 않는다."""
        from server.app.open_start import StartPolicy, StartVersions
        self.service.prepare_start = StartPolicy(self.service.prepare_start,
            lambda: StartVersions('test-rule', 'scripted', 'content-1.0'), self.service.repository.load)
        for from_form in (False, True):
            data = dict(preset_id='base6', mudang_variant='f', ending_choice='haewon:h1', start_token=str(uuid4()))
            if from_form:
                response = self.client.post('/games', urlencode(data), content_type='application/x-www-form-urlencoded')
            else:
                response = self.client.post('/games', data, content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.repository.docs, {})

    def test_progress_only_shows_collected_clues_and_is_read_only(self):
        """빈 진척·수집 단서·다른 계정과 JSON 계약을 확인한다."""
        path = '/players/me/progress'
        self.assertContains(self.client.get(path, HTTP_ACCEPT='text/html'), '아직 모은 단서가 없습니다.')
        clue = self.clues[0]
        self.service.progress.add_clue(self.player, clue['clue_id'], 'game-first', 2, 'clue-event')
        before = deepcopy(self.service.progress.items)
        for headers in ({'HTTP_ACCEPT': 'text/html'}, {'HTTP_HX_REQUEST': 'true'}):
            page = self.client.get(path, **headers)
            self.assertContains(page, clue['text_ko'])
            for other in self.clues[1:]:
                self.assertNotContains(page, other['text_ko'])
            self.assertNotContains(page, 'game-first')
        self.assertEqual(set(self.client.get(path).json()), {'progress'})
        self.assertEqual(self.service.progress.items, before)
        other = User.objects.create_user(username='story-other')
        self.register(other)
        self.client.force_login(other)
        self.assertNotContains(self.client.get(path, HTTP_ACCEPT='text/html'), clue['text_ko'])
        self.client.logout()
        self.assertEqual(self.client.get(path, HTTP_ACCEPT='application/json').status_code, 401)
        self.assertEqual(self.client.get(path, HTTP_ACCEPT='text/html').status_code, 302)

    def test_result_reasons_endings_and_privacy_for_full_and_fragment(self):
        """승패·종료 사유 3종과 중단·선택 결말·버전 경계를 대조한다."""
        gid = self.start().json()['view']['game_id']
        document = self.service.repository.docs[gid]
        document['truth'].secrets = {'jumo': 'private-secret-marker'}
        document['state'].meta.content_version = 'content-1.0'
        original = self.client.get(f'/games/{gid}', HTTP_ACCEPT='text/html')
        self.assertNotContains(original, 'id="game-result"')
        end = deepcopy(self.service.engine.views[-1])
        for reason in EndReason:
            winner = Faction.VILLAGE if reason is EndReason.ALL_POSSESSED_LOCKED else Faction.CHANGGWI
            view = replace(end, game_id=gid, result=GameResult(winner, reason, 2))
            for ending in self.endings:
                document['state'].meta.ending_choice = ending['ending_id']
                with patch.object(self.service.engine, 'player_view', return_value=view):
                    for headers in ({'HTTP_ACCEPT': 'text/html'}, {'HTTP_HX_REQUEST': 'true'}):
                        response = self.client.get(f'/games/{gid}', **headers)
                        self.assertContains(response, 'id="game-result"')
                        self.assertContains(response, '마을 승리' if winner is Faction.VILLAGE else '창귀 승리')
                        self.assertEqual(bool(response.context['game_result']['lines']), winner is Faction.VILLAGE)
                        if winner is Faction.VILLAGE:
                            self.assertContains(response, ending['lines_ko'][0])
                        self.assertNotContains(response, 'private-secret-marker')
                        self.assertNotContains(response, 'claim_truths')
                        self.assertContains(response, '감금 결과')
        view = replace(end, game_id=gid, result=GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 2, False))
        with patch.object(self.service.engine, 'player_view', return_value=view):
            self.assertContains(self.client.get(f'/games/{gid}', HTTP_ACCEPT='text/html'), '중단된 판')

    def test_packaged_catalog_matches_current_authored_public_text(self):
        """배포 문안 누락·같은 버전 원문 수정은 검증에서 차단한다."""
        bundle = json.loads((settings.BASE_DIR / 'content/bundle_v1.json').read_text(encoding='utf-8'))
        endings = json.loads((settings.BASE_DIR / 'content/endings_v1.json').read_text(encoding='utf-8'))
        self.assertEqual(bundle['content_version'], endings['content_version'])
        clues, texts = story_catalog(bundle['content_version'])
        self.assertEqual(clues, [{k: c[k] for k in ('clue_id', 'text_ko')} for c in bundle['grudge_clues']])
        self.assertEqual(texts, [{k: e[k] for k in ('ending_id', 'title_ko', 'lines_ko')} for e in endings['endings']])

    def test_catalog_upgrade_preserves_existing_game_and_collected_clues(self):
        """다음 콘텐츠 배포 후 재조회·htmx에도 판의 결말과 이전 진척 원문을 유지한다."""
        self.service.progress.unlock_ending(self.player, 'haewon:h1', 'unlock-event')
        gid = self.start('haewon:h1').json()['view']['game_id']
        state = self.service.repository.docs[gid]['state']
        state.meta.content_version = 'content-1.0'
        clue = self.clues[0]
        self.service.progress.add_clue(self.player, clue['clue_id'], gid, 2, 'clue-event')
        self.service.progress.items[self.player].content_version = 'content-1.0'
        before = deepcopy(self.service.progress.items)
        catalog_path = settings.BASE_DIR / 'server/story_catalog.json'
        catalogs = json.loads(catalog_path.read_text(encoding='utf-8'))
        updated = deepcopy(catalogs['content-1.0'])
        updated['grudge_clues'][0]['text_ko'] = '다음 버전의 단서 문안'
        for ending in updated['endings']:
            ending['lines_ko'] = ['다음 버전의 결말 문안']
        catalogs['content-2.0'] = updated
        read_text = Path.read_text
        def next_deployment(path, *args, **kwargs):
            if path == catalog_path:
                return json.dumps(catalogs, ensure_ascii=False)
            value = read_text(path, *args, **kwargs)
            if path in (settings.BASE_DIR / 'content/bundle_v1.json', settings.BASE_DIR / 'content/endings_v1.json'):
                data = json.loads(value)
                data['content_version'] = 'content-2.0'
                return json.dumps(data, ensure_ascii=False)
            return value
        end = replace(self.service.engine.views[-1], game_id=gid,
                      result=GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 2))
        with patch.object(Path, 'read_text', next_deployment), patch.object(self.service.engine, 'player_view', return_value=end):
            self.assertEqual(story_catalog()[0][0]['text_ko'], '다음 버전의 단서 문안')
            for ending in self.endings:
                state.meta.ending_choice = ending['ending_id']
                for headers in ({'HTTP_ACCEPT': 'text/html'}, {'HTTP_HX_REQUEST': 'true'}, {'HTTP_ACCEPT': 'text/html'}):
                    page = self.client.get(f'/games/{gid}', **headers)
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(page.context['game_result']['lines'], ending['lines_ko'])
                    self.assertNotContains(page, '다음 버전의 결말 문안')
                    progress = self.client.get('/players/me/progress', **headers)
                    self.assertContains(progress, clue['text_ko'])
                    self.assertNotContains(progress, '다음 버전의 단서 문안')
                    self.assertNotContains(progress, '이전 콘텐츠 버전의 수집 단서')
            self.assertEqual(story_catalog('unknown-version'), ([], []))
        self.assertEqual(self.service.progress.items, before)
        self.assertEqual(state.meta.content_version, 'content-1.0')

    def test_challenge_input_cannot_silently_start_an_ordinary_game(self):
        """확정되지 않은 도전 필드를 무시해 일반 판을 시작하지 않고 400으로 거부한다."""
        data = dict(preset_id='base6', session_id='test-session', mudang_variant='f', challenge='one_divination')
        self.assertEqual(self.client.post('/games', json.dumps(data), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/games', urlencode(data), content_type='application/x-www-form-urlencoded').status_code, 400)
        self.assertEqual(self.service.repository.docs, {})
