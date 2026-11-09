"""광고 gold·CTR 학습 행을 잠정/확정 마감별로 별도 보존한다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.ads_contract import campaign_daily_rows,gold_rows,reconcile_rows
from pipeline.spark.ads_job import session,merge_rows,CAMPAIGN_DAILY_SCHEMA,GOLD_SCHEMA,TRAINING_SCHEMA
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def main(argv=None):
    """silver 판정 snapshot과 같은 관찰 시각으로 집계·학습 행을 멱등 저장한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--as-of-ms',type=int,required=True)
    parser.add_argument('--phase',choices=['provisional','final'],required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--budgets',type=Path,required=True,help='귀속일 → 캠페인 → 일 예산(밀리원) JSON')
    args=parser.parse_args(argv)
    ledger=json.loads(args.ledger.read_text(encoding='utf-8'))
    budgets=json.loads(args.budgets.read_text(encoding='utf-8'))
    with session(args.config,'paldo-ads-gold') as (spark,config):
        from delta.tables import DeltaTable
        silver_version=DeltaTable.forPath(spark,config['ads_silver_path']).history(1).first().version
        frame=spark.read.format('delta').option('versionAsOf',silver_version).load(config['ads_silver_path'])
        rows=[r.asDict() for r in frame.collect()]
        if any(r['observation_cutoff_ms']!=args.as_of_ms for r in rows):raise ValueError('silver_cutoff_mismatch')
        audit=Path(config['ads_audit_dir'])
        silver_audit=json.loads((audit/'silver.json').read_text(encoding='utf-8'))
        if (silver_audit['silver_version'],silver_audit['observation_cutoff_ms'])!=(silver_version,args.as_of_ms):
            raise ValueError('silver_audit_mismatch')
        reconciliation=reconcile_rows(ledger,rows)
        if reconciliation['mismatches']:
            raise ValueError('gold_requires_reconciled_ledger')
        gold,training=gold_rows(rows,args.as_of_ms)
        daily=[row|{'observation_cutoff_ms':args.as_of_ms} for row in campaign_daily_rows(ledger,budgets)]
        path=config['ads_p25_gold_'+args.phase+'_path']
        daily_path=config['ads_p25_campaign_daily_'+args.phase+'_path']
        training_path=config['ad_p25_training_'+args.phase+'_path']
        # 이전 스키마와 P-25 산출물을 분리하고 같은 마감의 재처리 이력을 Delta에 남긴다.
        if len({path,daily_path,training_path})!=3:
            raise ValueError('distinct_gold_paths_required')
        for target_path in (path,daily_path,training_path):
            if DeltaTable.isDeltaTable(spark,target_path):
                previous=spark.read.format('delta').load(target_path).agg({'observation_cutoff_ms':'max'}).first()[0]
                if previous is not None and previous>args.as_of_ms:
                    raise ValueError('gold_cutoff_cannot_move_backwards')
        training=[row|{'observation_cutoff_ms':args.as_of_ms} for row in training]
        version=merge_rows(spark,path,gold,GOLD_SCHEMA,['attribution_day','campaign_id','slot','hour','source','load_test'])
        daily_version=merge_rows(spark,daily_path,daily,CAMPAIGN_DAILY_SCHEMA,['attribution_day','campaign_id','source'])
        training_version=merge_rows(spark,training_path,training,TRAINING_SCHEMA+', observation_cutoff_ms long',['attribution_day','creative_id','slot','preset_id','outcome','hour','source'])
        result={'phase':args.phase,'gold_rows':len(gold),'gold_version':version,'training_rows':len(training),'training_version':training_version,
            'valid_impressions':sum(r['impressions'] for r in gold),'valid_clicks':sum(r['clicks'] for r in gold),'cost_mw':sum(r['cost_mw'] for r in gold),
            'context_missing_events':sum(r['context_missing_events'] for r in gold),'observation_cutoff_ms':args.as_of_ms,
            'campaign_daily_rows':len(daily),'campaign_daily_version':daily_version,'reconciliation_mismatches':0}
        by_source={}
        for row in gold:
            counts=by_source.setdefault(row['source'],{})
            for key in ('impressions','clicks','cost_mw','invalid_impressions','invalid_clicks','load_test_events'):
                counts[key]=counts.get(key,0)+row[key]
        write_audit(audit/('gold-p25-'+args.phase+'.json'),result|{
            'fill':silver_audit['fill'],'campaign_daily':daily,'by_source':by_source})
        log('ads_gold','info','ads_gold_committed',**result)


if __name__=='__main__':main()
