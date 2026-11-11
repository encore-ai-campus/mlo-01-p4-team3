"""B-07-02 명시 실행: 실제 Mongo 보존·응답 유실·Kafka TLS 수신 검증."""
import json
import os
from pathlib import Path
import time
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TransactionTestCase
from django.utils import timezone
from engine.testing.fakes import FixtureContentSource, ScriptedGameEngine, FakeIntentParser
from pymongo.errors import NetworkTimeout
from server.accounts.models import AccountProfile
from server.app.interactions import InteractionUnavailable
from server.infra.game_factory import attach_interactions, INTERACTIONS
from server.infra.interaction_store import MongoInteractionStore
from server.paldo_web.composition import configure_game_service, configure_session_service
from server.paldo_web.mongo import open_mongo_runtime
from .support import make_service, issue_test_csrf


class MongoParticipants(TransactionTestCase):
    """로그인·CSRF·기존 HTTP를 거쳐 고유 접두어의 실제 저장소를 검사한다."""

    def setUp(self):
        """녹화 엔진 외 저장 대역 없이 실행별 6개 컬렉션을 준비한다."""
        self.prefix = 'b0702_' + uuid.uuid4().hex + '_'
        self.runtimes = []
        self.addCleanup(self.cleanup)
        self.reopen()
        self.runtime.content.insert(FixtureContentSource().load('*'))
        user = User.objects.create_user(username='participant-mongo')
        self.player = str(AccountProfile.objects.create(user=user, consented_at=timezone.now()).player_pseudonym)
        self.client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        self.client.force_login(user)
        self.csrf = issue_test_csrf(self.client)
        response = self.request('post', '/games', dict(preset_id='base6', session_id='test-session', mudang_variant='f'))
        self.assertEqual(response.status_code, 200, response.content)
        self.gid = response.json()['view']['game_id']
        self.url = f'/games/{self.gid}'
        self.entries = {next(n for n in self.view()['names'] if n != 'mudang'): 73}

    def reopen(self):
        """기존 클라이언트를 닫고 새 Mongo 연결·서비스·보조 저장을 설치한다."""
        for runtime in self.runtimes:
            runtime.close()
        runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None,
                                     uri=settings.MONGO_TEST_URI, collection_prefix=self.prefix)
        self.runtimes.append(runtime)
        self.runtime = runtime
        service = make_service(repository=runtime.game.repository, progress=runtime.game.progress)
        service.dependencies = runtime.game.dependencies
        service.load_test = True
        self.store = MongoInteractionStore(runtime.database[self.prefix + INTERACTIONS])
        self.service = attach_interactions(service, self.store, FakeIntentParser())
        configure_game_service(self.service)

    def cleanup(self):
        """자신의 정확한 6개 컬렉션만 지운다. DB·다른 판·Kafka 토픽은 삭제하지 않는다."""
        configure_game_service(None)
        configure_session_service(None)
        if self.runtimes:
            runtime = self.runtimes[-1]
            for name in runtime.collection_names + [self.prefix + INTERACTIONS]:
                assert name.startswith(self.prefix) and self.prefix.startswith('b0702_')
                runtime.database.drop_collection(name)
            for runtime in self.runtimes:
                runtime.close()

    def request(self, method, path, body, **headers):
        """현재 로그인과 CSRF를 유지한 JSON 요청을 보낸다."""
        return getattr(self.client, method)(path, body, content_type='application/json',
            HTTP_ACCEPT='application/json', HTTP_X_CSRFTOKEN=self.csrf, **headers)

    def view(self):
        """새 연결의 공개 화면을 읽는다."""
        return self.client.get(self.url, HTTP_ACCEPT='application/json').json()['view']

    def events(self, kind=None):
        """발행 여부와 관계없이 실제 게임 문서의 전체 이벤트를 읽는다."""
        rows = self.service.repository.collection.find_one({'_id': self.gid})['outbox']
        return [r['envelope'] for r in rows if kind is None or r['envelope']['event_type'] == kind]

    def finish(self):
        """녹화 명령을 기존 API로 끝까지 진행한다."""
        for step in self.service.engine.steps[self.view()['state_version'] + 1:]:
            body = dict(step['request'], game_id=self.gid, command_id=str(uuid.uuid4()))
            response = self.request('post', self.url + '/commands', body)
            self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(self.view()['result'])

    def notebook(self):
        """수첩과 확신도를 같은 계약 입력으로 보낸다."""
        return self.request('put', self.url + '/notebook',
                            dict(game_id=self.gid, entries=self.entries, vote_confidence=88))

    def worker(self, action, body=None):
        """독립 Python 프로세스에서 같은 Mongo 판을 HTTP로 다시 읽거나 제출한다."""
        env = dict(os.environ, DJANGO_SECRET_KEY=settings.SECRET_KEY)
        value = dict(prefix=self.prefix, game_id=self.gid, player=self.player, action=action, body=body)
        result = subprocess.run([sys.executable, '-B', '-m', 'server.tests.participant_worker'],
            input=json.dumps(value), text=True, encoding='utf-8', capture_output=True, timeout=45, env=env)
        self.assertEqual(result.returncode, 0, 'restart_worker_failed')
        return json.loads(result.stdout.splitlines()[-1])

    def survey(self, skipped=False, **headers):
        """정상 응답과 전체 건너뛰기를 같은 API로 보낸다."""
        body = dict(game_id=self.gid, skipped=skipped, fun=None if skipped else 4,
                    unfair=None if skipped else 2, npc_plausible=None if skipped else 5,
                    comment='' if skipped else '연락 010-1234-5678 test@example.com')
        return self.request('post', self.url + '/survey', body, **headers)

    def test_notebook_restart_replay_and_changed_value(self):
        """재시작 뒤 값·확신도·단계 메타를 유지하고 A→B→A는 각각 기록한다."""
        version = self.view()['state_version']
        self.assertEqual(self.notebook().status_code, 204)
        restarted = self.worker('view')
        self.assertEqual(restarted['status'], 200)
        self.assertEqual(restarted['data']['view']['player']['notebook'], self.entries)
        self.reopen()
        self.assertEqual(self.view()['player']['notebook'], self.entries)
        self.assertEqual(self.notebook().status_code, 204)
        self.assertEqual(len(self.events('notebook_updated')), 1)
        npc = next(iter(self.entries))
        for value in (10, 73):
            self.entries[npc] = value
            self.assertEqual(self.notebook().status_code, 204)
        self.assertEqual(len(self.events('notebook_updated')), 3)
        self.assertEqual(self.view()['state_version'], version)
        with self.store.edit(self.gid) as records:
            self.assertEqual(records.notebook['vote_confidence'], 88)
            self.assertIn('_saved_context', records.notebook)

    def test_notebook_append_response_loss_does_not_duplicate(self):
        """실제 append 성공 직후 응답 유실·재시작·동일 재요청도 이벤트는 하나다."""
        collection = self.service.repository.collection
        write = collection.update_one
        def lost(*args, **kwargs):
            write(*args, **kwargs)
            raise NetworkTimeout('injected')
        with patch.object(collection, 'update_one', side_effect=lost):
            self.assertEqual(self.notebook().status_code, 503)
        self.reopen()
        self.assertEqual(self.notebook().status_code, 204)
        self.assertEqual(len(self.events('notebook_updated')), 1)
        self.assertEqual(self.view()['player']['notebook'], self.entries)

    def test_notebook_state_save_loss_does_not_duplicate(self):
        """이벤트 저장 뒤 보조 상태 쓰기 실패도 재시작 후 복구한다."""
        save = self.store._save
        def failed(game, field, *args):
            if field == 'state':
                raise InteractionUnavailable('injected')
            return save(game, field, *args)
        with patch.object(self.store, '_save', side_effect=failed):
            self.assertEqual(self.notebook().status_code, 503)
        self.reopen()
        self.assertEqual(self.notebook().status_code, 204)
        self.assertEqual(len(self.events('notebook_updated')), 1)

    def test_notebook_state_write_response_loss_preserves_saved_value(self):
        """보조 상태의 실제 쓰기 성공 직후 응답 유실도 재시작·재전송에 단일 반영된다."""
        collection = self.store.collection
        write = collection.update_one
        def lost(match, update, **kwargs):
            result = write(match, update, **kwargs)
            if isinstance(update, dict) and 'state' in update.get('$set', {}):
                raise NetworkTimeout('injected_after_state_write')
            return result
        with patch.object(collection, 'update_one', side_effect=lost):
            self.assertEqual(self.notebook().status_code, 503)
        self.reopen()
        self.assertEqual(self.view()['player']['notebook'], self.entries)
        self.assertEqual(self.notebook().status_code, 204)
        self.assertEqual(len(self.events('notebook_updated')), 1)

    def test_survey_append_response_loss_preserves_first_payload(self):
        """설문 이벤트만 저장된 뒤 다른 내용으로 재요청하면 409이며 최초 답을 복구한다."""
        self.finish()
        collection = self.service.repository.collection
        write = collection.update_one
        def lost(*args, **kwargs):
            write(*args, **kwargs)
            raise NetworkTimeout('injected')
        with patch.object(collection, 'update_one', side_effect=lost):
            self.assertEqual(self.survey(skipped=True).status_code, 503)
        self.reopen()
        self.assertEqual(self.survey().status_code, 409)
        self.assertEqual(self.survey(skipped=True).status_code, 204)
        self.assertEqual(len(self.events('survey_submitted')), 1)

    def test_survey_restart_replay_conflict_and_masking(self):
        """정상 설문은 재시작 뒤 불변이며 의견 개인정보는 저장·이벤트에 마스킹된다."""
        self.finish()
        self.assertEqual(self.survey().status_code, 204)
        self.reopen()
        self.assertEqual(self.survey().status_code, 204)
        self.assertEqual(self.survey(skipped=True).status_code, 409)
        rows = self.events('survey_submitted')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['payload']['fun'], 4)
        self.assertNotIn('010-1234-5678', json.dumps(rows))
        self.assertNotIn('test@example.com', json.dumps(rows))

    def test_process_restart_recovers_pending_notebook(self):
        """이벤트만 저장된 상태에서 원 프로세스 캐시 없이 새 프로세스가 복구한다."""
        save = self.store._save
        def failed(game, field, *args):
            if field == 'state':
                raise InteractionUnavailable('injected')
            return save(game, field, *args)
        with patch.object(self.store, '_save', side_effect=failed):
            self.assertEqual(self.notebook().status_code, 503)
        response = self.worker('view')
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['data']['view']['player']['notebook'], self.entries)
        body = dict(game_id=self.gid, entries=self.entries, vote_confidence=88)
        self.assertEqual(self.worker('notebook', body)['status'], 204)
        self.assertEqual(len(self.events('notebook_updated')), 1)

    def test_survey_state_save_failure_recovers_skip_in_new_process(self):
        """건너뛴 설문 이벤트 뒤 상태 쓰기가 실패해도 새 프로세스 재전송은 1건이다."""
        self.finish()
        save = self.store._save
        def failed(game, field, *args):
            if field == 'state':
                raise InteractionUnavailable('injected')
            return save(game, field, *args)
        with patch.object(self.store, '_save', side_effect=failed):
            self.assertEqual(self.survey(skipped=True).status_code, 503)
        body = dict(game_id=self.gid, skipped=True, fun=None, unfair=None, npc_plausible=None, comment='')
        self.assertEqual(self.worker('survey', body)['status'], 204)
        self.assertEqual(self.survey().status_code, 409)
        self.assertEqual(len(self.events('survey_submitted')), 1)

    def test_two_processes_same_notebook_emit_once(self):
        """독립 프로세스 두 개의 같은 수첩 저장은 모두 204이고 이벤트는 하나다."""
        body = dict(game_id=self.gid, entries=self.entries, vote_confidence=88)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.worker('notebook', body), range(2)))
        self.assertEqual([r['status'] for r in responses], [204, 204])
        self.assertEqual(len(self.events('notebook_updated')), 1)

    def test_two_processes_different_surveys_accept_only_first(self):
        """독립 프로세스의 다른 설문은 최초 하나만 204이고 다른 하나는 409다."""
        self.finish()
        bodies = [dict(game_id=self.gid, skipped=True, fun=None, unfair=None, npc_plausible=None, comment=''),
                  dict(game_id=self.gid, skipped=False, fun=4, unfair=2, npc_plausible=5, comment='')]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda body: self.worker('survey', body), bodies))
        self.assertEqual(sorted(r['status'] for r in responses), [204, 409])
        rows = self.events('survey_submitted')
        self.assertEqual(len(rows), 1)
        winner = bodies[next(i for i, response in enumerate(responses) if response['status'] == 204)]
        self.assertEqual(rows[0]['payload']['skipped'], winner['skipped'])


