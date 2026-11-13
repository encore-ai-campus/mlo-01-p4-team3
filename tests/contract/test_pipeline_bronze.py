"""C-07-01: 외부 서비스 없이 공식 봉투·재전송·원본 충돌 경계를 검사한다."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.spark.bronze_contract import BronzeContractError, canonical, normalize, unique_rows
from pipeline.spark.ingest_bronze import load_config, write_audit

ROOT = Path(__file__).resolve().parents[2]


class BronzeContractTests(unittest.TestCase):
    """샘플 공식 이벤트를 기준으로 원본 보존과 중복 제거를 검사한다."""

    def setUp(self):
        self.event = json.loads((ROOT/'fixtures/events/sample_events.jsonl').read_text(encoding='utf-8').splitlines()[0])

    def row(self, event=None, offset=0, **overrides):
        event = event or self.event
        options = {'topic': event['topic'], 'key': event['game_id'], 'partition': 0, 'offset': offset, **overrides}
        return normalize(canonical(event), **options)

    def test_official_non_uuid_id_and_metadata_roundtrip(self):
        row = self.row()
        self.assertEqual(row['event_id'], self.event['event_id'])
        self.assertEqual(json.loads(row['envelope_json']), self.event)
        self.assertEqual(row['source'], 'scripted')
        self.assertFalse(row['load_test'])

    def test_all_fixture_events_follow_envelope_contract(self):
        for offset, raw in enumerate((ROOT/'fixtures/events/sample_events.jsonl').read_text(encoding='utf-8').splitlines()):
            event = json.loads(raw)
            self.assertEqual(self.row(event, offset)['event_id'], event['event_id'])

    def test_transport_and_both_existing_duplicate_paths(self):
        first, repeated = self.row(offset=1), self.row(offset=2)
        self.assertEqual(len(unique_rows([repeated, first])), 1)
        self.assertEqual(unique_rows([first, repeated])[0]['offset'], 1)
        self.assertEqual(unique_rows([first], {first['event_id']: first['envelope_hash']}), [])

    def test_duplicate_json_keys_and_nonfinite_numbers_fail(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(BronzeContractError):
                normalize(raw, topic='paldo.game', key='game', partition=0, offset=0)

    def test_changed_id_version_or_key_fails(self):
        for field, value in [('event_id', 'uuid-demo-id'), ('seq', -1), ('contract_version', 'unsupported')]:
            event = copy.deepcopy(self.event)
            event[field] = value
            with self.assertRaises(BronzeContractError): self.row(event)
        with self.assertRaises(BronzeContractError): self.row(key='other-game')

    def test_truth_requires_ended_even_for_future_event_types(self):
        for kind in ('truth_revealed', 'future_truth_event'):
            event = {**self.event, 'topic':'paldo.truth', 'event_type':kind}
            with self.assertRaises(BronzeContractError): self.row(event)
            self.assertEqual(self.row({**event, 'phase':'ended'})['topic'], 'paldo.truth')

    def test_known_event_cannot_use_wrong_topic(self):
        with self.assertRaises(BronzeContractError): self.row({**self.event, 'topic':'paldo.intent'})

    def test_unknown_event_type_is_retained(self):
        self.assertEqual(self.row({**self.event, 'event_type':'future_event'})['event_type'], 'future_event')

    def test_human_identity_and_load_test_are_preserved(self):
        event = {**self.event,'source':'human','player_pseudonym':'pseudonym','session_id':'session','load_test':True}
        row = self.row(event)
        self.assertEqual(row['source'], 'human')
        self.assertTrue(row['load_test'])
        self.assertEqual(json.loads(row['envelope_json'])['player_pseudonym'], 'pseudonym')
        regular = self.row({**event, 'session_id':None})
        self.assertIsNone(json.loads(regular['envelope_json'])['session_id'])
        with self.assertRaises(BronzeContractError): self.row({**event, 'player_pseudonym':None, 'session_id':None})
        with self.assertRaises(BronzeContractError): self.row({**event, 'session_id':''})
        with self.assertRaises(BronzeContractError): self.row({**event, 'source':'bot'})

    def test_conflicts_never_overwrite_committed_content(self):
        first = self.row()
        changed = self.row({**self.event,'payload':{'changed':True}}, offset=1)
        for rows, existing in [([first,changed], {}), ([changed], {first['event_id']:first['envelope_hash']})]:
            with self.assertRaises(BronzeContractError): unique_rows(rows, existing)

    def test_survey_null_scores_remain_null(self):
        event = {**self.event,'event_type':'survey_submitted','topic':'paldo.player',
                 'payload':{'skipped':True,'fun':None,'unfair':None,'npc_plausible':None,'comment':'','mask_version':'fixture'}}
        payload = json.loads(self.row(event)['envelope_json'])['payload']
        self.assertIsNone(payload['fun'])
        self.assertTrue(payload['skipped'])


class BronzeConfigTests(unittest.TestCase):
    """환경 설정이 checkpoint·데이터 경로와 TLS 검증을 보존하는지 검사한다."""

    def config(self):
        return {'bronze_path':'s3a://example-bucket/paldo/bronze','checkpoint_path':'/tmp/paldo-checkpoint',
                'lock_path':'/tmp/paldo-writer.lock','audit_dir':'/tmp/paldo-audit',
                'spark':{'spark.master':'spark://spark-master-01:7077'},
                'kafka':{'bootstrap.servers':'kafka-01:9092','security.protocol':'SSL'}}

    def read(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            path.write_text(json.dumps(value),encoding='utf-8')
            return load_config(path)

    def test_valid_defaults(self):
        self.assertEqual(self.read(self.config())['max_offsets'],100)

    def test_insecure_tls_or_unbounded_batch_rejected(self):
        for change in ('plaintext','hostname','unbounded','local'):
            value=self.config()
            if change=='plaintext': value['kafka']['security.protocol']='PLAINTEXT'
            elif change=='hostname': value['kafka']['ssl.endpoint.identification.algorithm']=''
            elif change=='local': value['spark']['spark.master']='local[2]'
            else: value['max_offsets']=0
            with self.assertRaises(ValueError): self.read(value)

    def test_checkpoint_cannot_overlap_bronze(self):
        for path in ('s3a://example-bucket/paldo/bronze','s3a://example-bucket/paldo/bronze/checkpoint','s3a://example-bucket/paldo'):
            value={**self.config(),'checkpoint_path':path}
            with self.assertRaises(ValueError): self.read(value)

    def test_audit_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'batch.json'
            write_audit(path,{'delta_version':1,'offsets':{'paldo.game:0':{'first':0,'last':2}}})
            write_audit(path,{'delta_version':2})
            self.assertEqual(json.loads(path.read_text())['delta_version'],2)
            self.assertFalse(path.with_suffix('.tmp').exists())
