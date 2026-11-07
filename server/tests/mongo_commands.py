"""명시 실행하는 B-06-02 실제 Mongo HTTP 멱등·20개 동시 명령 시험."""
import uuid
from server.paldo_web.mongo import open_mongo_runtime
from engine.testing.fakes import FixtureContentSource, ScriptedGameEngine
from django.conf import settings
from .api_bench import fixture_service
from .test_concurrent_commands import ConcurrentCommands


class MongoCommands(ConcurrentCommands):
    """메모리 시험과 같은 HTTP 경합 시나리오에서 저장소만 실제 Mongo로 교체한다."""

    def make_game_service(self):
        """고유 컬렉션과 실제 콘텐츠·진척·게임 저장을 구성한다."""
        self.prefix = 'b0602_' + uuid.uuid4().hex + '_'
        self.runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None,
                                         uri=settings.MONGO_TEST_URI, collection_prefix=self.prefix)
        self.addCleanup(self.cleanup_mongo)
        self.runtime.content.insert(FixtureContentSource().load('*'))
        return fixture_service(self.runtime)

    def cleanup_mongo(self):
        """현재 시험의 정확한 5개 컬렉션만 삭제하고 연결을 닫는다."""
        try:
            for name in self.runtime.collection_names:
                assert name.startswith(self.prefix) and self.prefix.startswith('b0602_')
                self.runtime.database.drop_collection(name)
        finally:
            self.runtime.close()

    def test_replay_conflict_and_rejection_preserve_storage(self):
        """실제 DB의 재생 200·충돌 409·엔진 거부 422와 상태 불변을 확인한다."""
        body = dict(game_id=self.gid, command_id=str(uuid.uuid4()), expected_state_version=0,
                    command=dict(kind='silence', actor='mudang'))
        before = self.service.repository.collection.find_one({'_id': self.gid})
        rejected = self.client.post(f'/games/{self.gid}/commands', body, content_type='application/json')
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.service.repository.collection.find_one({'_id': self.gid}), before)
        body['command']['kind'] = 'advance'
        first = self.client.post(f'/games/{self.gid}/commands', body, content_type='application/json')
        self.assertEqual(first.status_code, 200)
        stored = self.service.repository.collection.find_one({'_id': self.gid})
        replay = self.client.post(f'/games/{self.gid}/commands', body, content_type='application/json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(dict(replay.json(), replayed=False), first.json())
        body['command_id'] = str(uuid.uuid4())
        self.assertEqual(self.client.post(f'/games/{self.gid}/commands', body,
                                         content_type='application/json').status_code, 409)
        self.assertEqual(self.service.repository.collection.find_one({'_id': self.gid}), stored)
