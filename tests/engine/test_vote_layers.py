"""A-07-02 투표 3층과 의심 모델 포트 연결 테스트(태스크 문서 완료 조건 1~4).

층마다 손으로 만든 스냅샷·추정으로 단독 검사하고, 끝에 `PaldoEngine(RuleV0Policy)`에 가짜 의심 모델
(`FakeSuspicionModel`, 실패 주입 포함)을 넣어 판을 돌려 연결·폴백·`-fallback` 표시를 확인한다.
"""
from __future__ import annotations

import random
import time
import unittest

from engine.domain import params
from engine.domain.content import Temperament
from engine.domain.engine import PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import (
    PLAYER_ID, PRESETS, Beliefs, CommandEnvelope, CommandKind, DataSource, EvidenceGroup, EvidenceSource, GameMeta,
    ObservationSnapshot, Phase, Place, PrivateKnowledge, Proposition, PropositionKind, PublicRecord, Role, Slot,
    Speech, ConfessionRecord,
)
from engine.domain.npc import RuleV0Policy, VillagerPolicy
from engine.domain.npc import vote as vt
from engine.domain.observation import NO_EVIDENCE_CODES, ReasonCode
from engine.ports import EngineDeps, FeatureVersionMismatch, ModelUnavailable, SuspicionEstimate
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.perf.engine_bench import bench_player_command

CONTENT = FixtureContentSource().load("*")


def snap(observer="a", candidates=("b", "c", "d"), speeches=(), confessions=(), props=(), day=1,
         n_possessed=1) -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id=f"snap:g:v1:d000:{observer}", game_id="g", observer=observer, day=day, phase=Phase.VOTE,
        state_version=1, public=PublicRecord(speeches=list(speeches), confessions=list(confessions)),
        private=PrivateKnowledge(npc_id=observer, role=Role.VILLAGER), beliefs=Beliefs(propositions=list(props)),
        legal_kinds=[CommandKind.VOTE], candidates=list(candidates), n_possessed_remaining=n_possessed,
        preset_id="base6")


def est(probs: dict, contributions=None, feature="fake-1", cands=None) -> SuspicionEstimate:
    return SuspicionEstimate("a", list(cands or probs), dict(probs), contributions or {c: {} for c in probs},
                             "m-1", feature)


def temp(conformity=0.0, weights=None) -> Temperament:
    return Temperament("t", "시험", dict(weights or {}), 0.5, 0.5, conformity, 0.5)


def suspect(sid, speaker, target, day=1) -> Speech:
    return Speech(sid, day, Phase.ROUND1, speaker, CommandKind.SUSPECT, target=target)


def cleared(who) -> Proposition:
    return Proposition(PropositionKind.NOT_POSSESSED, who, EvidenceSource.OWN_WITNESS, 0, Slot.MIDNIGHT, Place.WELL)


class StubModel:
    """정해진 추정을 내거나 예외를 던지는 의심 모델. calls로 호출 수를 센다."""

    def __init__(self, out=None, raises=None, feature_version="fake-1", sleep=0.0):
        self.out, self.raises, self.feature_version, self.sleep, self.calls = out, raises, feature_version, sleep, 0

    def estimate(self, obs):
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        if self.raises is not None:
            raise self.raises
        return self.out(obs) if callable(self.out) else self.out


class Clock:
    """호출마다 정해진 값을 차례로 내는 가짜 시계."""

    def __init__(self, *ticks):
        self.ticks = list(ticks)

    def __call__(self):
        return self.ticks.pop(0)


