"""광고 원본의 판정·맥락·귀속·정수 금액을 계산한다. 외부 시스템에 쓰지 않는다."""
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
import json
from zoneinfo import ZoneInfo
from ads.domain.model import BillingType
from ads.domain.rules import GRACE_MS,TOKEN_TTL_MS,VIEWABLE_MS,VIEWABLE_RATIO,JUDGE_VERSION,charge_amount_mw,reconcile

SEOUL=ZoneInfo('Asia/Seoul')


def unique_events(records):
    """같은 ID의 동일 재전송만 제거하고 내용 충돌은 거절한다."""
    found={}
    for record in records:
        event=dict(record['event']);event['_topic']=record['topic'];identity=event['event_id']
        if identity in found and json.dumps(found[identity],sort_keys=True,allow_nan=False)!=json.dumps(event,sort_keys=True,allow_nan=False):raise ValueError('conflicting_ad_event_id')
        found[identity]=event
    return list(found.values())


def silver_rows(records,as_of_ms):
    """서버 수신 시각으로 재판정한다. 서명 판정은 온라인 증거를 유지하고 truth를 만들지 않는다."""
    events=unique_events(records)
    serves={e['serve_id']:e for e in events if e['_topic']=='ad.serve' and e.get('serve_id') and e['received_at_ms']<=as_of_ms}
    rejudges={e['source_event_id']:e for e in sorted(events,key=lambda e:e.get('occurred_at_ms',0)) if e['_topic']=='ad.rejudge' and e['occurred_at_ms']<=as_of_ms}
    rows=[]
    for e in events:
        if e['_topic'] not in ('ad.impression','ad.click') or e['received_at_ms']>as_of_ms:continue
        serve=serves.get(e.get('serve_id'));first=e['received_at_ms'];reason=e.get('invalid_reason');status='valid'
        if reason=='bad_signature':status='invalid'
        elif reason=='expired' or (serve and first>serve['occurred_at_ms']+TOKEN_TTL_MS):status,reason='invalid','expired'
        elif e.get('load_test',False):status,reason='invalid','load_test'
        elif not serve or not serve.get('filled'):
            status,reason=('pending',None) if as_of_ms-first<GRACE_MS else ('invalid','no_serve')
        elif reason=='duplicate':status='invalid'
        elif e['_topic']=='ad.impression' and not e.get('implied_by_click') and (e.get('visible_ratio',0)<VIEWABLE_RATIO or e.get('visible_ms',0)<VIEWABLE_MS):status,reason='invalid','not_viewable'
        else:reason=None
        correction=rejudges.get(e['event_id'])
        if correction and correction['charge_id']==e.get('charge_id') and correction['source']==e['source']:
            status,reason='invalid',correction['invalid_reason']
        stamp=serve['occurred_at_ms'] if serve else None
        when=datetime.fromtimestamp(stamp/1000,SEOUL) if stamp is not None else None
        context=serve.get('context') if serve else None
        row={k:e.get(k) for k in ('event_id','request_id','serve_id','source','campaign_id','creative_id','slot','billing','price_mw','received_at_ms','occurred_at_ms','contract_version')}
        row.update(topic=e['_topic'],load_test=e.get('load_test',False),online_status=e.get('status'),online_charge_id=e.get('charge_id'),
            online_charge_mw=e.get('charge_mw',0),status=status,invalid_reason=reason,expected_charge_id=None,expected_charge_mw=0,
            serve_time_ms=stamp,attribution_day=when.date().isoformat() if when else None,hour=when.hour if when else None,
            preset_id=context.get('preset_id') if context else None,outcome=context.get('outcome') if context else None,
            returning=context.get('returning') if context else None,context_missing=context is None,
            implied_by_click=e.get('implied_by_click',False),observation_cutoff_ms=as_of_ms,judge_version=JUDGE_VERSION)
        rows.append(row)
    groups=defaultdict(list)
    corrections_by_serve={r['serve_id']:rejudges[r['event_id']] for r in rows if r['event_id'] in rejudges and
        rejudges[r['event_id']]['charge_id']==r['online_charge_id'] and rejudges[r['event_id']]['source']==r['source']}
    blocked={c['charge_id'] for c in corrections_by_serve.values()}
    for row in rows:
        correction=corrections_by_serve.get(row['serve_id'])
        if correction and correction['invalid_reason']=='no_serve' and correction['source']==row['source']:
            row.update(status='invalid',invalid_reason='no_serve')
    for row in rows:groups[row['serve_id']].append(row)
    for sid,group in groups.items():
        if sid is None:continue
        clicks=sorted((r for r in group if r['topic']=='ad.click' and r['status']=='valid'),key=lambda r:(r['received_at_ms'],r['event_id']))
        for duplicate in clicks[1:]:duplicate.update(status='invalid',invalid_reason='duplicate')
        click=clicks[0] if clicks else None
        impressions=sorted((r for r in group if r['topic']=='ad.impression' and r['status']=='valid'),key=lambda r:(r['received_at_ms'],not r['implied_by_click'],r['event_id']))
        eligible=[]
        for row in impressions:
            if row['implied_by_click'] and not click:row.update(status='pending',invalid_reason=None)
            elif click and row['received_at_ms']>click['received_at_ms']:row.update(status='invalid',invalid_reason='duplicate')
            else:eligible.append(row)
        for duplicate in eligible[1:]:duplicate.update(status='invalid',invalid_reason='duplicate')
        impression=eligible[0] if eligible else None
        basis=click if click and click['billing']=='cpc' else (impression or click)
        if basis and basis['billing'] in ('cpc','cpm') and (basis['billing']=='cpm' or basis is click):
            amount=charge_amount_mw(BillingType(basis['billing']),basis['price_mw'])
            if amount<=0:raise ValueError('non_positive_valid_charge')
            identity=sid+(':click' if basis['billing']=='cpc' else ':impression')
            if identity not in blocked:basis.update(expected_charge_id=identity,expected_charge_mw=amount)
    return sorted(rows,key=lambda r:r['event_id'])


