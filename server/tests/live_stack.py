"""C 실환경 검증 준비. 실제 조립은 유지하고 자동화 판만 load_test로 표시한다."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import uuid


def build_marked_service():
    """운영 엔진·Mongo·파서를 그대로 조립하고 검증 이벤트 표식만 추가한다."""
    from server.infra.game_factory import build_game_service
    service=build_game_service();service.load_test=True;return service


def prepare(profile_path,out):
    """전용 합성 참가자·서버 세션·고정 회차를 생성해 보호 파일로 저장한다."""
    config=json.loads(Path(profile_path).read_text());os.environ.update(config['environment']);sys.path.insert(0,config['source'])
    os.environ['DJANGO_SETTINGS_MODULE']='server.paldo_web.settings'
    import django;django.setup()
    from django.contrib.auth import BACKEND_SESSION_KEY,HASH_SESSION_KEY,SESSION_KEY
    from django.contrib.auth.models import User
    from django.contrib.sessions.backends.db import SessionStore
    from django.conf import settings
    from django.utils import timezone
    from pymongo import MongoClient
    from engine.domain import params
    from server.accounts.models import AccountProfile
    from server.app.test_sessions import TestSessionService
    from server.infra.test_sessions import MongoSessionStore
    out=Path(out)
    if out.exists():raise ValueError('verification_profile_exists')
    result={'origin':settings.PUBLIC_ORIGIN,'actors':{}}
    with MongoClient(os.environ['PALDO_MONGO_URI'],timeoutMS=15000) as client:
        sessions=TestSessionService(MongoSessionStore(client.get_default_database()))
        for name in ('broker','publisher','browser','benchmark'):
            username='c07_'+uuid.uuid4().hex[:12];password='Test-'+secrets.token_urlsafe(20)+'9a!'
            user=User.objects.create_user(username=username,password=password)
            profile=AccountProfile.objects.create(user=user,consented_at=timezone.now())
            session=SessionStore();session[SESSION_KEY]=str(user.pk);session[BACKEND_SESSION_KEY]=settings.AUTHENTICATION_BACKENDS[0];session[HASH_SESSION_KEY]=user.get_session_auth_hash();session.create()
            sid='c07_'+name+'_'+uuid.uuid4().hex[:8]
            sessions.create(sid,[{'preset_id':'base6','seed':70301},{'preset_id':'base6','seed':70302}],
                [str(profile.player_pseudonym)],{'rule_version':params.RULE_PARAMS_VERSION,'engine_version':'paldo-engine','content_version':'content-1.0'})
            result['actors'][name]={'username':username,'password':password,'cookie':[settings.SESSION_COOKIE_NAME,session.session_key],'session_id':sid}
    descriptor=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:json.dump(result,stream)
    print(json.dumps({'verification_actors':len(result['actors']),'cookies_and_credentials':'protected_file_only'}))


def main(argv=None):
    """관리자 보호 프로필을 받아 전용 검증 참가자만 준비한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True);parser.add_argument('--out')
    parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=18000)
    args=parser.parse_args(argv)
    if args.serve:
        config=json.loads(Path(args.profile).read_text());os.environ.update(config['environment']);os.environ['PYTHONPATH']=config['source'];os.chdir(config['source'])
        os.execv(sys.executable,[sys.executable,'-m','daphne','-b','127.0.0.1','-p',str(args.port),'server.paldo_web.asgi:application'])
    if not args.out:parser.error('--out required for prepare')
    prepare(args.profile,args.out)


if __name__=='__main__':main()
