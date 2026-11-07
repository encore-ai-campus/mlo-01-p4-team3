"""보호된 로컬 테스트 인증으로 스키마 분리와 TLS를 확인한다."""
import argparse
import json
from pathlib import Path
import subprocess


def verify(directory):
    """자기 스키마 접속 성공과 상대 스키마 접속 거부를 확인한다."""
    results={}
    for role,own,other in [('ads','paldo_ads','paldo_auth'),('auth','paldo_auth','paldo_ads')]:
        profile=Path(directory)/(role+'-local.cnf')
        if profile.stat().st_mode & 0o077:raise ValueError('private_profile_required')
        command=['mysql','--defaults-extra-file='+str(profile),'--batch','--skip-column-names']
        own_result=subprocess.run(command+[own],input="SELECT DATABASE(); SHOW SESSION STATUS LIKE 'Ssl_cipher';",text=True,capture_output=True,timeout=15)
        denied=subprocess.run(command+[other],input='SELECT 1;',text=True,capture_output=True,timeout=15)
        if own_result.returncode or own not in own_result.stdout or not any(line.startswith('Ssl_cipher\t') and line.strip()!='Ssl_cipher' for line in own_result.stdout.splitlines()):raise RuntimeError('own_schema_tls_failed')
        if denied.returncode==0 or 'ERROR 1044' not in denied.stderr:raise RuntimeError('cross_schema_not_denied')
        results[role]={'own_schema_tls':'ok','cross_schema':'denied','error_code':1044}
    return results


def main(argv=None):
    """검증 결과만 출력하며 사용자명·주소·암호·접속 문자열은 출력하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--secrets-dir',type=Path,required=True);args=parser.parse_args(argv)
    try:print(json.dumps(verify(args.secrets_dir)))
    except Exception as error:print(json.dumps({'error_type':type(error).__name__}));raise SystemExit(1) from None


if __name__=='__main__':main()
