"""A-10-01 5번 꾀어내기 규칙: 피해 장소는 늘 공개 장소이고, 피해자·실행자가 한밤에 그곳에 있다."""
from __future__ import annotations

import random
import unittest

from engine.domain import params
from engine.domain.content import validate_assignment
from engine.domain.engine import PaldoEngine, _night_whereabouts, assignment_of, lures
from engine.domain.model import (
    PRESETS, PUBLIC_PLACES, CommandEnvelope, DataSource, GameMeta, NightPlan, Role, Slot,
)
from engine.ports import EngineDeps
from engine.sim.bots import BOT_TYPES
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource

CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
SEEDS = range(60)


def meta(preset_id: str, seed: int, rule_version: str = params.RULE_PARAMS_VERSION) -> GameMeta:
    return GameMeta(f"lure_{preset_id}_{seed:03d}", PRESETS[preset_id], seed, DataSource.BOT,
                    rule_version, "test", CONTENT.content_version)


def play(preset_id: str, seed: int, rule_version: str = params.RULE_PARAMS_VERSION):
    """무작위 봇으로 판을 끝까지 돌려 마지막 상태·진실을 돌려준다."""
    engine = PaldoEngine()
    t = engine.new_game(meta(preset_id, seed, rule_version), DEPS)
    state, truth = t.state, t.truth
    bot, rng = BOT_TYPES["random"](), random.Random(seed)
    for _ in range(500):
        view = engine.player_view(state, CONTENT)
        if view.result is not None:
            return state, truth
        cmd = bot.choose(view, rng)
        t = engine.apply(state, truth, CommandEnvelope(state.meta.game_id, f"c{view.state_version:05d}",
                                                       view.state_version, cmd), DEPS)
        assert t.accepted, t.reject
        state, truth = t.state, t.truth
    raise AssertionError("명령 상한 초과")


class Night0(unittest.TestCase):
    """0일째 배정(모든 프리셋)."""

    def test_crime_public_and_executor_with_victim(self):
        """피해 장소는 공개 장소, 실행자·피해자의 한밤 장소가 같고, 배정 검증기를 통과한다."""
        engine = PaldoEngine()
        for p in PRESETS:
            for s in SEEDS:
                r = engine.new_game(meta(p, s), DEPS)
                st, tr = r.state, r.truth
                with self.subTest(preset=p, seed=s):
                    rec = st.public.disappearances[0]
                    w, plan = tr.whereabouts[0], tr.night_plans[0]
                    self.assertIn(rec.place, PUBLIC_PLACES)
                    self.assertIs(w[plan.victim][Slot.MIDNIGHT], rec.place)
                    self.assertIs(w[plan.executor][Slot.MIDNIGHT], rec.place)
                    others = [n for n, role in tr.roles.items()
                              if role is Role.VILLAGER and n != plan.victim and w[n][Slot.MIDNIGHT] is rec.place]
                    self.assertEqual(others, [])
                    self.assertEqual(validate_assignment(PRESETS[p], CONTENT, assignment_of(st, tr)), [])

    def test_crime_place_spread(self):
        """공개 장소 4곳이 모두 피해 장소로 나온다(한 곳에 몰리지 않음)."""
        engine = PaldoEngine()
        seen = {engine.new_game(meta("base6", s), DEPS).state.public.disappearances[0].place for s in SEEDS}
        self.assertEqual(seen, set(PUBLIC_PLACES))

    def test_same_seed_same_result(self):
        """같은 시드면 피해 장소와 행적이 같다."""
        for s in range(20):
            a = PaldoEngine().new_game(meta("base6", s), DEPS)
            b = PaldoEngine().new_game(meta("base6", s), DEPS)
            self.assertEqual(a.truth.whereabouts, b.truth.whereabouts)
            self.assertEqual(a.state.public.disappearances, b.state.public.disappearances)


class LaterNights(unittest.TestCase):
    """1일째 밤 이후도 같은 규칙."""

    def test_rule_holds_every_night(self):
        """끝까지 돌린 판의 모든 밤에 공개 장소·실행자 동행·동석 금지가 지켜진다."""
        later = 0
        for p in ("base6", "base7", "large8"):
            for s in range(25):
                st, tr = play(p, s)
                for rec in st.public.disappearances:
                    w, plan = tr.whereabouts[rec.night], tr.night_plans[rec.night]
                    with self.subTest(preset=p, seed=s, night=rec.night):
                        self.assertIn(rec.place, PUBLIC_PLACES)
                        self.assertEqual(plan.victim, rec.victim)
                        self.assertIs(w[plan.victim][Slot.MIDNIGHT], rec.place)
                        self.assertIs(w[plan.executor][Slot.MIDNIGHT], rec.place)
                        if rec.night > 0:
                            later += 1
                            others = [n for n in w if n not in (plan.victim, plan.executor)
                                      and w[n][Slot.MIDNIGHT] is rec.place]
                            self.assertEqual(others, [])
        self.assertGreater(later, 0)

    def test_night_whereabouts_deterministic(self):
        """밤 행적은 같은 난수 시드면 같고, 피해 장소는 공개 장소다."""
        chars = {c.npc_id: c for c in CONTENT.characters}
        npcs = sorted(chars)[:5]
        plan = NightPlan(executor=npcs[0], victim=npcs[1])
        places = set()
        for s in range(60):
            a = _night_whereabouts(random.Random(s), npcs, chars, plan)
            self.assertEqual(a, _night_whereabouts(random.Random(s), npcs, chars, plan))
            crime = a[plan.victim][Slot.MIDNIGHT]
            self.assertIn(crime, PUBLIC_PLACES)
            self.assertIs(a[plan.executor][Slot.MIDNIGHT], crime)
            places.add(crime)
        self.assertEqual(places, set(PUBLIC_PLACES))



class LegacyVersion(unittest.TestCase):
    """이전 규칙 버전으로 시작한 판·회차는 이전 규칙(피해 장소 = 피해자의 한밤 장소)으로 이어 간다."""

    def test_lures_by_version(self):
        """`rule-0.6` 이하만 이전 규칙, 현재·모르는 버전은 꾀어내기 규칙."""
        self.assertTrue(lures(params.RULE_PARAMS_VERSION))
        self.assertTrue(lures("rule-test"))
        self.assertFalse(lures("rule-0.6.decision-1"))
        self.assertFalse(lures("rule-0.5.decision-1-fallback"))

    def test_legacy_games_keep_victim_place(self):
        """`rule-0.6` 판은 모든 밤 피해 장소가 피해자의 한밤 장소이고, 집도 나온다."""
        places = set()
        for s in range(25):
            st, tr = play("base6", s, "rule-0.6.decision-1")
            for rec in st.public.disappearances:
                w, plan = tr.whereabouts[rec.night], tr.night_plans[rec.night]
                self.assertIs(w[plan.victim][Slot.MIDNIGHT], rec.place)
                self.assertIs(w[plan.executor][Slot.MIDNIGHT], rec.place)
                places.add(rec.place)
        self.assertFalse(places <= set(PUBLIC_PLACES))


if __name__ == "__main__":
    unittest.main()
