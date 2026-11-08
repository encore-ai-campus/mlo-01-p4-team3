"""기존 내부 광고 HTTP·MySQL·Kafka를 합성 실행 ID로만 검증한다. 토큰은 출력하지 않는다."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def prepare(profile):
    """해당 광고 VM의 보호 런타임으로 동일 Django ORM을 준비한다."""
    config=json.loads(Path(profile).read_text());os.environ.update(config['environment'])
    os.environ['DJANGO_SETTINGS_MODULE']='ads.project.settings';sys.path.insert(0,config['source'])
    import django
    django.setup();return config


def export_run(run,out):
    """해당 실행의 이벤트와 원장만 내보낸다. 암호·서명 토큰·계정 정보는 포함하지 않는다."""
    from ads.web.models import AdOutbox,AdStoredRequest,AdLedgerEntry,Campaign
    requests=list(AdStoredRequest.objects.filter(pk__startswith=run))
    serves=[r.serve_id for r in requests if r.serve_id]
    identities=[sid+suffix for sid in serves for suffix in (':click',':impression',':click:refund',':impression:refund')]
    rows=AdOutbox.objects.filter(payload__request_id__startswith=run).exclude(topic='').order_by('id')
    for _ in range(40):
        if not rows.filter(published_at__isnull=True).exists():break
        time.sleep(.5)
    if rows.filter(published_at__isnull=True).exists():raise RuntimeError('ad_outbox_not_drained')
    events=[{'topic':r.topic,'event':r.payload} for r in rows]
    ledger=[{'charge_id':r.charge_id,'amount_mw':r.amount_mw,'state':r.state,'kind':r.kind,'source_event_id':r.source_event_id,
             'source':r.source,'campaign_id':r.budget.campaign_id,'attribution_day':r.budget.attribution_day.isoformat(),'slot':r.slot}
            for r in AdLedgerEntry.objects.select_related('budget').filter(pk__in=identities).order_by('pk')]
    root=Path(out);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,data in [('source-events.json',events),('ledger.json',ledger),('budgets.json',dict(Campaign.objects.filter(campaign_id__in={e['event']['campaign_id'] for e in events if e['event'].get('campaign_id')}).values_list('pk','daily_budget_mw')))]:
        p=root/name;p.write_text(json.dumps(data)+'\n');p.chmod(0o600)
    fault=next((e for e in events if e['topic']=='ad.click' and e['event']['status']=='valid' and e['event'].get('charge_mw',0)>0 and e['event']['source']=='synthetic'),None)
    if fault:
        p=root/'fault.json';p.write_text(json.dumps({'serve_id':fault['event']['serve_id'],'charge_id':fault['event']['charge_id'],'source_event_id':fault['event']['event_id']})+'\n');p.chmod(0o600)
    return {'source_events':len(events),'ledger_rows':len(ledger),'pending':0,'requests':len(requests),'confirmed_net_mw':sum(r['amount_mw'] for r in ledger if r['state']=='confirmed')}


def run_probe(config,run,count):
    """새 합성 캠페인과 새로운 가명 세션만 사용해 실제 내부 API를 호출한다."""
    from ads.web.models import Campaign,Creative,AdStoredRequest
    if AdStoredRequest.objects.filter(pk__startswith=run).exists():raise ValueError('existing_run_refused')
    for billing,slot,bid,budget,image in [('cpc','lobby_banner',300000,30000000,'gukbap_a'),('cpm','result_banner',4000000,20000000,'bujeok')]:
        campaign=Campaign(campaign_id=run+'_'+billing,advertiser_id='syn:c08',name='C08 synthetic '+billing,slots=[slot],billing=billing,max_bid_mw=bid,
            daily_budget_mw=budget,targeting={'preset_ids':['base6']},start_date='2026-11-05',end_date='2026-11-12',status='active')
        campaign.full_clean();campaign.save(force_insert=True)
        creative=Creative(creative_id=run+'_cr_'+billing,campaign=campaign,title='Synthetic validation',body='Virtual advertisement only',image_path='/static/ads/'+image+'.png')
        creative.full_clean();creative.save(force_insert=True)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        """내부 클릭의 상대 랜딩만 확인하고 다른 서버로 이동하지 않는다."""
        def redirect_request(self,*args,**kwargs):
            """리다이렉트를 응답으로 관찰한다."""
            return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    origin=config['environment']['PALDO_ADS_INTERNAL_URL'];statuses=[];filled=0
    def request(path,identity,body=None):
        """토큰·본문을 로그에 남기지 않고 내부 API 상태만 관찰한다."""
        req=urllib.request.Request(origin+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Request-ID':identity})
        try:
            with opener.open(req,timeout=15) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers
    for i in range(count):
        rid=run+'_'+str(i);slot='lobby_banner' if i%2==0 else 'result_banner';mode=i%5
        status,raw,_=request('/ads/v1/serve',rid,{'request_id':rid,'slot':slot,'session_pseudonym':rid+'_session',
            'context':{'preset_id':'base6','outcome':'lose','returning':False,'hour':12},'source':'synthetic','load_test':mode==4})
        statuses.append(status)
        if status!=200:raise RuntimeError('serve_failed_'+str(status))
        ad=json.loads(raw)
        if not ad['filled']:continue
        filled+=1;token=ad['serve_token']
        if mode!=1:
            status,_,_=request('/ads/v1/impression',rid+'_impression',{'token':token,'visible_ratio':.25 if mode==2 else .75,'visible_ms':1500,'occurred_at_ms':int(time.time()*1000)})
            statuses.append(status)
            if status!=204:raise RuntimeError('impression_failed')
        if mode in (0,1,4):
            status,_,headers=request('/ads/v1/click?t='+urllib.parse.quote(token,safe=''),rid+'_click')
            statuses.append(status)
            if status!=302 or not headers['Location'].startswith('/ads/landing/'):raise RuntimeError('click_contract_failed')
    return {'http_requests':len(statuses),'http_errors':sum(s not in (200,204,302) for s in statuses),'filled':filled,'synthetic_only':True}


def replay(config,out):
    """실제 DB에서 발행된 원본을 동일 ID·내용으로 재발행해 bronze 중복 제거를 시험한다."""
    from confluent_kafka import Producer
    client=json.loads(Path(config['kafka_config']).read_text());producer=Producer(client|{'enable.idempotence':True,'acks':'all'})
    events=json.loads((Path(out)/'source-events.json').read_text());errors=[];acked=[]
    def delivered(error,message):
        """브로커 ACK 수와 오류 종류만 모은다."""
        if error:errors.append(type(error).__name__)
        else:acked.append(message.offset())
    for row in events:producer.produce(row['topic'],key=row['event']['serve_id'],value=json.dumps(row['event']).encode(),on_delivery=delivered)
    remaining=producer.flush(30)
    if remaining or errors or len(acked)!=len(events):raise RuntimeError('replay_ack_failed')
    return len(acked)


def main(argv=None):
    """주소·DB 인증은 외부 프로필만 사용하며 명시 실행만 쓰거나 조회한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True);parser.add_argument('--run',required=True)
    parser.add_argument('--out',required=True);parser.add_argument('--requests',type=int,default=40)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--replay-events',action='store_true');args=parser.parse_args(argv)
    if not re.fullmatch(r'syn:c08_[A-Za-z0-9_]{1,45}',args.run) or not 10<=args.requests<=1000:parser.error('isolated bounded synthetic run required')
    config=prepare(args.profile);result=run_probe(config,args.run,args.requests) if args.execute else {}
    result.update(export_run(args.run,args.out))
    if args.replay_events:result['replay_acked']=replay(config,args.out)
    print(json.dumps(result))


if __name__=='__main__':main()
