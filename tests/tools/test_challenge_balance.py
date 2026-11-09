"""E-09-08 선택형 도전 승률 측정 도구의 집계·판정 규칙과 재현성."""
import unittest

from tools.perf.challenge_balance import flat_metrics, run, summarize, verdict, wilson


def game(winner="village", days=2, vote_days=2, no_lock=0, div=1, conf=0):
    return {"error": None, "winner": winner, "days": days, "vote_days": vote_days, "no_lock_days": no_lock,
            "divinations": div, "confessions": conf}


class Rules(unittest.TestCase):
    def test_wilson(self):
        self.assertEqual(wilson(0, 0), (0.0, 0.0))
        lo, hi = wilson(150, 300)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertAlmostEqual((lo + hi) / 2, 0.5, places=2)

    def test_summarize_counts_errors_as_failures(self):
        s = summarize([game(), game("possessed", days=3, no_lock=1), {"error": "reject vote x"}])
        self.assertEqual((s["games"], s["completed"], s["run_failures"]), (3, 2, 1))
        self.assertEqual(s["village_win"], 0.5)
        self.assertEqual(s["max_days"], 3)
        self.assertEqual(s["no_lock_day_rate"], 0.25)

    def test_verdict_needs_every_bot_lower_and_zero_failures(self):
        base = {"logic": {"village_win": 0.9}, "random": {"village_win": 0.7}}
        lower = {"logic": {"village_win": 0.8, "run_failures": 0}, "random": {"village_win": 0.6, "run_failures": 0}}
        self.assertEqual(verdict(base, lower, 0)["decision"], "공개")
        self.assertEqual(verdict(base, lower, 1)["decision"], "PM 판단")
        mixed = {**lower, "random": {"village_win": 0.7, "run_failures": 0}}
        v = verdict(base, mixed, 0)
        self.assertEqual(v["decision"], "PM 판단")
        self.assertEqual(v["lower_than_base"], {"logic": True, "random": False})


class SmallRun(unittest.TestCase):
    def test_reproducible_and_flat_metrics(self):
        a = run(2, 3, ["logic"], ["base6", "base6_div1"])
        b = run(2, 3, ["logic"], ["base6", "base6_div1"])
        self.assertEqual(a, b)
        self.assertEqual(a["presets"]["base6_div1"]["check_failures"], 0)
        self.assertIn("verdict", a["presets"]["base6_div1"])
        m = flat_metrics(a)
        self.assertIn("base6_div1_logic_village_win", m)
        self.assertEqual(m["base6_logic_mean_days"]["unit"], "day")


if __name__ == "__main__":
    unittest.main()
