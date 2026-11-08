"""기존 관리자 보호 파일로 PJ4만 복사·분리한다. 기본은 읽기 전용 계획이다."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

COLLECTIONS=['games','progress','content','b_test_sessions','b_ad_identities','b_interactions']
SCRIPT=r'''
const fs=require('fs'),crypto=require('crypto');
const q=__CONFIG__;
const credentials=JSON.parse(fs.readFileSync(q.admin_profile,'utf8'));
if(!db.getSiblingDB('admin').auth(credentials.username,credentials.password))throw new Error('admin_auth_failed');
if(!db.hello().isWritablePrimary)throw new Error('primary_required');
const source=db.getSiblingDB(q.source),target=db.getSiblingDB(q.target);
const users=target.runCommand({usersInfo:1});
if(!users.ok)throw new Error('users_query_failed');
if(target.getCollectionNames().length || users.users.length)throw new Error('target_not_empty');
const canonical=v=>Array.isArray(v)?v.map(canonical):(v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v);
const hash=rows=>crypto.createHash('sha256').update(JSON.stringify(canonical(JSON.parse(EJSON.stringify(rows,{relaxed:false}))))).digest('hex');
const snapshots=q.collections.map(name=>{
 const info=source.getCollectionInfos({name})[0];
 const rows=info?source.getCollection(name).find().sort({_id:1}).toArray():[];
 return {name,rows,options:info?info.options:{},indexes:info?source.getCollection(name).getIndexes():[],sha256:hash(rows)};
});
if(source.games.countDocuments({outbox:{$elemMatch:{published:false}}}))throw new Error('unpublished_games_require_drain');
if(!q.apply){print(JSON.stringify({plan_only:true,collections:snapshots.map(s=>({collection:s.name,documents:s.rows.length})),target_empty:true}));}
else {
 for(const s of snapshots){fs.writeFileSync(q.backup_dir+'/'+s.name+'.ejson',EJSON.stringify(s.rows,{relaxed:false})+'\n',{mode:0o600,flag:'wx'});}
 const manifest={source:q.source,target:q.target,collections:snapshots.map(s=>({collection:s.name,documents:s.rows.length,sha256:s.sha256,options:s.options,indexes:s.indexes}))};
 fs.writeFileSync(q.backup_dir+'/manifest.json',JSON.stringify(manifest)+'\n',{mode:0o600,flag:'wx'});
 const profile={username:q.app_user,password:crypto.randomBytes(32).toString('base64url'),auth_source:q.target,database:q.target,replica_set:q.replica_set,seed:q.seed,ready:false};
 fs.writeFileSync(q.profile_out,JSON.stringify(profile)+'\n',{mode:0o600,flag:'wx'});
 for(const s of snapshots){
  target.createCollection(s.name,{...s.options,writeConcern:{w:'majority'}});
  for(let start=0;start<s.rows.length;start+=100)target.getCollection(s.name).insertMany(s.rows.slice(start,start+100),{writeConcern:{w:'majority'}});
  for(const idx of s.indexes){if(idx.name==='_id_')continue;const opts={};for(const k of ['name','unique','sparse','expireAfterSeconds','partialFilterExpression','collation','hidden'])if(k in idx)opts[k]=idx[k];target.getCollection(s.name).createIndex(idx.key,opts);}
  const copied=target.getCollection(s.name).find().sort({_id:1}).toArray();
  if(hash(copied)!==s.sha256 || copied.length!==s.rows.length)throw new Error('copy_hash_mismatch');
 }
 target.createUser({user:profile.username,pwd:profile.password,roles:[{role:'readWrite',db:q.target}],mechanisms:['SCRAM-SHA-256']},{w:'majority'});
 profile.ready=true;fs.writeFileSync(q.profile_out,JSON.stringify(profile)+'\n',{mode:0o600});
 print(JSON.stringify({copied_collections:snapshots.length,copied_documents:snapshots.reduce((n,s)=>n+s.rows.length,0),hash_mismatches:0,source_preserved:true,dedicated_readwrite_role:true}));
}
'''


def validate(args):
    """명시 스키마·안전한 호스트·관리자 파일 권한을 mutation 전에 확인한다."""
    for value in (args.source,args.target,args.app_user,args.replica_set):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}',value):raise ValueError('invalid_identifier')
    if args.source==args.target or args.source in ('admin','local','config') or args.target in ('admin','local','config'):raise ValueError('dedicated_target_required')
    if not re.fullmatch(r'[A-Za-z0-9._-]+',args.seed):raise ValueError('single_seed_host_required')
    if args.admin_profile.is_symlink() or args.admin_profile.stat().st_mode & 0o077:raise ValueError('private_admin_file_required')
    if args.apply:
        if args.profile_out.exists():raise ValueError('profile_already_exists')
        if args.backup_dir.exists():raise ValueError('fresh_backup_directory_required')
        args.backup_dir.mkdir(mode=0o700,parents=True)
        args.profile_out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if args.profile_out.parent.stat().st_mode & 0o077:raise ValueError('private_profile_directory_required')


def main(argv=None):
    """TLS와 기존 인증을 유지하고 비밀 없는 요약만 출력한다. 기존 DB는 지우지 않는다."""
    parser=argparse.ArgumentParser()
    for name in ('admin-profile','ca','profile-out','backup-dir'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('seed','replica-set','source','target','app-user'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    validate(args)
    config={k:(str(v.resolve()) if isinstance(v,Path) else v) for k,v in vars(args).items()}
    config['collections']=COLLECTIONS
    script=SCRIPT.replace('__CONFIG__',json.dumps(config))
    result=subprocess.run(['mongosh','--norc','--quiet','--host',args.replica_set+'/'+args.seed+':27017',
        '--tls','--tlsCAFile',str(args.ca),'--eval',script],text=True,capture_output=True,timeout=180)
    if result.returncode:print(json.dumps({'error':'mongo_isolation_failed','exit':result.returncode}));return 1
    for line in result.stdout.splitlines():
        if line.startswith('{'):print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
