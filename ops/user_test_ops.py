"""E 확정 태그의 배포 묶음과 4th_private 전용 Mongo 조회 계정을 준비한다."""
import argparse,hashlib,json,os
from pathlib import Path
import re,subprocess,zipfile


def private_output(path):
    """확인된 기존 4th_private 아래의 새 보호 파일만 허용한다."""
    path=Path(path).resolve()
    if not any(p.name=='4th_private' for p in path.parents) or not path.parent.is_dir() or path.exists():raise ValueError('existing_4th_private_new_file_required')
    if os.name!='nt' and path.parent.stat().st_mode & 0o077:raise ValueError('private_directory_required')
    return path


def release_archive(repo,tag,out):
    """dev/main 대신 이미 존재하는 E 태그의 커밋만 고정해 zip을 만든다."""
    if not re.fullmatch(r'v-usertest-[12]',tag):raise ValueError('confirmed_usertest_tag_required')
    repo=Path(repo);out=Path(out)
    if out.exists():raise ValueError('new_archive_required')
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','--verify','refs/tags/'+tag+'^{commit}'],text=True).strip()
    if not re.fullmatch('[0-9a-f]{40}',sha):raise ValueError('invalid_release_commit')
    subprocess.run(['git','-C',str(repo),'archive','--format=zip','--output',str(out),sha],check=True)
    with zipfile.ZipFile(out,'a',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('.paldo-release.json',json.dumps({'tag':tag,'commit':sha,'frozen':True})+'\n')
    return {'tag':tag,'commit':sha,'archive_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}


def readonly_account(admin_profile,ca,seed,out):
    """기존 정상 관리자 인증과 TLS를 사용하고 read 역할·실제 쓰기 거절을 검증한다."""
    out=private_output(out);admin_profile=Path(admin_profile)
    if not re.fullmatch('[a-zA-Z0-9._-]+',seed) or admin_profile.is_symlink() or admin_profile.stat().st_mode & 0o077:raise ValueError('protected_admin_profile_required')
    config={'admin':str(admin_profile),'out':str(out),'seed':seed,'ca':str(ca)}
    script=r'''
const fs=require('fs'),crypto=require('crypto'),q=__CONFIG__;
const admin=JSON.parse(fs.readFileSync(q.admin,'utf8'));
if(!db.getSiblingDB('admin').auth(admin.username,admin.password))throw new Error('admin_auth_failed');
if(!db.hello().isWritablePrimary)throw new Error('primary_required');
const target=db.getSiblingDB('paldo_web'),user='paldo-reader-'+crypto.randomBytes(6).toString('hex');
if(target.getUser(user))throw new Error('unmanaged_existing_reader');
const profile={username:user,password:crypto.randomBytes(32).toString('base64url'),database:'paldo_web',auth_source:'paldo_web',replica_set:'rs-products',seed:q.seed,tls_ca:q.ca,ready:false};
fs.writeFileSync(q.out,JSON.stringify(profile)+'\n',{mode:0o600,flag:'wx'});
target.createUser({user:profile.username,pwd:profile.password,roles:[{role:'read',db:'paldo_web'}],mechanisms:['SCRAM-SHA-256']},{w:'majority'});
const uri='mongodb://'+encodeURIComponent(profile.username)+':'+encodeURIComponent(profile.password)+'@'+q.seed+':27017/paldo_web?authSource=paldo_web&replicaSet=rs-products&tls=true&tlsCAFile='+encodeURIComponent(q.ca);
const reader=new Mongo(uri).getDB('paldo_web');reader.content.countDocuments({});
const identity='c08-permission-'+crypto.randomUUID();
const result=reader.runCommand({insert:'_c08_permission_probe',documents:[{_id:identity}],writeConcern:{w:'majority'}});
if(result.ok || result.code!==13){target.getCollection('_c08_permission_probe').deleteOne({_id:identity});throw new Error('write_permission_not_denied');}
profile.ready=true;profile.write_denied_code=13;fs.writeFileSync(q.out,JSON.stringify(profile)+'\n',{mode:0o600});
print(JSON.stringify({readonly_account_created:true,read_verified:true,write_denied_code:13,credentials_in_4th_private_only:true}));
'''.replace('__CONFIG__',json.dumps(config))
    p=subprocess.run(['mongosh','--quiet','--tls','--tlsCAFile',str(ca),'--host',seed,'--port','27017'],input=script,text=True,capture_output=True)
    if p.returncode:raise RuntimeError('readonly_provision_failed_see_protected_server_audit')
    # Never propagate mongosh diagnostics, which may include a connection URI.
    if not out.exists() or not json.loads(out.read_text()).get('ready'):raise RuntimeError('readonly_verification_incomplete')
    return {'readonly_account_created':True,'read_verified':True,'write_denied_code':13}


def main(argv=None):
    """계정 원문을 출력하지 않고 작업 종류별 비밀 없는 결과만 반환한다."""
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='mode',required=True)
    release=sub.add_parser('release');release.add_argument('--repo',type=Path,required=True);release.add_argument('--tag',required=True);release.add_argument('--out',type=Path,required=True)
    reader=sub.add_parser('reader')
    for name in ('admin-profile','ca','out'):reader.add_argument('--'+name,type=Path,required=True)
    reader.add_argument('--seed',required=True);args=parser.parse_args(argv)
    result=release_archive(args.repo,args.tag,args.out) if args.mode=='release' else readonly_account(args.admin_profile,args.ca,args.seed,args.out)
    print(json.dumps(result))


if __name__=='__main__':main()
