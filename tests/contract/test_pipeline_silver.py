"""관찰/정답 경계·중단 판·출처·광고 DTO 보존의 회귀 계약."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from pathlib import Path
from engine.domain.model import ObservationSnapshot,PublicRecord,PrivateKnowledge,Beliefs,Role,Phase
from engine.domain.serde import to_jsonable
from pipeline.spark.silver_contract import observation_rows,label_rows
from pipeline.spark.bronze_contract import normalize,BronzeContractError


class SilverBoundary(unittest.TestCase):
    """정답이 특징을 바꾸거나 미완료 판에 새어 들어가지 않아야 한다."""

    def setUp(self):
        sample=Path(__file__).resolve().parents[2]/'fixtures/events/sample_events.jsonl'
        self.observation=json.loads(sample.read_text(encoding='utf-8').splitlines()[0])
        snap=ObservationSnapshot('s','g','observer',1,Phase.VOTE,1,PublicRecord(),
            PrivateKnowledge('observer',Role.VILLAGER,secret_id='never-a-feature'),
            Beliefs(suspicion={'a':30,'b':70}),[],['a','b'],1,'base6')
        self.observation.update(game_id='g',event_type='observation',source='bot',load_test=True,
            payload={'snapshot_id':'s','observer':'observer','snapshot':to_jsonable(snap)})
        self.rows=observation_rows(self.observation)
        self.end={'game_id':'g','event_type':'game_ended','seq':10,'payload':{'winner':'village'}}
        self.truth={'game_id':'g','event_type':'truth_revealed','seq':11,'phase':'ended',
            'occurred_at':'2026-11-08T00:00:00Z','payload':{'roles':{'a':'possessed','b':'villager'}}}

    def test_truth_is_only_a_post_completion_label(self):
        labeled=label_rows(self.rows,[self.truth,self.end])
        self.assertEqual([r['label'] for r in labeled],[1,0])
        self.assertEqual([r['features'] for r in labeled],[r['features'] for r in self.rows])
        self.assertEqual(self.rows[0]['label'],None)
        self.assertNotIn('never-a-feature',json.dumps(self.rows[0]['features']))

    def test_unfinished_aborted_and_early_truth_remain_unlabeled(self):
        for events in ([self.truth],[self.end],
            [dict(self.end,payload={'winner':'village','completed':False}),self.truth],
            [self.end,dict(self.truth,seq=9)],
            [self.end,dict(self.truth,phase='vote')]):
            self.assertTrue(all(r['label'] is None for r in label_rows(self.rows,events)))

    def test_source_load_tag_split_and_observer_role_are_preserved(self):
        self.assertTrue(all(r['source']=='bot' and r['load_test'] and r['observer_role']=='villager' for r in self.rows))
        self.assertEqual(self.rows[0]['split_key']['game_id'],'g')
        changed=deepcopy(self.observation);changed['payload']['snapshot']['private']['role']='possessed'
        self.assertEqual(observation_rows(changed)[0]['observer_role'],'possessed')

    def test_snapshot_identity_conflict_is_rejected(self):
        changed=deepcopy(self.observation);changed['payload']['snapshot']['game_id']='other'
        with self.assertRaises(ValueError):observation_rows(changed)

    def test_ad_rejudge_uses_its_own_contract_and_key(self):
        from ads.domain.events import AdRejudgeEvent
        from ads.domain.model import AD_CONTRACT_VERSION,InvalidReason
        reason=next(iter(InvalidReason))
        event=AdRejudgeEvent('rj:charge:judge','original','charge',reason,'judge',1000,'synthetic',AD_CONTRACT_VERSION)
        raw=json.dumps(to_jsonable(event));row=normalize(raw,topic='ad.rejudge',key='charge',partition=0,offset=0)
        self.assertIsNone(row['game_id']);self.assertFalse(row['load_test'])
        self.assertEqual(json.loads(row['envelope_json']),to_jsonable(event))
        with self.assertRaises(BronzeContractError):normalize(raw,topic='ad.rejudge',key='wrong',partition=0,offset=0)


if __name__=='__main__':unittest.main()
