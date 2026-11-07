"""A-07-03 통합 1(INT-07) 엔진 몫 테스트. 통합 판의 실제 구성(규칙 v0 정책 + 의심 모델 포트)으로 확인한다.

- #2 반례 4개(결선 재동점 무감금, 무당 감금, 점괘·고백 전환, 마지막 씐 자 감금): 시나리오 픽스처의 정해진 결정(표·밤 명령·
  캐묻기 반응)만 스크립트로 두고, 나머지 NPC 결정은 규칙 v0(`RuleV0Policy`, 투표 3층·가짜 의심 모델)이 한다.
  A-05-02·A-06-01 테스트는 같은 시나리오를 기본 규칙(`BaselinePolicy`) 위에서 재생한다.
- #1 버튼만으로 6인·창귀 1 판 완주(엔진 몫): 플레이어 명령을 화면 버튼과 같은 합법 명령(`bench_player_command`)으로만 내 3판 완주.
- #3 피해 장소 공개(엔진 몫): 0일째·1일째 아침 발표 payload의 `crime_scene`과 플레이어 화면 정보(`player_view`)의 실종 기록 장소.
웹 화면 재현은 사람이 통합 자리에서 한다(증빙 표의 사람 항목).
"""
from __future__ import annotations

import random
import unittest
from typing import Optional

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import (
    PRESETS, CommandEnvelope, CommandKind, DataSource, DivinationPayload, DivinationResult, GameMeta, NightPlan, Phase,
    ReactionIntent,
)
from engine.domain.npc import RuleV0Policy, VoteOutcome
from engine.domain.observation import Decision, ReasonCode
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel
from tests.engine.test_night import (
    DAY1_LOCK_MEOSUM, NightPolicy, confess_day1, night1_divine, to_night_yakcho,
)
from tests.engine.test_scenarios_engine import (
    CONTENT, DAY1_LOCK_YAKCHO, DEPS, drive_to, env, evaluate_with_engine, game_base6, scenario, step,
)
from tools.perf.engine_bench import bench_player_command

VOTE_SCENARIOS = ("runoff_tie_no_lock", "mudang_locked", "last_possessed_locked")


class OverRuleV0:
    """스크립트 결정(시나리오가 정한 표·밤 명령·반응·말)만 그대로 쓰고, 나머지는 규칙 v0(투표는 의심 모델 3층)."""

    version = "rule-v0"

    def __init__(self, scripted: Optional[object]):
        self.s = scripted
        self.rule = RuleV0Policy(CONTENT)

    def _got(self, table: str, key):
        return getattr(self.s, table, {}).get(key) if self.s is not None else None

    def _vote(self, obs) -> Optional[str]:
        round_ = "runoff" if obs.phase is Phase.RUNOFF else "main"
        return (self._got("votes", (obs.day, round_)) or {}).get(obs.observer)

    def update_beliefs(self, obs):
        """믿음 갱신은 규칙 v0."""
        return self.rule.update_beliefs(obs)

    def talk(self, obs, rng):
        """정해 둔 말이 있으면 그것, 없으면 규칙 v0."""
        got = self._got("talks", (obs.day, obs.phase, obs.observer))
        return Decision(got, [ReasonCode.SCRIPTED]) if got else self.rule.talk(obs, rng)

    def vote(self, obs, candidates, rng):
        """정해 둔 표가 있으면 그것, 없으면 규칙 v0 투표."""
        got = self._vote(obs)
        return Decision(got, [ReasonCode.SCRIPTED]) if got else self.rule.vote(obs, candidates, rng)

    def vote_with_model(self, obs, candidates, rng, model, clock=None):
        """정해 둔 표가 있으면 그것, 없으면 투표 3층(A-07-02)."""
        got = self._vote(obs)
        if got:
            return VoteOutcome(Decision(got, [ReasonCode.SCRIPTED]))
        return self.rule.vote_with_model(obs, candidates, rng, model, clock)

    def night_plan(self, obs, executors, victims, rng):
        """정해 둔 밤 명령이 있으면 그것, 없으면 씐 자 전략."""
        got = self._got("nights", obs.day)
        return Decision(got, [ReasonCode.SCRIPTED]) if got else self.rule.night_plan(obs, executors, victims, rng)

    def react(self, obs, cmd, rng):
        """정해 둔 캐묻기 반응이 있으면 그것, 없으면 규칙 v0."""
        got = self._got("reactions", (obs.day, obs.observer))
        return Decision(got, [ReasonCode.SCRIPTED]) if got else self.rule.react(obs, cmd, rng)


def over(pol) -> PaldoEngine:
    return PaldoEngine(OverRuleV0(pol))


