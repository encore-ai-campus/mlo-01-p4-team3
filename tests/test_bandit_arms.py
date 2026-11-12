"""11/12 운영 연결: 확정 팔 파일, 판별 팔 결정, 최신 버전 규칙(D-09-02)."""
import json
import tempfile
import unittest
from pathlib import Path

from ml import bandit


class ArmFileTests(unittest.TestCase):
    def test_repo_arm_file_matches_default_portfolio(self):
        """저장소의 확정 팔 18개는 기본 포트폴리오와 같고 초기 상태가 받아들인다."""
        arms = bandit.load_arms()
        self.assertEqual(len(arms), 18)
        self.assertEqual(bandit.initial_state(arms)['arms'], bandit.initial_state()['arms'])

    def test_invalid_arm_file_is_rejected(self):
        """빈 객체나 StrategyMix가 아닌 값은 읽을 때 실패한다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'arms.json'
            for value in ({}, [], {'a': {'lie_type': {'bogus': 1.0}}}):
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises((ValueError, TypeError)):
                    bandit.load_arms(path)


class ArmChoiceTests(unittest.TestCase):
    def test_latest_version_is_bandit_zero_without_publication(self):
        state = bandit.initial_state(bandit.load_arms())
        self.assertEqual(bandit.latest_version(state), 'bandit-0')
        state['versions'].append(dict(version='bandit-1', ratios={}))
        self.assertEqual(bandit.latest_version(state), 'bandit-1')

    def test_arm_is_deterministic_per_game_and_version_and_matches_mix(self):
        """같은 판 ID·버전이면 같은 팔이고, mix_for는 그 팔의 혼합 비율을 돌려준다."""
        state = bandit.initial_state(bandit.load_arms())
        source = bandit.VersionedMixSource(state, 'bandit-0')
        arms = [source.arm_for(f'g{i}') for i in range(200)]
        self.assertEqual(arms, [bandit.VersionedMixSource(state, 'bandit-0').arm_for(f'g{i}') for i in range(200)])
        self.assertGreater(len(set(arms)), 10)   # 균등 비율이면 여러 팔이 고루 나온다
        self.assertEqual(source.mix_for('g0').mix_id, arms[0])


if __name__ == '__main__':
    unittest.main()
