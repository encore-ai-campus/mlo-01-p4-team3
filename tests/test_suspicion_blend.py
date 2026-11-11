"""의심 점수 섞기(α) 규칙과 서빙 하위 호환 시험."""
import math
import pickle
import tempfile
import unittest
from pathlib import Path

from engine.testing.fakes import FakeSuspicionModel
from ml.suspicion.blend import ALPHA_GRID, blend, check_alpha, effectiveness, select_alpha
from tests.contract.test_fakes import obs


class BlendRule(unittest.TestCase):
    def test_grid_is_fixed_and_covers_both_ends(self):
        self.assertEqual(ALPHA_GRID[0], 0.0)
        self.assertEqual(ALPHA_GRID[-1], 1.0)
        self.assertEqual(len(ALPHA_GRID), 11)

    def test_blend_endpoints(self):
        self.assertEqual(blend(3.25, -1.0, 1.0), 3.25)      # 모델 로짓 그대로
        self.assertEqual(blend(3.25, -1.0, 0.0), -1.0)      # 규칙 점수 그대로
        self.assertAlmostEqual(blend(2.0, 4.0, 0.25), 3.5)

    def test_alpha_validation(self):
        for bad in (-0.1, 1.1, math.nan, math.inf, True, "0.5", None):
            with self.assertRaises(ValueError):
                check_alpha(bad)
        self.assertEqual(check_alpha(1), 1.0)

    def test_select_lowest_loss_among_passing(self):
        trials = [dict(alpha=1.0, reasons=["benchmark:village_win_rate"], log_loss=0.70),
                  dict(alpha=0.6, reasons=[], log_loss=0.80),
                  dict(alpha=0.4, reasons=[], log_loss=0.85),
                  dict(alpha=0.0, reasons=[], log_loss=1.06)]
        self.assertEqual(select_alpha(trials), 0.6)
        self.assertEqual(select_alpha(list(reversed(trials))), 0.6)  # 순서 무관

    def test_tie_prefers_larger_alpha_and_none_when_all_fail(self):
        self.assertEqual(select_alpha([dict(alpha=0.3, reasons=[], log_loss=0.8),
                                       dict(alpha=0.5, reasons=[], log_loss=0.8)]), 0.5)
        self.assertIsNone(select_alpha([dict(alpha=0.5, reasons=["benchmark:ece"], log_loss=0.8)]))
        self.assertIsNone(select_alpha([dict(alpha=0.5, reasons=[], log_loss=math.nan)]))

    def test_effective_candidate_needs_alpha_and_gain(self):
        self.assertEqual(effectiveness(0.6, 0.80, 1.06), [])
        self.assertEqual(effectiveness(0.3, 0.80, 0.81), [])            # 경계값은 유효
        self.assertEqual(effectiveness(0.2, 0.80, 1.06), ["alpha_too_small"])
        self.assertEqual(effectiveness(0.0, 1.06, 1.06), ["alpha_too_small", "no_meaningful_gain"])
        self.assertEqual(effectiveness(0.8, 1.055, 1.06), ["no_meaningful_gain"])
        self.assertEqual(effectiveness(0.8, math.nan, 1.06), ["no_meaningful_gain"])
        self.assertEqual(effectiveness(None, math.nan, 1.06), ["no_passing_alpha"])


class ArtifactCompat(unittest.TestCase):
    """서빙 산출물: α가 없으면 기존 동작(α=1), α=0이면 규칙 의심도(FakeSuspicionModel)와 같은 확률."""

    @classmethod
    def setUpClass(cls):
        from sklearn.linear_model import LogisticRegression
        from ml.suspicion.features import FEATURE_NAMES, FEATURE_VERSION
        n = len(FEATURE_NAMES)
        x = [[(i * 7 + j * 3) % 5 / 4 for j in range(n)] for i in range(20)]
        y = [i % 2 for i in range(20)]
        model = LogisticRegression(max_iter=200).fit(x, y)
        cls.base = dict(models={"logistic": model}, temperatures={"logistic": 2.0},
                        feature_version=FEATURE_VERSION, feature_names=FEATURE_NAMES, code_commit="test")
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def load(self, name, **extra):
        from ml.adapters.suspicion_model import _ArtifactModel
        path = Path(self.tmp.name) / name
        path.write_bytes(pickle.dumps({**self.base, **extra}))
        return _ArtifactModel(path)

    def test_missing_alpha_equals_alpha_one(self):
        snapshot = obs(["a", "b", "c"], 1, {"a": 80.0, "b": 40.0, "c": 55.0})
        old, one = self.load("old.pkl").estimate(snapshot), self.load("one.pkl", blend_alpha=1.0).estimate(snapshot)
        for c in "abc":
            self.assertEqual(old.probs[c], one.probs[c])

    def test_alpha_zero_matches_rule_suspicion(self):
        snapshot = obs(["a", "b", "c"], 1, {"a": 80.0, "b": 40.0, "c": 55.0})
        zero = self.load("zero.pkl", blend_alpha=0.0).estimate(snapshot)
        rule = FakeSuspicionModel().estimate(snapshot)
        for c in "abc":
            self.assertAlmostEqual(zero.probs[c], rule.probs[c], places=12)

    def test_invalid_alpha_rejected_at_load(self):
        with self.assertRaises(ValueError):
            self.load("bad.pkl", blend_alpha=1.5)


if __name__ == "__main__":
    unittest.main()
