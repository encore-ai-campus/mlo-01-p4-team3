"""D-07-02 경계 테스트. 엔진에서 직접 만든 판만 사용한다."""
import copy
import math
import unittest
import tempfile
from pathlib import Path
from dataclasses import replace

from engine.domain import serde
from engine.domain.model import Proposition, PropositionKind, EvidenceSource
from engine.testing.fakes import ListEventSink
from tools.run_engine_games import run_games
from ml.suspicion import features as ft
from ml.suspicion import evaluate as ev


class Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sink = ListEventSink()
        run_games('base6', 12, 71, 'rule-v0', sink)
        cls.events = [serde.to_jsonable(e) for e in sink.items]

    def test_completed_town_rows_and_no_truth_features(self):
        rows, audit = ft.engine_rows(self.events)
        truths = {e['game_id']: e['payload']['roles'] for e in self.events
                  if e['event_type'] == 'truth_revealed'}
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(truths[r.game_id][r.observer], 'villager')
            self.assertNotIn(r.candidate, ('mudang', r.observer))
            self.assertEqual(r.label, int(truths[r.game_id][r.candidate] == 'possessed'))
            self.assertEqual(set(r.features), set(ft.FEATURE_NAMES))
        self.assertEqual(audit['legal_action_violations'], 0)
        self.assertEqual(audit['town_inference_violations'], 0)

    def test_truth_is_only_used_after_feature_extraction(self):
        rows, _ = ft.engine_rows(self.events)
        changed = copy.deepcopy(self.events)
        for e in changed:
            if e['event_type'] == 'truth_revealed':
                e['payload']['lies'] = ['fake-private-truth']
                e['payload']['secrets'] = {}
        altered, _ = ft.engine_rows(changed)
        self.assertEqual([r.features for r in rows], [r.features for r in altered])

    def test_incomplete_games_are_not_labelled(self):
        events = [e for e in self.events if e['event_type'] != 'truth_revealed']
        rows, audit = ft.engine_rows(events)
        self.assertEqual(rows, [])
        self.assertEqual(audit['excluded_incomplete_games'], 12)

    def test_truth_before_end_rejected(self):
        bad = copy.deepcopy(self.events)
        t = next(e for e in bad if e['event_type'] == 'truth_revealed')
        t['phase'] = 'meeting'
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_mixed_policy_rejected(self):
        bad = copy.deepcopy(self.events)
        bad[-1]['npc_policy_versions'] = {'x': 'other-policy'}
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_feature_allowlist_and_snapshot_completeness(self):
        rows, _ = ft.engine_rows(self.events)
        with self.assertRaises(ValueError):
            ft.validate_rows([replace(rows[0], features={**rows[0].features, 'truth': 1.0})] + rows[1:])
        with self.assertRaises(ValueError):
            ft.validate_rows(rows[1:])

    def test_candidate_and_observer_guards(self):
        bad = copy.deepcopy(self.events)
        obs = next(e for e in bad if e['event_type'] == 'observation'
                   and e['payload']['snapshot']['private']['role'] == 'villager')
        obs['payload']['snapshot']['candidates'].append('mudang')
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_missing_negative_candidate_in_snapshot_rejected(self):
        bad = copy.deepcopy(self.events)
        obs = next(e for e in bad if e['event_type'] == 'observation'
                   and e['payload']['snapshot']['private']['role'] == 'villager'
                   and len(e['payload']['snapshot']['candidates']) > 2)
        roles = next(e['payload']['roles'] for e in bad
                     if e['game_id'] == obs['game_id'] and e['event_type'] == 'truth_revealed')
        candidates = obs['payload']['snapshot']['candidates']
        candidates.remove(next(c for c in candidates if roles[c] == 'villager'))
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_decision_policy_mismatch_rejected(self):
        bad = copy.deepcopy(self.events)
        next(e for e in bad if e['event_type'] == 'decision')['payload']['policy_version'] = 'other'
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_truth_timestamp_before_snapshot_rejected(self):
        bad = copy.deepcopy(self.events)
        next(e for e in bad if e['event_type'] == 'truth_revealed')['occurred_at'] = '2000-01-01T00:00:00Z'
        with self.assertRaises(ValueError):
            ft.engine_rows(bad)

    def test_explicitly_incomplete_not_labelled(self):
        bad = copy.deepcopy(self.events)
        for e in bad:
            if e['event_type'] == 'game_ended':
                e['payload']['completed'] = False
        rows, audit = ft.engine_rows(bad)
        self.assertEqual(rows, [])
        self.assertEqual(audit['excluded_incomplete_games'], 12)

    def test_unknown_vote_target_is_counted_illegal(self):
        bad = copy.deepcopy(self.events)
        d = next(e for e in bad if e['event_type'] == 'decision' and e['payload']['kind'] == 'vote')
        d['payload']['target'] = 'unknown_npc'
        _, audit = ft.engine_rows(bad)
        self.assertEqual(audit['legal_action_violations'], 1)

    def test_split_disjoint_reproducible(self):
        rows, _ = ft.engine_rows(self.events)
        split = ev.split_games(rows, 7)
        self.assertEqual(split, ev.split_games(list(reversed(rows)), 7))
        self.assertEqual(set(split.values()), {'train', 'dev', 'test'})
        self.assertEqual(len(split), 12)

    def test_decision_audits_separate_illegal_and_town_reason(self):
        bad = copy.deepcopy(self.events)
        d = next(e for e in bad if e['event_type'] == 'decision'
                 and any(o['event_type'] == 'observation'
                         and o['payload']['snapshot_id'] == e['payload']['snapshot_id']
                         and o['payload']['snapshot']['private']['role'] == 'villager' for o in bad))
        d['payload']['kind'] = 'vote'
        d['payload']['target'] = d['payload']['actor']
        o = next(o for o in bad if o['event_type'] == 'observation'
                 and o['payload']['snapshot_id'] == d['payload']['snapshot_id'])
        o['payload']['snapshot']['beliefs']['propositions'].append(serde.to_jsonable(
            Proposition(PropositionKind.NOT_POSSESSED, d['payload']['actor'], EvidenceSource.OWN_WITNESS)))
        _, audit = ft.engine_rows(bad)
        self.assertGreater(audit['legal_action_violations'], 0)
        self.assertGreater(audit['town_inference_violations'], 0)