def rejudge_events(rows,as_of_ms,emitted=()):
    """원래 확정한 정확한 이벤트만 원장 환입용 결정적 ID로 발행 후보를 만든다."""
    emitted=set(emitted);result=[];supported={r['expected_charge_id'] for r in rows if r['expected_charge_id']}
    for row in rows:
        if row['online_status']!='valid' or row['status']!='invalid' or not row['online_charge_id'] or row['online_charge_mw']<=0:continue
        if row['online_charge_id'] in supported:continue
        identity='rj:'+row['online_charge_id']+':'+JUDGE_VERSION
        if identity in emitted:continue
        result.append({'event_id':identity,'source_event_id':row['event_id'],'charge_id':row['online_charge_id'],
            'invalid_reason':row['invalid_reason'],'judge_version':JUDGE_VERSION,'occurred_at_ms':as_of_ms,
            'source':row['source'],'contract_version':row['contract_version']})
        emitted.add(identity)
    return result


def closing_times(day):
    """귀속일 다음 자정과, 그날 마지막 serve의 클릭·유예 기한을 구분한다."""
    provisional=datetime.fromisoformat(day).replace(tzinfo=SEOUL)+timedelta(days=1)
    return int(provisional.timestamp()*1000),int(provisional.timestamp()*1000)+TOKEN_TTL_MS+GRACE_MS


def gold_rows(rows,as_of_ms):
    """원래 serve의 날짜·시간에 비용과 CTR을 집계하고 맥락 없는 행은 학습에서 제외한다."""
    groups=defaultdict(list);learning=defaultdict(list)
    for row in rows:
        key=tuple(row.get(k) for k in ('attribution_day','campaign_id','slot','hour','source','load_test'))
        groups[key].append(row)
        if not row['context_missing'] and not row['load_test']:
            lk=tuple(row.get(k) for k in ('attribution_day','creative_id','slot','preset_id','outcome','hour','source'))
            learning[lk].append(row)
    def measures(items):
        reportable=[r for r in items if not r['load_test']]
        valid=[r for r in reportable if r['status']=='valid']
        invalid=[r for r in reportable if r['status']=='invalid']
        clicks={r['serve_id'] for r in valid if r['topic']=='ad.click'}
        impressions={r['serve_id'] for r in valid if r['topic']=='ad.impression'}|clicks
        cost=sum(r['expected_charge_mw'] for r in reportable)
        invalid_impressions=sum(r['topic']=='ad.impression' for r in invalid)
        invalid_clicks=sum(r['topic']=='ad.click' for r in invalid)
        impression_total=len(impressions)+invalid_impressions
        click_total=len(clicks)+invalid_clicks
        judged=len(valid)+len(invalid)
        return {'impressions':len(impressions),'clicks':len(clicks),'ctr':len(clicks)/len(impressions) if impressions else None,
            'cost_mw':cost,'ecpm_mw':cost*1000/len(impressions) if impressions else None,'cpc_mw':cost/len(clicks) if clicks else None,
            'invalid_events':len(invalid),'pending_events':sum(r['status']=='pending' for r in reportable),
            'invalid_ratio':len(invalid)/judged if judged else None,
            'invalid_impressions':invalid_impressions,'invalid_clicks':invalid_clicks,
            'invalid_impression_rate':invalid_impressions/impression_total if impression_total else None,
            'invalid_click_rate':invalid_clicks/click_total if click_total else None,
            'load_test_events':len(items)-len(reportable),
            'context_missing_events':sum(r['context_missing'] for r in reportable),'judged_events':judged}
    gold=[]
    for key,items in groups.items():
        row=dict(zip(('attribution_day','campaign_id','slot','hour','source','load_test'),key));row.update(measures(items))
        row['observation_cutoff_ms']=as_of_ms
        provisional,final=closing_times(row['attribution_day']) if row['attribution_day'] else (None,None)
        row.update(provisional_close_ms=provisional,final_close_ms=final,provisional_closed=bool(provisional and as_of_ms>=provisional),
                   final_closed=bool(final and as_of_ms>=final))
        gold.append(row)
    training=[]
    for key,items in learning.items():
        measured=measures(items)
        if not measured['impressions']:continue
        row=dict(zip(('attribution_day','creative_id','slot','preset_id','outcome','hour','source'),key));row.update(measured);training.append(row)
    return gold,training


