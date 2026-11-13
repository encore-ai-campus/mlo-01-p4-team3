"""k3s Job의 ACK manifest와 공식 bronze의 전체 seq·해시를 대조한다."""
import argparse,json
from pathlib import Path
from pipeline.spark.ingest_bronze import load_config,write_audit


def main(argv=None):
    """동일 판 ID의 누락·중복·본문 변경과 출처 변경을 실패시킨다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args(argv)
    from pyspark.sql import SparkSession,functions as F
    config=load_config(args.config);manifest=json.loads(args.manifest.read_text());expected=manifest['games']
    if manifest['source']!='bot' or manifest['illegal_commands']!=0 or len(expected)!=manifest['completed']:raise ValueError('invalid_sim_manifest')
    builder=SparkSession.builder.appName('paldo-sim-verification')
    for key,value in config['spark'].items():builder=builder.config(key,value)
    spark=builder.getOrCreate()
    try:
        frame=spark.read.format('delta').load(config.get('official_bronze_path',config['bronze_path'])).where(F.col('game_id').isin(sorted(expected)))
        digest=F.sha2(F.concat_ws('',F.transform(F.sort_array(F.collect_list(F.struct('seq','envelope_hash'))),lambda x:x['envelope_hash'])),256)
        rows=frame.groupBy('game_id').agg(F.count('*').alias('events'),F.countDistinct('event_id').alias('ids'),F.countDistinct('seq').alias('seqs'),
            F.min('seq').alias('first'),F.max('seq').alias('last'),digest.alias('sha256')).collect()
        if frame.where(F.col('source')!='bot').count():raise RuntimeError('simulation_source_mismatch')
        actual={r.game_id:{'events':r.events,'sha256':r.sha256} for r in rows}
        if actual!=expected or any(r.ids!=r.events or r.seqs!=r.events or r.first!=0 or r.last!=r.events-1 for r in rows):raise RuntimeError('simulation_bronze_mismatch')
        result={'completed_games':len(rows),'source_events':sum(g['events'] for g in expected.values()),'bronze_events':frame.count(),
            'hash_mismatches':0,'duplicate':0,'lost':0,'source':'bot','illegal_commands':0}
        write_audit(args.out,result);print(json.dumps(result))
    finally:spark.stop()


if __name__=='__main__':main()
