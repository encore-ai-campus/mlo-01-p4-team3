"""`tools.perf.int07_scenarios`: 찾은 시드·명령 순서가 결정적으로 같은 반례 상황을 만든다(INT-07 #2 웹 재현 준비)."""
import unittest

from tools.perf.int07_scenarios import Lab, play, replay, search

# 11/8 탐색 결과(`python -m tools.perf.int07_scenarios`)의 시드·전략 번호
KNOWN = {"false_reveal": (2, 2), "last_possessed": (10, 0), "confession_switch": (6, 3)}


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = Lab()

    def test_known_seeds_reproduce_and_replay(self):
        for goal, (seed, strategy) in KNOWN.items():
            with self.subTest(goal=goal):
                found = play(self.lab, goal, seed, strategy)
                self.assertIsNotNone(found)
                self.assertTrue(replay(self.lab, found))

    def test_search_returns_shortest_for_seed(self):
        best = search(self.lab, "last_possessed", seeds=[10], strategies=6, stop_after=0)
        self.assertEqual(best["seed"], 10)
        self.assertTrue(replay(self.lab, best))

    def test_replay_rejects_wrong_target(self):
        found = play(self.lab, "false_reveal", *KNOWN["false_reveal"])
        self.assertFalse(replay(self.lab, dict(found, target="nobody")))


if __name__ == "__main__":
    unittest.main()
