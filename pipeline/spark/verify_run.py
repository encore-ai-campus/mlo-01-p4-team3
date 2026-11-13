"""실제 엔진 원본·bronze·silver·export와 driver/executor 버전을 대조한다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.bronze_contract import normalize
from pipeline.spark.ingest_bronze import load_config,write_audit
from tools.jsonlog import log


def verify(spark,config,events):
    """원본 전체 ID/hash·연속 seq·완료 판 라벨·학습 필터를 실제 Delta에서 검증한다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    import sys
    hashes={};games=set()
    for offset,line in enumerate(Path(events).open(encoding='utf-8')):
        e=json.loads(line);row=normalize(line,topic=e['topic'],key=e['game_id'],partition=0,offset=offset)
        if row['event_id'] in hashes:raise ValueError('duplicate_source_id')
        hashes[row['event_id']]=row['envelope_hash'];games.add(e['game_id'])
    bronze=spark.read.format('delta').load(config['bronze_path'])
    scoped=bronze.where(F.col('game_id').isin(sorted(games)))
    actual={r.event_id:r.envelope_hash for r in scoped.select('event_id','envelope_hash').collect()}
    if actual!=hashes or scoped.count()!=len(hashes):raise RuntimeError('bronze_source_mismatch')
    seqs=scoped.groupBy('game_id').agg(F.count('*').alias('n'),F.countDistinct('seq').alias('unique'),F.min('seq').alias('first'),F.max('seq').alias('last')).collect()
    if any(r.first!=0 or r.n!=r.unique or r.n!=r.last+1 for r in seqs):raise RuntimeError('seq_gap_or_duplicate')
    silver=spark.read.format('delta').load(config['silver_path']).where(F.col('game_id').isin(sorted(games)))
    labeled=silver.where('label IS NOT NULL').select('game_id').distinct().count()
    ended=scoped.where("event_type='game_ended'").select('game_id').distinct().count()
    if labeled!=ended:raise RuntimeError('completed_label_game_mismatch')
    training=spark.read.json(config['training_export_path'])
    if training.where('label IS NULL').limit(1).count():raise RuntimeError('unlabeled_export')
    versions=spark.sparkContext.parallelize(range(2),2).map(lambda _:__import__('sys').version.split()[0]).collect()
    if not sys.version.startswith('3.14') or any(not v.startswith('3.14') for v in versions):raise RuntimeError('python_version_mismatch')
    result={'input_games':len(games),'input_events':len(hashes),'bronze_events':len(actual),'lost':0,'duplicate':0,
        'completed_games':ended,'labeled_games':labeled,'silver_rows':silver.count(),'training_rows':training.count(),
        'bronze_total':bronze.count(),'bronze_version':DeltaTable.forPath(spark,config['bronze_path']).history(1).first().version,
        'silver_version':DeltaTable.forPath(spark,config['silver_path']).history(1).first().version,
        'driver_python':sys.version.split()[0],'executor_python':versions,
        'games':[{'game_id':r.game_id,'next_seq':r.last+1,'unique_events':r.unique} for r in sorted(seqs,key=lambda r:r.game_id)]}
    return result


def main(argv=None):
    """외부 설정으로 대조하고 검증 결과 JSON을 감사 경로에 쓴다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--events',required=True)
    parser.add_argument('--name',default='verification');args=parser.parse_args(argv);config=load_config(args.config)
    from pyspark.sql import SparkSession
    builder=SparkSession.builder.appName('paldo-verify')
    for key,value in config['spark'].items():builder=builder.config(key,value)
    spark=builder.getOrCreate()
    try:
        result=verify(spark,config,args.events);write_audit(Path(config['audit_dir'])/(args.name+'.json'),result)
        log('verify_run','info','verified',**{k:v for k,v in result.items() if k!='games'})
    finally:spark.stop()


if __name__=='__main__':main()