# ---------------------------------------------------------------- 조건 1: 추정 층
class EstimateLayer(unittest.TestCase):
    def test_success(self):
        e = est({"b": 0.5, "c": 0.3, "d": 0.2})
        out, why = vt.estimate(StubModel(e), snap())
        self.assertIs(out, e)
        self.assertIsNone(why)

    def test_fake_model_success_matches_snapshot(self):
        out, why = vt.estimate(FakeSuspicionModel(), snap())
        self.assertIsNone(why)
        self.assertEqual(out.candidates, ["b", "c", "d"])
        self.assertAlmostEqual(sum(out.probs.values()), 1.0)

    def test_unavailable(self):
        self.assertEqual(vt.estimate(FakeSuspicionModel(fail=True), snap()), (None, vt.FallbackReason.UNAVAILABLE))
        self.assertEqual(vt.estimate(StubModel(raises=ModelUnavailable("x")), snap())[1],
                         vt.FallbackReason.UNAVAILABLE)

    def test_version_mismatch(self):
        self.assertEqual(vt.estimate(StubModel(raises=FeatureVersionMismatch("x")), snap())[1],
                         vt.FallbackReason.VERSION)
        e = est({"b": 0.5, "c": 0.3, "d": 0.2}, feature="feat-9")       # 출력이 모델 선언과 다른 특징 버전
        self.assertEqual(vt.estimate(StubModel(e), snap())[1], vt.FallbackReason.VERSION)

    def test_none_output(self):
        self.assertEqual(vt.estimate(StubModel(lambda obs: None), snap())[1], vt.FallbackReason.INVALID)

    def test_sum_matches_possessed_remaining(self):
        two = est({"b": 0.8, "c": 0.7, "d": 0.5})                           # 창귀 2명: 합 2
        self.assertIsNone(vt.estimate(StubModel(two), snap(n_possessed=2))[1])
        self.assertEqual(vt.estimate(StubModel(two), snap(n_possessed=1))[1], vt.FallbackReason.INVALID)
        self.assertIsNone(vt.estimate(FakeSuspicionModel(), snap(n_possessed=2))[1])

    def test_timeout_over_100ms(self):
        e = est({"b": 0.5, "c": 0.3, "d": 0.2})
        self.assertEqual(vt.estimate(StubModel(e), snap(), clock=Clock(10.0, 10.1001))[1], vt.FallbackReason.TIMEOUT)
        self.assertIsNone(vt.estimate(StubModel(e), snap(), clock=Clock(10.0, 10.1))[1])   # 정확히 100ms는 통과

    def test_invalid_output(self):
        wrong_order = est({"b": 0.5, "c": 0.3, "d": 0.2}, cands=["c", "b", "d"])
        missing = est({"b": 0.5, "c": 0.5})
        out_of_range = est({"b": 1.5, "c": 0.3, "d": 0.2})
        all_zero = est({"b": 0.0, "c": 0.0, "d": 0.0})                     # 합이 남은 창귀 수(1)가 아님
        bad_contrib = est({"b": 0.5, "c": 0.3, "d": 0.2}, {"b": {EvidenceGroup.WITNESS: float("nan")}})
        text_contrib = est({"b": 0.5, "c": 0.3, "d": 0.2}, {"b": {EvidenceGroup.WITNESS: "1.0"}})
        stranger = est({"b": 0.5, "c": 0.3, "d": 0.2}, {"z": {EvidenceGroup.WITNESS: 1.0}})
        no_probs = est({"b": 0.5, "c": 0.3, "d": 0.2})
        no_probs.probs = None
        for e in (wrong_order, missing, out_of_range, all_zero, bad_contrib, text_contrib, stranger, no_probs):
            self.assertEqual(vt.estimate(StubModel(e), snap())[1], vt.FallbackReason.INVALID)


# ---------------------------------------------------------------- 조건 1·2: 결정 층과 무당 결합
RULE_V0_DECISION = params.DecisionParams("decision-0", conformity_scale=0.2, vote_noise=0.02, q_max=0.5,
                                         witness_prob=0.7, tell_prob=0.5, routine_randomness=0.0)


