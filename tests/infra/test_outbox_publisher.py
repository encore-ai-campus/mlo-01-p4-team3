"""Mongo lease·실패 재시도·ack 뒤 장애를 실제 저장소에서 확인한다."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import unittest
import uuid


class OutboxTests(unittest.TestCase):
    """고유 검증 컬렉션만 만들며 서비스 원본은 변경하지 않는다."""

    @classmethod
    def setUpClass(cls):
        """보호 환경의 기존 서비스 인증으로 실제 Replica Set에 연결한다."""
        if not os.environ.get('PALDO_TEST_MONGO_URI'):raise unittest.SkipTest('PALDO_TEST_MONGO_URI required')
        from pymongo import MongoClient
        cls.client=MongoClient(os.environ['PALDO_TEST_MONGO_URI'],timeoutMS=15000)
        cls.database=cls.client.get_default_database()

    @classmethod
    def tearDownClass(cls):
        """검증 연결을 닫는다."""
        cls.client.close()

    def setUp(self):
        """매 시나리오에 별도 컬렉션과 지표 registry를 배정한다."""
        from prometheus_client import CollectorRegistry
        from server.infra.outbox_publisher import MongoOutboxStore,Publisher
        self.registry=CollectorRegistry()
        self.collection=self.database['paldo_c06_outbox_'+uuid.uuid4().hex]
        self.store=MongoOutboxStore(self.collection)
        root=Path(__file__).resolve().parents[2]
        self.envelope=json.loads((root/'fixtures/events/sample_events.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.envelope.update(game_id='outbox_'+uuid.uuid4().hex)
        entries=[]
        for seq in range(3):
            event=deepcopy(self.envelope);event.update(seq=seq,event_id=f'{event["game_id"]}:{seq:05d}')
            entries.append({'envelope':event,'published':False,'attempts':0,'committed_at':datetime.now(timezone.utc)})
        self.collection.insert_one({'_id':self.envelope['game_id'],'outbox':entries,'next_seq':3})
        self.sent=[]
        parent=self
        class Sender:
            def send(self,envelope):
                parent.sent.append(envelope)
                return {'topic':envelope.topic,'partition':0,'offset':len(parent.sent)-1}
        self.publisher=Publisher(self.store,Sender(),self.registry)

    def test_order_and_acknowledgement_metrics(self):
        self.assertEqual(self.publisher.tick(),3)
        self.assertEqual([e.seq for e in self.sent],[0,1,2])
        self.assertEqual(self.store.pending(),0)
        from prometheus_client import generate_latest
        text=generate_latest(self.registry).decode()
        self.assertIn('paldo_outbox_publish_lag_seconds_count 3.0',text)
        self.assertIn('paldo_outbox_pending_events 0.0',text)

    def test_delivery_failure_keeps_pending_and_retries_in_order(self):
        original=self.publisher.sender.send
        def fail(envelope):raise RuntimeError('injected_before_ack')
        self.publisher.sender.send=fail
        with self.assertRaises(RuntimeError):self.publisher.tick()
        self.assertEqual(self.store.pending(),3)
        self.publisher.sender.send=original
        self.assertEqual(self.publisher.tick(),3)
        self.assertEqual([e.seq for e in self.sent],[0,1,2])

    def test_ack_then_mark_failure_replays_same_id_and_dedups(self):
        from pipeline.consumers.dedup import plan_game_events
        original=self.store.acknowledge
        self.store.acknowledge=lambda *args:False
        with self.assertRaises(RuntimeError):self.publisher.tick()
        self.assertEqual(self.store.pending(),3)
        self.store.acknowledge=original
        self.assertEqual(self.publisher.tick(),3)
        self.assertEqual([e.seq for e in self.sent],[0,0,1,2])
        rows=plan_game_events(self.sent,{})
        self.assertEqual(len(rows),3)
        self.assertEqual(plan_game_events(self.sent,{r['event_id']:r['envelope_hash'] for r in rows}),[])

    def test_only_one_owner_and_expired_owner_cannot_ack(self):
        game=self.envelope['game_id'];event_id=f'{game}:00000'
        self.assertIsNotNone(self.store.claim('first'))
        self.assertIsNone(self.store.claim('second'))
        self.collection.update_one({'_id':game},{'$set':{'publisher_lease.until':datetime.now(timezone.utc)-timedelta(seconds=1)}})
        self.assertFalse(self.store.acknowledge(game,'first',event_id))
        self.assertIsNotNone(self.store.claim('second'))
        self.store.release(game,'first')
        self.assertEqual(self.collection.find_one({'_id':game})['publisher_lease']['owner'],'second')

    def test_truth_before_end_is_never_sent(self):
        self.collection.update_one({'_id':self.envelope['game_id']},{'$set':{
            'outbox.0.envelope.topic':'paldo.truth','outbox.0.envelope.event_type':'truth_revealed',
            'outbox.0.envelope.phase':'morning'}})
        with self.assertRaises(ValueError):self.publisher.tick()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.store.pending(),3)

    def test_invalid_game_does_not_starve_other_games(self):
        document=self.collection.find_one({'_id':self.envelope['game_id']})
        other=deepcopy(document);other['_id']='z_healthy_'+uuid.uuid4().hex
        for entry in other['outbox']:
            envelope=entry['envelope'];envelope['game_id']=other['_id']
            envelope['event_id']=f'{other["_id"]}:{envelope["seq"]:05d}'
        self.collection.insert_one(other)
        self.collection.update_one({'_id':document['_id']},{'$set':{'outbox.0.envelope.topic':'invalid'}})
        with self.assertRaises(ValueError):self.publisher.tick()
        self.assertEqual(self.publisher.tick(),3)
        self.assertEqual([e.game_id for e in self.sent],[other['_id']]*3)


if __name__=='__main__':unittest.main()
