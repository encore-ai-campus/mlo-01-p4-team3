"""Spark가 commit한 offset과 Kafka high-water 차이를 읽기 전용으로 수집한다."""
import argparse,json,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading


def ads_p25_metrics(root, run):
    """P-25 집계의 source·phase와 일간/지면 범위를 유지해 노출한다."""
    def metric(name, value, **labels):
        encoded = ','.join(f'{key}={json.dumps(str(val), ensure_ascii=False)}' for key, val in labels.items())
        return f'paldo_ads_{name}{{{encoded}}} {value}'

    for phase in ('provisional', 'final'):
        path = Path(root) / f'gold-p25-{phase}.json'
        if not path.exists():
            continue
        audit = json.loads(path.read_text(encoding='utf-8'))
        for source, counts in audit['by_source'].items():
            labels = dict(source=source, run=run, phase=phase)
            for name in ('impressions', 'clicks', 'cost_mw', 'load_test_events'):
                yield metric(f'gold_{name}', counts[name], **labels)
            for name in ('impressions', 'clicks'):
                invalid = counts[f'invalid_{name}']
                total = counts[name] + invalid
                if total:
                    yield metric(f'gold_invalid_{name}_ratio', invalid / total, **labels)
        for row in audit['fill']:
            if row['fill_ratio'] is not None:
                yield metric('gold_fill_ratio', row['fill_ratio'], source=row['source'],
                             run=run, phase=phase, slot=row['slot'] or 'all')
        for row in audit['campaign_daily']:
            if row['burn_ratio'] is not None:
                yield metric('gold_budget_burn_ratio', row['burn_ratio'], source=row['source'],
                             run=run, phase=phase, day=row['attribution_day'], campaign=row['campaign_id'])
        yield metric('reconciliation_mismatches', audit['reconciliation_mismatches'], run=run, phase=phase)


def collect(config):
    """미완료 batch offset은 제외하고 알 수 없는 수치를 0으로 만들지 않는다."""
    from confluent_kafka import Consumer,TopicPartition
    checkpoint=Path(config['checkpoint']);commits=checkpoint/'commits'
    batches=[int(p.name) for p in commits.iterdir() if p.name.isdigit()]
    if not batches:raise ValueError('no_committed_batch')
    batch=max(batches);lines=(checkpoint/'offsets'/str(batch)).read_text().splitlines()
    offsets=[json.loads(line) for line in lines[2:] if line and line!='-']
    client=json.loads(Path(config['kafka_client']).read_text())
    client.update({'group.id':'paldo-bronze-metrics','enable.auto.commit':False,'auto.offset.reset':'earliest'})
    consumer=Consumer(client);result=[]
    try:
        for stream in offsets:
            for topic,partitions in stream.items():
                for partition,offset in partitions.items():
                    low,high=consumer.get_watermark_offsets(TopicPartition(topic,int(partition)),timeout=5,cached=False)
                    result.append(f'paldo_pipeline_consumer_lag{{topic="{topic}",partition="{partition}"}} {max(0,high-offset)}')
    finally:consumer.close()
    result.extend([f'paldo_pipeline_committed_batch {batch}',f'paldo_pipeline_last_commit_timestamp_seconds {(commits/str(batch)).stat().st_mtime}'])
    audits=sorted(Path(config['audit']).glob('batch-*.json'),key=lambda p:p.stat().st_mtime)
    if audits:
        audit=json.loads(audits[-1].read_text())
        for name,key,scale in [('batch_duration_seconds','duration_ms',.001),('batch_received_events','received',1),('batch_inserted_events','inserted',1)]:
            if key in audit:result.append(f'paldo_pipeline_{name} {audit[key]*scale}')
    if config.get('ads_p25_audit'):
        result.extend(ads_p25_metrics(config['ads_p25_audit'], config['ads_run']))
    elif config.get('ads_audit'):
        root=Path(config['ads_audit'])
        for phase in ('provisional','final'):
            path=root/('gold-'+phase+'.json')
            if not path.exists():continue
            audit=json.loads(path.read_text());labels='source="synthetic",run="c08_r1",phase="'+phase+'"'
            for name,key in [('impressions','valid_impressions'),('clicks','valid_clicks'),('cost_mw','cost_mw')]:
                result.append(f'paldo_ads_gold_{name}{{{labels}}} {audit[key]}')
        path=root/'reconciliation.json'
        if path.exists():result.append('paldo_ads_reconciliation_mismatches{source="synthetic",run="c08_r1"} '+str(json.loads(path.read_text())['mismatches']))
    return '\n'.join(result)+'\n'


def main(argv=None):
    """인증정보는 외부 파일에서만 읽고 localhost에서 지표만 제공한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args(argv)
    config=json.loads(args.config.read_text());snapshot={'body':'paldo_pipeline_collection_success 0\n'}
    def refresh():
        """실패 시 오래된 성공 지표를 제거하고 수집 실패만 남긴다."""
        while True:
            try:snapshot['body']=collect(config)+'paldo_pipeline_collection_success 1\n'
            except Exception:snapshot['body']='paldo_pipeline_collection_success 0\n'
            time.sleep(15)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """정해진 metrics 경로만 반환한다."""
            if self.path!='/metrics':self.send_error(404);return
            body=snapshot['body'].encode();self.send_response(200);self.end_headers();self.wfile.write(body)
        def log_message(self,*args):
            """지표 수집 요청의 중복 로그를 생략한다."""
    threading.Thread(target=refresh,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',9110),Handler).serve_forever()


if __name__=='__main__':main()