class DecisionLayer(unittest.TestCase):
    """층 단위 계산은 규칙 v0 기준값(decision-0)으로 고정해 확인한다(조정 값은 decision_params.json, A-08-03)."""

    def setUp(self):
        self._saved = params.current()
        params.apply(RULE_V0_DECISION)

    def tearDown(self):
        params.apply(self._saved)

    def test_temperament_weights_shift_log_odds(self):
        e = est({"b": 0.4, "c": 0.4}, {"b": {EvidenceGroup.WITNESS: 1.0}, "c": {EvidenceGroup.SOCIAL: 1.0}})
        self.assertEqual(vt.weighted_probs(e, None), {"b": 0.4, "c": 0.4})                 # 성향 없음: 그대로
        self.assertEqual(vt.weighted_probs(e, temp(weights={EvidenceGroup.WITNESS: 1.0})), {"b": 0.4, "c": 0.4})
        w = vt.weighted_probs(e, temp(weights={EvidenceGroup.WITNESS: 2.0}))
        self.assertGreater(w["b"], 0.4)                    # 목격 묶음을 무겁게 보는 성향은 b를 더 의심
        self.assertEqual(w["c"], 0.4)
        self.assertLess(vt.weighted_probs(e, temp(weights={EvidenceGroup.WITNESS: 0.5}))["b"], 0.4)

    def test_mudang_q(self):
        self.assertEqual(params.Q_MAX, 0.5)
        self.assertEqual(vt.mudang_q(0), 0.5)
        self.assertEqual(vt.mudang_q(50), 0.25)
        self.assertEqual(vt.mudang_q(100), 0.0)
        self.assertEqual(vt.mudang_q(130), 0.0)            # 범위 밖은 자른다
        self.assertEqual(vt.mudang_q(50, q_max=0.2), 0.1)

    def test_combined_suspicion(self):
        out = vt.combined_suspicion({"b": 0.6, "c": 0.4}, trust_in_mudang=20)
        q = 0.5 * 80 / 100
        self.assertAlmostEqual(out[PLAYER_ID], q)
        self.assertAlmostEqual(out["b"], (1 - q) * 0.6)
        self.assertAlmostEqual(out["c"], (1 - q) * 0.4)

    def test_expected_votes(self):
        s = snap(speeches=[suspect("sp1", "b", "c"), suspect("sp2", "b", "c"), suspect("sp3", "d", "c"),
                           suspect("sp4", "c", "d", day=0), suspect("sp5", "a", "d"),
                           Speech("sp6", 1, Phase.ROUND1, "c", CommandKind.SILENCE)])
        ev = vt.expected_votes(s)
        # 다른 투표자 = 오늘 말 차례를 쓴 b·c·d + 무당 = 4명. c는 b(두 번이어도 1)와 d가 의심.
        self.assertEqual(ev, {"c": 0.5})
        self.assertNotIn("d", ev)                          # 어제 의심과 관찰자 자신의 의심은 세지 않는다

    def test_conformity_follows_expected_votes(self):
        e = est({"b": 0.40, "c": 0.34, "d": 0.26})        # 차이 0.06 > 잡음 폭 2 × 0.02
        s = snap(speeches=[suspect("sp1", "b", "c"), suspect("sp2", "d", "c")])
        b = Beliefs(trust_in_mudang=100.0)                 # q = 0: 무당 후보 점수 0
        t0, codes0 = vt.choose_target(s, b, ["a", "b", "c", "d", PLAYER_ID], e, temp(conformity=0.0),
                                      random.Random(1))
        t1, codes1 = vt.choose_target(s, b, ["a", "b", "c", "d", PLAYER_ID], e, temp(conformity=1.0),
                                      random.Random(1))
        self.assertEqual((t0, t1), ("b", "c"))
        self.assertNotIn(ReasonCode.VOTE_CONFORM, codes0)
        self.assertIn(ReasonCode.VOTE_CONFORM, codes1)
        self.assertEqual(codes1[0], ReasonCode.MODEL_ESTIMATE)

    def test_noise_bounded_and_seeded(self):
        susp = {"b": 0.5, "c": 0.3}
        s1 = vt.decision_scores(susp, {}, 0.0, random.Random(7))
        s2 = vt.decision_scores(susp, {}, 0.0, random.Random(7))
        self.assertEqual(s1, s2)
        for c in susp:
            self.assertLessEqual(abs(s1[c] - susp[c]), params.VOTE_NOISE)

    def test_mudang_chosen_when_trust_low(self):
        e = est({"b": 0.34, "c": 0.33, "d": 0.33})
        t, _ = vt.choose_target(snap(), Beliefs(trust_in_mudang=0.0), ["a", "b", "c", "d", PLAYER_ID], e, None,
                                random.Random(0))
        self.assertEqual(t, PLAYER_ID)                     # q = 0.5 > 0.5 × 0.34
        t, _ = vt.choose_target(snap(), Beliefs(trust_in_mudang=90.0), ["a", "b", "c", "d", PLAYER_ID], e, None,
                                random.Random(0))
        self.assertNotEqual(t, PLAYER_ID)


