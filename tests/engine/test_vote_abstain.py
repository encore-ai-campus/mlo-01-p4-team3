"""투표 기권(rule-0.9, 11/11 사용자 결정 ②안) 시험.

- 집계: 기권표는 세지 않고, 모두 기권이면 결선 없이 무감금. 기권을 허용하지 않는 규칙에서 기권표는 오류.
- 엔진: rule-0.9 판의 투표·결선에서만 무당 기권(`CommandKind.ABSTAIN`)이 합법이고, rule-0.8 판은 그대로 거부한다.
- NPC: 마을 주민은 1·2위 점수 차가 문턱 이하이면 기권(규칙 v0·모델 경로 같은 기준), 씐 자는 기권하지 않는다.
- 회귀: rule-0.8 판은 운영 조립(`ContentPolicyEngine`)에서 같은 시드로 바이트 단위 같은 이벤트·상태를 낸다.
"""
from __future__ import annotations

import hashlib
import random
import unittest
from pathlib import Path

from engine.domain import params, serde
from engine.domain.engine import PaldoEngine, abstain_margin_of, abstains
from engine.domain.events import EventType
from engine.domain.model import (
    ENGINE_VERSION, PLAYER_ID, PRESETS, CommandEnvelope, CommandKind, DataSource, GameMeta, Phase, RejectCode,
    resolve_vote,
)
from engine.domain.npc import vote as vt
from engine.domain.observation import Decision, ReasonCode
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.testing.fakes import FakeSuspicionModel
from tests.engine.test_scenarios_engine import (
    CONTENT, DEPS, ScriptedPolicy, drive_to, env, game_base6, step,
)

RULE09 = params.rule_params_version(params.ABSTAIN_RULE_BASE)
ABSTAIN = object()   # 정해 준 NPC 기권


class AbstainScripted(ScriptedPolicy):
    """`ScriptedPolicy` + 정해 준 기권(값 `ABSTAIN`)."""

    def vote(self, obs, candidates, rng):
        round_ = "runoff" if obs.phase is Phase.RUNOFF else "main"
        if self.votes.get((obs.day, round_), {}).get(obs.observer) is ABSTAIN:
            return Decision(None, [ReasonCode.ABSTAIN_UNCERTAIN])
        return super().vote(obs, candidates, rng)


def _rule09(state):
    state.meta.rule_version = RULE09
    return state


class Tally(unittest.TestCase):
    def test_abstain_not_counted(self):
        voters = ["mudang", "a", "b", "c"]
        out = resolve_vote({"mudang": None, "a": "b", "b": "c", "c": "b"}, voters=voters, abstain=True)
        self.assertEqual((out.locked, out.counts), ("b", {"b": 2, "c": 1}))

    def test_all_abstain_no_lock_no_runoff(self):
        out = resolve_vote({"mudang": None, "a": None, "b": None}, voters=["mudang", "a", "b"], abstain=True)
        self.assertEqual((out.locked, out.runoff_candidates, out.counts), (None, [], {}))

    def test_runoff_abstain_and_tie(self):
        main = {"mudang": "a", "a": "b", "b": "a", "c": "b"}
        out = resolve_vote(main, {"mudang": None, "a": "b", "b": "a", "c": None},
                           voters=["mudang", "a", "b", "c"], abstain=True)
        self.assertIsNone(out.locked)
        out = resolve_vote(main, {"mudang": None, "a": "b", "b": None, "c": "b"},
                           voters=["mudang", "a", "b", "c"], abstain=True)
        self.assertEqual(out.locked, "b")

    def test_abstain_rejected_when_rule_disallows(self):
        with self.assertRaises(ValueError):
            resolve_vote({"mudang": None, "a": "b", "b": "a"}, voters=["mudang", "a", "b"])

    def test_rule_version_gate(self):
        self.assertFalse(abstains(params.RULE_PARAMS_VERSION))
        self.assertTrue(abstains(RULE09))
        self.assertTrue(abstains("rule-0.10.decision-2"))
        self.assertFalse(abstains("rule-test"))
        self.assertIsNone(abstain_margin_of(params.RULE_PARAMS_VERSION))
        self.assertEqual(abstain_margin_of(RULE09), params.ABSTAIN_MARGIN)
        self.assertEqual(params.rule_params_version(""), params.RULE_PARAMS_VERSION)
        with self.assertRaises(ValueError):
            params.rule_params_version("rule-9.9")


