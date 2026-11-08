"""실제 합성 실행의 source/공식 bronze를 전량 대조하고 격리 검증 snapshot을 만든다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.bronze_contract import normalize
from pipeline.spark.ads_job import session
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def main(argv=None):
    """지연 serve 주입은 synthetic 전용 검증 경로에서만 허용하며 원본은 수정하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--source-events',type=Path,required=True)
    parser.add_argument('--withhold-fault',type=Path);args=parser.parse_args(argv)
    with session(args.config,'paldo-ad-probe-snapshot') as (spark,config):
        if not config.get('validation_mode') or config['bronze_path']==config['official_bronze_path']:raise ValueError('isolated_validation_required')
        from pyspark.sql import functions as F
        records=json.loads(args.source_events.read_text());hashes={};ids=set()
        for record in records:
            event=record['event']
            if event['source']!='synthetic' or not event['request_id'].startswith('syn:c08_'):raise ValueError('synthetic_run_required')
            row=normalize(json.dumps(event),topic=record['topic'],key=event.get('serve_id'),partition=0,offset=0)
            if event['event_id'] in ids:raise ValueError('duplicate_source_id')
            hashes[event['event_id']]=row['envelope_hash'];ids.add(event['event_id'])
        source=spark.read.format('delta').load(config['official_bronze_path'])
        official=source.where(F.col('event_id').isin(sorted(ids)))
        actual={r.event_id:r.envelope_hash for r in official.select('event_id','envelope_hash').collect()}
        if actual!=hashes or official.count()!=len(ids):raise RuntimeError('source_bronze_mismatch')
        corrections=source.where((F.col('topic')=='ad.rejudge') & F.get_json_object('envelope_json','$.source_event_id').isin(sorted(ids)))
        target=official.unionByName(corrections);withheld=0
        if args.withhold_fault:
            fault=json.loads(args.withhold_fault.read_text())
            selected=[r for r in records if r['event']['event_id']==fault['source_event_id']]
            if len(selected)!=1 or selected[0]['event'].get('charge_id')!=fault['charge_id'] or selected[0]['event'].get('charge_mw',0)<=0:raise ValueError('charged_source_required')
            mask=(F.col('topic')=='ad.serve') & (F.get_json_object('envelope_json','$.serve_id')==fault['serve_id'])
            withheld=target.where(mask).count()
            if withheld!=1:raise ValueError('one_serve_fault_required')
            target=target.where(~mask)
        target.write.format('delta').mode('overwrite').option('overwriteSchema','true').save(config['bronze_path'])
        result={'source_unique':len(ids),'official_bronze_unique':official.count(),'source_hash_mismatches':0,'withheld_serves':withheld,
            'rejudge_events':corrections.count(),'validation_bronze_rows':target.count()}
        write_audit(Path(config['ads_audit_dir'])/'source-comparison.json',result);log('ads_probe_snapshot','info','actual_source_verified',**result)


if __name__=='__main__':main()
