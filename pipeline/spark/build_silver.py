"""Delta bronze 관측을 학습 행으로 변환한다. 진실 조인은 별도 단계다."""
import argparse
import json
from pathlib import Path
from pipeline.spark.ingest_bronze import load_config,writer_lock,write_audit
from pipeline.spark.silver_contract import observation_rows
from tools.jsonlog import log

TRAINING_SCHEMA=('row_id string,game_id string,snapshot_id string,observer string,candidate string,'
 'day int,phase string,preset_id string,n_active int,n_possessed int,n_possessed_remaining int,'
 'n_candidates int,feature_version string,features map<string,double>,source string,'
 'split_key struct<user:string,game_id:string,time_bucket:string,lineage_root:string>,'
 'label int,label_joined_at string,load_test boolean,observer_role string,snapshot_json string')


def build(spark,config):
    """bronze 고정 버전의 관찰을 변환하고 row_id 기준의 새 행만 MERGE한다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    version=DeltaTable.forPath(spark,config['bronze_path']).history(1).first().version
    source=spark.read.format('delta').option('versionAsOf',version).load(config['bronze_path'])
    source=source.where(F.col('event_type')=='observation').select('envelope_json')
    records=source.rdd.flatMap(lambda r:observation_rows(json.loads(r.envelope_json)))
    frame=spark.createDataFrame(records,TRAINING_SCHEMA).dropDuplicates(['row_id'])
    path=config['silver_path']
    if DeltaTable.isDeltaTable(spark,path):
        DeltaTable.forPath(spark,path).alias('t').merge(frame.alias('s'),'t.row_id=s.row_id').whenNotMatchedInsertAll().execute()
    else:frame.write.format('delta').mode('errorifexists').save(path)
    result={'bronze_version':version,'silver_rows':spark.read.format('delta').load(path).count(),
            'silver_version':DeltaTable.forPath(spark,path).history(1).first().version}
    write_audit(Path(config['audit_dir'])/'silver-build.json',result);return result


def main(argv=None):
    """외부 Standalone/S3 설정으로 단일 writer에서 실행한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args(argv)
    config=load_config(args.config)
    from pyspark.sql import SparkSession
    with writer_lock(config['lock_path']):
        builder=SparkSession.builder.appName('paldo-silver')
        for key,value in config['spark'].items():builder=builder.config(key,value)
        spark=builder.getOrCreate()
        try:log('build_silver','info','silver_built',**build(spark,config))
        finally:spark.stop()


if __name__=='__main__':main()
