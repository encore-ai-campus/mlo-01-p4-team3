"""광고 배치의 고정 Delta snapshot·멱등 저장·영속 재판정 outbox를 조립한다."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from pipeline.spark.ingest_bronze import load_config,writer_lock,write_audit

SILVER_SCHEMA='event_id string, request_id string, serve_id string, source string, campaign_id string, creative_id string, slot string, billing string, price_mw long, received_at_ms long, occurred_at_ms long, contract_version string, topic string, load_test boolean, online_status string, online_charge_id string, online_charge_mw long, status string, invalid_reason string, expected_charge_id string, expected_charge_mw long, serve_time_ms long, attribution_day string, hour int, preset_id string, outcome string, returning boolean, context_missing boolean, implied_by_click boolean, observation_cutoff_ms long, judge_version string'
MEASURES=('impressions long, clicks long, ctr double, cost_mw long, ecpm_mw double, cpc_mw double, '
          'invalid_events long, pending_events long, invalid_ratio double, context_missing_events long, judged_events long, '
          'invalid_impressions long, invalid_clicks long, invalid_impression_rate double, invalid_click_rate double, load_test_events long')
GOLD_SCHEMA='attribution_day string, campaign_id string, slot string, hour int, source string, load_test boolean, '+MEASURES+', observation_cutoff_ms long, provisional_close_ms long, final_close_ms long, provisional_closed boolean, final_closed boolean'
CAMPAIGN_DAILY_SCHEMA=('attribution_day string, campaign_id string, source string, confirmed_net_mw long, '
                       'budget_mw long, burn_ratio double, observation_cutoff_ms long')
TRAINING_SCHEMA='attribution_day string, creative_id string, slot string, preset_id string, outcome string, hour int, source string, '+MEASURES


@contextmanager
def session(path,name):
    """광고 작업은 별도 포트·writer lock을 써 게임 bronze 스트림과 분리한다."""
    config=load_config(path)
    from pyspark.sql import SparkSession
    with writer_lock(config['ads_lock_path']):
        builder=SparkSession.builder.appName(name).config('spark.redaction.regex','(?i)secret|password|token|access[.]?key')
        for key,value in config['spark'].items():builder=builder.config(key,value)
        spark=builder.getOrCreate()
        try:yield spark,config
        finally:spark.stop()


def bronze_records(spark,config):
    """한 Delta 버전의 광고만 읽고 명시 상한을 넘으면 부분 결과 대신 실패한다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    version=DeltaTable.forPath(spark,config['bronze_path']).history(1).first().version
    frame=spark.read.format('delta').option('versionAsOf',version).load(config['bronze_path']).where(F.col('topic').startswith('ad.'))
    bound=config.get('max_ad_rows',100000);rows=frame.select('topic','envelope_json').limit(bound+1).collect()
    if len(rows)>bound:raise ValueError('bounded_ad_snapshot_exceeded')
    return [{'topic':r.topic,'event':json.loads(r.envelope_json)} for r in rows],version


def merge_rows(spark,path,rows,schema,keys):
    """전체 범위 snapshot을 row hash로 MERGE하고 사라진 그룹도 제거한다."""
    from delta.tables import DeltaTable
    prepared=[]
    for source in rows:
        row=dict(source)
        row['row_id']=hashlib.sha256(json.dumps([row.get(k) for k in keys],sort_keys=True).encode()).hexdigest()
        row['row_hash']=hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        prepared.append(row)
    frame=spark.createDataFrame(prepared,schema+', row_id string, row_hash string')
    if DeltaTable.isDeltaTable(spark,path):
        (DeltaTable.forPath(spark,path).alias('t').merge(frame.alias('s'),'t.row_id=s.row_id')
          .whenMatchedUpdateAll(condition='t.row_hash <> s.row_hash').whenNotMatchedInsertAll().whenNotMatchedBySourceDelete().execute())
    else:frame.write.format('delta').mode('errorifexists').save(path)
    return DeltaTable.forPath(spark,path).history(1).first().version


def publish_rejudges(events,config):
    """결정적 ID를 SQLite outbox에 먼저 보존하고 Kafka ACK 뒤 발행 표시한다."""
    from confluent_kafka import Producer
    path=Path(config['ad_rejudge_outbox']);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0)')
        for event in events:
            previous=db.execute('SELECT payload FROM events WHERE id=?',(event['event_id'],)).fetchone()
            if previous:
                old=json.loads(previous[0]);new=dict(event);old.pop('occurred_at_ms');new.pop('occurred_at_ms')
                if old!=new:raise ValueError('rejudge_identity_conflict')
            else:db.execute('INSERT INTO events(id,payload) VALUES(?,?)',(event['event_id'],json.dumps(event,separators=(',',':'))))
        db.commit()
        client=json.loads(Path(config['ad_rejudge_client']).read_text())
        if client.get('security.protocol')!='SSL' or client.get('ssl.endpoint.identification.algorithm','https')!='https':raise ValueError('verified_tls_required')
        producer=Producer(client|{'enable.idempotence':True,'acks':'all','delivery.timeout.ms':10000})
        count=0
        for identity,payload in db.execute('SELECT id,payload FROM events WHERE published=0 ORDER BY id').fetchall():
            event=json.loads(payload);outcome=[]
            producer.produce('ad.rejudge',key=event['charge_id'],value=payload.encode(),on_delivery=lambda error,message:outcome.append(error))
            remaining=producer.flush(15)
            if remaining or not outcome or outcome[0] is not None:raise RuntimeError('rejudge_ack_failed')
            db.execute('UPDATE events SET published=1 WHERE id=?',(identity,));db.commit();count+=1
        return count
    finally:db.close()
