"""Real-server mode validates its manifest and reports HTTP observations only."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import ad_traffic_sim as sim
from tools.gen_synthetic_ads import synthetic_campaigns


class LiveTrafficTests(unittest.TestCase):
    def test_remote_origin_rejects_credentials_and_paths(self):
        for origin in ('file:///tmp/a', 'http://user:pass@localhost', 'http://localhost/other',
                       'http://localhost?x=1', 'http://localhost#part'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                sim._RemoteServer(origin)

    def test_real_mode_uses_existing_http_host_and_keeps_truth_client_side(self):
        run = 'live-test'
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / 'campaigns.json'
            manifest.write_text(json.dumps({'run_id': run,
                'campaigns': [asdict(c) for c in synthetic_campaigns()]}, default=str), encoding='utf-8')
            with sim._Server(Path(folder) / 'host') as host:
                # The existing isolated host runs actual Django views; this mode controls no clock or signing key.
                origin = 'http://127.0.0.1:' + str(host.http.port)
                hidden = sim.hidden_ctrs
                def dense(campaigns, rng):
                    return {key: .9 for key in hidden(campaigns, rng)}
                with patch.object(sim, 'hidden_ctrs', side_effect=dense):
                    result = sim.run_live_traffic(base_url=origin, campaigns_manifest=manifest,
                        run_id=run, requests=120, seed=17, invalid_rate=.2, root=Path(folder))
                state = host.control('snapshot')
            self.assertEqual(result['mode'], 'real-server')
            self.assertGreater(result['filled'], 0)
            self.assertFalse(result['ledger_verified'])
            self.assertNotIn('invalid_charges', result)
            self.assertTrue((Path(folder) / f'out/ad_truth/{run}.json').exists())
            self.assertEqual(state['invalid_charges'], 0)
            self.assertGreater(result['invalid_types']['duplicate'], 0)
            self.assertIn('bad_signature', result['unsupported_invalid_types'])
            self.assertTrue(all(row['source'] == 'synthetic' for row in state['events']))

    def test_manifest_must_belong_to_run_and_have_synthetic_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'manifest.json'
            path.write_text(json.dumps({'run_id': 'other', 'campaigns': []}))
            with self.assertRaises(ValueError):
                sim.run_live_traffic(base_url='http://127.0.0.1:1', campaigns_manifest=path,
                                    run_id='live-test', requests=1, root=Path(folder))

    def test_live_attack_rate_is_bounded_before_any_io(self):
        with self.assertRaises(ValueError):
            sim.run_live_traffic(base_url='http://127.0.0.1:1', campaigns_manifest=Path('absent.json'),
                                run_id='live-test', requests=10, invalid_rate=.21)