# ---------------------------------------------------------------- INT-07 #2 반례 4개
class Counterexamples(unittest.TestCase):
    def test_vote_scenarios_under_rule_v0(self):
        """결선 재동점 무감금·무당 감금·마지막 씐 자 감금: 시나리오 checks 전부(규칙 v0 위)."""
        n = 0
        for sid in VOTE_SCENARIOS:
            sc = scenario(sid)
            for k, c in enumerate(sc.checks):
                with self.subTest(scenario=sid, check=k, type=c.type):
                    got = evaluate_with_engine(self, sc, c, engine_factory=over)
                    for key, want in c.expect.items():
                        self.assertEqual(got.get(key), want, f"{key}: {c.note}")
                    n += 1
        self.assertGreater(n, 0)

    def confess_engine(self) -> PaldoEngine:
        return over(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}, nights={1: NightPlan("blacksmith", "hunjang")},
                                reactions={(1, "yakcho"): ReactionIntent.CONFESS}))

    def test_divination_switches_after_confession(self):
        """점괘·고백 전환: 고백 전 비밀 보유자는 탁함, 고백한 뒤에는 맑음(secret_confession checks)."""
        sc = scenario("secret_confession")
        want = {(c.input["is_possessed"], c.input["has_unconfessed_secret"]): c.expect["result"]
                for c in sc.checks if c.type == "divination"}
        engine = self.confess_engine()
        st, tr = game_base6(engine)
        st, tr, _ = night1_divine(self, engine, st, tr, "yakcho")
        before = st.player.divinations[1]["yakcho"].value
        engine = self.confess_engine()
        st, tr, r = confess_day1(self, engine)
        self.assertTrue(any(e.event_type is EventType.CONFESSION for e in r.events))
        st, tr, _ = night1_divine(self, engine, st, tr, "yakcho")
        after = st.player.divinations[1]["yakcho"].value
        self.assertEqual((before, after), (want[(False, True)], want[(False, False)]))
        self.assertEqual((before, after), (DivinationResult.MURKY.value, DivinationResult.CLEAR.value))

    def test_false_divination_reveal(self):
        """점괘 공개: 실제 탁함을 맑음으로 공개해도 합법, 공개 기록은 맑음·개인 기록은 탁함(false_divination_reveal)."""
        sc = scenario("false_divination_reveal")
        actual = sc.setup["actual"]
        engine = over(NightPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO}, nights={1: NightPlan("blacksmith", "hunjang")}))
        st, tr = to_night_yakcho(self, engine)
        st, tr, _ = step(self, engine, st, tr, CommandKind.DIVINE, target=actual["target"])
        st, tr = drive_to(self, engine, st, tr, Phase.ROUND1)
        cmd = sc.steps[0]["command"]["divination"]
        r = engine.apply(st, tr, env(st, CommandKind.REVEAL_DIVINATION, divination=DivinationPayload(
            cmd["target"], cmd["night"], DivinationResult(cmd["result"]))), DEPS)
        self.assertTrue(r.accepted, r.reject)
        c = r.state.public.claims[-1]
        (chk,) = [x for x in sc.checks if x.type == "reveal_record"]
        self.assertEqual((c.divination_result.value, r.state.player.divinations[c.night][c.subject].value),
                         (chk.expect["public_result"], chk.expect["private_result"]))


# ---------------------------------------------------------------- INT-07 #1·#3 엔진 몫
def play_rule_v0(seed: int):
    """base6(6인·창귀 1) 규칙 v0 + 가짜 의심 모델 판. 플레이어는 합법 버튼 명령만. (최종 상태, 이벤트, 아침 화면들)."""
    engine = PaldoEngine(RuleV0Policy(CONTENT))
    deps = EngineDeps(CONTENT, FakeSuspicionModel())
    meta = GameMeta(f"g_int07_{seed:03d}", PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    "test", CONTENT.content_version)
    r = engine.new_game(meta, deps)
    st, tr, rng, events = r.state, r.truth, random.Random(seed), list(r.events)
    views = [engine.player_view(st, CONTENT)]
    while st.result is None:
        view = engine.player_view(st, CONTENT)
        cmd = bench_player_command(view, rng)
        assert cmd.kind in view.legal_kinds, (cmd.kind, view.legal_kinds)
        r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version}", st.state_version, cmd), deps)
        assert r.accepted, (r.reject, r.reject_detail)
        st, tr = r.state, r.truth
        events += r.events
        if st.phase is Phase.MORNING:
            views.append(engine.player_view(st, CONTENT))
    return st, events, views


class GamesAndMorning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = {seed: play_rule_v0(seed) for seed in range(3)}

    def test_three_games_finish(self):
        """#1 엔진 몫: 6인·창귀 1 판 3판이 버튼 명령만으로 거부 없이 끝난다."""
        self.assertEqual(PRESETS["base6"].n_possessed, 1)
        for seed, (st, _, _) in self.games.items():
            self.assertIsNotNone(st.result, seed)

    def test_crime_scene_announced_day0_and_day1(self):
        """#3 엔진 몫: 0일째·1일째 아침 발표에 피해 장소가 있고, 플레이어 화면 정보의 실종 기록과 같다."""
        reached = 0
        for seed, (st, events, views) in self.games.items():
            ann = [e for e in events if e.event_type is EventType.MORNING_ANNOUNCED]
            first = views[0].public.disappearances
            self.assertEqual([d.night for d in first], [0])
            self.assertEqual(ann[0].payload["crime_scene"]["place"], first[0].place.value)
            if len(views) > 1:                              # 1일째 밤을 지나 2일째 아침이 온 판
                reached += 1
                d1 = [d for d in views[1].public.disappearances if d.night == 1]
                self.assertEqual(len(d1), 1, seed)
                self.assertEqual(ann[1].payload["crime_scene"]["place"], d1[0].place.value)
                self.assertEqual(ann[1].payload["missing"], [d1[0].victim])
        self.assertGreater(reached, 0)


if __name__ == "__main__":
    unittest.main()
