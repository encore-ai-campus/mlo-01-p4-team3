"""Primary의 기존 데이터는 유지하고 PJ4 두 스키마·최소 범위 계정만 만든다."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess


def mysql(sql):
    """로컬 관리자 소켓으로 SQL을 전달한다. 비밀번호는 argv·로그에 쓰지 않는다."""
    result=subprocess.run(['mysql','--no-defaults','--protocol=socket','-uroot','--batch','--skip-column-names'],
        input=sql,text=True,capture_output=True,timeout=30)
    if result.returncode:raise RuntimeError('mysql_operation_failed')
    return result.stdout.strip()


def protected_write(path,value):
    """새 보호 파일만 만든다. 기존 비밀 파일을 덮어쓰지 않는다."""
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:stream.write(value)


def provision(directory,ads_host,auth_host,db_host,ca):
    """두 계정의 스키마 권한을 분리하고 재실행 시 저장한 인증을 유지한다."""
    for value in (ads_host,auth_host,db_host):
        if not re.fullmatch(r'[a-zA-Z0-9.:-]+',value):raise ValueError('literal_host_required')
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.stat().st_mode & 0o077:raise ValueError('private_directory_required')
    plans=[('ads','paldo_ads','paldo_ads_app',ads_host),('auth','paldo_auth','paldo_auth_app',auth_host)]
    for role,schema,user,host in plans:
        profile_path=directory/(role+'.json')
        if profile_path.exists():
            profile=json.loads(profile_path.read_text())
            if profile['user']!=user or profile['name']!=schema or profile['allowed_host']!=host:raise ValueError('profile_mismatch')
        else:
            existing=mysql(f"SELECT COUNT(*) FROM mysql.user WHERE User='{user}';")
            if existing!='0':raise ValueError('unmanaged_account_exists')
            profile={'host':db_host,'port':3306,'name':schema,'user':user,'password':secrets.token_urlsafe(36),
                     'allowed_host':host,'ca':str(ca),'ssl_mode':'VERIFY_IDENTITY'}
            protected_write(profile_path,json.dumps(profile)+'\n')
        password=profile['password']
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,}',password):raise ValueError('managed_password_format')
        mysql(f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;")
        for origin in dict.fromkeys((host,db_host)):
            mysql(f"CREATE USER IF NOT EXISTS '{user}'@'{origin}' IDENTIFIED BY '{password}' REQUIRE SSL;"
                  f"GRANT ALL PRIVILEGES ON `{schema}`.* TO '{user}'@'{origin}';")
        # Existing MySQL listens on its LAN interface. Verify that exact TLS identity.
        option=directory/(role+'-local.cnf')
        value=f'[client]\nuser={user}\npassword={password}\nhost={db_host}\nprotocol=TCP\nssl-mode=VERIFY_IDENTITY\nssl-ca={ca}\n'
        if option.exists():
            if option.is_symlink() or option.stat().st_mode & 0o077:raise ValueError('private_profile_required')
            if option.read_text()!=value:option.write_text(value)
        else:protected_write(option,value)
    return {'schemas_ready':2,'service_roles_ready':2,'profile_storage':'protected_files_only'}


def main(argv=None):
    """명시 --apply와 외부 호스트·보호 경로를 받아 실행한다."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--secrets-dir',type=Path,required=True)
    parser.add_argument('--ads-host',required=True);parser.add_argument('--auth-host',required=True)
    parser.add_argument('--db-host',required=True);parser.add_argument('--ca',type=Path,required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    if not args.apply:print(json.dumps({'plan':'two isolated schemas, TLS service accounts, local verification accounts'}));return
    try:print(json.dumps(provision(args.secrets_dir,args.ads_host,args.auth_host,args.db_host,args.ca)))
    except Exception as error:print(json.dumps({'error_type':type(error).__name__}));raise SystemExit(1) from None


if __name__=='__main__':main()
