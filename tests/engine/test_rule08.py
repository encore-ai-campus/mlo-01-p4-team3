"""A-10-01 1번 rule-0.8: 실행자 알리바이에 집 포함·한밤 동선 비례, 한밤 집 가중치 ×0.25, 결정 파라미터 decision-2.

rule-0.7 이하로 시작한 판은 배포 뒤에도 이전 규칙·decision-1(목격·기색 확률은 엔진, 무당 의심 상한은
`RuleV0Policy(q_max=...)`)으로 이어 간다.
"""
from __future__ import annotations

import hashlib
import random
import unittest
from collections import Counter

from engine.domain import params
from engine.domain.engine import (
    HOME_MIDNIGHT_SCALE, PaldoEngine, _slot_weights, decision_of, executor_alibi, home_alibi, lures,
)
from engine.domain.model import (
    PRESETS, PUBLIC_PLACES, ClaimType, CommandEnvelope, DataSource, GameMeta, Place, Slot,
)
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.sim.bots import BOT_TYPES
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource

CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
CHARS = {c.npc_id: c for c in CONTENT.characters}
LEGACY = "rule-0.7.decision-1"

# origin/dev 00fa457(rule-0.7) 엔진으로 같은 판을 돌린 궤적 해시(LogicBot, 픽스처 콘텐츠, base6).
# rule-0.8 코드에서 rule-0.7 판이 한 글자도 달라지지 않는지 본다. 만든 방법은 아래 `_trajectory_hash`와 같다.
LEGACY_GOLDEN = {
    0: "844a095048e6f483", 1: "a9fa9fe348b84ef7", 2: "26a9dcbb05066450", 3: "9587a2bb531af2ba",
    4: "2c436d12ac168438", 5: "2c98d6e04740fe90", 6: "d140cd77120e4851", 7: "4f454788efcf0e33",
    8: "b4662f7c5513bc97", 9: "19ced32ea1608290", 10: "a867873379417b00", 11: "b70a80a601ee5dde",
}


def _meta(preset: str, seed: int, rule_version: str) -> GameMeta:
    return GameMeta(f"g_{preset}_{seed}", PRESETS[preset], seed, DataSource.BOT, rule_version, "t",
                    CONTENT.content_version)


def _trajectory_hash(engine: PaldoEngine, preset: str, seed: int, rule_version: str) -> str:
    t = engine.new_game(_meta(preset, seed, rule_version), DEPS)
    st, tr = t.state, t.truth
    bot, rng = BOT_TYPES["logic"](), random.Random(seed)
    h = hashlib.sha256()
    for _ in range(500):
        view = engine.player_view(st, CONTENT)
        if view.result is not None:
            break
        cmd = bot.choose(view, rng)
        t = engine.apply(st, tr, CommandEnvelope(st.meta.game_id, f"c{view.state_version:05d}",
                                                 view.state_version, cmd), DEPS)
        assert t.accepted, t.reject
        st, tr = t.state, t.truth
        h.update(repr(st.public).encode())
    h.update(repr(tr.whereabouts).encode())
    h.update(repr(st.result).encode())
    return h.hexdigest()[:16]


class Versions(unittest.TestCase):
    """규칙 버전 판정과 결정 파라미터."""

    def test_rule_base_version(self):
        """현재 규칙은 rule-0.8 + decision-2."""
        self.assertEqual(params.RULE_BASE_VERSION, "rule-0.8")
        self.assertEqual(params.DEFAULT.version, "decision-2")
        self.assertEqual(params.RULE_PARAMS_VERSION, "rule-0.8.decision-2")

    def test_home_alibi_by_version(self):
        """rule-0.7 이하만 이전 규칙, 현재·모르는 버전은 새 규칙. 꾀어내기는 rule-0.7부터 그대로."""
        self.assertTrue(home_alibi(params.RULE_PARAMS_VERSION))
        self.assertTrue(home_alibi("rule-test"))
        self.assertFalse(home_alibi(LEGACY))
        self.assertFalse(home_alibi("rule-0.5.decision-1-fallback"))
        self.assertTrue(lures(LEGACY))

    def test_decision_params(self):
        """decision-2 값(후보 1)과 이전 판용 decision-1 고정값."""
        d = params.DEFAULT
        self.assertEqual((d.q_max, d.witness_prob, d.tell_prob), (1.0, 0.7, 0.2))
        self.assertEqual((d.conformity_scale, d.vote_noise, d.routine_randomness), (0.1, 0.02, 0.0))
        old = params.LEGACY_DECISION
        self.assertEqual((old.version, old.q_max, old.witness_prob, old.tell_prob), ("decision-1", 0.7, 0.5, 0.4))
        self.assertIs(decision_of(LEGACY), old)
        self.assertEqual(decision_of(params.RULE_PARAMS_VERSION), params.current())


