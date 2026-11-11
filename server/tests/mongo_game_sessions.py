"""명시 실행하는 실제 Replica Set·엔진·B HTTP 회차 연결 시험."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import uuid
from unittest.mock import patch
from pymongo.errors import AutoReconnect, NetworkTimeout

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from engine.domain.engine import PaldoEngine
from engine.domain.npc import RuleV0Policy
from engine.testing.fakes import FixtureContentSource
from server.accounts.models import AccountProfile
from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service, configure_session_service
from server.paldo_web.mongo import open_mongo_runtime


class MongoGameSessions(TestCase):
    """고유 접두어의 컬렉션만 만들고 지운다. 기존 참가자 자료는 접근하지 않는다."""

    def setUp(self):
        """실제 Mongo 저장소와 규칙 NPC·모델 없는 실제 엔진을 연결한다."""
        self.prefix = 'b0603_' + uuid.uuid4().hex + '_'
        self.runtimes = []
        self.runtime = self.reconnect()
        self.addCleanup(self.cleanup_mongo)
        self.content = FixtureContentSource().load('*')
        self.runtime.content.insert(self.content)
        self.users, self.players = [], []
        for name in ('mongo-owner', 'mongo-second'):
            user = User.objects.create_user(username=name)
            profile = AccountProfile.objects.create(user=user, consented_at=timezone.now())
            self.users.append(user)
            self.players.append(str(profile.player_pseudonym))
        self.body = dict(preset_id='base6', session_id='mongo-session', mudang_variant='f', seed=7)
        self.versions = dict(rule_version='rule-v0', engine_version='test-real-engine',
                             content_version=self.content.content_version)
        self.runtime.sessions.create('mongo-session', [{'preset_id': 'base6', 'seed': 7}],
                                     self.players, self.versions)

    def reconnect(self):
        """새 클라이언트·서비스를 조립해 프로세스 메모리를 재사용하지 않는다."""
        runtime = open_mongo_runtime(engine=PaldoEngine(RuleV0Policy()), suspicion=None,
                                     uri=settings.MONGO_TEST_URI, collection_prefix=self.prefix)
        self.runtimes.append(runtime)
        runtime.configure()
        return runtime

    def test_ad_identity_concurrent_first_create_and_read_only_reuse(self):
        """실제 Mongo의 동시 최초 생성은 UUID 하나이며 재조회는 갱신하지 않는다."""
        store = self.runtime.sessions.store
        key = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(lambda _: store.ad_identity(self.players[0], key), range(20)))
        self.assertEqual(len(set(ids)), 1)
        with patch.object(store.identities, 'find_one_and_update', side_effect=AssertionError('read only')):
            self.assertEqual(store.ad_identity(self.players[0], key), ids[0])
        store.delete_player(self.players[0])
        self.assertNotEqual(store.ad_identity(self.players[0], key), ids[0])

    def cleanup_mongo(self):
        """이 시험의 정확한 접두어가 붙은 5개 컬렉션만 삭제한다."""
        configure_game_service(None)
        configure_session_service(None)
        last = self.runtimes[-1]
        for name in last.collection_names:
            if not name.startswith(self.prefix) or not self.prefix.startswith('b0603_'):
                raise AssertionError('unsafe_cleanup_target')
            last.database.drop_collection(name)
        for runtime in self.runtimes:
            runtime.close()

    def _commit_fault(self, after_write=False, permanent=False):
        """실제 Mongo 쓰기 경계에 일시 오류를 주입하고 상태·명령·이벤트 중복을 확인한다."""
        self.client.force_login(self.users[0])
        gid = self.client.post('/games', self.body, content_type='application/json').json()['view']['game_id']
        for error_type in (AutoReconnect, NetworkTimeout):
            with self.subTest(error=error_type.__name__):
                view = self.runtime.game.detail(gid, self.players[0]).view
                payload = dict(game_id=gid, command_id=str(uuid.uuid4()), expected_state_version=view.state_version,
                               command=dict(kind='silence' if any(k.value == 'silence' for k in view.legal_kinds)
                                            else 'advance', actor='mudang', evidence=[]))
                repo = self.runtime.game.repository
                before = repo.collection.find_one({'_id': gid})
                update = repo.collection.update_one
                calls = []
                transitions = []
                apply = self.runtime.game.engine.apply
                def transition(*args, **kwargs):
                    result = apply(*args, **kwargs)
                    transitions.append(result)
                    return result
                def fail(*args, **kwargs):
                    calls.append((args, kwargs))
                    if permanent or len(calls) == 1:
                        if after_write:
                            update(*args, **kwargs)
                        raise error_type('test_lost_commit_response')
                    return update(*args, **kwargs)
                with patch.object(repo.collection, 'update_one', side_effect=fail), patch.object(
                        self.runtime.game.engine, 'apply', side_effect=transition) as engine_apply:
                    if permanent:
                        with self.assertRaisesRegex(ConnectionError, '^mongo_commit_unavailable$'):
                            self.client.post(f'/games/{gid}/commands', payload, content_type='application/json')
                    else:
                        response = self.client.post(f'/games/{gid}/commands', payload, content_type='application/json')
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()['replayed'], after_write)
                    self.assertEqual(engine_apply.call_count, 1)
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], calls[1])
                document = repo.collection.find_one({'_id': gid})
                self.assertEqual(document['state']['state_version'], view.state_version + (not permanent))
                self.assertEqual(len(document['commands']) - len(before['commands']), 0 if permanent else 1)
                if permanent:
                    self.assertEqual(document, before)
                else:
                    self.assertEqual(len(document['outbox']) - len(before['outbox']), len(transitions[0].events))
                    retry = self.client.post(f'/games/{gid}/commands', payload, content_type='application/json')
                    self.assertEqual(retry.status_code, 200)
                    self.assertTrue(retry.json()['replayed'])
                    self.assertEqual(repo.collection.find_one({'_id': gid}), document)

    def test_commit_connection_error_before_write_retries_once(self):
        """저장 전 연결 오류는 같은 인자로 한 번 재시도해 한 번만 저장한다."""
        self._commit_fault()

    def test_commit_lost_response_returns_saved_replay(self):
        """실제 저장 뒤 응답 유실은 CAS 충돌을 거쳐 저장된 응답을 200으로 돌려준다."""
        self._commit_fault(after_write=True)

    def test_commit_connection_failure_stops_after_one_retry(self):
        """연결 오류가 계속돼도 commit은 두 번, 엔진 실행은 한 번으로 끝난다."""
        self._commit_fault(permanent=True)

    def test_http_same_board_events_and_consent(self):
        """같은 시드의 실제 배정과 계정별 human 이벤트·소유권을 확인한다."""
        boards, games = [], []
        for user, player in zip(self.users, self.players):
            self.client.force_login(user)
            response = self.client.post('/games', self.body, content_type='application/json')
            self.assertEqual(response.status_code, 200)
            gid = response.json()['view']['game_id']
            games.append(gid)
            state, truth = self.runtime.game.repository.load(gid)
            boards.append((state.active, truth.roles, truth.secrets))
            event = self.runtime.game.repository.unpublished(gid)[0]
            self.assertEqual((event.source.value, event.player_pseudonym, event.session_id),
                             ('human', player, 'mongo-session'))
            self.assertEqual(event.content_version, self.content.content_version)
            html = self.client.get(f'/games/{gid}', HTTP_ACCEPT='text/html')
            self.assertEqual(html.status_code, 200)
            for hidden in ('claim_truths', 'npc_private', '"roles"', '"truth"'):
                self.assertNotContains(html, hidden)
        self.assertEqual(boards[0], boards[1])
        self.assertNotEqual(games[0], games[1])
        self.assertEqual(self.client.get('/games/' + games[0]).status_code, 404)
        AccountProfile.objects.filter(user=self.users[1]).update(consented_at=None)
        self.assertEqual(self.client.post('/games', self.body, content_type='application/json').status_code, 403)
        self.client.force_login(self.users[0])
        self.runtime.sessions.create('missing-content', [{'preset_id': 'base6', 'seed': 7}], [self.players[0]],
                                     {**self.versions, 'content_version': 'not-loaded'})
        response = self.client.post('/games', {**self.body, 'session_id': 'missing-content'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'error': 'game_service_unavailable'})

    def test_reopen_command_progress_content_and_ad_identity(self):
        """실제 명령 저장 뒤 새 연결에서도 회차·판·진척·광고 가명·구버전을 보존한다."""
        self.client.force_login(self.users[0])
        gid = self.client.post('/games', self.body, content_type='application/json').json()['view']['game_id']
        view = self.runtime.game.detail(gid, self.players[0]).view
        kind = 'silence' if any(item.value == 'silence' for item in view.legal_kinds) else 'advance'
        payload = dict(game_id=gid, command_id=str(uuid.uuid4()), expected_state_version=view.state_version,
                       command=dict(kind=kind, actor='mudang', evidence=[]))
        response = self.client.post(f'/games/{gid}/commands', payload, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['accepted'])
        self.assertEqual(response.json()['current_state_version'], view.state_version + 1)
        self.runtime.game.progress.add_clue(self.players[0], self.content.grudge_clues[0].clue_id, gid, 1, 'storage-check')
        self.client.get('/ads/slot/lobby_banner')
        identities = self.runtime.sessions.store.ad_identities(self.players[0])
        self.assertEqual(len(identities), 1)
        self.assertNotEqual(identities[0], self.players[0])
        self.runtime.content.insert(replace(self.content, content_version=self.content.content_version + '-next'))
        self.runtime.close()
        self.runtime = self.reconnect()
        restored = self.client.get(f'/games/{gid}').json()['view']
        self.assertEqual(restored['state_version'], view.state_version + 1)
        replay = self.client.post(f'/games/{gid}/commands', payload, content_type='application/json')
        self.assertTrue(replay.json()['replayed'])
        state, _ = self.runtime.game.repository.load(gid)
        self.assertEqual(self.runtime.game.dependencies(state.meta).content.content_version, self.content.content_version)
        self.assertIn(self.content.grudge_clues[0].clue_id, self.client.get('/players/me/progress').json()['progress']['clues'])
        self.client.get('/ads/slot/lobby_banner')
        self.assertEqual(self.runtime.sessions.store.ad_identities(self.players[0]), identities)
        self.assertContains(self.client.get('/'), '이어 하기')

    def test_concurrent_start_does_not_duplicate_game_or_progress(self):
        """같은 참가자의 동시 시작 20건은 같은 판·진척·시작 이벤트 한 건이다."""
        data = StartGameInput(self.players[0], 'base6', 'mongo-session', 'f', seed=7)
        with ThreadPoolExecutor(max_workers=20) as pool:
            games = list(pool.map(lambda _: self.runtime.game.start(data, self.players[0], True).view.game_id,
                                  range(20)))
        self.assertEqual(len(set(games)), 1)
        self.assertEqual(self.runtime.game.progress.load(self.players[0]).game_ids, [games[0]])
        events = self.runtime.game.repository.unpublished(games[0])
        self.assertEqual(sum(event.event_type == 'game_started' for event in events), 1)
        self.assertEqual(self.runtime.database[self.runtime.collection_names[0]].count_documents({}), 1)

    def test_admin_session_registration_survives_new_connection(self):
        """운영자 HTTP로 등록한 고정 회차와 참가자 목록을 새 연결로 조회한다."""
        self.assertEqual(self.client.get('/admin/sessions').status_code, 302)
        self.client.force_login(self.users[0])
        self.assertEqual(self.client.get('/admin/sessions').status_code, 403)
        admin = User.objects.create_user(username='mongo-admin', is_staff=True)
        self.client.force_login(admin)
        form = dict(session_id='registered', plans=json.dumps([{'preset_id': 'base6', 'seed': 9}]),
                    participants='\n'.join(self.players), **self.versions)
        self.assertEqual(self.client.post('/admin/sessions', form).status_code, 302)
        self.runtime.close()
        self.runtime = self.reconnect()
        session = self.runtime.sessions.store.get('registered')
        self.assertEqual(session['participants'], sorted(self.players))
        self.assertEqual(session['plans'], [{'preset_id': 'base6', 'seed': 9}])
