"""Prove the no-lock upper day bound on the real engine's complete tied-vote path."""
from pathlib import Path
import random
import unittest

from engine.domain import params
from engine.domain.engine import BaselinePolicy, PaldoEngine
from engine.domain.model import PRESETS, Command, CommandEnvelope, CommandKind, DataSource, GameMeta, PLAYER_ID
from engine.domain.observation import Decision, ReasonCode
from engine.ports import EngineDeps
from tools.check_content import load_bundle
from tools.perf.vote_balance import PassiveBot
from ml.preset_report import no_lock_day_limit, secret_combination


def cycle_target(actor, active):
    """각 활동 인물에게 표가 한 장씩 가도록 다음 사람을 고른다."""
    ordered = sorted(active)
    return ordered[(ordered.index(actor) + 1) % len(ordered)]


class CyclePolicy(BaselinePolicy):
    """매일 일반·결선 모두 동점으로 만드는 통제 정책."""
    def vote(self, obs, candidates, rng):
        """자기 표를 순환의 다음 인물에게 보낸다."""
        return Decision(cycle_target(obs.observer, candidates), [ReasonCode.SCRIPTED])


class NoLockBounds(unittest.TestCase):
    """무감금 상한과 비밀 조합 식별자의 경계를 검증한다."""
    def test_every_day_can_end_without_locking_anyone_and_still_terminates_at_the_bound(self):
        """감금 0건의 전체 판이 규칙에서 계산한 마지막 날에 종료된다."""
        content = load_bundle(Path(__file__).resolve().parents[1] / "content/bundle_v1.json")
        for preset_id in ("short5", "base6", "base7", "large8"):
            with self.subTest(preset=preset_id):
                engine = PaldoEngine(CyclePolicy())
                deps = EngineDeps(content, None)
                meta = GameMeta("no_lock_" + preset_id, PRESETS[preset_id], 7, DataSource.BOT,
                                params.RULE_PARAMS_VERSION, "test", content.content_version)
                transition = engine.new_game(meta, deps)
                state, truth = transition.state, transition.truth
                bot, rng = PassiveBot(), random.Random(7)
                for i in range(500):
                    if state.result:
                        break
                    view = engine.player_view(state, content)
                    command = (Command(CommandKind.VOTE, PLAYER_ID, target=cycle_target(PLAYER_ID, view.active))
                               if CommandKind.VOTE in view.legal_kinds else bot.choose(view, rng))
                    transition = engine.apply(state, truth, CommandEnvelope(meta.game_id, str(i),
                                              state.state_version, command), deps)
                    self.assertTrue(transition.accepted, transition.reject)
                    state, truth = transition.state, transition.truth
                self.assertIsNotNone(state.result)
                self.assertEqual(state.result.day, no_lock_day_limit(meta.preset))
                self.assertEqual(state.result.reason.value, "possessed_parity")
                self.assertEqual(len(state.public.locks), state.result.day)
                self.assertTrue(all(lock.locked is None for lock in state.public.locks))

    def test_combinations_include_holders_and_secret_ids(self):
        """같은 비밀 목록도 보유자가 바뀌면 다른 조합으로 본다."""
        first = secret_combination("base6", {"a": "s1", "b": "s2"})
        self.assertEqual(first, secret_combination("base6", {"b": "s2", "a": "s1"}))
        self.assertNotEqual(first, secret_combination("base6", {"a": "s2", "b": "s1"}))
