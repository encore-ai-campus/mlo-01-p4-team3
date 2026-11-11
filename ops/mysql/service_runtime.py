"""보호 프로필로 PJ4 웹·광고·작업자를 실행한다. 인증값은 인자·로그에 쓰지 않는다."""
import argparse
import json
import os
from pathlib import Path
import sys

# 게임 웹 daphne 포트. 8001은 광고 서버다. Caddy(ops/proxy/Caddyfile) upstream과 같아야 한다.
WEB_PORTS=(8000,8002)
# 광고 서버(11/11): daphne(ASGI)는 요청마다 작업 스레드를 새로 만들어 DB 연결을 재사용하지 못한다(CONN_MAX_AGE 무효).
# gunicorn gthread(고정 스레드)로 띄워 스레드별 영속 연결을 쓴다. 프로세스 1개: Prometheus 지표 레지스트리와 AdService 상태가
# 프로세스 안에만 있어(ads/web/telemetry.py) 여러 프로세스면 /metrics가 한 프로세스 값만 보인다. 스레드 16: 게임 웹
# 2프로세스 × 동시 serve 8(server/app/ads_port.py MAX_CONCURRENT)과 중계 호출을 받을 여유. 되돌리기: 프로필 ads_server="daphne"
# 또는 환경 변수 PALDO_ADS_SERVER=daphne.
ADS_SERVERS=('gunicorn','daphne')
ADS_THREADS=16  # 기본값. 프로필 ads_threads(1~64)로 바꿀 수 있다(11/11 지연 비교용).


def ads_command(config):
    """광고 서버 실행 인자(gunicorn 기본, daphne로 되돌릴 수 있음)."""
    server=os.environ.get('PALDO_ADS_SERVER') or config.get('ads_server','gunicorn')
    if server not in ADS_SERVERS:
        raise SystemExit('ads_server는 '+'·'.join(ADS_SERVERS)+' 중 하나다')
    bind=config.get('bind','127.0.0.1')
    if server=='daphne':
        return ['-m','daphne','-b',bind,'-p','8001','ads.project.asgi:application']
    threads=config.get('ads_threads',ADS_THREADS)
    if type(threads) is not int or not 1<=threads<=64:
        raise SystemExit('ads_threads는 1~64 정수다')
    return ['-m','gunicorn','--worker-class','gthread','--workers','1','--threads',str(threads),
            '--bind',f'{bind}:8001','--timeout','30','--graceful-timeout','20','--keep-alive','5',
            '--error-logfile','-','ads.project.wsgi:application']


def main(argv=None):
    """환경만 주입한 뒤 같은 가상환경의 단일 서비스 프로세스로 교체한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('role',choices=['web','ads','migrate-web','migrate-ads','load-content','publish','expire','refund','outbox','collectstatic'])
    parser.add_argument('--port',type=int,default=WEB_PORTS[0],help='web 역할의 loopback 포트(E-09-10: 8000, 두 번째 프로세스 8002)')
    args=parser.parse_args(argv)
    if args.role=='web' and args.port not in WEB_PORTS:
        parser.error('web --port는 '+'·'.join(map(str,WEB_PORTS))+' 중 하나다(Caddy upstream과 같아야 한다)')
    config=json.loads(args.profile.read_text());source=config['source']
    os.environ.update(config['environment']);os.environ['PYTHONPATH']=source
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.chdir(source)
    if args.role=='web':os.environ['PALDO_WEB_INSTANCE']=str(args.port)  # 프로세스별 로그 파일(settings.WEB_LOG_FILE)
    web=['-m','daphne','-b','127.0.0.1','-p',str(args.port),'server.paldo_web.asgi:application']
    ads=ads_command(config) if args.role=='ads' else None
    commands={'web':web,'ads':ads,'migrate-web':['server/manage.py','migrate','--noinput'],
        'migrate-ads':['ads/manage.py','migrate','--noinput'],
        'load-content':['-m','tools.load_content','content/bundle_v1.json'],
        'outbox':['-m','server.infra.outbox_publisher','--kafka-config',config.get('kafka_config','')],
        'collectstatic':['server/manage.py','collectstatic','--noinput']}
    for role,port in [('publish','9111'),('expire','9112'),('refund','9113')]:
        commands[role]=['ads/manage.py','ad_worker',role,'--metrics-port',port]
        if role!='expire':commands[role]+= ['--kafka-config',config.get('kafka_config','')]
    os.execv(sys.executable,[sys.executable,*commands[args.role]])


if __name__=='__main__':main()