class EngineAbstain(unittest.TestCase):
    def at_vote(self, votes=None, rule09=True):
        engine = PaldoEngine(AbstainScripted(votes))
        st, tr = game_base6(engine)
        if rule09:
            _rule09(st)
        st, tr = drive_to(self, engine, st, tr, Phase.VOTE)
        return engine, st, tr

    def test_legal_kinds(self):
        engine, st, _ = self.at_vote()
        self.assertIn(CommandKind.ABSTAIN, engine.player_view(st, CONTENT).legal_kinds)
        engine, st, _ = self.at_vote(rule09=False)
        self.assertNotIn(CommandKind.ABSTAIN, engine.player_view(st, CONTENT).legal_kinds)

    def test_rule08_rejects_abstain(self):
        engine, st, tr = self.at_vote(rule09=False)
        r = engine.apply(st, tr, env(st, CommandKind.ABSTAIN), DEPS)
        self.assertIs(r.reject, RejectCode.ILLEGAL_PHASE)

    def test_abstain_outside_vote_rejected(self):
        engine = PaldoEngine(AbstainScripted())
        st, tr = game_base6(engine)
        _rule09(st)
        r = engine.apply(st, tr, env(st, CommandKind.ABSTAIN), DEPS)
        self.assertIs(r.reject, RejectCode.ILLEGAL_PHASE)

    def test_player_abstain_recorded(self):
        engine, st, tr = self.at_vote({(1, "main"): {"hunjang": "yakcho", "jumo": "yakcho", "blacksmith": "yakcho",
                                                     "meosum": "jumo", "yakcho": "jumo"}})
        st, tr, r = step(self, engine, st, tr, CommandKind.ABSTAIN)
        rv = next(e for e in r.events if e.event_type is EventType.VOTES_REVEALED)
        self.assertIsNone(rv.payload["votes"][PLAYER_ID])
        self.assertNotIn(PLAYER_ID, rv.payload["counts"])
        mine = [v for v in st.public.votes if v.voter == PLAYER_ID]
        self.assertEqual([v.target for v in mine], [None])
        self.assertIn("yakcho", st.locked)
        self.assertEqual(serde.loads(type(st), serde.dumps(st)).public.votes, st.public.votes)

    def test_everyone_abstains_no_lock(self):
        everyone = {n: ABSTAIN for n in ("hunjang", "jumo", "blacksmith", "meosum", "yakcho")}
        engine, st, tr = self.at_vote({(1, "main"): everyone})
        st, tr, r = step(self, engine, st, tr, CommandKind.ABSTAIN)
        lock = next(e for e in r.events if e.event_type is EventType.LOCK_RESOLVED)
        self.assertIsNone(lock.payload["locked"])
        self.assertIsNot(st.phase, Phase.RUNOFF)
        dec = [e for e in r.events if e.event_type is EventType.DECISION]
        self.assertTrue(all(e.payload["target"] is None for e in dec if e.payload["kind"] == "vote"))

    def test_npc_abstain_rejected_in_rule08(self):
        engine = PaldoEngine(AbstainScripted({(1, "main"): {"hunjang": ABSTAIN}}))
        st, tr = game_base6(engine)
        with self.assertRaises(ValueError):
            drive_to(self, engine, st, tr, Phase.VOTE)