def campaign_daily_rows(ledger_rows, budgets):
    """캠페인·귀속일·출처별 확정 순액을 해당 일 예산과 비교한다."""
    amounts=defaultdict(int)
    for row in ledger_rows:
        if not row.get('load_test',False):
            key=(row['attribution_day'],row['campaign_id'],row['source'])
            amounts[key]+=row['amount_mw'] if row['state']=='confirmed' else 0
    results=[]
    for (day,campaign,source),net in sorted(amounts.items()):
        budget=budgets.get(day,{}).get(campaign)
        results.append({'attribution_day':day,'campaign_id':campaign,'source':source,
                        'confirmed_net_mw':net,'budget_mw':budget,'burn_ratio':net/budget if budget else None})
    return results


def fill_rows(records, as_of_ms):
    """중복 없는 serve의 전체·지면별 채움률을 출처별로 계산한다."""
    counts=defaultdict(Counter)
    for event in unique_events(records):
        if event['_topic']!='ad.serve' or event['received_at_ms']>as_of_ms:
            continue
        for slot in (None,event['slot']):
            count=counts[(event['source'],slot)]
            if event.get('load_test',False):
                count['load_test']+=1
            else:
                count['total']+=1
                count['filled']+=int(event['filled'])
    return [{'source':source,'slot':slot,'serves':count['total'],'filled':count['filled'],
             'load_test_serves':count['load_test'],'fill_ratio':count['filled']/count['total'] if count['total'] else None}
            for (source,slot),count in counts.items()]


def reconcile_rows(ledger_rows,silver):
    """확정 원과금과 음수 환입의 순액을 Delta 재계산 금액과 다섯 범주로 대조한다."""
    confirmed=[r for r in ledger_rows if r['state']=='confirmed']
    ledger={r['charge_id']:r['amount_mw'] for r in confirmed}
    delta=defaultdict(list)
    for row in silver:
        if row['expected_charge_id']:delta[row['expected_charge_id']].append(row['expected_charge_mw'])
    differences=reconcile(ledger,dict(delta))
    duplicate_ids={identity for identity,count in Counter(r['charge_id'] for r in confirmed).items() if count>1}
    differences['duplicate']=sorted(set(differences['duplicate'])|duplicate_ids)
    return {'mismatches':sum(len(v) for v in differences.values()),'differences':differences,
            'ledger_net_mw':sum(r['amount_mw'] for r in confirmed),'delta_mw':sum(sum(v) for v in delta.values())}


CTR_CELL_KEYS=('campaign_id','creative_id','slot','source','attribution_day','hour')


def ctr_cells(rows,as_of_ms):
    """확정 마감이 지난 날의 유효 노출·클릭된 노출을 `ad_update ctr` 칸으로 접는다(부하 시험 제외).

    칸은 (캠페인, 소재, 지면, 출처, 귀속일, 시간)이고 serve 하나는 한 번만 센다. 클릭만 온 serve도
    노출로 친다(gold `measures`와 같은 규칙). 맥락·귀속이 없는 행과 마감 전 날은 넣지 않는다."""
    shown=defaultdict(set);clicked=defaultdict(set)
    for row in rows:
        day=row['attribution_day']
        if (row['load_test'] or row['status']!='valid' or not day or row['hour'] is None or not row['serve_id']
                or not row['campaign_id'] or not row['creative_id'] or closing_times(day)[1]>as_of_ms):
            continue
        cell=tuple(row[k] for k in CTR_CELL_KEYS)
        shown[cell].add(row['serve_id'])
        if row['topic']=='ad.click':clicked[cell].add(row['serve_id'])
    return [dict(zip(CTR_CELL_KEYS,cell),valid_impressions=len(serves),clicked_impressions=len(clicked[cell]&serves))
            for cell,serves in sorted(shown.items(),key=lambda item:tuple(str(v) for v in item[0]))]


def ctr_snapshot(rows,as_of_ms,source_version):
    """`ad_update ctr` 입력 봉투(계약 ads.md 4절): 확정 표시·출처 버전·관찰 시각·전체 칸."""
    if not source_version:raise ValueError('source_version_required')
    return {'finalized':True,'source_version':source_version,'as_of_ms':as_of_ms,'rows':ctr_cells(rows,as_of_ms)}


def final_closed_scope(ledger_rows,rows,as_of_ms):
    """대조 범위를 확정 마감이 지난 귀속일로 좁힌다(원장·silver 양쪽).

    운영 중 하루 집계는 원장을 내보내는 순간에도 오늘 과금이 늘고 bronze 반영이 늦을 수 있다. 확정 마감이 지난
    날은 새 과금이 생기지 않으므로(환입은 원래 귀속일 원장에 붙음) 이 범위만 대조하면 같은 시점을 비교한다."""
    closed=lambda day:bool(day) and closing_times(day)[1]<=as_of_ms
    return ([r for r in ledger_rows if closed(r['attribution_day'])],
            [r for r in rows if closed(r['attribution_day'])])
