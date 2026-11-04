"""가짜 구현이 포트 계약을 지키는지, 녹화 판 재생·뷰 모델·배정 검증기가 동작하는지."""
from __future__ import annotations

import math
from dataclasses import replace
import unittest

from engine.domain import serde
from engine.domain.content import Assignment, ContentBundle, validate_assignment
from engine.domain.events import EventEnvelope
from engine.domain.model import (
    PRESETS, Beliefs, Command, CommandEnvelope, CommandKind, DataSource, GameMeta, GameState, GameTruth,
    ObservationSnapshot, Phase, PrivateKnowledge, PublicRecord, RejectCode, Role, InterrogationKind,
)
from engine.ports import (
    ContentSource, EngineDeps, EventSink, GameRepository, IntentParser, LineRenderer, ModelSelector,
    ModelUnavailable, ParseContext, ParseFailure, ProgressRepository, RenderRequest, RenderStatus,
    StoredCommand, SuspicionModel, VersionConflict,
)
from engine.testing.fakes import (
    FakeIntentParser, FakeLineRenderer, FakeModelSelector, FakeSuspicionModel, FixtureContentSource,
    InMemoryGameRepository, InMemoryProgressRepository, ListEventSink, ScriptedGameEngine,
)
from server.api.viewmodels import build_meeting_vm
from server.app.dto import CommandOutput

from ._util import load_json, load_jsonl

META = GameMeta("g_test", PRESETS["base6"], 1, DataSource.SCRIPTED, "rule-0.1", "test", "content-min-0.1")


def make_state(ver: int) -> GameState:
    return GameState(META, ver, 1, Phase.MORNING, ["mudang", "hunjang"])


def obs(cands, k, susp=None) -> ObservationSnapshot:
    return ObservationSnapshot("s", "g", "hunjang", 1, Phase.VOTE, 1, PublicRecord(), PrivateKnowledge("hunjang", Role.VILLAGER),
                               Beliefs(suspicion=susp or {}), [CommandKind.VOTE], cands, k, "base6")


class ProtocolConformance(unittest.TestCase):
    def test_isinstance(self):
        pairs = [(FakeSuspicionModel(), SuspicionModel), (FakeIntentParser(), IntentParser),
                 (FakeLineRenderer(), LineRenderer), (InMemoryGameRepository(), GameRepository),
                 (InMemoryProgressRepository(), ProgressRepository), (FixtureContentSource(), ContentSource),
                 (ListEventSink(), EventSink), (FakeModelSelector(), ModelSelector)]
        for impl, proto in pairs:
            self.assertIsInstance(impl, proto, proto.__name__)


class Suspicion(unittest.TestCase):
    def test_single_ghost_softmax(self):
        e = FakeSuspicionModel().estimate(obs(["a", "b", "c"], 1, {"a": 90, "b": 10}))
        self.assertEqual(e.candidates, ["a", "b", "c"])
        self.assertAlmostEqual(sum(e.probs.values()), 1.0)
        self.assertEqual(max(e.probs, key=e.probs.get), "a")
        self.assertIsNone(e.pair_probs)

    def test_two_ghosts_marginals(self):
        e = FakeSuspicionModel().estimate(obs(["a", "b", "c", "d"], 2))
        self.assertAlmostEqual(sum(e.pair_probs.values()), 1.0)
        self.assertAlmostEqual(sum(e.probs.values()), 2.0)   # 주변확률 합 = 남은 창귀 수
        self.assertTrue(all(0 <= p <= 1 for p in e.probs.values()))

    def test_failure_raises(self):
        with self.assertRaises(ModelUnavailable):
            FakeSuspicionModel(fail=True).estimate(obs(["a"], 1))


