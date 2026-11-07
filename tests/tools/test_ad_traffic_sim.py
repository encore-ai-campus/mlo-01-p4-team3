"""D-06-03: real HTTP, hidden truth, fatigue and replay regressions."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import ad_traffic_sim as sim

ROOT = Path(__file__).resolve().parents[2]


class TrafficTests(unittest.TestCase):
    def test_truth_model_matches_v1_without_advancing_extra_randomness(self):
        import random
        from tools.gen_synthetic_ads import _Sim, hidden_ctrs, synthetic_campaigns
        rng = random.Random(7)
        self.assertEqual(hidden_ctrs(synthetic_campaigns(), rng), _Sim(7, .05).hidden)
        self.assertEqual(rng.random(), _Sim(7, .05).rng.random())

    def test_fatigue_and_visibility_boundaries(self):
        self.assertEqual(sim.click_probability(.1, 0, .2), .1)
        self.assertAlmostEqual(sim.click_probability(.1, 2, .2), .064)
        self.assertTrue(sim.viewable(.5, 1000))
        self.assertFalse(sim.viewable(.49, 1000))
        self.assertFalse(sim.viewable(.5, 999))

    def test_server_cannot_read_evaluation_truth(self):
        for path in (ROOT / 'ads').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn('ad_truth', node.value, str(path))
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, {'hidden_ctrs', '_Sim', 'generate_ads'}, str(path))

    def test_http_invalid_mix_replay_and_seed_reproduction(self):
        # A fixed high-CTR client fixture gives all four attacks in a small HTTP run.
        from tools.gen_synthetic_ads import hidden_ctrs
        def dense_truth(camps, rng):
            return {key: .5 for key in hidden_ctrs(camps, rng)}
        with tempfile.TemporaryDirectory() as tmp, patch.object(sim, 'hidden_ctrs', dense_truth):
            reports = []
            for rate in (0., .05, .2):
                run = 'http-' + str(round(rate * 100))
                report = sim.run_traffic(run_id=run, seed=17, requests=1000,
                    invalid_rate=rate, fatigue_decay=.2, replay=True, root=Path(tmp))
                reports.append(report)
                self.assertEqual(report['invalid_charges'], 0)
                self.assertEqual(report['replay_new_charges'], 0)
                self.assertEqual(report['replay_new_reservations'], 0)
                self.assertEqual(report['replay_response_mismatches'], 0)
                self.assertGreater(report['valid_clicks'], 0)
                self.assertGreater(report['implied_by_click'], 0)
                self.assertGreater(report['fatigued_views'], 0)
                self.assertEqual(report['unexpected_judgements'], 0)
                if rate:
                    self.assertTrue(all(report['invalid_types'][kind] > 0 for kind in
                        ('duplicate', 'expired', 'bad_signature', 'no_serve')))
                    self.assertAlmostEqual(report['actual_invalid_rate'], rate, delta=.01)
                    self.assertGreater(report['no_serve_pending'], 0)
                else:
                    self.assertEqual(sum(report['invalid_types'].values()), 0)
                events = [json.loads(line) for line in
                    (Path(tmp) / 'out/ad_traffic' / run / 'events.jsonl').read_text().splitlines()]
                self.assertEqual(len({e['event_id'] for e in events}), len(events))
                for event in events:
                    self.assertEqual(event['source'], 'synthetic')
                    self.assertIn(run, event['event_id'])
                    for key in ('serve_id', 'session_pseudonym'):
                        if event.get(key):
                            self.assertIn(run, event[key])
                    for key in ('campaign_id', 'creative_id'):
                        if event.get(key):
                            self.assertTrue(event[key].startswith('syn:'))
                    if event.get('status') == 'invalid':
                        self.assertEqual(event['charge_mw'], 0)
                    self.assertNotIn('hidden_ctr', event)
                    if 'visible_ratio' in event and not event['implied_by_click']:
                        self.assertGreaterEqual(event['visible_ratio'], .5)
                        self.assertGreaterEqual(event['visible_ms'], 1000)
            again = sim.run_traffic(run_id='http-5', seed=17, requests=1000,
                invalid_rate=.05, fatigue_decay=.2, replay=True, root=Path(tmp))
            self.assertEqual(again, reports[1])
            other = sim.run_traffic(run_id='another-run', seed=17, requests=1000,
                invalid_rate=.05, fatigue_decay=.2, root=Path(tmp))
            left = json.loads((Path(tmp) / 'out/ad_truth/http-5.json').read_text())
            right = json.loads((Path(tmp) / 'out/ad_truth/another-run.json').read_text())
            self.assertEqual(left['ctr'], right['ctr'])
            self.assertNotEqual(left['run_id'], right['run_id'])

    def test_reject_invalid_config_before_starting_server(self):
        for kwargs in ({'run_id': '../escape'}, {'invalid_rate': 1.},
                       {'fatigue_decay': float('nan')}, {'requests': -1}, {'requests': 180001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sim.run_traffic(**({'run_id': 'bad', 'requests': 1} | kwargs))


class SyntheticServeIds(unittest.TestCase):
    def test_server_serve_and_charge_source_ids_include_run_id(self):
        from ads.app.serve import AdService
        from ads.app.click import handle_click
        from ads.domain.model import AdRequest, AdSlot, AdContext
        from ads.domain.rules import verify_token
        from tools.gen_synthetic_ads import synthetic_campaigns
        from tools.build_ads_fixtures import slot_policies
        service = AdService(synthetic_campaigns, {p.slot: p for p in slot_policies()},
                            b'test-only', clock=lambda: 1793923200000)
        out = service.serve(AdRequest('syn:run-test:req:1', AdSlot.LOBBY_BANNER,
                            'syn:run-test:s:1', AdContext(), 'synthetic'))
        self.assertTrue(out.filled)
        claims, _ = verify_token(out.serve_token, service.key, service.clock())
        self.assertIn('run-test', claims.serve_id)
        result = handle_click(service, out.serve_token)
        self.assertIn('run-test', service.book.rows[result.charge_id].source_event_id)


    def test_synthetic_request_cannot_overwrite_human_serve(self):
        from ads.app.serve import AdService
        from ads.domain.model import AdRequest, AdSlot, AdContext, BillingType
        from ads.domain.rules import verify_token
        from tools.build_ads_fixtures import campaigns, slot_policies
        from tools.gen_synthetic_ads import synthetic_campaigns
        syn = synthetic_campaigns()[0]
        syn.billing, syn.max_bid_mw = BillingType.CPM, 4_000_000
        service = AdService(lambda: campaigns() + [syn],
            {p.slot: p for p in slot_policies()}, b'test-only', clock=lambda: 1793923200000)
        human = service.serve(AdRequest('human-request', AdSlot.LOBBY_BANNER, 'human-session', AdContext()))
        claims, _ = verify_token(human.serve_token, service.key, service.clock())
        original = service.state.serves[claims.serve_id]
        synthetic = service.serve(AdRequest(claims.serve_id, AdSlot.LOBBY_BANNER,
            'syn:attack', AdContext(), 'synthetic'))
        self.assertTrue(synthetic.filled)
        self.assertIs(service.state.serves[claims.serve_id], original)
        syn_claims, _ = verify_token(synthetic.serve_token, service.key, service.clock())
        self.assertTrue(syn_claims.serve_id.startswith('syn:'))
        self.assertNotEqual(syn_claims.serve_id, claims.serve_id)
