"""보호 프로필로 PJ4 웹·광고·작업자를 실행한다. 인증값은 인자·로그에 쓰지 않는다."""
import argparse
import json
import os
from pathlib import Path
import sys

# 게임 웹 daphne 포트. 8001은 광고 서버다. Caddy(ops/proxy/Caddyfile) upstream과 같아야 한다.
WEB_PORTS=(8000,8002)


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
    ads=['-m','daphne','-b',config.get('bind','127.0.0.1'),'-p','8001','ads.project.asgi:application']
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
