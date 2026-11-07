"""공식 게임 토픽 → Delta bronze. 설정·인증값은 저장소 밖 JSON에서 읽는다.

실행: spark-submit <검증된 Spark/Delta/Kafka 옵션> pipeline/spark/ingest_bronze.py
엔진·계약 패키지를 driver에서 import할 수 있게 저장소 루트를 PYTHONPATH에 둔다.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from pipeline.spark.bronze_contract import GAME_TOPICS, normalize, unique_rows
from tools.jsonlog import log

SCHEMA = ('event_id string, game_id string, seq long, event_type string, topic string, '
          'source string, load_test boolean, contract_version string, occurred_at string, '
          'envelope_json string, envelope_hash string, partition int, offset long')


def load_config(path):
    """외부 설정의 경로 분리·TLS·입력 크기·단일 writer 조건을 확인한다."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    required = {'bronze_path', 'checkpoint_path', 'lock_path', 'audit_dir', 'kafka', 'spark'}
    if not required <= set(value):
        raise ValueError('pipeline_config_fields')
    paths = [value['bronze_path'].rstrip('/'), value['checkpoint_path'].rstrip('/')]
    if not all(paths) or paths[0] == paths[1] or any(a.startswith(b + '/') for a, b in (paths, paths[::-1])):
        raise ValueError('checkpoint_must_be_separate')
    if value['kafka'].get('security.protocol') != 'SSL':
        raise ValueError('tls_required')
    if value['kafka'].get('ssl.endpoint.identification.algorithm', 'https') != 'https':
        raise ValueError('tls_hostname_verification_required')
    if not value['kafka'].get('bootstrap.servers'):
        raise ValueError('bootstrap_required')
    value.setdefault('max_offsets', 100)
    if type(value['max_offsets']) is not int or not 1 <= value['max_offsets'] <= 10000:
        raise ValueError('bounded_batch_required')
    if not value['spark'].get('spark.master', '').startswith('spark://'):
        raise ValueError('standalone_cluster_required')
    return value


@contextmanager
def writer_lock(path):
    """같은 호스트의 동일 lake writer 중복 실행을 거절한다(Linux VM 전용)."""
    import fcntl
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_audit(path, value):
    """offset 범위·Delta 버전·시간을 원자 저장한다. 인증값·이벤트 본문은 제외한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def process_batch(spark, config, batch, batch_id):
    """배치를 전체 검증한 뒤 event_id MERGE하고, 성공해야 체크포인트를 전진시킨다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    started = time.monotonic()
    transport = batch.select('topic', 'partition', 'offset',
                             F.col('key').cast('string').alias('key'), F.base64('value').alias('raw_base64'))
    messages = transport.limit(config['max_offsets'] + 1).collect()
    if not messages:
        return
    if len(messages) > config['max_offsets']:
        raise ValueError('batch_limit_exceeded')
    import base64
    rows = [normalize(base64.b64decode(r.raw_base64).decode('utf-8'), topic=r.topic, key=r.key,
                      partition=r.partition, offset=r.offset) for r in messages]
    path = config['bronze_path']
    present = DeltaTable.isDeltaTable(spark, path)
    existing = {}
    if present:
        identities = list({row['event_id'] for row in rows})
        existing = {r.event_id: r.envelope_hash for r in spark.read.format('delta').load(path)
                    .where(F.col('event_id').isin(identities)).select('event_id', 'envelope_hash').collect()}
    new_rows = unique_rows(rows, existing)
    if new_rows:
        incoming = spark.createDataFrame(new_rows, SCHEMA)
        if present:
            DeltaTable.forPath(spark, path).alias('t').merge(incoming.alias('s'), 't.event_id=s.event_id').whenNotMatchedInsertAll().execute()
        else:
            incoming.write.format('delta').mode('errorifexists').save(path)
    version = DeltaTable.forPath(spark, path).history(1).select('version').first().version
    ranges = {}
    for row in rows:
        key = f"{row['topic']}:{row['partition']}"
        offset = row['offset']
        values = ranges.setdefault(key, {'first': offset, 'last': offset})
        values['first'], values['last'] = min(values['first'], offset), max(values['last'], offset)
    audit = {'batch_id': batch_id, 'delta_version': version, 'offsets': ranges,
             'received': len(rows), 'inserted': len(new_rows), 'duplicates': len(rows) - len(new_rows),
             'duration_ms': round((time.monotonic() - started) * 1000, 3),
             'completed_at': datetime.now(timezone.utc).isoformat()}
    write_audit(Path(config['audit_dir']) / f'batch-{batch_id:020d}.json', audit)
    log('ingest_bronze', 'info', 'bronze_batch_committed', **audit)


def main(argv=None):
    """인자/환경을 읽어 단일 writer Kafka Structured Streaming 작업을 실행한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.environ.get('PALDO_PIPELINE_CONFIG'))
    parser.add_argument('--available-now', action='store_true')
    args = parser.parse_args(argv)
    if not args.config:
        parser.error('--config 또는 PALDO_PIPELINE_CONFIG 필요')
    spark = None
    try:
        config = load_config(args.config)
        from pyspark.sql import SparkSession
        with writer_lock(config['lock_path']):
            builder = SparkSession.builder.appName('paldo-bronze')
            builder = builder.config('spark.redaction.regex', '(?i)secret|password|token|access[.]?key|sasl[.]jaas')
            for key, value in config['spark'].items():
                builder = builder.config(key, value)
            spark = builder.getOrCreate()
            log('ingest_bronze', 'info', 'stream_started', topic_count=len(GAME_TOPICS))
            source = spark.readStream.format('kafka').option('subscribe', ','.join(GAME_TOPICS))
            for key, value in config['kafka'].items():
                source = source.option('kafka.' + key, value)
            source = source.option('groupIdPrefix', 'paldo-bronze').option('startingOffsets', 'earliest')
            source = source.option('failOnDataLoss', 'true').option('maxOffsetsPerTrigger', config['max_offsets'])
            query = source.load().writeStream.foreachBatch(lambda frame, identity: process_batch(spark, config, frame, identity))
            query = query.option('checkpointLocation', config['checkpoint_path'])
            query = query.trigger(availableNow=True) if args.available_now else query.trigger(processingTime='30 seconds')
            query.start().awaitTermination()
            log('ingest_bronze', 'info', 'stream_stopped')
    except Exception as error:
        log('ingest_bronze', 'error', 'stream_failed', error_type=type(error).__name__)
        raise SystemExit(1) from None
    finally:
        if spark is not None:
            spark.stop()


if __name__ == '__main__':
    main()
