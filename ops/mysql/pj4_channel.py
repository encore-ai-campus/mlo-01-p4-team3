"""PJ3 데이터·GTID를 건드리지 않고 PJ4만 새 스냅샷/파일 위치 채널로 복제한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

SCHEMAS=('paldo_ads','paldo_auth')
CHANNEL='pj4'


def sql(statement):
    """기존 관리자 소켓 인증으로 SQL 결과를 메모리에서만 반환한다."""
    p=subprocess.run(['mysql','--no-defaults','--protocol=socket','-uroot','--batch','--raw'],
        input=statement,text=True,capture_output=True,timeout=30)
    if p.returncode:
        error=re.search(r'ERROR (\d+)',p.stderr)
        raise RuntimeError('mysql_statement_failed_'+(error[1] if error else 'unknown')+'_'+statement.split()[0])
    lines=p.stdout.splitlines()
    return [dict(zip(lines[0].split('\t'),line.split('\t'))) for line in lines[1:]] if lines else []


def private_write(path,value):
    """기존 파일을 덮어쓰지 않고 관리자만 읽는 새 파일을 만든다."""
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(value)


def snapshot(directory):
    """PJ4 두 스키마만 일관 백업하고 스냅샷의 파일/위치·해시를 기록한다."""
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.stat().st_mode & 0o077:raise ValueError('private_directory_required')
    destination=directory/'pj4.sql'
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        p=subprocess.run(['mysqldump','--no-defaults','--protocol=socket','-uroot',
            '--single-transaction','--source-data=2','--set-gtid-purged=OFF','--skip-add-drop-table',
            '--routines','--events','--triggers','--hex-blob','--databases',*SCHEMAS],stdout=stream,stderr=subprocess.PIPE,timeout=180)
    if p.returncode:raise RuntimeError('snapshot_failed')
    raw=destination.read_bytes();text=raw.decode()
    match=re.search(r"CHANGE REPLICATION SOURCE TO SOURCE_LOG_FILE='([^']+)', SOURCE_LOG_POS=(\d+)",text)
    if not match or not re.fullmatch(r'[A-Za-z0-9_.-]+',match[1]):raise ValueError('snapshot_coordinates_missing')
    if 'GTID_PURGED=' in text or 'DROP TABLE' in text:raise ValueError('unsafe_snapshot')
    manifest={'schemas':list(SCHEMAS),'file':match[1],'position':int(match[2]),'sha256':hashlib.sha256(raw).hexdigest()}
    private_write(directory/'manifest.json',json.dumps(manifest)+'\n')
    return {'snapshot_schemas':len(SCHEMAS),'snapshot_bytes':len(raw),'snapshot_sha256':manifest['sha256']}


def quote(value):
    """SQL 안의 문자열 값만 인코딩한다. 셸 문자열을 만들지 않는다."""
    return "'"+str(value).replace('\\','\\\\').replace("'","''")+"'"


def checked_snapshot(directory):
    """보호 경로·스키마·파일 좌표·전체 덤프 해시를 다시 검증한다."""
    directory=Path(directory).resolve(strict=True)
    if directory.stat().st_mode & 0o077:raise ValueError('private_directory_required')
    for name in ('manifest.json','pj4.sql'):
        path=directory/name
        if path.is_symlink() or path.stat().st_mode & 0o077:raise ValueError('private_regular_file_required')
    manifest=json.loads((directory/'manifest.json').read_text())
    raw=(directory/'pj4.sql').read_bytes()
    if manifest.get('schemas')!=list(SCHEMAS) or hashlib.sha256(raw).hexdigest()!=manifest.get('sha256'):raise ValueError('snapshot_hash_mismatch')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',manifest.get('file','')) or type(manifest.get('position')) is not int or manifest['position']<4:raise ValueError('invalid_snapshot_coordinates')
    return directory,manifest,raw


def configure(directory):
    """빈 PJ4 스키마에만 복원하고 기존 채널 인증으로 새 필터 채널을 만든다."""
    directory,manifest,raw=checked_snapshot(directory)
    if sql("SELECT CHANNEL_NAME FROM performance_schema.replication_connection_configuration WHERE CHANNEL_NAME='pj4'"):
        raise ValueError('channel_already_exists')
    if int(sql("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema IN ('paldo_ads','paldo_auth')")[0]['n']):raise ValueError('target_not_empty')
    previous=sql("SELECT * FROM mysql.slave_master_info WHERE Channel_name=''")
    if len(previous)!=1:raise ValueError('existing_channel_required')
    previous=previous[0]
    if not (directory/'existing-channel-private.json').exists():
        private_write(directory/'existing-channel-private.json',json.dumps(previous)+'\n')
    before=sql('SELECT @@GLOBAL.gtid_executed AS gtid')[0]['gtid']
    if not (directory/'before-private.json').exists():
        private_write(directory/'before-private.json',json.dumps({'gtid':before,'schemas':sql('SHOW DATABASES')})+'\n')
    # No RESET, GTID injection, old database import, or source identity changes.
    restore='SET SESSION sql_log_bin=0;\n'+raw.decode()
    super_read_only=int(sql('SELECT @@GLOBAL.super_read_only AS n')[0]['n'])
    try:
        if super_read_only:sql('SET GLOBAL super_read_only=OFF')
        sql(restore)
    finally:
        if super_read_only:sql('SET GLOBAL super_read_only=ON')
    private_write(directory/'restore-complete.json',json.dumps({'sha256':manifest['sha256'],'schemas':list(SCHEMAS)})+'\n')
    return start_channel(directory)


def start_channel(directory):
    """검증된 PJ4 스냅샷 뒤 새 채널만 기동한다. 기존 데이터는 쓰지 않는다."""
    directory,manifest,_=checked_snapshot(directory)
    restored=json.loads((directory/'restore-complete.json').read_text())
    if restored!={'sha256':manifest['sha256'],'schemas':list(SCHEMAS)}:raise ValueError('verified_restore_required')
    if sql("SELECT CHANNEL_NAME FROM performance_schema.replication_connection_configuration WHERE CHANNEL_NAME='pj4'"):raise ValueError('channel_already_exists')
    previous=json.loads((directory/'existing-channel-private.json').read_text())
    statement=("CHANGE REPLICATION SOURCE TO SOURCE_HOST="+quote(previous['Host'])+
        ',SOURCE_PORT='+str(int(previous['Port']))+',SOURCE_USER='+quote(previous['User_name'])+
        f",SOURCE_PASSWORD={quote(previous['User_password'])}"+',SOURCE_AUTO_POSITION=0,SOURCE_LOG_FILE='+quote(manifest['file'])+
        ',SOURCE_LOG_POS='+str(int(manifest['position']))+',SOURCE_SSL=1,SOURCE_SSL_CA='+quote(previous['Ssl_ca'])+
        ",SOURCE_SSL_VERIFY_SERVER_CERT=1,SOURCE_CONNECT_RETRY=10,SOURCE_RETRY_COUNT=86400 FOR CHANNEL 'pj4'")
    sql(statement)
    sql("CHANGE REPLICATION FILTER REPLICATE_DO_DB=(paldo_ads,paldo_auth) FOR CHANNEL 'pj4'")
    config=Path('/etc/mysql/mysql.conf.d/pj4-channel.cnf')
    value='[mysqld]\nreplicate-do-db=pj4:paldo_ads\nreplicate-do-db=pj4:paldo_auth\n'
    if config.exists():
        if config.read_text()!=value:raise ValueError('existing_filter_config_conflict')
    else:private_write(config,value)
    config.chmod(0o644)
    sql("START REPLICA FOR CHANNEL 'pj4'")
    result={}
    for _ in range(20):
        row=sql("SHOW REPLICA STATUS FOR CHANNEL 'pj4'")[0]
        lag=row['Seconds_Behind_Source']
        result={'io_running':int(row['Replica_IO_Running']=='Yes'),'sql_running':int(row['Replica_SQL_Running']=='Yes'),
                'lag_seconds':None if lag=='NULL' else int(lag),'last_io_errno':int(row['Last_IO_Errno']),
                'last_sql_errno':int(row['Last_SQL_Errno']),'channel':CHANNEL}
        if result['io_running'] and result['sql_running'] and lag!='NULL':break
        time.sleep(1)
    if not result.get('io_running') or not result.get('sql_running'):raise RuntimeError('new_channel_not_ready')
    return result


def main(argv=None):
    """관리자 보호 디렉터리에서 명시 단계만 수행하고 비밀 없는 요약을 출력한다."""
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['snapshot','configure','start-channel'])
    parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args(argv)
    try:print(json.dumps({'snapshot':snapshot,'configure':configure,'start-channel':start_channel}[args.stage](args.directory)))
    except Exception as error:print(json.dumps({'error_type':type(error).__name__,'code':str(error) if isinstance(error,(ValueError,RuntimeError)) else 'operation_failed'}));raise SystemExit(1) from None


if __name__=='__main__':main()
