"""Explicit local-only Mongo CAS, lost-ack retry and immutable version integration."""
import os
import unittest
import uuid
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests.test_bandit import games, uniform_state


@unittest.skipUnless(os.environ.get('PALDO_BANDIT_MONGO_URI'), 'isolated local Mongo required')
class MongoBanditTests(unittest.TestCase):
    def setUp(self):
        from pymongo import MongoClient
        from pymongo.uri_parser import parse_uri
        uri = os.environ['PALDO_BANDIT_MONGO_URI']
        if any(host not in ('localhost', '127.0.0.1', '::1') for host, _ in parse_uri(uri)['nodelist']):
            raise ValueError('local Mongo only')
        self.client = MongoClient(uri, serverSelectionTimeoutMS=3000, timeoutMS=10000)
        self.collection = self.client['paldo_d0902_test']['run_' + uuid.uuid4().hex]
        self.addCleanup(self.client.close)
        self.addCleanup(self.collection.drop)
        from ml.mongo_bandit import MongoBanditStore
        self.store = MongoBanditStore(self.collection, uniform_state()['arms'])

    def test_concurrent_workers_publish_only_one_version_and_retry_is_noop(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.store.update(games()), range(24)))
        self.assertEqual(sum(r is not None for r in results), 1)
        stored = self.store.load()
        self.assertEqual(stored['revision'], 1)
        self.assertEqual(stored['posterior']['uniform'], {'alpha': 21., 'beta': 1.})
        self.assertIsNone(self.store.update(games()))
        self.assertEqual(self.store.load(), stored)

    def test_lost_ack_then_new_process_store_does_not_reapply_reward(self):
        from pymongo.errors import NetworkTimeout
        from ml.mongo_bandit import MongoBanditStore
        original = self.store.collection.replace_one
        def lost_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise NetworkTimeout('test lost ack')
        with patch.object(self.store.collection, 'replace_one', side_effect=lost_ack):
            with self.assertRaises(NetworkTimeout):
                self.store.update(games())
        reopened = MongoBanditStore(self.collection)
        self.assertIsNone(reopened.update(games()))
        self.assertEqual(reopened.load()['revision'], 1)
        self.assertEqual(len(reopened.load()['versions'][0]['reward_game_ids']), 20)

    def test_new_responses_publish_new_version_and_previous_source_stays_pinned(self):
        from ml.bandit import VersionedMixSource
        self.store.update(games())
        old = VersionedMixSource(self.store.load(), 'bandit-1')
        rows = games(40)
        self.store.update(rows)
        state = self.store.load()
        self.assertEqual(state['revision'], 2)
        self.assertEqual(len(state['versions'][0]['reward_game_ids']), 20)
        self.assertEqual(len(state['versions'][1]['reward_game_ids']), 20)
        self.assertEqual(state['posterior']['uniform'], {'alpha': 41., 'beta': 1.})
        self.assertEqual(old.version, 'bandit-1')
        self.assertEqual(old.mix_for('g-new').mix_id, 'uniform')

    def test_export_cli_in_separate_process_and_replay(self):
        events = []
        for row in games():
            meta = dict(game_id=row['game_id'], player_pseudonym=row['player_pseudonym'],
                        source='human', load_test=False, occurred_at=row['ended_at'],
                        model_versions={'bandit': 'bandit-0', 'bandit_arm': 'uniform'})
            events += [dict(meta, event_id=row['game_id']+':1', topic='paldo.game', phase='ended',
                            event_type='game_ended', payload={'winner':'village' if row['village_win'] else 'changgwi'}),
                       dict(meta, event_id=row['game_id']+':2', topic='paldo.player',
                            event_type='survey_submitted', payload=row['survey'])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
            (path/'consent.json').write_text(json.dumps([r['player_pseudonym'] for r in games()]), encoding='utf-8')
            # Append an explicit DB name to the local test URI without printing it.
            uri = os.environ['PALDO_BANDIT_MONGO_URI'].replace('/?', '/paldo_d0902_test?')
            command = [sys.executable, '-m', 'ml.mongo_bandit', '--events', str(path/'events.jsonl'),
                       '--consented-users', str(path/'consent.json'), '--collection', self.collection.name]
            env = dict(os.environ, PALDO_MONGO_URI=uri)
            for expected in (True, False):
                run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(json.loads(run.stdout)['updated'], expected)
                self.assertNotIn('u19', run.stdout)
            self.assertEqual(self.store.load()['revision'], 1)

    def test_privacy_cleanup_retry_and_next_update(self):
        self.store.update(games())
        self.store.forget_games({'g000'})
        after = self.store.load()
        self.assertNotIn('g000', str(after))
        self.store.forget_games({'g000'})
        self.assertEqual(self.store.load(), after)
        version = self.store.update(games(40)[1:])
        self.assertEqual(version['version'], 'bandit-2')
        self.assertEqual(self.store.load()['posterior']['uniform'], {'alpha': 40., 'beta': 1.})