class Evaluation(unittest.TestCase):
    def test_stable_softmax_and_input_rejection(self):
        self.assertAlmostEqual(sum(ev.softmax([10000, 10001])), 1)
        self.assertGreater(ev.softmax([0, 1])[1], .5)
        for bad in ([], [math.nan], [math.inf]):
            with self.assertRaises(ValueError):
                ev.softmax(bad)

    def test_known_metrics(self):
        metrics = ev.metrics([[1, 0], [0, 1]], [[.8, .2], [.4, .6]])
        self.assertAlmostEqual(metrics['log_loss'], -(math.log(.8) + math.log(.6)) / 2)
        self.assertEqual(metrics['top1_accuracy'], 1)
        self.assertAlmostEqual(metrics['ece'], .3)
        with self.assertRaises(ValueError):
            ev.metrics([[1, 0]], [[.2, .2]])


class Training(unittest.TestCase):
    def test_training_uses_only_train_partition_and_real_estimators(self):
        from ml.suspicion.train import fit_models, model_scores
        sink = ListEventSink()
        run_games('base6', 7, 82, 'rule-v0', sink)
        rows, _ = ft.engine_rows([serde.to_jsonable(e) for e in sink.items])
        split = ev.split_games(rows, 7)
        models = fit_models(rows, split, 7)
        mutated = [replace(r, label=1-r.label) if split[r.game_id] != 'train' else r for r in rows]
        second = fit_models(mutated, split, 7)
        for name in ('logistic', 'lightgbm'):
            self.assertEqual(model_scores(models[name], rows), model_scores(second[name], rows))
            self.assertTrue(all(math.isfinite(s) for s in model_scores(models[name], rows)))

    def test_manifest_rejects_modified_rows_and_policy_mix(self):
        from ml.suspicion.train import load_dataset
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'rows.jsonl'
            p.write_text('{}\n', encoding='utf-8')
            m = dict(rows_sha256='0'*64, npc_policy_version='rule-v0',
                     feature_version=ft.FEATURE_VERSION)
            (p.parent/'manifest.json').write_text(json.dumps(m), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_dataset(p, p.parent/'manifest.json')


if __name__ == '__main__':
    unittest.main()
