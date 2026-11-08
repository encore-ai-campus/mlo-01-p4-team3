"""보호 프로필로 PJ4 웹·광고·작업자를 실행한다. 인증값은 인자·로그에 쓰지 않는다."""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    """환경만 주입한 뒤 같은 가상환경의 단일 서비스 프로세스로 교체한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('role',choices=['web','ads','migrate-web','migrate-ads','load-content','publish','expire','refund','outbox','collectstatic'])
    args=parser.parse_args(argv)
    config=json.loads(args.profile.read_text());source=config['source']
    os.environ.update(config['environment']);os.environ['PYTHONPATH']=source
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.chdir(source)
    web=['-m','daphne','-b','127.0.0.1','-p','8000','server.paldo_web.asgi:application']
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
