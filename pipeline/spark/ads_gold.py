"""광고 gold·CTR 학습 행을 잠정/확정 마감별로 별도 보존한다."""
import argparse
from pathlib import Path
from pipeline.spark.ads_contract import gold_rows
from pipeline.spark.ads_job import session,merge_rows,GOLD_SCHEMA,TRAINING_SCHEMA
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def main(argv=None):
    """silver 판정 snapshot과 같은 관찰 시각으로 집계·학습 행을 멱등 저장한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--as-of-ms',type=int,required=True)
    parser.add_argument('--phase',choices=['provisional','final'],required=True);args=parser.parse_args(argv)
    with session(args.config,'paldo-ads-gold') as (spark,config):
        frame=spark.read.format('delta').load(config['ads_silver_path']);rows=[r.asDict() for r in frame.collect()]
        if any(r['observation_cutoff_ms']!=args.as_of_ms for r in rows):raise ValueError('silver_cutoff_mismatch')
        gold,training=gold_rows(rows,args.as_of_ms,config.get('ad_budgets',{}))
        path=config['ads_gold_'+args.phase+'_path']
        if args.phase=='final':
            from delta.tables import DeltaTable
            if DeltaTable.isDeltaTable(spark,path) and not config.get('final_restatement_approved',False):
                import json
                previous=[{k:v for k,v in r.asDict().items() if k not in ('row_id','row_hash')} for r in spark.read.format('delta').load(path).collect()]
                canonical=lambda items:sorted(json.dumps(r,sort_keys=True,allow_nan=False) for r in items)
                if any(r['final_closed'] for r in previous) and canonical(previous)!=canonical(gold):raise ValueError('final_restatement_requires_pm_approval')
        version=merge_rows(spark,path,gold,GOLD_SCHEMA,['attribution_day','campaign_id','slot','hour','source','load_test'])
        training_version=merge_rows(spark,config['ad_training_path'],training,TRAINING_SCHEMA,['attribution_day','creative_id','slot','preset_id','outcome','hour','source'])
        result={'phase':args.phase,'gold_rows':len(gold),'gold_version':version,'training_rows':len(training),'training_version':training_version,
            'valid_impressions':sum(r['impressions'] for r in gold),'valid_clicks':sum(r['clicks'] for r in gold),'cost_mw':sum(r['cost_mw'] for r in gold),
            'context_missing_events':sum(r['context_missing_events'] for r in gold),'observation_cutoff_ms':args.as_of_ms}
        write_audit(Path(config['ads_audit_dir'])/('gold-'+args.phase+'.json'),result);log('ads_gold','info','ads_gold_committed',**result)


if __name__=='__main__':main()