# ---------------------------------------------------------------- 조건 1: 안전 층
class SafetyLayer(unittest.TestCase):
    def test_excludes_self_cleared_and_confessed(self):
        s = snap(candidates=("c", "d"), confessions=[ConfessionRecord(1, "b", "s1")])
        b = Beliefs(trust_in_mudang=40.0, propositions=[cleared("c")])   # 신뢰가 문턱 아래라 무당도 후보
        pool, codes = vt.safe_pool(s, b, ["a", "b", "c", "d", PLAYER_ID])
        self.assertEqual(pool, ["d", PLAYER_ID])
        self.assertEqual(codes, [ReasonCode.EXCLUDE_PROPOSITION, ReasonCode.SKIP_CONFESSED])

    def test_trusted_mudang_excluded_from_pool(self):
        """A-09-06: 신뢰도가 문턱(50) 이상이면 다른 후보가 있을 때 무당을 빼고 이유 코드를 남긴다."""
        s = snap()
        for trust in (vt.MODEL_MUDANG_VOTE_TRUST, 90.0):
            pool, codes = vt.safe_pool(s, Beliefs(trust_in_mudang=trust), ["b", "c", PLAYER_ID])
            self.assertNotIn(PLAYER_ID, pool)
            self.assertIn(ReasonCode.SKIP_TRUSTED_MUDANG, codes)
        pool, codes = vt.safe_pool(s, Beliefs(trust_in_mudang=vt.MODEL_MUDANG_VOTE_TRUST - 1), ["b", "c", PLAYER_ID])
        self.assertIn(PLAYER_ID, pool)
        self.assertNotIn(ReasonCode.SKIP_TRUSTED_MUDANG, codes)

    def test_trusted_mudang_kept_when_only_candidate(self):
        """남은 후보가 무당 하나면 빼지 않는다(결선 등)."""
        s = snap(candidates=())
        pool, codes = vt.safe_pool(s, Beliefs(trust_in_mudang=90.0), [PLAYER_ID])
        self.assertEqual(pool, [PLAYER_ID])
        self.assertNotIn(ReasonCode.SKIP_TRUSTED_MUDANG, codes)

    def test_trusted_mudang_not_voted_even_if_top_score(self):
        """무당 점수가 가장 커도(q_max 0.7·신뢰 50 → q 0.35) 신뢰가 시작값 이상이면 주민에게 표가 간다."""
        e = est({"b": 0.34, "c": 0.33, "d": 0.33})
        t, codes = vt.choose_target(snap(), Beliefs(trust_in_mudang=50.0), ["a", "b", "c", "d", PLAYER_ID], e, None,
                                    random.Random(0))
        self.assertNotEqual(t, PLAYER_ID)
        self.assertIn(ReasonCode.SKIP_TRUSTED_MUDANG, codes)

    def test_cleared_top_suspect_is_not_voted(self):
        e = est({"b": 0.8, "c": 0.1, "d": 0.1})
        b = Beliefs(trust_in_mudang=100.0, propositions=[cleared("b")])
        t, codes = vt.choose_target(snap(), b, ["a", "b", "c", "d"], e, None, random.Random(0))
        self.assertNotEqual(t, "b")
        self.assertIn(ReasonCode.EXCLUDE_PROPOSITION, codes)

    def test_all_removed_falls_back_to_max_suspicion(self):
        e = est({"b": 0.7, "c": 0.3})
        s = snap(candidates=("b", "c"))
        b = Beliefs(trust_in_mudang=100.0, propositions=[cleared("b"), cleared("c")])
        t, codes = vt.choose_target(s, b, ["b", "c"], e, None, random.Random(0))   # 결선 후보가 모두 확정 무고
        self.assertEqual(t, "b")
        self.assertIn(ReasonCode.FALLBACK_MAX_SUSPICION, codes)

    def test_never_self(self):
        e = est({"b": 0.5, "c": 0.5})
        for seed in range(20):
            t, _ = vt.choose_target(snap(candidates=("b", "c")), Beliefs(), ["a", "b", "c"], e, None,
                                    random.Random(seed))
            self.assertIn(t, ("b", "c"))


