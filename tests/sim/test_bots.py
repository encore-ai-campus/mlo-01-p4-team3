"""D-06-01 플레이어 봇: 공개 정보·합법 후보·정책 차이."""
import copy
import random
import unittest

from engine.domain.model import (
    PLAYER_ID, Claim, ClaimType, CommandKind, ConfessionRecord, DivinationResult, Phase, Place,
    PlayerState, PublicRecord, Slot, TellRecord,
)
from engine.ports import PlayerView
from engine.sim.bots import LogicBot, RandomBot, RepeatSuspectBot, LiePressureBot, legal_commands


def view(phase=Phase.ROUND1, kinds=None):
    return PlayerView("g", 0, 1, phase, [PLAYER_ID, "a", "b"], [], [], PublicRecord(),
        PlayerState(), kinds or [CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.SILENCE],
        PLAYER_ID, [], None, {"a": "갑", "b": "을"})


class Bots(unittest.TestCase):
    def test_same_seed_and_view_not_mutated(self):
        v = view()
        before = copy.deepcopy(v)
        for bot_type in (RandomBot, LogicBot, RepeatSuspectBot, LiePressureBot):
            a, b = bot_type(), bot_type()
            ra, rb = random.Random(17), random.Random(17)
            self.assertEqual([a.choose(v, ra) for _ in range(8)], [b.choose(v, rb) for _ in range(8)])
        self.assertEqual(v, before)

    def test_runoff_no_self_no_locked_or_missing(self):
        v = view(Phase.RUNOFF, [CommandKind.VOTE])
        v.active += ["c"]
        v.locked, v.missing, v.runoff_candidates = ["locked"], ["missing"], [PLAYER_ID, "b"]
        self.assertEqual({c.target for c in legal_commands(v)}, {"b"})
        for bot_type in (RandomBot, LogicBot, RepeatSuspectBot, LiePressureBot):
            self.assertEqual(bot_type().choose(v, random.Random(1)).target, "b")

    def test_divination_reveal_requires_own_result(self):
        v = view(kinds=[CommandKind.REVEAL_DIVINATION, CommandKind.SILENCE])
        self.assertFalse(any(c.kind is CommandKind.REVEAL_DIVINATION for c in legal_commands(v)))
        v.player.divinations = {0: {"a": DivinationResult.CLEAR}}
        reveals = [c for c in legal_commands(v) if c.kind is CommandKind.REVEAL_DIVINATION]
        self.assertEqual(len(reveals), 1)
        self.assertEqual(reveals[0].divination.result, DivinationResult.CLEAR)
        self.assertEqual(LogicBot().choose(v, random.Random(1)).kind, CommandKind.REVEAL_DIVINATION)

    def test_logic_uses_own_clear_result_and_public_confession(self):
        v = view(Phase.VOTE, [CommandKind.VOTE])
        v.player.divinations = {0: {"a": DivinationResult.CLEAR, "b": DivinationResult.MURKY}}
        self.assertEqual(LogicBot().choose(v, random.Random(2)).target, "b")
        v.active.append("c")
        v.public.confessions = [ConfessionRecord(1, "b", "public-secret")]
        self.assertEqual(LogicBot().choose(v, random.Random(2)).target, "c")

    def test_repeat_target_persists_until_inactive(self):
        v, bot, rng = view(), RepeatSuspectBot(), random.Random(4)
        target = bot.choose(v, rng).target
        self.assertEqual(bot.choose(v, rng).target, target)
        v.active.remove(target)
        self.assertNotEqual(bot.choose(v, rng).target, target)

    def test_lie_pressure_uses_real_public_pair_and_tell(self):
        v = view(Phase.INTERROGATION, [CommandKind.INTERROGATE, CommandKind.ADVANCE])
        v.public.claims = [Claim("c1", "a", ClaimType.WHEREABOUTS, "a", 0, Slot.MIDNIGHT, Place.WELL),
                           Claim("c2", "a", ClaimType.WHEREABOUTS, "a", 0, Slot.MIDNIGHT, Place.SHRINE)]
        v.public.tells = [TellRecord("c1", 1, True)]
        c = LiePressureBot().choose(v, random.Random(1))
        self.assertEqual(c.addressee, "a")
        self.assertEqual(c.interrogation_kind.value, "contradiction")
        self.assertEqual(c.evidence[0].record_id, "c1|c2")
        v.public.claims[1].corrects = "c1"
        self.assertFalse(any(c.interrogation_kind and c.interrogation_kind.value == "contradiction"
                             for c in legal_commands(v)))

    def test_no_action_after_end_or_outside_player_turn(self):
        v = view(Phase.ENDED)
        v.legal_kinds = []
        self.assertEqual(legal_commands(v), [])
        with self.assertRaises(ValueError):
            RandomBot().choose(v, random.Random(1))
        v = view()
        v.awaiting = "a"
        self.assertEqual(legal_commands(v), [])
