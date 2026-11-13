"""공식 광고 bronze의 유효성·serve 맥락을 조인하고 필요하면 재판정을 발행한다."""
import argparse
from pathlib import Path
from pipeline.spark.ads_contract import fill_rows,silver_rows,rejudge_events
from pipeline.spark.ads_job import session,bronze_records,merge_rows,publish_rejudges,SILVER_SCHEMA
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def main(argv=None):
    """관찰 종료 시각을 필수로 받아 같은 snapshot을 반복 실행할 수 있게 한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--as-of-ms',required=True,type=int)
    parser.add_argument('--publish-rejudges',action='store_true');args=parser.parse_args(argv)
    with session(args.config,'paldo-ads-silver') as (spark,config):
        records,version=bronze_records(spark,config);rows=silver_rows(records,args.as_of_ms)
        target=merge_rows(spark,config['ads_silver_path'],rows,SILVER_SCHEMA,['event_id'])
        emitted=[r['event']['event_id'] for r in records if r['topic']=='ad.rejudge']
        corrections=rejudge_events(rows,args.as_of_ms,emitted)
        published=publish_rejudges(corrections,config) if args.publish_rejudges else 0
        result={'bronze_version':version,'input_ad_events':len(records),'silver_rows':len(rows),'silver_version':target,
            'rejudge_candidates':len(corrections),'rejudge_acked':published,'observation_cutoff_ms':args.as_of_ms,
            'valid':sum(r['status']=='valid' for r in rows),'invalid':sum(r['status']=='invalid' for r in rows),'pending':sum(r['status']=='pending' for r in rows)}
        write_audit(Path(config['ads_audit_dir'])/'silver.json',result|{'fill':fill_rows(records,args.as_of_ms)})
        log('ads_silver','info','ads_silver_committed',**result)


if __name__=='__main__':main()
