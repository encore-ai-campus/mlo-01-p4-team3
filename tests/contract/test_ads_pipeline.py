"""광고 시각·정수 과금·늦은 맥락·확정 환입·중복의 회계 경계를 검증한다."""
from copy import deepcopy
from datetime import datetime
import unittest
from pipeline.spark.ads_contract import silver_rows,gold_rows,rejudge_events,reconcile_rows,closing_times
from ads.domain.rules import TOKEN_TTL_MS,GRACE_MS

T=int(datetime.fromisoformat('2026-11-08T23:59:59+09:00').timestamp()*1000)


def event(topic,identity,delta=0,**changes):
    """서버 계약 필드와 합성 출처를 갖는 작은 판정 자료를 만든다."""
    e={'event_id':identity,'request_id':'syn:c08_r','serve_id':'syn:c08_s','occurred_at_ms':T+delta,'received_at_ms':T+delta,
       'slot':'lobby_banner','campaign_id':'syn:c08_c','creative_id':'syn:c08_cr','session_pseudonym':'syn:session','source':'synthetic',
       'policy_version':'policy','ctr_version':'ctr','contract_version':'ads-0.6','billing':'cpc','price_mw':100999,'load_test':False}
    if topic=='ad.serve':e.update(filled=True,context={'preset_id':'base6','outcome':'win','returning':False,'hour':23})
    else:e.update(status='valid',invalid_reason=None,charge_id=None,charge_mw=0)
    if topic=='ad.impression':e.update(visible_ratio=.5,visible_ms=1000,implied_by_click=False)
    e.update(changes);return {'topic':topic,'event':e}


