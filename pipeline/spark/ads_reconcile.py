"""확정 MySQL 원장 export와 Delta silver의 재계산 금액을 다섯 범주로 대조한다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.ads_contract import reconcile_rows
from pipeline.spark.ads_job import session
from pipeline.spark.ingest_bronze import write_audit
from tools.jsonlog import log


def main(argv=None):
    """인증정보 없는 원장 snapshot만 읽고 불일치가 있으면 실패 종료한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--ledger',type=Path,required=True);args=parser.parse_args(argv)
    with session(args.config,'paldo-ads-reconcile') as (spark,config):
        rows=[r.asDict() for r in spark.read.format('delta').load(config['ads_silver_path']).collect()]
        result=reconcile_rows(json.loads(args.ledger.read_text()),rows)
        write_audit(Path(config['ads_audit_dir'])/'reconciliation.json',result);log('ads_reconcile','info','ads_reconciled',**result)
        if result['mismatches']:raise SystemExit(1)


if __name__=='__main__':main()
