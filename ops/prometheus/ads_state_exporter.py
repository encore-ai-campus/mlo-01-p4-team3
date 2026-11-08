"""자기 광고 DB의 예산 순액만 SELECT하는 localhost 지표 수집기."""
import argparse,json,os,sys,time
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo


def collect():
    """원장 ID·본문·계정 없이 캠페인 예산과 균등 목표 편차만 반환한다."""
    from django.db import connection,transaction
    from ads.web.models import AdDailyBudget,Campaign
    now=datetime.now(ZoneInfo('Asia/Seoul'));fraction=(now.hour*3600+now.minute*60+now.second)/86400
    result=[]
    with connection.cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')
    with transaction.atomic():
        budgets=dict(Campaign.objects.values_list('campaign_id','daily_budget_mw'))
        for campaign,committed in AdDailyBudget.objects.filter(attribution_day=now.date()).values_list('campaign_id','committed_mw'):
            budget=budgets.get(campaign)
            if budget is None:continue
            scope='synthetic' if campaign.startswith('syn:') else 'production'
            labels='campaign='+json.dumps(campaign)+',source='+json.dumps(scope)
            result.extend([f'paldo_ads_committed_mw{{{labels}}} {committed}',f'paldo_ads_daily_budget_mw{{{labels}}} {budget}'])
            if budget:result.append(f'paldo_ads_pacing_deviation_ratio{{{labels},target_curve="uniform"}} {committed/budget-fraction}')
    return '\n'.join(result)+'\n'


def main(argv=None):
    """운영 앱과 같은 보호 설정을 자기 서비스 사용자로만 읽는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True);args=parser.parse_args(argv)
    config=json.loads(args.profile.read_text());sys.path.insert(0,config['source']);os.environ.update(config['environment'])
    os.environ['PALDO_LOG_DIR']='/tmp/paldo-metrics-logs'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','ads.project.settings')
    import django
    django.setup()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """수집 실패를 0원이나 정상 상태로 오인하지 않게 표시한다."""
            if self.path!='/metrics':self.send_error(404);return
            try:body=collect()+'paldo_ads_state_collection_success 1\n'
            except Exception:body='paldo_ads_state_collection_success 0\n'
            finally:
                from django.db import connections
                connections.close_all()
            self.send_response(200);self.end_headers();self.wfile.write(body.encode())
        def log_message(self,*args):
            """인증정보가 없는 수집 요청의 반복 로그를 생략한다."""
    ThreadingHTTPServer(('127.0.0.1',9114),Handler).serve_forever()


if __name__=='__main__':main()

