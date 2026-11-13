"""실제 장애 판의 Delta 메타데이터를 비밀 없는 대조 JSON으로 내보낸다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.ingest_bronze import load_config,write_audit


def main(argv=None):
    """Mongo에서 관찰한 판만 선택하며 인증정보·이벤트 본문은 출력하지 않는다."""
    parser=argparse.ArgumentParser()
    for name in ('config','mongo','out'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv);config=load_config(args.config)
    from pyspark.sql import SparkSession,functions as F
    builder=SparkSession.builder.appName('paldo-fault-reconcile')
    for key,value in config['spark'].items():builder=builder.config(key,value)
    spark=builder.getOrCreate()
    try:
        games=[r['game_id'] for r in json.loads(Path(args.mongo).read_text())]
        if not games:raise ValueError('games_required')
        rows=spark.read.format('delta').load(config['bronze_path']).where(F.col('game_id').isin(games))
        result=[r.asDict() for r in rows.select('game_id','event_id','seq','envelope_hash').collect()]
        write_audit(Path(args.out),result)
        print(json.dumps({'exported_events':len(result),'games':len(games)}))
    finally:spark.stop()


if __name__=='__main__':main()