class AdsPipeline(unittest.TestCase):
    """실제 금액 공식을 공유하되 원장 대조·주입 경계에서 실패를 숨기지 않는다."""
    def normal(self,billing='cpc'):
        """유효 serve·노출·클릭 한 묶음을 만든다."""
        return [event('ad.serve','s',billing=billing),event('ad.impression','i',1000,billing=billing),
                event('ad.click','c',2000,billing=billing,charge_id='syn:c08_s:click' if billing=='cpc' else None,charge_mw=100999 if billing=='cpc' else 0)]

    def test_attribution_uses_serve_not_next_day_click(self):
        rows=silver_rows(self.normal(),T+3000)
        self.assertTrue(all(r['attribution_day']=='2026-11-08' and r['hour']==23 for r in rows))

    def test_cpc_and_cpm_are_integer_per_charge(self):
        for billing,expected in [('cpc',100999),('cpm',100)]:
            rows=silver_rows(self.normal(billing),T+3000)
            self.assertEqual(sum(r['expected_charge_mw'] for r in rows),expected)

    def test_client_timestamp_does_not_expire_on_time_receipt(self):
        data=self.normal();data[-1]['event']['occurred_at_ms']=T+TOKEN_TTL_MS*2
        rows=silver_rows(data,T+TOKEN_TTL_MS*3)
        self.assertEqual(next(r for r in rows if r['event_id']=='c')['status'],'valid')

    def test_token_deadline_is_inclusive(self):
        for delta,status in [(TOKEN_TTL_MS,'valid'),(TOKEN_TTL_MS+1,'invalid')]:
            rows=silver_rows([event('ad.serve','s'),event('ad.click','c',delta)],T+delta)
            self.assertEqual(rows[0]['status'],status)

    def test_click_implies_only_one_cpm_impression(self):
        data=[event('ad.serve','s',billing='cpm'),event('ad.click','c',500,billing='cpm'),
              event('ad.impression','implied',500,billing='cpm',implied_by_click=True),event('ad.impression','late',1500,billing='cpm')]
        rows=silver_rows(data,T+2000);gold,_=gold_rows(rows,T+2000)
        self.assertEqual((gold[0]['impressions'],gold[0]['clicks'],gold[0]['cost_mw']),(1,1,100))
        self.assertEqual(next(r for r in rows if r['event_id']=='late')['invalid_reason'],'duplicate')

    def test_no_serve_grace_and_exact_refund_source(self):
        record=event('ad.click','c',charge_id='syn:c08_s:click',charge_mw=100999)
        self.assertEqual(silver_rows([record],T+GRACE_MS-1)[0]['status'],'pending')
        rows=silver_rows([record],T+GRACE_MS);corrections=rejudge_events(rows,T+GRACE_MS)
        self.assertEqual((rows[0]['invalid_reason'],corrections[0]['source_event_id']),('no_serve','c'))
        self.assertEqual(rejudge_events(rows,T+GRACE_MS,[corrections[0]['event_id']]),[])

    def test_online_signature_failure_precedes_load_and_pairing(self):
        rows=silver_rows([event('ad.click','c',status='invalid',invalid_reason='bad_signature',load_test=True)],T+GRACE_MS)
        self.assertEqual(rows[0]['invalid_reason'],'bad_signature')

    def test_load_test_does_not_charge_or_train(self):
        data=self.normal()
        for record in data:record['event']['load_test']=True
        rows=silver_rows(data,T+3000);_,training=gold_rows(rows,T+3000)
        self.assertEqual(sum(r['expected_charge_mw'] for r in rows),0);self.assertEqual(training,[])

    def test_id_replay_deduplicates_but_conflict_fails(self):
        data=self.normal();self.assertEqual(len(silver_rows(data+[deepcopy(data[0])],T+3000)),2)
        bad=deepcopy(data[0]);bad['event']['price_mw']+=1
        with self.assertRaisesRegex(ValueError,'conflicting_ad_event_id'):silver_rows(data+[bad],T+3000)

    def test_zero_denominators_are_null(self):
        rows=silver_rows([event('ad.click','c',status='invalid',invalid_reason='bad_signature')],T+GRACE_MS)
        gold,training=gold_rows(rows,T+GRACE_MS)
        self.assertTrue(all(gold[0][k] is None for k in ('ctr','ecpm_mw','cpc_mw')));self.assertEqual(training,[])

    def test_missing_context_is_excluded_until_serve_arrives(self):
        data=self.normal();rows=silver_rows(data[1:],T+3000);_,training=gold_rows(rows,T+3000)
        self.assertEqual(training,[])
        _,training=gold_rows(silver_rows(data,T+3000),T+3000);self.assertEqual(len(training),1)

    def test_rejudge_stays_invalid_when_context_arrives(self):
        data=self.normal();without=silver_rows(data[1:],T+GRACE_MS+3000)
        correction=rejudge_events(without,T+GRACE_MS+3000)[0]
        rows=silver_rows(data+[{'topic':'ad.rejudge','event':correction}],T+GRACE_MS+4000)
        click=next(r for r in rows if r['event_id']=='c')
        self.assertEqual(click['status'],'invalid');self.assertFalse(click['context_missing'])

    def test_refund_net_zero_matches_no_charge(self):
        rows=silver_rows(self.normal(),T+3000);ledger=[{'charge_id':'syn:c08_s:click','amount_mw':100999,'state':'confirmed'}]
        self.assertEqual(reconcile_rows(ledger,rows)['mismatches'],0)
        correction={'topic':'ad.rejudge','event':{'event_id':'rj','source_event_id':'c','charge_id':'syn:c08_s:click','invalid_reason':'no_serve','source':'synthetic','occurred_at_ms':T+4000}}
        rows=silver_rows(self.normal()+[correction],T+5000)
        ledger.append({'charge_id':'syn:c08_s:click:refund','amount_mw':-100999,'state':'confirmed'})
        result=reconcile_rows(ledger,rows);self.assertEqual(result['mismatches'],0);self.assertEqual(result['ledger_net_mw'],0)

    def test_orphan_refund_is_not_ignored(self):
        result=reconcile_rows([{'charge_id':'missing:click:refund','amount_mw':-10,'state':'confirmed'}],[])
        self.assertEqual(result['differences']['orphan_refund'],['missing:click:refund'])

    def test_cpm_refund_is_not_recharged_by_the_click(self):
        data=self.normal('cpm');data[1]['event'].update(charge_id='syn:c08_s:impression',charge_mw=100)
        correction=rejudge_events(silver_rows(data[1:],T+GRACE_MS+3000),T+GRACE_MS+3000)[0]
        rows=silver_rows(data+[{'topic':'ad.rejudge','event':correction}],T+GRACE_MS+4000)
        self.assertEqual(sum(r['expected_charge_mw'] for r in rows),0)
        self.assertTrue(all(r['status']=='invalid' for r in rows))

    def test_provisional_and_final_close_differ_by_window(self):
        provisional,final=closing_times('2026-11-08')
        self.assertEqual(final-provisional,TOKEN_TTL_MS+GRACE_MS)

    def test_duplicate_ledger_export_is_not_collapsed_to_success(self):
        """손상된 원장 export의 중복 charge가 dict 변환으로 숨겨지면 안 된다."""
        row={'charge_id':'s:click','amount_mw':100,'state':'confirmed'}
        result=reconcile_rows([row,row],[])
        self.assertIn('s:click',result['differences']['duplicate'])
        self.assertEqual(result['ledger_net_mw'],200)
