"""Self-play reward boundaries, legality and frozen evaluation reproducibility."""
from copy import deepcopy
import unittest

from engine.domain.npc import RuleV0Policy
from engine.testing.fakes import FixtureContentSource
from ml.selfplay import SelfPlayPolicy, episode


class SelfPlayTests(unittest.TestCase):
    """진영별 학습과 안전·평가 고정의 경계를 검사한다."""
    def test_first_visit_reward_is_per_faction(self):
        """같은 판의 중복 방문을 한 번만 보상하고 진영별 승패를 구분한다."""
        policy = SelfPlayPolicy(FixtureContentSource().load("*"))
        village = ("villager", 1, "vote", 2, 3)
        possessed = ("possessed", 1, "vote", 2, 3)
        policy.episode = [(village, 0), (village, 0), (possessed, 1)]
        policy.learn("village")
        self.assertEqual(policy.values[village][0], 1)
        self.assertEqual(policy.values[possessed][1], 0)
        self.assertEqual(policy.counts[village][0], 1)
        self.assertEqual(policy.episode, [])

    def test_both_factions_train_and_evaluation_freezes_the_table(self):
        """양쪽 진영을 학습한 뒤 같은 시드의 고정 정책 재현을 확인한다."""
        content = FixtureContentSource().load("*")
        policy = SelfPlayPolicy(content)
        policy.training = True
        for seed in range(3):
            run = episode(policy, content, seed, f"train{seed}")
            policy.learn(run.winner)
        self.assertEqual({key[0] for key in policy.values}, {"villager", "possessed"})
        before = deepcopy(policy.values)
        policy.training = False
        first = episode(policy, content, 12, "evaluation")
        second = episode(policy, content, 12, "evaluation")
        self.assertEqual(first, second)
        self.assertEqual(policy.values, before)
        self.assertEqual(policy.episode, [])
        self.assertEqual(first.illegal_commands, 0)
        self.assertIsInstance(policy, RuleV0Policy)


    def test_empty_safety_pool_always_uses_maximum_suspicion(self):
        """안전 층이 후보를 모두 제외하면 최대 의심도 폴백을 유지한다."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from engine.domain.model import Role
        policy = SelfPlayPolicy(FixtureContentSource().load("*"))
        obs = SimpleNamespace(observer="observer", private=SimpleNamespace(role=Role.POSSESSED))
        beliefs = SimpleNamespace(suspicion={"a": 1, "b": 2}, trust_in_mudang=50)
        with patch.object(policy, "update_beliefs", return_value=beliefs), \
                patch("ml.selfplay.vt.safe_pool", return_value=([], [])):
            outcome = policy.vote_with_model(obs, ["a", "b", "observer"], None, None)
        self.assertEqual(outcome.decision.value, "b")
        self.assertIn("fallback_max_suspicion", [code.value for code in outcome.decision.reason_codes])
