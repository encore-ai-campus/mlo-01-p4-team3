"""C-06-02: 요청 프로세스와 분리된 순차 Mongo Outbox → Kafka 발행기."""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import time
import uuid
from engine.domain.events import EventEnvelope,EventType,topic_of
from engine.domain.serde import from_jsonable,to_jsonable
from tools.jsonlog import log


class MongoOutboxStore:
    """게임 문서별 lease를 사용한다. 상태·next_seq는 발행기가 변경하지 않는다."""

    def __init__(self,collection,lease_seconds=45):
        from pymongo.write_concern import WriteConcern
        self.collection=collection.with_options(write_concern=WriteConcern('majority'))
        self.lease_seconds=lease_seconds

    def claim(self,owner):
        """미발행 판 하나를 단일 발행자에게 빌려준다."""
        from pymongo import ReturnDocument
        now=datetime.now(timezone.utc)
        return self.collection.find_one_and_update(
            {'outbox':{'$elemMatch':{'published':False}},'$or':[{'publisher_lease':{'$exists':False}},
             {'publisher_lease.until':{'$lte':now}}]},
            {'$set':{'publisher_lease':{'owner':owner,'until':now+timedelta(seconds=self.lease_seconds)}}},
            sort=[('_id',1)],return_document=ReturnDocument.AFTER)

    def renew(self,game,owner):
        """유효한 자기 lease만 연장한다. 만료 후에는 발행을 중단한다."""
        now=datetime.now(timezone.utc)
        return self.collection.update_one({'_id':game,'publisher_lease.owner':owner,'publisher_lease.until':{'$gt':now}},
            {'$set':{'publisher_lease.until':now+timedelta(seconds=self.lease_seconds)}}).matched_count==1

    def attempt(self,game,owner,event_id):
        """각 전송 시도를 기록한다. 실패해도 미발행 상태는 유지한다."""
        return self.collection.update_one({'_id':game,'publisher_lease.owner':owner,
            'publisher_lease.until':{'$gt':datetime.now(timezone.utc)}},
            {'$inc':{'outbox.$[entry].attempts':1}},array_filters=[{'entry.envelope.event_id':event_id,'entry.published':False}]).modified_count==1

    def acknowledge(self,game,owner,event_id):
        """브로커 확인 뒤에만 published를 바꾼다. lease를 잃었으면 표시하지 않는다."""
        return self.collection.update_one({'_id':game,'publisher_lease.owner':owner,
            'publisher_lease.until':{'$gt':datetime.now(timezone.utc)}},
            {'$set':{'outbox.$[entry].published':True,'outbox.$[entry].published_at':datetime.now(timezone.utc)}},
            array_filters=[{'entry.envelope.event_id':event_id,'entry.published':False}]).modified_count==1

    def release(self,game,owner):
        """다른 발행자의 lease는 해제하지 않는다."""
        self.collection.update_one({'_id':game,'publisher_lease.owner':owner},{'$unset':{'publisher_lease':''}})

    def pending(self):
        """전체 미발행 이벤트 수를 gauge에 쓸 수 있게 조회한다."""
        rows=list(self.collection.aggregate([{'$unwind':'$outbox'},{'$match':{'outbox.published':False}},{'$count':'n'}]))
        return rows[0]['n'] if rows else 0


class KafkaSender:
    """acks=all/idempotence와 제한된 delivery timeout으로 한 건씩 확인한다."""

    def __init__(self,config):
        from confluent_kafka import Producer
        if config.get('security.protocol')!='SSL' or config.get('ssl.endpoint.identification.algorithm','https')!='https':
            raise ValueError('verified_tls_required')
        config={**config,'acks':'all','enable.idempotence':True,'delivery.timeout.ms':10000,
                'request.timeout.ms':5000,'client.id':'paldo-outbox'}
        self.producer=Producer(config)

    def send(self,envelope):
        """성공한 브로커의 실제 topic/partition/offset을 반환한다."""
        outcome=[]
        def delivered(error,message):outcome.append((error,message))
        self.producer.produce(envelope.topic,key=envelope.game_id.encode(),
            value=json.dumps(to_jsonable(envelope),ensure_ascii=False,sort_keys=True).encode(),callback=delivered)
        remaining=self.producer.flush(12)
        if remaining or not outcome or outcome[0][0]:raise RuntimeError('kafka_delivery_not_acknowledged')
        message=outcome[0][1]
        return {'topic':message.topic(),'partition':message.partition(),'offset':message.offset()}


