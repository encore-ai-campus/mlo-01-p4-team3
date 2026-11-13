"""광고 gold·CTR 학습 행을 잠정/확정 마감별로 별도 보존한다."""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from pipeline.spark.ads_contract import campaign_daily_rows,closing_times,ctr_snapshot,final_closed_scope,gold_rows,reconcile_rows
from pipeline.spark.ads_job import session,merge_rows,CAMPAIGN_DAILY_SCHEMA,GOLD_SCHEMA,TRAINING_SCHEMA
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def cpc_bid_days(silver, as_of_ms):
    """Allow only the latest finalized day for each campaign if all valid events are CPC."""
    latest, billings = {}, defaultdict(set)
    for row in silver:
        day, cid = row['attribution_day'], row['campaign_id']
        if not day or not cid or row['load_test'] or row['status'] != 'valid' or closing_times(day)[1] > as_of_ms:
            continue
        key = cid, row['source']
        latest[key] = max(day, latest.get(key, day))
        billings[(*key, day)].add(row['billing'])
    return {(*key, day) for key, day in latest.items() if billings[(*key, day)] == {'cpc'}}


def export_optimization(spark, directory, as_of_ms, training_path, training_version, gold_path, gold_version, bid_days,
                        silver_rows=(), silver_path='', silver_version=None):
    """Export finalized rows from the committed Delta versions for CTR training, CPC bids and Beta CTR cells.

    `ctr.json`(맥락 학습 행, `ml.ad_ctr` 입력)·`bid.json`은 그대로 두고, `ad_update ctr`가 받는 칸 형식은
    같은 silver snapshot에서 `ctr_cells.json`으로 따로 쓴다."""
    for name, path, version in (("ctr", training_path, training_version), ("bid", gold_path, gold_version)):
        table = spark.read.format("delta").option("versionAsOf", version).load(path)
        rows = [row.asDict() for row in table.toLocalIterator() if row.attribution_day
                and closing_times(row.attribution_day)[1] <= as_of_ms]
        if name == "bid":
            rows = [row | {"billing": "cpc"} for row in rows if not row["load_test"]
                    and (row["campaign_id"], row["source"], row["attribution_day"]) in bid_days]
        revision = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        write_audit(directory / f"{name}.json", dict(finalized=True, as_of_ms=as_of_ms, rows=rows,
            source_version=f"delta:ads-{name}:{revision}:{version}"))
    if silver_version is not None:
        revision = hashlib.sha256(str(silver_path).encode()).hexdigest()[:16]
        write_audit(directory / "ctr_cells.json",
                    ctr_snapshot(silver_rows, as_of_ms, f"delta:ads-silver:{revision}:{silver_version}"))


def main(argv=None):
    """silver 판정 snapshot과 같은 관찰 시각으로 집계·학습 행을 멱등 저장한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--as-of-ms',type=int,required=True)
    parser.add_argument('--phase',choices=['provisional','final'],required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--budgets',type=Path,required=True,help='귀속일 → 캠페인 → 일 예산(밀리원) JSON')
    parser.add_argument('--export-optimization', type=Path, help='확정 Delta에서 CTR·CPC 입력 내보내기')
    parser.add_argument('--reconcile-closed-days', action='store_true',
                        help='원장 대조를 확정 마감이 지난 귀속일로 좁힘(운영 중 하루 집계용)')
    args=parser.parse_args(argv)
    if args.export_optimization and args.phase != 'final':
        parser.error('--export-optimization requires --phase final')
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
        scope_ledger,scope_rows=final_closed_scope(ledger,rows,args.as_of_ms) if args.reconcile_closed_days else (ledger,rows)
        reconciliation=reconcile_rows(scope_ledger,scope_rows)
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
        if args.export_optimization:
            export_optimization(spark, args.export_optimization, args.as_of_ms,
                                training_path, training_version, path, version, cpc_bid_days(rows, args.as_of_ms),
                                rows, config['ads_silver_path'], silver_version)
        log('ads_gold','info','ads_gold_committed',**result)


if __name__=='__main__':main()
