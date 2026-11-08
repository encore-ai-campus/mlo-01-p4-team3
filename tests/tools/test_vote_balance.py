"""`tools/perf/vote_balance.py` 집계(도메인)와 판 진행 1회(응용) 시험(A-09-06)."""
from __future__ import annotations

import unittest

from tools.perf import vote_balance as vb


def _run(winner="village", days=2, lock_day=None, d1=5, silence=2, sus=1):
    return {"winner": winner, "days": days, "mudang_locked": lock_day is not None, "mudang_locked_day": lock_day,
            "npc_speeches_d1": d1, "npc_silence_d1": silence, "npc_suspect_npc_d1": sus}


class Summarize(unittest.TestCase):
    def test_rates(self):
        s = vb.summarize([_run(), _run(winner="changgwi", days=1, lock_day=1), _run(lock_day=2, d1=5, silence=3)])
        self.assertEqual(s["games"], 3)
        self.assertEqual(s["village_win"], round(2 / 3, 3))
        self.assertEqual(s["mudang_locked"], round(2 / 3, 3))
        self.assertEqual(s["mudang_locked_d1"], round(1 / 3, 3))
        self.assertEqual(s["mean_days"], round(5 / 3, 3))
        self.assertEqual(s["silence_rate_d1"], round(7 / 15, 3))
        self.assertEqual(s["npc_suspect_npc_d1_per_game"], 1.0)

    def test_empty_and_no_speech(self):
        self.assertEqual(vb.summarize([]), {})
        self.assertIsNone(vb.summarize([_run(d1=0, silence=0)])["silence_rate_d1"])

    def test_flat_metrics_skips_games_and_none(self):
        m = vb.flat_metrics({"bots": {"passive": {"games": 2, "village_win": 0.5, "silence_rate_d1": None}}})
        self.assertEqual(m, {"passive_village_win": {"value": 0.5, "unit": ""}})


class SmallRun(unittest.TestCase):
    def test_reproducible(self):
        a = vb.run(2, 3, ["passive"])
        b = vb.run(2, 3, ["passive"])
        self.assertEqual(a, b)
        self.assertEqual(a["bots"]["passive"]["games"], 2)


if __name__ == "__main__":
    unittest.main()
