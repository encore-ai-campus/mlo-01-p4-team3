"""Lifecycle acceptance boundaries; reports below are fixtures, not human evidence."""
import copy
import unittest

from ml import gate


def suspicion():
    return {s: dict(games=50, log_loss=.51, ece=.05, village_win_rate=.55)
            for s in ('benchmark', 'human')}


def intent():
    return dict(dataset='test_human', rows=200, command_exact_match=.8, macro_f1=.8,
                unsupported_false_positive_rate=.1, contrast_accuracy=.8,
                slot_errors={s: .1 for s in gate.INTENT_SLOTS})


class GateTests(unittest.TestCase):
    def test_suspicion_all_limits_inclusive_and_each_failure_keeps_rule_v0(self):
        ref = suspicion()
        for r in ref.values():
            r.update(log_loss=.5, village_win_rate=.5)
        self.assertTrue(gate.suspicion_gate(suspicion(), ref).accepted)
        for split in ('benchmark', 'human'):
            for field, value in (('log_loss', .510001), ('ece', .050001),
                                 ('village_win_rate', .550001), ('village_win_rate', .449999)):
                candidate = suspicion()
                candidate[split][field] = value
                decision = gate.suspicion_gate(candidate, ref)
                self.assertFalse(decision.accepted, (split, field))
                self.assertEqual(decision.serving_version, 'rule-v0')
                self.assertTrue(decision.reasons)
        self.assertFalse(gate.suspicion_gate({'benchmark': ref['benchmark']}, ref).accepted)
        # 사람 판 시험 세트가 양쪽 모두 없으면 봇 판 시험 세트만으로 판단한다(11/11 사용자 결정).
        bench_only = {'benchmark': copy.deepcopy(ref['benchmark'])}
        self.assertTrue(gate.suspicion_gate({'benchmark': suspicion()['benchmark']}, bench_only).accepted)
        worse = {'benchmark': dict(suspicion()['benchmark'], log_loss=.510001)}
        self.assertFalse(gate.suspicion_gate(worse, bench_only).accepted)
        self.assertFalse(gate.suspicion_gate(suspicion(), bench_only).accepted)

    def test_small_suspicion_requires_nonoverlapping_loss_intervals_in_both_sets(self):
        candidate, ref = suspicion(), suspicion()
        for report in candidate.values():
            report.update(games=49, log_loss=.3, bootstrap_95={'log_loss': [.2, .4]})
        for report in ref.values():
            report.update(games=49, bootstrap_95={'log_loss': [.41, .6]})
        self.assertTrue(gate.suspicion_gate(candidate, ref).accepted)
        for ci in ([.4, .6], [.3, .6], None):
            ref['human']['bootstrap_95'] = {'log_loss': ci}
            self.assertFalse(gate.suspicion_gate(candidate, ref).accepted)

    def test_intent_each_metric_and_slot_limit(self):
        ref = intent()
        candidate = intent()
        candidate['slot_errors'] = {s: .12 for s in gate.INTENT_SLOTS}
        self.assertTrue(gate.intent_gate(candidate, ref).accepted)
        for field, value in (('command_exact_match', .799999), ('macro_f1', .799999),
                             ('unsupported_false_positive_rate', .100001), ('contrast_accuracy', .799999)):
            bad = copy.deepcopy(candidate)
            bad[field] = value
            result = gate.intent_gate(bad, ref, current_version='operating-v2')
            self.assertFalse(result.accepted, field)
            self.assertEqual(result.serving_version, 'operating-v2')
        for slot in gate.INTENT_SLOTS:
            bad = copy.deepcopy(candidate)
            bad['slot_errors'][slot] = .120001
            self.assertFalse(gate.intent_gate(bad, ref).accepted, slot)

    def test_intent_small_sample_and_missing_or_invalid_measurements_fail_closed(self):
        candidate, ref = intent(), intent()
        candidate.update(rows=199, command_exact_match=.9, command_exact_match_ci95=[.85, .95])
        ref.update(rows=199, command_exact_match_ci95=[.7, .8])
        self.assertTrue(gate.intent_gate(candidate, ref).accepted)
        for ci in ([.7, .85], [.7, .9], None):
            ref['command_exact_match_ci95'] = ci
            self.assertFalse(gate.intent_gate(candidate, ref).accepted)
        for field, value in (('dataset', 'dev'), ('rows', 0), ('macro_f1', float('nan')),
                             ('contrast_accuracy', None), ('slot_errors', {})):
            bad = intent()
            bad[field] = value
            self.assertFalse(gate.intent_gate(bad, intent()).accepted, field)
        self.assertEqual(gate.intent_gate(intent(), intent()).reference_version, 'rule-baseline')


if __name__ == '__main__':
    unittest.main()
