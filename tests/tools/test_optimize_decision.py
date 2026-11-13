"""A-08-03 결정 층 최적화 도구 테스트: 격자 표본, 윌슨 구간, 후보 선택, 판 묶음 나누기, 소규모 실행 재현."""
from __future__ import annotations

import contextlib
import io
import random
import unittest

from engine.domain import params
from tools import optimize_decision as od

SPACE = {"a": (0.0, 0.5, 1.0), "b": (0.1, 0.2)}
BASE = {"a": 0.5, "b": 0.1}


class PureFunctions(unittest.TestCase):
    def test_sample_grid_seeded_distinct_and_capped(self):
        one = od.sample_grid(SPACE, 4, random.Random(3))
        self.assertEqual(one, od.sample_grid(SPACE, 4, random.Random(3)))       # 같은 시드면 같은 점
        self.assertEqual(len({tuple(p.values()) for p in one}), 4)               # 서로 다름
        self.assertTrue(all(p["a"] in SPACE["a"] and p["b"] in SPACE["b"] for p in one))
        self.assertEqual(len(od.sample_grid(SPACE, 100, random.Random(0))), 6)   # 격자 크기로 자름

    def test_wilson(self):
        lo, hi = od.wilson(500, 1000)
        self.assertAlmostEqual(lo, 0.4691, places=3)
        self.assertAlmostEqual(hi, 0.5309, places=3)
        self.assertEqual(od.wilson(0, 0), (0.0, 1.0))
        lo, hi = od.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertLess(hi, 0.35)

    def test_in_target_inclusive(self):
        self.assertTrue(od.in_target(0.40, (0.40, 0.60)))
        self.assertTrue(od.in_target(0.60, (0.40, 0.60)))
        self.assertFalse(od.in_target(0.61, (0.40, 0.60)))

    def test_choose_nearest_mid_then_closest_to_base(self):
        c = [{"point": {"a": 1.0, "b": 0.2}, "win_rate": 0.52},
             {"point": {"a": 0.5, "b": 0.2}, "win_rate": 0.48},     # 중앙 거리 같음, 기준값과 더 가까움
             {"point": {"a": 0.0, "b": 0.1}, "win_rate": 0.70}]     # 구간 밖
        self.assertEqual(od.choose(c, (0.40, 0.60), BASE, SPACE)["point"], {"a": 0.5, "b": 0.2})
        self.assertIsNone(od.choose(c[2:], (0.40, 0.60), BASE, SPACE))
        # 난이도 구간(쉬움 근처)도 같은 함수로 고른다
        self.assertEqual(od.choose(c, (0.65, 0.75), BASE, SPACE)["point"], {"a": 0.0, "b": 0.1})

    def test_chunks_cover_all_games_once(self):
        for games, workers in ((10, 3), (1, 8), (1000, 8), (7, 1)):
            got = od.chunks(games, workers)
            self.assertLessEqual(len(got), workers)
            self.assertEqual(sorted(i for s, c in got for i in range(s, s + c)), list(range(games)))

    def test_space_contains_rule_v0_base(self):
        base = od.base_point()
        self.assertEqual(set(base), set(od.SPACE))
        rule_v0 = {"conformity_scale": 0.2, "vote_noise": 0.02, "q_max": 0.5, "witness_prob": 0.7,
                   "tell_prob": 0.5, "routine_randomness": 0.0}
        for k, v in rule_v0.items():
            self.assertIn(v, od.SPACE[k])


class Arguments(unittest.TestCase):
    def test_params_out_requires_version(self):
        """난이도별 파일은 서로 다른 버전이어야 판 메타(rule_version)로 구분된다."""
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            od.main(["--params-out", "out/x.json"])

    def test_bad_target_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            od.main(["--target", "0.6", "0.4"])


class SmallRun(unittest.TestCase):
    def setUp(self):
        self._saved = params.current()

    def tearDown(self):
        params.apply(self._saved)

    def test_evaluate_is_reproducible_and_chunking_invariant(self):
        """같은 시드면 같은 결과, 직렬 한 묶음과 여러 묶음 합산이 같다. 마을 추론 위반 0건."""
        pt = od.base_point()
        a = od.evaluate([pt], "base6", "logic", 5, 6, None, 1)[0]
        b = od.evaluate([pt], "base6", "logic", 5, 6, None, 1)[0]
        self.assertEqual(a, b)
        parts = od.Tally()
        for s, c in od.chunks(6, 3):
            parts.add(od.run_chunk((pt, "base6", "logic", 5, s, c)))
        self.assertEqual((parts.games, parts.village_wins, parts.violations), (6, a["village_wins"], 0))
        self.assertEqual(a["violations"], 0)
        self.assertGreater(a["villager_votes"], 0)


if __name__ == "__main__":
    unittest.main()