# ---------------------------------------------------------------- 조건 3: 폴백(그 결정만 규칙 v0)
class Fallback(unittest.TestCase):
    def test_policy_falls_back_to_rule_v0(self):
        s = snap()
        rule = VillagerPolicy(CONTENT).vote(s, ["a", "b", "c", "d", PLAYER_ID], random.Random(3))
        for model, why in ((FakeSuspicionModel(fail=True), vt.FallbackReason.UNAVAILABLE),
                           (StubModel(raises=FeatureVersionMismatch("x")), vt.FallbackReason.VERSION)):
            out = VillagerPolicy(CONTENT).vote_with_model(s, ["a", "b", "c", "d", PLAYER_ID], random.Random(3), model)
            self.assertEqual(out.fallback, why)
            self.assertEqual(out.decision.value, rule.value)                    # 규칙 v0와 같은 대상
            self.assertEqual(out.decision.reason_codes, rule.reason_codes + [vt.FALLBACK_CODE[why]])

    def test_policy_timeout_uses_clock(self):
        e = est({"b": 0.5, "c": 0.3, "d": 0.2})
        out = VillagerPolicy(CONTENT).vote_with_model(snap(), ["b", "c", "d"], random.Random(0), StubModel(e),
                                                      clock=Clock(0.0, 0.25))
        self.assertEqual(out.fallback, vt.FallbackReason.TIMEOUT)
        self.assertIn(ReasonCode.MODEL_FALLBACK_TIMEOUT, out.decision.reason_codes)

    def test_fallback_codes_need_no_evidence(self):
        for code in vt.FALLBACK_CODE.values():
            self.assertIn(code, NO_EVIDENCE_CODES)

    def test_possessed_does_not_call_model(self):
        m = StubModel(raises=ModelUnavailable("x"))
        s = snap()
        s.private = PrivateKnowledge(npc_id="a", role=Role.POSSESSED, partner_id=None)
        out = RuleV0Policy(CONTENT).vote_with_model(s, ["a", "b", "c", "d"], random.Random(0), m)
        self.assertIsNone(out.fallback)
        self.assertEqual(m.calls, 0)


def play(model, preset="base6", seed=1, games=1):
    """규칙 v0 엔진 판을 돌려 이벤트 목록을 돌려준다(무당은 시험용 봇)."""
    engine = PaldoEngine(RuleV0Policy(CONTENT))
    deps = EngineDeps(CONTENT, model)
    events = []
    for g in range(games):
        meta = GameMeta(f"g_vote_{preset}_{seed + g}", PRESETS[preset], seed + g, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "test", CONTENT.content_version)
        r = engine.new_game(meta, deps)
        st, tr, rng = r.state, r.truth, random.Random(seed + g)
        events += r.events
        while st.result is None:
            cmd = bench_player_command(engine.player_view(st, CONTENT), rng)
            r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version}", st.state_version, cmd),
                             deps)
            assert r.accepted, (r.reject, r.reject_detail)
            st, tr = r.state, r.truth
            events += r.events
    return events


def votes(events):
    return [e.payload for e in events if e.event_type is EventType.DECISION and e.payload["kind"] == "vote"]


