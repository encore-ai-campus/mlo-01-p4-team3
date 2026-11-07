"""기존 복제 설정을 변경하지 않고 새 PJ4 probe와 IO/SQL·지연 상태를 대조한다."""
import argparse
import json
import re
import subprocess


def query(sql):
    """로컬 관리자 소켓의 결과를 메모리에서만 읽는다."""
    p=subprocess.run(['mysql','--no-defaults','--protocol=socket','-uroot','--batch'],input=sql,text=True,capture_output=True,timeout=15)
    if p.returncode:raise RuntimeError('query_failed')
    lines=p.stdout.splitlines()
    return [dict(zip(lines[0].split('\t'),line.split('\t'))) for line in lines[1:]] if lines else []


def check(role,marker):
    """Primary는 새 probe만 쓰고 Replica는 상태·일치 여부만 조회한다."""
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',marker):raise ValueError('invalid_marker')
    if role=='primary':
        query('CREATE TABLE IF NOT EXISTS paldo_ads.c06_replication_probe (marker VARCHAR(64) PRIMARY KEY, created_at TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6));')
        query(f"INSERT IGNORE INTO paldo_ads.c06_replication_probe(marker) VALUES ('{marker}');")
        return {'probe_written':1,'marker':marker}
    status=query('SHOW REPLICA STATUS;')
    if not status:raise RuntimeError('replica_not_configured')
    data=status[0]
    present=query("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema='paldo_ads' AND table_name='c06_replication_probe';")[0]['n']=='1'
    replicated=present and query(f"SELECT COUNT(*) AS n FROM paldo_ads.c06_replication_probe WHERE marker='{marker}';")[0]['n']=='1'
    lag=data['Seconds_Behind_Source']
    return {'io_running':int(data['Replica_IO_Running']=='Yes'),'sql_running':int(data['Replica_SQL_Running']=='Yes'),
            'lag_seconds':None if lag=='NULL' else int(lag),'last_io_errno':int(data['Last_IO_Errno']),
            'probe_replicated':int(replicated),'marker':marker}


def main(argv=None):
    """상태만 출력한다. GTID·주소·계정·원문 오류는 외부 출력하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=['primary','replica'],required=True)
    parser.add_argument('--marker',required=True);args=parser.parse_args(argv)
    try:
        result=check(args.role,args.marker);print(json.dumps(result))
        if args.role=='replica' and not all(result[k] for k in ('io_running','sql_running','probe_replicated')):raise SystemExit(1)
    except Exception as error:print(json.dumps({'error_type':type(error).__name__}));raise SystemExit(1) from None


if __name__=='__main__':main()