class Intent(unittest.TestCase):
    def ctx(self):
        b = FixtureContentSource().load("*")
        return ParseContext("g", 1, Phase.ROUND1, PublicRecord(),
                            {c.npc_id: c.aliases for c in b.characters[:6]})

    def test_multi_intent_targets(self):
        r = FakeIntentParser().parse("훈장은 수상하지만 머슴은 결백해", self.ctx())
        got = {c.command.kind: c.command.target for c in r.candidates}
        self.assertEqual(got, {CommandKind.SUSPECT: "hunjang", CommandKind.DEFEND: "meosum"})
        self.assertTrue(r.needs_confirmation)

    def test_failures(self):
        p = FakeIntentParser()
        self.assertIs(p.parse("  ", self.ctx()).failure, ParseFailure.EMPTY_INPUT)
        self.assertIs(p.parse("배고프다", self.ctx()).failure, ParseFailure.UNSUPPORTED_INTENT)

    def test_interrogate_slots(self):
        r = FakeIntentParser().parse("주모, 어디 있었소?", self.ctx())
        c = r.candidates[0]
        self.assertEqual((c.command.addressee, c.command.interrogation_kind), ("jumo", InterrogationKind.WHEREABOUTS))
        self.assertEqual(sorted(c.missing_slots), ["night", "slot"])


class Renderer(unittest.TestCase):
    def req(self):
        return RenderRequest("j1", "g", 3, "sp", "jumo", "주모", None, "한밤엔 우물가에 있었소.", ["주모"],
                             "한밤엔 우물가에 있었소.")

    def test_modes(self):
        for mode, status in (("done", RenderStatus.DONE), ("timeout", RenderStatus.TIMEOUT),
                             ("fail", RenderStatus.FAILED)):
            r = FakeLineRenderer(mode)
            r.submit(self.req())
            res = r.poll("j1")
            self.assertIs(res.status, status)
            # 폴백 여부는 DONE이 아닐 때만 True(11/4 PM 결정 #18)
            self.assertEqual(res.fallback, status is not RenderStatus.DONE)

    def test_cancel(self):
        r = FakeLineRenderer()
        r.submit(self.req())
        r.cancel("j1")
        self.assertIs(r.poll("j1").status, RenderStatus.CANCELLED)
        self.assertTrue(r.poll("j1").fallback)


class Repository(unittest.TestCase):
    def test_cas_and_outbox(self):
        repo = InMemoryGameRepository()
        repo.create(make_state(0), GameTruth("g_test", {}), [])
        env = serde.from_jsonable(EventEnvelope, load_jsonl("fixtures/events/sample_events.jsonl")[0])
        repo.commit("g_test", 0, make_state(1), GameTruth("g_test", {}), [env], StoredCommand("c1", "{}"))
        self.assertEqual(len(repo.unpublished("g_test")), 1)
        with self.assertRaises(VersionConflict) as cm:
            repo.commit("g_test", 0, make_state(1), GameTruth("g_test", {}), [], StoredCommand("c2", "{}"))
        self.assertEqual((cm.exception.expected, cm.exception.actual), (0, 1))
        with self.assertRaises(ValueError):
            repo.commit("g_test", 1, make_state(5), GameTruth("g_test", {}), [], StoredCommand("c3", "{}"))
        self.assertIsNotNone(repo.find_command("g_test", "c1"))

    def test_sink_dedup(self):
        s = ListEventSink()
        env = serde.from_jsonable(EventEnvelope, load_jsonl("fixtures/events/sample_events.jsonl")[0])
        s.append([env]); s.append([env])
        self.assertEqual(len(s.items), 1)

    def test_selector_canary(self):
        sel = FakeModelSelector(canary_every=2)
        self.assertEqual([sel.select(f"g{i}", None)[1] for i in range(4)], [False, True, False, True])