class EngineVote(unittest.TestCase):
    """엔진 연결: 의심 모델이 있으면 마을 주민 표는 투표 3층, 실패하면 그 결정만 `-fallback`."""

    def roles(self, events):
        """snapshot_id → 관찰자 역할(판마다 역할이 달라 NPC ID로 묶지 않는다)."""
        return {e.payload["snapshot_id"]: e.payload["snapshot"]["private"]["role"]
                for e in events if e.event_type is EventType.OBSERVATION}

    def test_model_votes(self):
        ev = play(FakeSuspicionModel(), games=3)
        vs = votes(ev)
        self.assertTrue(vs)
        self.assertTrue(all(not vt.is_fallback_version(v["policy_version"]) for v in vs))
        self.assertTrue(any(ReasonCode.MODEL_ESTIMATE.value in v["reason_codes"] for v in vs))
        self.assertEqual(vt.fallback_count(ev), 0)

    def test_failure_marks_only_vote_decisions(self):
        ev = play(FakeSuspicionModel(fail=True), games=3)
        roles = self.roles(ev)
        vs = votes(ev)
        villager = [v for v in vs if roles[v["snapshot_id"]] != Role.POSSESSED.value]
        possessed = [v for v in vs if roles[v["snapshot_id"]] == Role.POSSESSED.value]
        self.assertTrue(villager and possessed)
        for v in villager:
            self.assertEqual(v["policy_version"], "rule-v0-fallback")
            self.assertIn(ReasonCode.MODEL_FALLBACK_UNAVAILABLE.value, v["reason_codes"])
        self.assertTrue(all(v["policy_version"] == "rule-v0" for v in possessed))
        others = [e.payload for e in ev if e.event_type is EventType.DECISION and e.payload["kind"] != "vote"]
        self.assertTrue(others and all(p["policy_version"] == "rule-v0" for p in others))
        self.assertEqual(vt.fallback_count(ev), len(villager))

    def test_fallback_is_per_decision(self):
        """호출마다 성공·실패가 번갈아 나는 모델: 같은 판 안에서 실패한 결정만 `-fallback`."""
        fake = FakeSuspicionModel()
        n = {"i": 0}

        def alternate(obs):
            n["i"] += 1
            if n["i"] % 2 == 0:
                raise ModelUnavailable("짝수 호출 실패")
            return fake.estimate(obs)

        m = StubModel(out=alternate)
        ev = play(m, games=2)
        flags = [vt.is_fallback_version(v["policy_version"]) for v in votes(ev)
                 if ReasonCode.MODEL_ESTIMATE.value in v["reason_codes"] or vt.is_fallback_version(v["policy_version"])]
        self.assertIn(True, flags)
        self.assertIn(False, flags)
        self.assertEqual(vt.fallback_count(ev), n["i"] // 2)

    def test_slow_model_times_out(self):
        """실제 시간 초과: 120ms 걸리는 모델은 결과를 버리고 규칙 v0로(엔진 기본 시계 time.monotonic)."""
        fake = FakeSuspicionModel()
        m = StubModel(out=fake.estimate, sleep=0.12)
        ev = play(m, preset="short5")
        vs = [v for v in votes(ev) if vt.is_fallback_version(v["policy_version"])]
        self.assertTrue(vs)
        self.assertTrue(all(ReasonCode.MODEL_FALLBACK_TIMEOUT.value in v["reason_codes"] for v in vs))

    def test_version_mismatch_in_engine(self):
        fake = FakeSuspicionModel()
        m = StubModel(out=fake.estimate, feature_version="feat-other")    # 출력은 fake-1, 선언은 다름
        ev = play(m)
        self.assertTrue(vt.fallback_count(ev) > 0)
        self.assertTrue(all(ReasonCode.MODEL_FALLBACK_VERSION.value in v["reason_codes"]
                            for v in votes(ev) if vt.is_fallback_version(v["policy_version"])))


# ---------------------------------------------------------------- 조건 4: 폴백 횟수 지표
class FallbackMetric(unittest.TestCase):
    def test_metric_name_and_count(self):
        self.assertEqual(vt.FALLBACK_METRIC, "paldo_engine_suspicion_fallback_total")
        self.assertTrue(vt.is_fallback_version("rule-v0-fallback"))
        self.assertFalse(vt.is_fallback_version("rule-v0"))
        self.assertEqual(vt.fallback_count([]), 0)


if __name__ == "__main__":
    unittest.main()
