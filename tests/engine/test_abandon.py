"""판 중단 규칙(11/10 사용자 결정): 진행 중인 판을 지우지 않고 completed=false로 끝낸다."""
from __future__ import annotations

import unittest

from engine.domain import params, serde
from engine.domain.abandon import AlreadyEnded, abandon
from engine.domain.engine import PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import (PLAYER_ID, PRESETS, Command, CommandEnvelope, CommandKind, DataSource, EndReason,
                                 Faction, GameMeta, GameResult, Phase, RejectCode,
                                 winner_of)
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource

CONTENT = FixtureContentSource().load("*")


def started():
    meta = GameMeta("g_abandon", PRESETS["base6"], 3, DataSource.HUMAN, params.RULE_PARAMS_VERSION, "test",
                    CONTENT.content_version)
    return PaldoEngine().new_game(meta, EngineDeps(CONTENT, FakeSuspicionModel())).state


class Abandon(unittest.TestCase):
    def test_abandon_ends_game_without_winner(self):
        """새 상태는 버전 +1·ended·승자 없음·completed=false이고 입력 상태는 그대로다."""
        state = started()
        before = serde.dumps(state)
        new_state, events = abandon(state)
        self.assertEqual(serde.dumps(state), before)
        self.assertEqual(new_state.state_version, state.state_version + 1)
        self.assertEqual(new_state.phase, Phase.ENDED)
        self.assertEqual(new_state.result, GameResult(None, EndReason.ABORTED, state.day, completed=False))
        self.assertEqual((new_state.turn_order, new_state.pending_votes), ([], {}))

    def test_single_game_ended_event_without_truth(self):
        """이벤트는 game_ended 1건(winner=null, reason=aborted, completed=false)이고 진실 이벤트는 없다."""
        _, events = abandon(started())
        self.assertEqual([e.event_type for e in events], [EventType.GAME_ENDED])
        self.assertEqual(events[0].payload, {"winner": None, "reason": "aborted", "completed": False})
        self.assertEqual(events[0].phase, Phase.ENDED)

    def test_already_ended_game_rejected(self):
        """완주했거나 이미 중단한 판은 다시 중단하지 않는다."""
        aborted, _ = abandon(started())
        finished = started()
        finished.result = GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 2)
        for state in (aborted, finished):
            with self.subTest(reason=state.result.reason), self.assertRaises(AlreadyEnded):
                abandon(state)

    def test_commands_rejected_after_abandon(self):
        """중단한 판에는 어떤 명령도 받지 않는다(GAME_ENDED)."""
        engine = PaldoEngine()
        state = started()
        aborted, _ = abandon(state)
        deps = EngineDeps(CONTENT, FakeSuspicionModel())
        truth = engine.new_game(state.meta, deps).truth
        env = CommandEnvelope(state.meta.game_id, "c1", aborted.state_version, Command(CommandKind.ADVANCE, PLAYER_ID))
        result = engine.apply(aborted, truth, env, deps)
        self.assertEqual((result.accepted, result.reject), (False, RejectCode.GAME_ENDED))

    def test_aborted_state_round_trips_and_has_no_winner(self):
        """저장 직렬화(winner=None 포함)가 왕복되고, 중단 사유에는 승자를 매기지 않는다."""
        new_state, _ = abandon(started())
        self.assertEqual(serde.loads(type(new_state), serde.dumps(new_state)).result, new_state.result)
        with self.assertRaises(ValueError):
            winner_of(EndReason.ABORTED)


if __name__ == "__main__":
    unittest.main()