class ScriptedReplay(unittest.TestCase):
    def test_full_replay(self):
        eng = ScriptedGameEngine()
        deps = EngineDeps(FixtureContentSource().load("*"), FakeSuspicionModel())
        tr = eng.new_game(META, deps)
        st, truth = tr.state, tr.truth
        rec = load_json("fixtures/api/recorded_game_base6.json")
        for step in rec["steps"][1:]:
            req = step["request"]
            cmd = serde.from_jsonable(Command, req["command"])
            # 엉뚱한 명령은 거부되고 상태는 그대로
            bad = eng.apply(st, truth, CommandEnvelope("g_test", "x", st.state_version,
                                                       Command(CommandKind.EXPLAIN, "mudang")), deps)
            if cmd.kind is not CommandKind.EXPLAIN:
                self.assertFalse(bad.accepted)
            stale = eng.apply(st, truth, CommandEnvelope("g_test", "y", st.state_version - 1, cmd), deps)
            self.assertIs(stale.reject, RejectCode.STATE_VERSION_CONFLICT)
            tr = eng.apply(st, truth, CommandEnvelope("g_test", req["command_id"], st.state_version, cmd), deps)
            self.assertTrue(tr.accepted, tr.reject_detail)
            st = tr.state
            vm = build_meeting_vm(eng.player_view(st, deps.content))
            self.assertEqual(vm.state_version, st.state_version)
        self.assertIsNotNone(st.result)
        end = eng.apply(st, truth, CommandEnvelope("g_test", "z", st.state_version,
                                                   Command(CommandKind.ADVANCE, "mudang")), deps)
        self.assertIs(end.reject, RejectCode.GAME_ENDED)

    def test_missing_slot_rejected(self):
        eng = ScriptedGameEngine()
        deps = EngineDeps(FixtureContentSource().load("*"), FakeSuspicionModel())
        st = eng.new_game(META, deps).state
        for kind in ("advance", "suspect"):
            st = eng.apply(st, None, CommandEnvelope("g", "a", st.state_version, Command(
                CommandKind(kind), "mudang", target="jumo")), deps).state
        tr = eng.apply(st, None, CommandEnvelope("g", "b", st.state_version, Command(
            CommandKind.INTERROGATE, "mudang", addressee="jumo", interrogation_kind=InterrogationKind.WHEREABOUTS)), deps)
        self.assertIs(tr.reject, RejectCode.MISSING_SLOT)

    def test_viewmodel_pending_job(self):
        eng = ScriptedGameEngine()
        vm = build_meeting_vm(eng.views[3])
        pending = [s for s in vm.speeches if s.pending_job_id]
        self.assertEqual([s.pending_job_id for s in pending], ["job_001"])
        self.assertIn("interrogate", vm.allowed_actions)
        self.assertNotIn("night_plan", build_meeting_vm(eng.views[6]).allowed_actions)

    def test_command_output_json_roundtrip(self):
        eng = ScriptedGameEngine()
        out = CommandOutput(True, 1, eng.views[1])
        data = serde.to_jsonable(out)
        self.assertEqual(serde.to_jsonable(serde.from_jsonable(CommandOutput, data)), data)


class AssignmentValidator(unittest.TestCase):
    def setUp(self):
        self.b = serde.from_jsonable(ContentBundle, load_json("fixtures/content_min/bundle.json"))

    def test_valid(self):
        for name, pid in (("valid_base6.json", "base6"), ("valid_duo9.json", "duo9")):
            a = serde.from_jsonable(Assignment, load_json(f"fixtures/assignment/{name}"))
            self.assertEqual(validate_assignment(PRESETS[pid], self.b, a), [], name)

    def test_conflicts(self):
        for c in load_json("fixtures/assignment/conflicts.json"):
            with self.subTest(case=c["case_id"]):
                a = serde.from_jsonable(Assignment, c["assignment"])
                got = sorted({v.code for v in validate_assignment(PRESETS[c["preset_id"]], self.b, a)})
                self.assertEqual(got, sorted(c["expect_codes"]))


if __name__ == "__main__":
    unittest.main()


