"""판 종료 뒤의 truth만 game_id로 조인하고 학습 JSON을 쓴다."""
import argparse
from pathlib import Path
from contracts.datasets import TrainingRow
from dataclasses import fields
from pipeline.spark.ingest_bronze import load_config,writer_lock,write_audit
from tools.jsonlog import log


def join(spark,config):
    """정상 종료 뒤의 truth만 라벨로 붙이고 학습 대상 조건을 명시해 출력한다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    bronze=spark.read.format('delta').load(config['bronze_path'])
    payload=lambda path:F.get_json_object('envelope_json',path)
    ends=bronze.where((F.col('event_type')=='game_ended') &
        payload('$.payload.winner').isin('village','changgwi') &
        (F.coalesce(payload('$.payload.completed'),F.lit('true'))=='true') &
        (F.coalesce(payload('$.payload.reason'),F.lit(''))!='aborted')).select('game_id',F.col('seq').alias('end_seq'))
    truths=bronze.where((F.col('event_type')=='truth_revealed') & (payload('$.phase')=='ended')).select(
        'game_id',F.col('seq').alias('truth_seq'),F.from_json(payload('$.payload.roles'),'map<string,string>').alias('roles'),
        F.col('occurred_at').alias('joined_at'))
    completed=truths.join(ends,'game_id').where('truth_seq > end_seq').dropDuplicates(['game_id'])
    path=config['silver_path'];silver=spark.read.format('delta').load(path)
    labels=silver.join(completed,'game_id').select('row_id',
        (F.element_at('roles',F.col('candidate'))=='possessed').cast('int').alias('new_label'),
        F.col('joined_at').alias('new_joined_at'))
    DeltaTable.forPath(spark,path).alias('t').merge(labels.alias('s'),'t.row_id=s.row_id').whenMatchedUpdate(
        set={'label':'s.new_label','label_joined_at':'s.new_joined_at'}).execute()
    current=spark.read.format('delta').load(path)
    training=current.where((F.col('observer_role')=='villager') & F.col('label').isNotNull() & ~F.col('load_test'))
    training.select(*[f.name for f in fields(TrainingRow)]).write.mode('overwrite').json(config['training_export_path'])
    result={'silver_rows':current.count(),'labeled_games':current.where('label IS NOT NULL').select('game_id').distinct().count(),
            'completed_games':completed.count(),'training_rows':training.count(),
            'silver_version':DeltaTable.forPath(spark,path).history(1).first().version}
    write_audit(Path(config['audit_dir'])/'label-join.json',result);return result


def main(argv=None):
    """외부 S3·Spark 설정으로 종료 후 라벨 조인과 마을 관찰자 export를 실행한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args(argv)
    config=load_config(args.config)
    from pyspark.sql import SparkSession
    with writer_lock(config['lock_path']):
        builder=SparkSession.builder.appName('paldo-labels')
        for key,value in config['spark'].items():builder=builder.config(key,value)
        spark=builder.getOrCreate()
        try:log('join_labels','info','labels_joined',**join(spark,config))
        finally:spark.stop()


if __name__=='__main__':main()