class Publisher:
    """전송 실패 시 뒤 순번을 보내지 않는다. ack 직후 장애의 중복은 소비자가 제거한다."""

    def __init__(self,store,sender,registry=None):
        from prometheus_client import REGISTRY,Gauge,Histogram
        self.store,self.sender=store,sender
        self.owner=str(uuid.uuid4())
        registry=registry if registry is not None else REGISTRY
        self.lag=Histogram('paldo_outbox_publish_lag_seconds','Commit to Kafka acknowledgement',registry=registry)
        self.pending_gauge=Gauge('paldo_outbox_pending_events','Unpublished outbox events',registry=registry)

    def tick(self):
        """판 하나의 미발행 목록을 seq 순으로 처리하고 발행 건수를 반환한다."""
        self.pending_gauge.set(self.store.pending())
        game=self.store.claim(self.owner)
        if game is None:return 0
        count=0
        try:
            entries=sorted((e for e in game['outbox'] if not e['published']),key=lambda e:e['envelope']['seq'])
            for entry in entries:
                envelope=from_jsonable(EventEnvelope,entry['envelope'])
                if envelope.event_id!=f'{envelope.game_id}:{envelope.seq:05d}':raise ValueError('invalid_event_id')
                if envelope.topic!=topic_of(EventType(envelope.event_type)):raise ValueError('invalid_topic')
                if envelope.topic=='paldo.truth' and envelope.phase!='ended':raise ValueError('truth_before_end')
                if not self.store.renew(game['_id'],self.owner) or not self.store.attempt(game['_id'],self.owner,envelope.event_id):
                    raise RuntimeError('publisher_lease_lost')
                position=self.sender.send(envelope)
                if not self.store.acknowledge(game['_id'],self.owner,envelope.event_id):raise RuntimeError('acknowledgement_not_recorded')
                committed=entry['committed_at']
                if committed.tzinfo is None:committed=committed.replace(tzinfo=timezone.utc)
                self.lag.observe(max(0,(datetime.now(timezone.utc)-committed).total_seconds()))
                count+=1
                log('outbox_publisher','info','event_published',game_id=envelope.game_id,event_id=envelope.event_id,**position)
        finally:
            self.store.release(game['_id'],self.owner)
            self.pending_gauge.set(self.store.pending())
        return count


def main(argv=None):
    """별도 프로세스를 실행한다. 연결 문자열은 환경, TLS 설정은 보호 파일로 받는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--kafka-config',type=Path,required=True)
    parser.add_argument('--collection',default='games');parser.add_argument('--once',action='store_true')
    parser.add_argument('--metrics-port',type=int,default=9108)
    args=parser.parse_args(argv)
    from pymongo import MongoClient
    from prometheus_client import start_http_server
    client=MongoClient(os.environ['PALDO_MONGO_URI'],serverSelectionTimeoutMS=5000,timeoutMS=15000)
    try:
        config=json.loads(args.kafka_config.read_text())
        if os.environ.get('PALDO_KAFKA_BOOTSTRAP'):config['bootstrap.servers']=os.environ['PALDO_KAFKA_BOOTSTRAP']
        publisher=Publisher(MongoOutboxStore(client.get_default_database()[args.collection]),KafkaSender(config))
        start_http_server(args.metrics_port,addr='localhost')
        log('outbox_publisher','info','publisher_started')
        while True:
            try:published=publisher.tick()
            except Exception as error:
                log('outbox_publisher','error','publish_retry_pending',error_type=type(error).__name__)
                if args.once:raise SystemExit(1) from None
                time.sleep(2);continue
            if args.once:break
            if not published:time.sleep(1)
    finally:client.close()


if __name__=='__main__':main()
