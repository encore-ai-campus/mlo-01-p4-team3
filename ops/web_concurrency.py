"""전용 loopback 검증 서버에서 실제 인증·Mongo 판을 동시에 실행한다."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json,os,random,secrets,ssl,sys,threading,time,uuid
from pathlib import Path
import urllib.request


def main(argv=None):
    """합성 계정만 만들고 전 판이 시작된 구간을 유지해 모니터링을 촬영하게 한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--ca',required=True);parser.add_argument('--concurrency',type=int,default=30);parser.add_argument('--hold-seconds',type=int,default=120)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args(argv)
    config=json.loads(args.profile.read_text());origin=config['environment']['PALDO_PUBLIC_ORIGIN']
    if not args.execute or origin!='https://localhost:18443' or not 1<=args.concurrency<=30 or not 1<=args.hold_seconds<=300:raise ValueError('bounded_loopback_validation_only')
    os.environ.update(config['environment']);sys.path.insert(0,config['source']);os.environ['DJANGO_SETTINGS_MODULE']='server.paldo_web.settings'
    import django
    django.setup()
    from django.contrib.auth import BACKEND_SESSION_KEY,HASH_SESSION_KEY,SESSION_KEY
    from django.contrib.auth.models import User
    from django.contrib.sessions.backends.db import SessionStore
    from django.conf import settings
    from django.utils import timezone
    from pymongo import MongoClient
    from engine.domain import params
    from engine.domain.model import ENGINE_VERSION
    from server.accounts.models import AccountProfile
    from server.app.test_sessions import TestSessionService
    from server.infra.test_sessions import MongoSessionStore
    from tools.perf.int07_web_run import command_body
    actors=[]
    with MongoClient(os.environ['PALDO_MONGO_URI'],timeoutMS=15000) as client:
        sessions=TestSessionService(MongoSessionStore(client.get_default_database()))
        for index in range(args.concurrency):
            name='c08_load_'+uuid.uuid4().hex[:16];user=User.objects.create_user(username=name,password=secrets.token_urlsafe(24))
            account=AccountProfile.objects.create(user=user,consented_at=timezone.now())
            session=SessionStore();session[SESSION_KEY]=str(user.pk);session[BACKEND_SESSION_KEY]=settings.AUTHENTICATION_BACKENDS[0];session[HASH_SESSION_KEY]=user.get_session_auth_hash();session.create()
            sid=name+'_session';sessions.create(sid,[{'preset_id':'base6','seed':80800+index}],[str(account.player_pseudonym)],
                {'rule_version':params.RULE_PARAMS_VERSION,'engine_version':ENGINE_VERSION,'content_version':'content-1.0'})
            csrf=secrets.token_hex(16)
            actors.append({'cookie':settings.SESSION_COOKIE_NAME+'='+session.session_key+'; csrftoken='+csrf,'csrf':csrf,'session_id':sid})
    barrier=threading.Barrier(args.concurrency,timeout=90);latencies=[];lock=threading.Lock();active=0;peak=0
    slots=threading.BoundedSemaphore(12)
    def play(pair):
        """각 스레드의 인증 상태는 해당 웹 호스트 프로세스 메모리에만 둔다."""
        nonlocal active,peak
        index,actor=pair;context=ssl.create_default_context(cafile=args.ca);rng=random.Random(80800+index);calls=0
        def request(path,body):
            """실제 HTTPS 지연을 재고 실패 응답은 판 완료로 세지 않는다."""
            nonlocal calls
            started=time.perf_counter();request=urllib.request.Request(origin+path,data=json.dumps(body).encode(),headers={'Cookie':actor['cookie'],'X-CSRFToken':actor['csrf'],'Content-Type':'application/json','Origin':origin,'Referer':origin+'/'})
            with slots, urllib.request.urlopen(request,context=context,timeout=60) as response:
                if response.status!=200:raise RuntimeError('unexpected_http_status')
                result=json.load(response)
            with lock:latencies.append((time.perf_counter()-started)*1000)
            calls+=1;return result['view']
        view=request('/games',{'preset_id':'base6','session_id':actor['session_id'],'mudang_variant':'f'})
        with lock:active+=1;peak=max(peak,active)
        barrier.wait()
        if index==0:
            args.out.parent.mkdir(parents=True,exist_ok=True);args.out.with_name('load-active.json').write_text(json.dumps({'active_games':active,'hold_seconds':args.hold_seconds,'load_test':True,'at':time.time()}))
        time.sleep(args.hold_seconds)
        for _ in range(400):
            if view.get('result') is not None:break
            view=request('/games/'+view['game_id']+'/commands',command_body(view,rng))
        else:raise RuntimeError('game_step_limit')
        if not view['result']['completed']:raise RuntimeError('incomplete_game')
        with lock:active-=1
        return {'game_id':view['game_id'],'completed':True,'api_requests':calls}
    started=time.perf_counter()
    failures=[]
    def guarded(pair):
        """장벽 실패 뒤에 실제 HTTP 오류가 가려지지 않게 코드만 기록한다."""
        try:return play(pair)
        except Exception as error:
            failures.append({'error':type(error).__name__,'status':getattr(error,'code',None)})
            barrier.abort();return None
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:results=list(pool.map(guarded,enumerate(actors)))
    if failures:
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps({'concurrent_games':peak,'failures':failures}))
        raise RuntimeError('concurrency_validation_failed')
    ordered=sorted(latencies);result={'concurrent_games':peak,'completed_games':len(results),'api_requests':len(ordered),'api_errors':0,
        'p50_ms':ordered[int((len(ordered)-1)*.5)],'p95_ms':ordered[int((len(ordered)-1)*.95)],'duration_seconds':time.perf_counter()-started,'load_test':True,'max_inflight_http':12,'games':results}
    args.out.write_text(json.dumps(result));args.out.chmod(0o600)
    print(json.dumps({k:v for k,v in result.items() if k!='games'}))


if __name__=='__main__':main()