class CodexReviewRegressions(unittest.TestCase):
    """설계 크로스체크(docs/contracts/crosscheck.md)에서 나온 지적의 회귀 테스트."""

    def test_vote_requires_all_voters(self):
        from engine.domain.model import resolve_vote
        voters = ["mudang", "a", "b", "c"]
        main = {"mudang": "a", "a": "b", "b": "a", "c": "b"}
        self.assertEqual(resolve_vote(main, voters=voters).runoff_candidates, ["a", "b"])
        with self.assertRaises(ValueError):
            resolve_vote(main, {"a": "b"}, voters=voters)     # 결선 표 누락
        with self.assertRaises(ValueError):
            resolve_vote({"mudang": "a", "a": "b"}, voters=voters)

    def test_scripted_engine_uses_new_game_id_and_render_requests(self):
        eng = ScriptedGameEngine()
        deps = EngineDeps(FixtureContentSource().load("*"), FakeSuspicionModel())
        meta = replace(META, game_id="g_new")
        st = eng.new_game(meta, deps).state
        tr = None
        for step in load_json("fixtures/api/recorded_game_base6.json")["steps"][1:4]:
            tr = eng.apply(st, None, CommandEnvelope("g_new", step["request"]["command_id"], st.state_version,
                                                     serde.from_jsonable(Command, step["request"]["command"])), deps)
            st = tr.state
        self.assertEqual(eng.player_view(st, deps.content).game_id, "g_new")
        self.assertEqual([(r.job_id, r.game_id) for r in tr.render_requests], [("job_001", "g_new")])

    def test_append_events_dedup_and_seq(self):
        repo = InMemoryGameRepository()
        env = serde.from_jsonable(EventEnvelope, load_jsonl("fixtures/events/sample_events.jsonl")[0])
        repo.create(make_state(0), GameTruth("g_test", {}), [env])
        self.assertTrue(repo.append_events("g_test", [env], "attempt:a1:parsed"))
        self.assertFalse(repo.append_events("g_test", [env], "attempt:a1:parsed"))
        repo.commit("g_test", 0, make_state(1), GameTruth("g_test", {}), [env], StoredCommand("c1", "{}"))
        self.assertEqual([e.seq for e in repo.unpublished("g_test")], [0, 1, 2])
        self.assertEqual(repo.load("g_test")[0].state_version, 1)

    def test_progress_atomic_idempotent(self):
        repo = InMemoryProgressRepository()
        self.assertTrue(repo.add_clue("p", "c1", "g1", 1, "g1:00010"))
        self.assertTrue(repo.add_clue("p", "c2", "g2", 1, "g2:00004"))   # 다른 판의 동시 획득도 보존
        self.assertFalse(repo.add_clue("p", "c1", "g1", 1, "g1:00010"))  # 재처리
        self.assertEqual(sorted(repo.load("p").clues), ["c1", "c2"])
        self.assertEqual(repo.load("p").endings_unlocked, ["exorcism"])

    def test_quote_question_is_interrogation(self):
        b = FixtureContentSource().load("*")
        ctx = ParseContext("g", 1, Phase.ROUND1, PublicRecord(), {c.npc_id: c.aliases for c in b.characters[:6]})
        kinds = [c.command.kind for c in FakeIntentParser().parse("훈장이 수상하다고?", ctx).candidates]
        self.assertEqual(kinds, [CommandKind.INTERROGATE])


class SinkBatchDedup(unittest.TestCase):
    def test_same_batch_duplicates(self):
        env = serde.from_jsonable(EventEnvelope, load_jsonl("fixtures/events/sample_events.jsonl")[0])
        s = ListEventSink()
        s.append([env, env])
        self.assertEqual(len(s.items), 1)


class ViewModelClaims(unittest.TestCase):
    def test_sighting_cards_name_seen_people(self):
        vm = build_meeting_vm(ScriptedGameEngine().views[0])
        card = next(c for c in vm.claims if c.claim_id == "cl05")      # 머슴: 초저녁 주막에서 훈장·주모를 봤다
        self.assertIn("훈장", card.text)
        self.assertIn("봤다", card.text)
