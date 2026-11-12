"""11/12 운영 연결: 확정 팔 파일, 판별 팔 결정, 최신 버전 규칙(D-09-02)."""
import json
import tempfile
import unittest
from pathlib import Path

from ml import bandit


class ArmFileTests(unittest.TestCase):
    def test_repo_arm_file_is_valid_and_mongo_safe(self):
        """저장소의 확정 팔은 모두 StrategyMix로 읽히고, 팔 ID는 Mongo 필드 이름으로 쓸 수 있다."""
        arms = bandit.load_arms()
        self.assertGreaterEqual(len(arms), 2)
        self.assertEqual(sorted(bandit.initial_state(arms)['arms']), sorted(arms))
        for arm in arms:
            self.assertRegex(arm, r'^[A-Za-z0-9_:-]+$')

    def test_one_sided_frame_arms_are_excluded(self):
        """모의 판에서 마을 승률이 0.9를 넘은 '모순·의심 주민만 겨누는' 팔은 확정 목록에 없다(11/12 사용자 결정)."""
        for arm, mix in bandit.load_arms().items():
            targets = {k for k, v in mix['frame_target'].items() if v > 0}
            self.assertIn('mudang', targets, arm)

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
        self.assertEqual(set(arms), set(state['arms']))   # 균등 비율 200판이면 모든 팔이 나온다
        self.assertEqual(source.mix_for('g0').mix_id, arms[0])


if __name__ == '__main__':
    unittest.main()