class MidnightWeights(unittest.TestCase):
    """한밤 집 가중치 ×0.25, 줄어든 몫은 다른 장소에 비율대로."""

    def test_scaled_midnight(self):
        """훈장 한밤 (집 0.9, 서낭당 0.1) → (0.225, 0.775). 콘텐츠 값은 그대로."""
        w = _slot_weights(CHARS["hunjang"], Slot.MIDNIGHT, True)
        self.assertAlmostEqual(w[Place.HOME], 0.225)
        self.assertAlmostEqual(w[Place.SHRINE], 0.775)
        self.assertAlmostEqual(CHARS["hunjang"].routine[Slot.MIDNIGHT][Place.HOME], 0.9)

    def test_ratio_among_others_kept_and_total_kept(self):
        """다른 장소끼리 비율과 합계는 유지된다(주모: 주막 0.5·집 0.3·서낭당 0.2)."""
        raw = CHARS["jumo"].routine[Slot.MIDNIGHT]
        w = _slot_weights(CHARS["jumo"], Slot.MIDNIGHT, True)
        self.assertAlmostEqual(sum(w.values()), sum(raw.values()))
        self.assertAlmostEqual(w[Place.HOME], raw[Place.HOME] * HOME_MIDNIGHT_SCALE)
        self.assertAlmostEqual(w[Place.TAVERN] / w[Place.SHRINE], raw[Place.TAVERN] / raw[Place.SHRINE])

    def test_unchanged_cases(self):
        """이전 규칙, 한밤 아닌 시간대, 집밖에 없는 인물은 그대로."""
        for ch in CONTENT.characters:
            for slot in ch.routine:
                with self.subTest(npc=ch.npc_id, slot=slot):
                    self.assertEqual(_slot_weights(ch, slot, False), ch.routine[slot])
                    if slot is not Slot.MIDNIGHT:
                        self.assertEqual(_slot_weights(ch, slot, True), ch.routine[slot])
        self.assertEqual(_slot_weights(CHARS["boy"], Slot.MIDNIGHT, True), {Place.HOME: 1.0})


class ExecutorAlibi(unittest.TestCase):
    """실행자 거짓 알리바이."""

    def test_legacy_public_only(self):
        """동선 가중치가 없으면(이전 규칙) 공공장소 균등, 집은 나오지 않는다."""
        got = {executor_alibi(random.Random(s), Place.WELL, []) for s in range(200)}
        self.assertEqual(got, set(PUBLIC_PLACES) - {Place.WELL})

    def test_home_and_proportional(self):
        """동선 가중치를 주면 집도 후보이고, 가중치에 비례한다. 범행 장소·가중치 0인 곳은 안 나온다."""
        prefs = _slot_weights(CHARS["hunjang"], Slot.MIDNIGHT, True)   # 집 0.225, 서낭당 0.775
        got = Counter(executor_alibi(random.Random(s), Place.WELL, [], prefs) for s in range(2000))
        self.assertEqual(set(got), {Place.HOME, Place.SHRINE})
        self.assertAlmostEqual(got[Place.HOME] / 2000, 0.225, delta=0.03)

    def test_known_places_excluded_and_zero_weight_fallback(self):
        """범행 장소를 빼고 남은 후보 가중치가 모두 0이면 남은 후보 중 균등."""
        prefs = {Place.SHRINE: 1.0}
        got = {executor_alibi(random.Random(s), Place.SHRINE, [], prefs) for s in range(200)}
        self.assertEqual(got, {Place.HOME} | (set(PUBLIC_PLACES) - {Place.SHRINE}))


class Games(unittest.TestCase):
    """판 단위: 새 판은 집 알리바이가 나오고, 이전 판은 이전 엔진과 같다."""

    def test_new_games_home_alibi_appears(self):
        """rule-0.8 판에서 실행자가 한밤에 '집'이라고 말하는 판이 있다."""
        engine = PaldoEngine(RuleV0Policy(CONTENT))
        places = Counter()
        for s in range(80):
            t = engine.new_game(_meta("base6", s, params.RULE_PARAMS_VERSION), DEPS)
            ex = t.truth.night_plans[0].executor
            claim = next(c for c in t.state.public.claims if c.speaker == ex and c.slot is Slot.MIDNIGHT
                         and c.claim_type is ClaimType.WHEREABOUTS)
            places[claim.place] += 1
        self.assertGreater(places[Place.HOME], 0)

    def test_legacy_games_identical_to_rule07_engine(self):
        """rule-0.7 판(q_max 고정 정책)은 rule-0.7 엔진으로 돌린 궤적과 같다(배포 경계)."""
        engine = PaldoEngine(RuleV0Policy(CONTENT, q_max=params.LEGACY_DECISION.q_max))
        for seed, want in LEGACY_GOLDEN.items():
            with self.subTest(seed=seed):
                self.assertEqual(_trajectory_hash(engine, "base6", seed, LEGACY), want)


if __name__ == "__main__":
    unittest.main()
