"""C-06-01: 실제 majority replica set에서 계약·CAS·진척·콘텐츠를 검증한다."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import unittest
import uuid

from engine.domain import serde
from engine.domain.events import EventEnvelope
from engine.domain.model import GameTruth, PlayerProgress
from engine.ports import StoredCommand, VersionConflict
from engine.testing.fakes import FixtureContentSource
from tests.contract.test_fakes import make_state
from tests.contract._util import load_jsonl


@unittest.skipUnless(os.environ.get('PALDO_TEST_MONGO_URI'),'actual replica set URI required')
class MongoContractTests(unittest.TestCase):
    """각 실행의 고유 컬렉션만 생성한다. 기존 데이터는 변경·삭제하지 않는다."""

    @classmethod
    def setUpClass(cls):
        from pymongo import MongoClient
        from prometheus_client import CollectorRegistry, Histogram
        from server.infra.mongo_repo import MongoGameRepository
        from server.infra.progress_store import MongoProgressRepository
        from server.infra.content_store import MongoContentSource
        cls.client=MongoClient(os.environ['PALDO_TEST_MONGO_URI'],serverSelectionTimeoutMS=5000,timeoutMS=15000)
        cls.database=cls.client.get_default_database()
        cls.prefix='paldo_c06_'+uuid.uuid4().hex+'_'
        cls.histogram=Histogram('paldo_repo_commit_duration_seconds','test histogram',['status'],registry=CollectorRegistry())
        cls.repo=MongoGameRepository(cls.database[cls.prefix+'games'],cls.histogram)
        cls.progress=MongoProgressRepository(cls.database[cls.prefix+'progress'])
        cls.content=MongoContentSource(cls.database[cls.prefix+'content'])
        cls.environment=cls.client.admin.command('hello')
        if not cls.environment.get('setName'): raise RuntimeError('replica_set_required')

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def make_game(self):
        state=make_state(0)
        state.meta=replace(state.meta,game_id='g_'+uuid.uuid4().hex)
        truth=GameTruth(state.meta.game_id,{})
        self.repo.create(state,truth,[])
        event=serde.from_jsonable(EventEnvelope,load_jsonl('fixtures/events/sample_events.jsonl')[0])
        return state,truth,replace(event,game_id=state.meta.game_id)

    def test_cas_outbox_response_and_copy_semantics(self):
        state,truth,event=self.make_game();game=state.meta.game_id
        next_state=replace(state,state_version=1)
        self.repo.commit(game,0,next_state,truth,[event],StoredCommand('command.$id','{}'))
        self.assertEqual(self.repo.find_command(game,'command.$id').response_json,'{}')
        self.assertEqual(self.repo.unpublished(game)[0].event_id,game+':00000')
        self.assertEqual(self.repo.load(game)[0].state_version,1)
        with self.assertRaises(VersionConflict):self.repo.commit(game,0,next_state,truth,[],StoredCommand('other','{}'))
        with self.assertRaises(ValueError):self.repo.commit(game,1,replace(state,state_version=5),truth,[],StoredCommand('bad','{}'))
        with self.assertRaises(ValueError):self.repo.create(state,truth,[])
        self.assertEqual(self.repo.collection.write_concern.document['w'],'majority')

    def test_20_simultaneous_commits_have_one_winner(self):
        state,truth,event=self.make_game();game=state.meta.game_id
        def commit(index):
            try:
                self.repo.commit(game,0,replace(state,state_version=1),truth,[event],StoredCommand(str(index),'{}'))
                return 1
            except VersionConflict:return 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(sum(pool.map(commit,range(20))),1)
        self.assertEqual(len(self.repo.unpublished(game)),1)

    def test_append_events_dedup_and_shared_seq(self):
        state,truth,event=self.make_game();game=state.meta.game_id
        self.assertTrue(self.repo.append_events(game,[event],'attempt:one'))
        self.assertFalse(self.repo.append_events(game,[event],'attempt:one'))
        self.repo.commit(game,0,replace(state,state_version=1),truth,[event],StoredCommand('c','{}'))
        self.assertEqual([e.seq for e in self.repo.unpublished(game)],[0,1])
        self.assertEqual(self.repo.load(game)[0].state_version,1)

    def test_parallel_append_never_loses_seq_or_dedup(self):
        state,truth,event=self.make_game();game=state.meta.game_id
        with ThreadPoolExecutor(max_workers=20) as pool:
            results=list(pool.map(lambda i:self.repo.append_events(game,[event],str(i)),range(20)))
        self.assertTrue(all(results))
        self.assertEqual([e.seq for e in self.repo.unpublished(game)],list(range(20)))

    def test_progress_atomic_idempotent(self):
        player='player_'+uuid.uuid4().hex
        self.assertEqual(self.progress.load(player),PlayerProgress(player))
        self.assertTrue(self.progress.add_clue(player,'clue.$a','first-game',1,'event1'))
        self.assertFalse(self.progress.add_clue(player,'clue.$a','second-game',2,'event2'))
        self.assertFalse(self.progress.add_clue(player,'other','second-game',2,'event1'))
        self.assertTrue(self.progress.unlock_ending(player,'grudge','ending1'))
        self.assertFalse(self.progress.unlock_ending(player,'grudge','ending2'))
        with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(lambda i:self.progress.add_game(player,str(i)),range(20)))
        result=self.progress.load(player)
        self.assertEqual(result.clues['clue.$a'].first_game_id,'first-game')
        self.assertEqual(len(result.game_ids),20)
        self.assertEqual(result.endings_unlocked,['exorcism','grudge'])

    def test_content_versions_remain_available_and_immutable(self):
        version=uuid.uuid4().hex
        first=replace(FixtureContentSource().load('*'),content_version=version)
        second=replace(first,content_version=version+'-next')
        self.content.insert(first);self.content.insert(second)
        self.assertEqual(self.content.load(version),first)
        self.assertEqual(self.content.load(version+'-next'),second)
        with self.assertRaises(ValueError):self.content.insert(first)
        with self.assertRaises(KeyError):self.content.load('missing-'+version)
        if first.grudge_clues:
            with self.assertRaises(ValueError):
                self.content.insert(replace(first,content_version=version+'-removed',grudge_clues=first.grudge_clues[1:]))
            self.content.retire_clues([first.grudge_clues[0].clue_id])
            self.assertEqual(self.content.load(version),first)
