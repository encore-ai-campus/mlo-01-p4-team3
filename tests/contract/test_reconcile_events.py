"""next_seq 대조는 누락·초과·ID 충돌을 놓치지 않고 Kafka 재전송은 허용한다."""
import unittest
from tools.reconcile_game_events import reconcile


class Reconcile(unittest.TestCase):
    """실제 대조의 실패 경계를 확인한다."""

    def setUp(self):
        self.source=[{'game_id':'g','next_seq':3}]
        self.events=[{'game_id':'g','seq':n,'event_id':f'g:{n:05d}'} for n in range(3)]

    def test_kafka_replay_does_not_create_a_false_loss(self):
        result=reconcile(self.source,self.events+[self.events[0]],self.events)
        self.assertTrue(result['ok']);self.assertEqual(result['games'][0]['published_raw_duplicates'],1)

    def test_missing_sequence_is_named(self):
        result=reconcile(self.source,self.events[:2],self.events)
        self.assertFalse(result['ok']);self.assertEqual(result['games'][0]['published_missing_seq'],[2])

    def test_delta_duplicate_and_wrong_id_fail(self):
        self.assertFalse(reconcile(self.source,self.events,self.events+[self.events[0]])['ok'])
        wrong=[dict(e,event_id='wrong') for e in self.events]
        self.assertFalse(reconcile(self.source,wrong,self.events)['ok'])

    def test_hash_conflict_is_named(self):
        source=[dict(self.source[0],event_hashes={e['event_id']:'expected' for e in self.events})]
        data=[dict(e,envelope_hash='changed') for e in self.events]
        self.assertFalse(reconcile(source,data,data)['ok'])


if __name__=='__main__':unittest.main()