class KafkaParticipants(MongoParticipants):
    """Mongo 회귀와 함께 C의 TLS 발행기·실제 Kafka 소비 결과를 대조한다."""

    def kafka_roundtrip(self, skipped):
        """실제 API → Mongo 아웃박스 → C 발행기 → paldo.player 본문을 비교한다."""
        from confluent_kafka import Consumer, TopicPartition
        from confluent_kafka.admin import AdminClient, NewTopic
        from prometheus_client import CollectorRegistry
        from server.infra.outbox_publisher import KafkaSender, MongoOutboxStore, Publisher
        config = json.loads(Path(os.environ['PALDO_TEST_KAFKA_CONFIG']).read_text(encoding='utf-8-sig'))
        self.assertEqual(config['security.protocol'], 'SSL')
        self.assertEqual(config.get('ssl.endpoint.identification.algorithm', 'https'), 'https')
        self.assertEqual(self.request('put', self.url + '/notebook',
            dict(game_id=self.gid, entries=self.entries)).status_code, 204)
        for step in self.service.engine.steps[self.view()['state_version'] + 1:]:
            if self.view()['phase'] == 'vote':
                break
            response = self.request('post', self.url + '/commands',
                dict(step['request'], game_id=self.gid, command_id=str(uuid.uuid4())))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.view()['phase'], 'vote')
        self.assertEqual(self.notebook().status_code, 204)
        self.finish()
        self.assertEqual(self.survey(skipped=skipped).status_code, 204)
        self.reopen()
        self.assertEqual(self.survey(skipped=skipped).status_code, 204)
        self.assertEqual(self.survey(skipped=not skipped).status_code, 409)
        expected = self.events()
        topics = sorted({row['topic'] for row in expected})
        admin = AdminClient(config)
        known = admin.list_topics(timeout=10).topics
        missing = [NewTopic(topic, 1, 1) for topic in topics if topic not in known]
        if missing:
            for future in admin.create_topics(missing).values():
                future.result(timeout=15)
        deadline = time.monotonic() + 30
        while True:
            ready = admin.list_topics(timeout=5).topics
            if all(topic in ready and ready[topic].partitions and all(
                    partition.leader >= 0 and partition.isrs for partition in ready[topic].partitions.values()) for topic in topics):
                break
            if time.monotonic() >= deadline:
                self.fail('kafka_topic_not_ready')
            time.sleep(.2)
        store = MongoOutboxStore(self.service.repository.collection)
        sender = KafkaSender(config)
        # 수신 계약 시험이므로 별도 Producer의 TLS 연결·메타데이터 준비를 먼저 확인한다.
        sender.producer.list_topics(timeout=15)
        positions = []
        send = sender.send
        def record(event):
            position = send(event)
            positions.append(position)
            return position
        sender.send = record
        publisher = Publisher(store, sender, CollectorRegistry())
        self.assertEqual(publisher.tick(), len(expected))
        self.assertEqual(publisher.tick(), 0)
        self.assertEqual(store.pending(), 0)
        self.assertTrue(all(row['published'] for row in self.service.repository.collection.find_one({'_id': self.gid})['outbox']))
        player_events = [row for row in expected if row['topic'] == 'paldo.player']
        self.assertEqual(len(player_events), 3)
        self.assertEqual([row['event_type'] for row in player_events],
                         ['notebook_updated', 'notebook_updated', 'survey_submitted'])
        self.assertIsNone(player_events[0]['payload']['vote_confidence'])
        self.assertEqual(player_events[1]['payload']['vote_confidence'], 88)
        replay = []
        if skipped:
            from engine.domain.events import EventEnvelope
            from engine.domain.serde import from_jsonable
            replay = [next(row for row in player_events if row['event_type'] == 'survey_submitted')]
            # Kafka 발행은 at-least-once다. 같은 ID의 전송 재생을 실제 브로커에 추가한다.
            sender.send(from_jsonable(EventEnvelope, replay[0]))
        consumer = Consumer({**config, 'group.id': 'b0702-' + uuid.uuid4().hex, 'enable.auto.commit': False})
        received = []
        try:
            consumer.assign([TopicPartition('paldo.player', 0, 0)])
            deadline = time.monotonic() + 25
            while len(received) < len(player_events) + len(replay) and time.monotonic() < deadline:
                message = consumer.poll(1)
                if message is None:
                    continue
                self.assertIsNone(message.error())
                value = json.loads(message.value())
                if value.get('game_id') == self.gid:
                    self.assertEqual(message.key().decode(), self.gid)
                    received.append(value)
        finally:
            consumer.close()
        self.assertEqual(received, player_events + replay)
        self.assertEqual(len({row['event_id'] for row in received}), 3)
        from engine.domain.events import EventEnvelope
        from engine.domain.serde import from_jsonable
        from pipeline.consumers.dedup import plan_game_events
        unique = plan_game_events([from_jsonable(EventEnvelope, row) for row in received], {})
        self.assertEqual(len(unique), 3)
        self.assertEqual(plan_game_events([from_jsonable(EventEnvelope, row) for row in received],
            {row['event_id']: row['envelope_hash'] for row in unique}), [])
        survey = next(row['payload'] for row in received if row['event_type'] == 'survey_submitted')
        self.assertEqual(survey['skipped'], skipped)
        self.assertEqual([survey[k] for k in ('fun', 'unfair', 'npc_plausible')], [None]*3 if skipped else [4, 2, 5])
        for row in received:
            self.assertTrue(row['load_test'])
            self.assertNotIn('_saved_context', json.dumps(row))
            self.assertNotIn('comment_digest', json.dumps(row))
            self.assertNotIn('010-1234-5678', json.dumps(row))
            self.assertNotIn('test@example.com', json.dumps(row))
        print(json.dumps(dict(scenario='skip' if skipped else 'answered', player_events=len(received),
                              unique_player_events=len(unique), transport_replays=len(replay),
                              all_events=len(expected), pending=store.pending(), tls_verified=True)), flush=True)

    def test_kafka_answered_survey(self):
        """수첩·확신도와 정상 설문 본문을 실제 TLS Kafka에서 그대로 수신한다."""
        self.kafka_roundtrip(False)

    def test_kafka_skipped_survey_null_scores(self):
        """건너뜀 true·null 점수·빈 의견을 Kafka에서도 그대로 보존한다."""
        self.kafka_roundtrip(True)
