"""C-06-02 실제 Kafka 시연. 보호 환경 URI·TLS 프로필을 주입해 명시 실행한다."""
import json,os
from pathlib import Path
root=Path(__file__).resolve().parents[2]
from copy import deepcopy
from datetime import datetime,timezone
import time,uuid,math
from pymongo import MongoClient
from prometheus_client import CollectorRegistry,generate_latest
from confluent_kafka import Consumer
from server.infra.outbox_publisher import MongoOutboxStore,KafkaSender,Publisher
from engine.domain.serde import from_jsonable
from engine.domain.events import EventEnvelope
from pipeline.consumers.dedup import plan_game_events
uri=os.environ['PALDO_TEST_MONGO_URI']
kafka=json.loads(Path(os.environ['PALDO_TEST_KAFKA_CONFIG']).read_text())
client=MongoClient(uri,timeoutMS=20000)
game='c06-live-'+uuid.uuid4().hex
collection=client.get_default_database()['paldo_c06_live_outbox_'+uuid.uuid4().hex]
sample=json.loads((root/'fixtures/events/sample_events.jsonl').read_text().splitlines()[0])
now=datetime.now(timezone.utc);entries=[]
for seq in range(10):
    value=deepcopy(sample);value.update(game_id=game,event_id=f'{game}:{seq:05d}',seq=seq,occurred_at=now.isoformat().replace('+00:00','Z'),source='scripted',load_test=True)
    entries.append({'envelope':value,'published':False,'attempts':0,'committed_at':now})
collection.insert_one({'_id':game,'next_seq':len(entries),'outbox':entries})
registry=CollectorRegistry();sender=KafkaSender(kafka)
store=MongoOutboxStore(collection)
publisher=Publisher(store,sender,registry)
positions=[]
send=sender.send
def record(envelope):
    position=send(envelope);positions.append(position);return position
sender.send=record
published=publisher.tick()
# Inject ack-before-mark: same ID appears again at a distinct Kafka offset.
sender.send(from_jsonable(EventEnvelope,entries[0]['envelope']))
consumer=Consumer({**kafka,'group.id':'paldo-c06-'+uuid.uuid4().hex,'auto.offset.reset':'earliest','enable.auto.commit':False})
consumer.subscribe(['paldo.game']);received=[];deadline=time.monotonic()+30
while time.monotonic()<deadline and len(received)<11:
    message=consumer.poll(1)
    if message is None:continue
    if message.error():raise RuntimeError('consumer_error')
    value=json.loads(message.value())
    if value.get('game_id')!=game:continue
    assert message.key().decode()==game
    received.append(from_jsonable(EventEnvelope,value))
    print('CONSUMED '+json.dumps({'topic':message.topic(),'partition':message.partition(),'offset':message.offset(),'event_id':value['event_id']}))
consumer.close()
rows=plan_game_events(received,{})
assert published==10 and len(received)==11 and len(rows)==10
assert sorted(r['seq'] for r in rows)==list(range(10))
assert plan_game_events(received,{r['event_id']:r['envelope_hash'] for r in rows})==[]
document=collection.find_one({'_id':game})
assert all(e['published'] for e in document['outbox'])
lags=sorted((e['published_at'].replace(tzinfo=timezone.utc)-e['committed_at'].replace(tzinfo=timezone.utc)).total_seconds() for e in document['outbox'])
metrics=generate_latest(registry).decode()
assert 'paldo_outbox_publish_lag_seconds_count 10.0' in metrics
assert 'paldo_outbox_pending_events 0.0' in metrics
print(metrics)
result={'published':published,'received':len(received),'unique':len(rows),'pending':store.pending(),'publish_lag_p95_ms':lags[math.ceil(len(lags)*.95)-1]*1000,'partitions':sorted(set(p['partition'] for p in positions)),'offset_min':min(p['offset'] for p in positions),'offset_max':max(p['offset'] for p in positions),'game_id':game}
destination=Path(os.environ.get('PALDO_TEST_RESULT','out/perf/outbox_kafka.json'))
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_text(json.dumps(result)+'\n')
print('PALDO_MEASURE '+json.dumps(result))
client.close()