class NpcAbstain(unittest.TestCase):
    def test_uncertain(self):
        self.assertFalse(vt.uncertain({"a": 0.5, "b": 0.49}, None))
        self.assertTrue(vt.uncertain({"a": 0.5, "b": 0.47}, 0.05))
        self.assertFalse(vt.uncertain({"a": 0.5, "b": 0.4}, 0.05))
        self.assertFalse(vt.uncertain({"a": 0.5}, 0.05))

    def _decisions(self, margin, n=40):
        """rule-0.9 판을 돌려 NPC 투표 결정(역할 포함)을 모은다."""
        from engine.domain.npc import RuleV0Policy
        from tools.check_content import load_bundle
        content = load_bundle(Path("content/bundle_v1.json"))
        deps = EngineDeps(content, FakeSuspicionModel())
        engine = PaldoEngine(RuleV0Policy(content, abstain_margin=margin))
        out = []
        for i in range(n):
            gid = f"abs_{i}"
            meta = GameMeta(gid, PRESETS["base6"], 300 + i, DataSource.BOT, RULE09, ENGINE_VERSION,
                            content.content_version)
            r = engine.new_game(meta, deps)
            st, tr = r.state, r.truth
            bot, rng = LogicBot(), random.Random(i)
            while st.result is None:
                r = engine.apply(st, tr, CommandEnvelope(gid, f"c{st.state_version}", st.state_version,
                                                         bot.choose(engine.player_view(st, content), rng)), deps)
                self.assertTrue(r.accepted)
                st, tr = r.state, r.truth
                out += [(tr.roles[e.payload["actor"]].value, e.payload) for e in r.events
                        if e.event_type is EventType.DECISION and e.payload["kind"] == "vote"]
        return out

    def test_villagers_abstain_possessed_never(self):
        got = self._decisions(0.2)
        abst = [(role, p) for role, p in got if p["target"] is None]
        self.assertTrue(abst, "문턱 0.2에서 기권이 하나도 없음")
        self.assertTrue(all(role != "possessed" for role, _ in abst))
        self.assertTrue(all(p["reason_codes"][0] == "abstain_uncertain" and p["evidence"] == [] for _, p in abst))

    def test_no_margin_no_abstain(self):
        self.assertTrue(all(p["target"] is not None for _, p in self._decisions(None, 10)))


class Rule08Bytes(unittest.TestCase):
    """rule-0.8 판 바이트 회귀: 기권 규칙이 없는 dev(a44dcc2, content-1.1)와 같은 해시. 운영 조립 + FakeSuspicionModel + LogicBot.

    콘텐츠가 바뀌면 해시도 바뀐다. 그때는 기권 규칙이 없는 커밋에서 같은 절차로 다시 구해 맞춘다(fd02936·content-1.0.1 때
    7635b6ab…, 두 커밋에서 같았음)."""

    GOLDEN = "0e9eb0d3ccbbc440ea0d17b9db8218bfa0ad2c3c73cb4358ec3827b8866713e9"

    def test_same_bytes(self):
        from server.infra.game_factory import ContentPolicyEngine
        from tools.check_content import load_bundle
        content = load_bundle(Path("content/bundle_v1.json"))
        deps = EngineDeps(content, FakeSuspicionModel())
        eng = ContentPolicyEngine()
        h = hashlib.sha256()
        for i in range(20):
            seed, gid = 9100 + i, f"golden_{i}"
            meta = GameMeta(gid, PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION, ENGINE_VERSION,
                            content.content_version, model_versions={"suspicion": FakeSuspicionModel.model_version})
            r = eng.new_game(meta, deps)
            st, tr = r.state, r.truth
            h.update(serde.dumps(r.events).encode())
            bot, rng = LogicBot(), random.Random(seed)
            while st.result is None:
                cmd = bot.choose(eng.player_view(st, content), rng)
                r = eng.apply(st, tr, CommandEnvelope(gid, f"c{st.state_version:05d}", st.state_version, cmd), deps)
                self.assertTrue(r.accepted)
                st, tr = r.state, r.truth
                h.update(serde.dumps(r.events).encode())
            h.update(serde.dumps(st).encode())
            h.update(serde.dumps(tr).encode())
        self.assertEqual(params.RULE_PARAMS_VERSION, "rule-0.8.decision-2")
        self.assertEqual(h.hexdigest(), self.GOLDEN)


if __name__ == "__main__":
    unittest.main()
