"""A-05-02 회의·캐묻기·투표·종료 판정 테스트(태스크 문서 완료 조건 1~4).

시나리오 체크(`fixtures/scenarios/*.json`)를 기준 규칙 함수 대신 **실제 엔진**(`PaldoEngine.apply`)으로
재현해 같은 기대값을 확인한다. NPC 결정은 `ScriptedPolicy`로 정해 끼우고, 규칙 처리는 엔진이 한다.
"""
from __future__ import annotations

import copy
import json
import unittest
from typing import Optional

from contracts.datasets import Scenario
from engine.domain import params, serde
from engine.domain.content import Assignment, validate_assignment
from engine.domain.engine import (
    BaselinePolicy, PaldoEngine, assignment_of, end_reason, setup_from_assignment,
)
from engine.domain.events import EventType
from engine.domain.model import (
    LIMITS, PHASE_RULES, PLAYER_ID, PRESETS, ClaimType, Command, CommandEnvelope, CommandKind,
    DataSource, DivinationPayload, DivinationResult, EvidenceRef, EvidenceType, GameMeta, InterrogationKind,
    NightPlan, Phase, RejectCode, Role, Slot,
)
from engine.ports import EngineDeps
from engine.testing.fakes import FIXTURES, FakeSuspicionModel, FixtureContentSource

CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
SCENARIOS = ("normal_game", "runoff_tie_no_lock", "mudang_locked", "last_possessed_locked")
VALID_BASE6 = "fixtures/assignment/valid_base6.json"
DAY1_LOCK_YAKCHO = {"hunjang": "yakcho", "jumo": "yakcho", "blacksmith": "yakcho", "meosum": "yakcho",
                    "yakcho": "jumo"}   # 녹화 판 1일째 NPC 표(약초꾼 감금, 씐 자 아님)


def load(rel: str) -> dict:
    return json.loads((FIXTURES.parent / rel).read_text(encoding="utf-8"))


def scenario(sid: str) -> Scenario:
    return serde.from_jsonable(Scenario, load(f"fixtures/scenarios/{sid}.json"))


class ScriptedPolicy(BaselinePolicy):
    """정해진 NPC 결정. 없으면 기본 규칙. votes[(day, round)][npc] = 대상, nights[day] = NightPlan."""

    def __init__(self, votes: Optional[dict] = None, nights: Optional[dict] = None):
        self.votes = votes or {}
        self.nights = nights or {}

    def vote(self, state, npc_id, candidates, rng):
        round_ = "runoff" if state.phase is Phase.RUNOFF else "main"
        got = self.votes.get((state.day, round_), {}).get(npc_id)
        return got if got is not None else super().vote(state, npc_id, candidates, rng)

    def night_plan(self, state, rng):
        return self.nights.get(state.day) or super().night_plan(state, rng)


def meta(preset_id: str, seed: int = 0, game_id: str = "g_test") -> GameMeta:
    return GameMeta(game_id, PRESETS[preset_id], seed, DataSource.SCRIPTED,
                    params.RULE_PARAMS_VERSION, "test", CONTENT.content_version)


def game_base6(engine: PaldoEngine, seed: int = 0):
    """valid_base6 배정(훈장·주모·대장장이(씐 자)·머슴·약초꾼, 0일째 과부 실종)으로 1일째 아침."""
    a = serde.from_jsonable(Assignment, load(VALID_BASE6))
    r = engine.new_game_from_setup(meta("base6", seed, a.game_id), DEPS, setup_from_assignment(a, CONTENT, seed))
    return r.state, r.truth


def env(state, kind: CommandKind, **kw) -> CommandEnvelope:
    return CommandEnvelope(state.meta.game_id, f"c{state.state_version:03d}", state.state_version,
                           Command(kind, PLAYER_ID, **kw))


def step(t: unittest.TestCase, engine: PaldoEngine, state, truth, kind: CommandKind, **kw):
    r = engine.apply(state, truth, env(state, kind, **kw), DEPS)
    t.assertTrue(r.accepted, f"{kind.value} 거부: {r.reject} {r.reject_detail}")
    t.assertEqual(r.state.state_version, state.state_version + 1)
    return r.state, r.truth, r


def drive_to(t, engine, state, truth, phase: Phase):
    """아침 → 1라운드 → 캐묻기 → 2라운드 → 투표 순서로 플레이어가 최소 행동(advance·침묵)만 해서 phase까지 간다."""
    plan = {Phase.MORNING: CommandKind.ADVANCE, Phase.ROUND1: CommandKind.SILENCE,
            Phase.INTERROGATION: CommandKind.ADVANCE, Phase.ROUND2: CommandKind.SILENCE}
    while state.phase is not phase:
        state, truth, _ = step(t, engine, state, truth, plan[state.phase])
    return state, truth


def dump(x) -> str:
    return json.dumps(serde.to_jsonable(x), ensure_ascii=False, sort_keys=True)


def end_payload(events) -> list:
    return [(e.payload["locked"], e.payload["revealed_possessed"], e.payload["end"])
            for e in events if e.event_type is EventType.LOCK_RESOLVED]


# ---------------------------------------------------------------- 시나리오 체크를 실제 엔진으로
def _compose(engine, t, preset_id: str, g: int, v: int, m: bool):
    """활동 인원을 (씐 자 g, 마을 주민 v, 무당 m)으로 줄인 2라운드 직전 상태. 뺀 사람은 이전에 갇힌 것으로 둔다."""
    for pid in [preset_id] + sorted(PRESETS, key=lambda p: PRESETS[p].n_active):
        p = PRESETS[pid]
        if g <= p.n_possessed and v <= p.n_active - 1 - p.n_possessed:
            break
    else:
        raise AssertionError(f"구성 불가 g={g} v={v}")
    r = engine.new_game(meta(pid), DEPS)
    st, tr = r.state, r.truth
    poss = [n for n in st.active if tr.roles[n] is Role.POSSESSED][:g]
    vill = [n for n in st.active if tr.roles[n] is Role.VILLAGER][:v]
    keep = ([PLAYER_ID] if m else []) + poss + vill
    for n in [n for n in st.active if n not in keep]:
        st.active.remove(n)
        st.locked.append(n)
    return st, tr


def eval_end_check(t, engine_factory, sc: Scenario, i: dict) -> dict:
    """감금 직후 활동 인원이 (g, v, M)이 되도록 판을 만들고 실제 투표로 가둬 엔진의 종료 판정을 본다."""
    g, v, m_active, m_locked = i["g"], i["v"], i["mudang_active"], i["mudang_locked"]
    if m_locked:
        st, tr = _compose(PaldoEngine(), t, sc.preset_id, g, v, True)
        target = PLAYER_ID
    elif g == 0:
        st, tr = _compose(PaldoEngine(), t, sc.preset_id, 1, v, m_active)
        target = next(n for n in st.active if tr.roles[n] is Role.POSSESSED)
    else:
        st, tr = _compose(PaldoEngine(), t, sc.preset_id, g, v + 1, m_active)
        target = next(n for n in st.active if tr.roles[n] is Role.VILLAGER)
    other = next(n for n in st.active if n not in (PLAYER_ID, target))
    votes = {n: (target if n != target else other) for n in st.active if n != PLAYER_ID}
    engine = engine_factory(ScriptedPolicy(votes={(1, "main"): votes}))
    st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
    st, tr, r = step(t, engine, st, tr, CommandKind.VOTE, target=target if target != PLAYER_ID else other)
    t.assertEqual(st.locked[-1], target)
    gg = sum(1 for n in st.active if tr.roles[n] is Role.POSSESSED)
    vv = sum(1 for n in st.active if tr.roles[n] is Role.VILLAGER)
    t.assertEqual((gg, vv, PLAYER_ID in st.active, PLAYER_ID in st.locked), (g, v, m_active, m_locked))
    (_, _, end), = end_payload(r.events)
    out = {"reason": end, "night_runs": st.phase is Phase.NIGHT}
    if st.result is not None:
        t.assertEqual(st.result.reason.value, end)
        t.assertIs(st.phase, Phase.ENDED)
        out["winner"] = st.result.winner.value
    return out


def eval_vote(t, engine_factory, i: dict) -> dict:
    """valid_base6 판(활동 6명)에서 NPC 표를 주고 무당 표를 실제로 내 감금·결선 결과를 본다."""
    main, runoff = i["main"], i.get("runoff")
    votes = {(1, "main"): {k: x for k, x in main.items() if k != PLAYER_ID}}
    if runoff:
        votes[(1, "runoff")] = {k: x for k, x in runoff.items() if k != PLAYER_ID}
    engine = engine_factory(ScriptedPolicy(votes=votes))
    st, tr = game_base6(engine)
    t.assertEqual(sorted(st.active), sorted(main))
    st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
    st, tr, r = step(t, engine, st, tr, CommandKind.VOTE, target=main[PLAYER_ID])
    if st.phase is Phase.RUNOFF and runoff:
        st, tr, r = step(t, engine, st, tr, CommandKind.VOTE, target=runoff[PLAYER_ID])
    locks = [x.locked for x in st.public.locks if x.day == 1]
    return {"locked": locks[0] if locks else None, "runoff_candidates": list(st.runoff_candidates)}


def eval_phase_legal(t, engine_factory, i: dict) -> dict:
    phase, kind, is_player = Phase(i["phase"]), CommandKind(i["kind"]), i["is_player"]
    engine = engine_factory(ScriptedPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO}))
    st, tr = game_base6(engine)
    if phase is Phase.NIGHT:
        st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
        st, tr, _ = step(t, engine, st, tr, CommandKind.VOTE, target="yakcho")
    else:
        st, tr = drive_to(t, engine, st, tr, phase)
    t.assertIs(st.phase, phase)
    if not is_player:
        # NPC 명령은 엔진이 스스로 낸다. 밤 명령(night_plan)은 밤에 들어갈 때 정해져 진실에 남는다.
        if kind is CommandKind.NIGHT_PLAN:
            return {"legal": st.day in tr.night_plans}
        raise AssertionError(f"엔진 재생 미지원 NPC 체크: {phase} {kind}")
    r = engine.apply(st, tr, env(st, kind, **sample_slots(st, kind)), DEPS)
    return {"legal": r.reject is not RejectCode.ILLEGAL_PHASE}


def sample_slots(st, kind: CommandKind) -> dict:
    """필수 슬롯을 채운 견본 명령 인자(단계 합법성만 보려고 대상·근거를 유효하게 채운다)."""
    npc = next(n for n in st.active if n != PLAYER_ID)
    claims = [c.claim_id for c in st.public.claims][:2]
    return {
        CommandKind.SUSPECT: {"target": npc},
        CommandKind.DEFEND: {"target": npc},
        CommandKind.POINT_CONTRADICTION: {"evidence": [EvidenceRef(EvidenceType.CLAIM, c) for c in claims]},
        CommandKind.EXPLAIN: {},
        CommandKind.SILENCE: {},
        CommandKind.REVEAL_DIVINATION: {"divination": DivinationPayload(npc, 1, DivinationResult.CLEAR)},
        CommandKind.INTERROGATE: {"addressee": npc, "interrogation_kind": InterrogationKind.WHEREABOUTS,
                                  "night": 0, "slot": Slot.MIDNIGHT},
        CommandKind.VOTE: {"target": npc},
        CommandKind.DIVINE: {"target": npc},
        CommandKind.NIGHT_PLAN: {"night_plan": NightPlan(npc, npc)},
        CommandKind.ADVANCE: {},
    }[kind]


def eval_assignment(t, engine_factory, i: dict) -> dict:
    """배정 픽스처로 실제 엔진이 판을 만들고, 판에서 다시 꺼낸 배정을 검증기에 넣는다."""
    a = serde.from_jsonable(Assignment, load(i["assignment"]))
    p = PRESETS[i["preset_id"]]
    r = engine_factory(None).new_game_from_setup(meta(p.preset_id, 0, a.game_id), DEPS,
                                                 setup_from_assignment(a, CONTENT, 0))
    return {"violations": [x.code for x in validate_assignment(p, CONTENT, assignment_of(r.state, r.truth))]}


def evaluate_with_engine(t, sc: Scenario, check, engine_factory=lambda pol: PaldoEngine(pol)) -> dict:
    f = {"vote": lambda: eval_vote(t, engine_factory, check.input),
         "end_check": lambda: eval_end_check(t, engine_factory, sc, check.input),
         "phase_legal": lambda: eval_phase_legal(t, engine_factory, check.input),
         "assignment": lambda: eval_assignment(t, engine_factory, check.input)}.get(check.type)
    if f is None:
        raise AssertionError(f"엔진 재생 미지원 체크: {check.type}")
    return f()


class ScenarioChecksWithEngine(unittest.TestCase):
    """조건 1: 시나리오 4개의 모든 checks를 실제 엔진으로 통과."""

    def test_all_checks(self):
        n = 0
        for sid in SCENARIOS:
            sc = scenario(sid)
            self.assertTrue(sc.checks)
            for k, c in enumerate(sc.checks):
                with self.subTest(scenario=sid, check=k, type=c.type):
                    got = evaluate_with_engine(self, sc, c)
                    for key, want in c.expect.items():
                        self.assertEqual(got.get(key), want, f"{key}: {c.note}")
                    n += 1
        self.assertEqual(n, 13)


class NormalGameReplay(unittest.TestCase):
    """조건 1: normal_game의 steps(녹화 판의 플레이어 명령 12개)를 실제 엔진으로 재생한다.

    NPC 표와 밤 홀림은 녹화 판의 결정을 `ScriptedPolicy`로 준다. 단계·인원·결과·합법 명령·횟수는 엔진이 정한다.
    """

    def test_replay_recording(self):
        sc = scenario("normal_game")
        rec = load(sc.steps[0]["recording"])
        final = rec["steps"][-1]["view"]["public"]
        votes: dict = {}
        for v in final["votes"]:
            if v["voter"] != PLAYER_ID:
                votes.setdefault((v["day"], v["round"]), {})[v["voter"]] = v["target"]
        nights = {d["night"]: NightPlan("blacksmith", d["victim"]) for d in final["disappearances"] if d["night"] > 0}
        engine = PaldoEngine(ScriptedPolicy(votes, nights))
        st, tr = game_base6(engine)
        claim_map: dict[str, str] = {}
        for k, s in enumerate(rec["steps"][1:], start=1):
            cmd = serde.from_jsonable(Command, s["request"]["command"])
            for e in cmd.evidence:
                e.record_id = claim_map.get(e.record_id, e.record_id)
            r = engine.apply(st, tr, CommandEnvelope(st.meta.game_id, s["request"]["command_id"],
                                                     s["request"]["expected_state_version"], cmd), DEPS)
            with self.subTest(step=k, kind=cmd.kind.value):
                self.assertTrue(r.accepted, f"{r.reject} {r.reject_detail}")
                st, tr = r.state, r.truth
                want, got = s["view"], engine.player_view(st, CONTENT)
                self.assertEqual(got.state_version, want["state_version"])
                self.assertEqual((got.day, got.phase.value), (want["day"], want["phase"]))
                self.assertEqual((got.active, got.locked, got.missing),
                                 (want["active"], want["locked"], want["missing"]))
                self.assertEqual([x.value for x in got.legal_kinds], want["legal_kinds"])
                self.assertEqual((got.player.interrogations_left, got.player.reveals_left_today),
                                 (want["player"]["interrogations_left"], want["player"]["reveals_left_today"]))
                self.assertEqual({str(n): {t: x.value for t, x in d.items()} for n, d in got.player.divinations.items()},
                                 want["player"]["divinations"])
                self.assertEqual(serde.to_jsonable(got.result), want["result"])
                want_locks = [(e["payload"]["locked"], e["payload"]["revealed_possessed"], e["payload"]["end"])
                              for e in s.get("events", []) if e["event_type"] == "lock_resolved"]
                self.assertEqual(end_payload(r.events), want_locks)
            if cmd.kind is CommandKind.REVEAL_DIVINATION:
                rec_claim = next(e["payload"]["claim_id"] for e in s["events"] if e["event_type"] == "claim_made")
                claim_map[rec_claim] = st.public.claims[-1].claim_id
        self.assertEqual(st.result.reason.value, "all_possessed_locked")


# ---------------------------------------------------------------- 조건 2: 거부와 상태 불변
class Rejections(unittest.TestCase):
    """조건 2: PHASE_RULES 밖 명령·LIMITS 초과 명령은 거부되고 상태(state_version 포함)가 바뀌지 않는다."""

    def setUp(self):
        self.engine = PaldoEngine(ScriptedPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO},
                                                 nights={1: NightPlan("blacksmith", "hunjang")}))
        self.st0, self.tr0 = game_base6(self.engine)

    def assertRejected(self, st, tr, e: CommandEnvelope, code: RejectCode):
        before = (dump(st), dump(tr), st.state_version)
        r = self.engine.apply(st, tr, e, DEPS)
        self.assertFalse(r.accepted)
        self.assertIs(r.reject, code, r.reject_detail)
        self.assertIsNone(r.state)
        self.assertEqual(r.events, [])
        self.assertEqual((dump(st), dump(tr), st.state_version), before)

    def states_by_phase(self):
        e = self.engine
        st, tr = self.st0, self.tr0
        out = {Phase.MORNING: (st, tr)}
        for ph in (Phase.ROUND1, Phase.INTERROGATION, Phase.ROUND2, Phase.VOTE):
            st, tr = drive_to(self, e, st, tr, ph)
            out[ph] = (st, tr)
        st, tr, _ = step(self, e, st, tr, CommandKind.VOTE, target="yakcho")
        out[Phase.NIGHT] = (st, tr)
        return out

    def test_illegal_phase_every_kind(self):
        n = 0
        for ph, (st, tr) in self.states_by_phase().items():
            for kind in CommandKind:
                if kind in PHASE_RULES[ph].player_kinds:
                    continue
                with self.subTest(phase=ph.value, kind=kind.value):
                    self.assertRejected(st, tr, env(st, kind, **sample_slots(st, kind)), RejectCode.ILLEGAL_PHASE)
                    n += 1
        self.assertGreater(n, 40)

    def test_legal_kinds_accepted(self):
        for ph, (st, tr) in self.states_by_phase().items():
            for kind in PHASE_RULES[ph].player_kinds:
                if kind is CommandKind.REVEAL_DIVINATION:   # 1일째에는 점친 적이 없어 대상 거부
                    continue
                with self.subTest(phase=ph.value, kind=kind.value):
                    r = self.engine.apply(st, tr, env(st, kind, **sample_slots(st, kind)), DEPS)
                    self.assertTrue(r.accepted, f"{r.reject} {r.reject_detail}")

    def test_interrogation_limit(self):
        st, tr = drive_to(self, self.engine, self.st0, self.tr0, Phase.INTERROGATION)
        q = sample_slots(st, CommandKind.INTERROGATE)
        for left in (1, 0):
            st, tr, _ = step(self, self.engine, st, tr, CommandKind.INTERROGATE, **q)
            self.assertEqual(st.player.interrogations_left, left)
        self.assertRejected(st, tr, env(st, CommandKind.INTERROGATE, **q), RejectCode.LIMIT_EXCEEDED)
        self.assertEqual([k.value for k in self.engine.player_view(st, CONTENT).legal_kinds], ["advance"])
        self.assertEqual(LIMITS["interrogations_per_day"], 2)

    def test_reveal_limit_per_day(self):
        st, tr = self._day2_round1()
        rv = {"divination": DivinationPayload("blacksmith", 1, DivinationResult.CLEAR)}   # 거짓 공개도 합법
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.REVEAL_DIVINATION, **rv)
        self.assertEqual(st.player.reveals_left_today, 0)
        st, tr = drive_to(self, self.engine, st, tr, Phase.ROUND2)
        self.assertRejected(st, tr, env(st, CommandKind.REVEAL_DIVINATION, **rv), RejectCode.LIMIT_EXCEEDED)
        self.assertNotIn(CommandKind.REVEAL_DIVINATION, self.engine.player_view(st, CONTENT).legal_kinds)

    def test_talk_limit_per_round(self):
        st, tr = drive_to(self, self.engine, self.st0, self.tr0, Phase.ROUND1)
        st = copy.deepcopy(st)
        st.player.talk_used_this_round = True
        self.assertRejected(st, tr, env(st, CommandKind.SILENCE), RejectCode.LIMIT_EXCEEDED)

    def test_other_rejections(self):
        st, tr = drive_to(self, self.engine, self.st0, self.tr0, Phase.ROUND1)
        e = env(st, CommandKind.SILENCE)
        e.expected_state_version -= 1
        self.assertRejected(st, tr, e, RejectCode.STATE_VERSION_CONFLICT)
        self.assertRejected(st, tr, env(st, CommandKind.SUSPECT), RejectCode.MISSING_SLOT)
        self.assertRejected(st, tr, env(st, CommandKind.SUSPECT, target=PLAYER_ID), RejectCode.ILLEGAL_TARGET)
        self.assertRejected(st, tr, env(st, CommandKind.SUSPECT, target="gwabu"), RejectCode.ILLEGAL_TARGET)
        self.assertRejected(st, tr, env(st, CommandKind.SUSPECT, target="jumo",
                                        evidence=[EvidenceRef(EvidenceType.CLAIM, "cl99")]),
                            RejectCode.UNKNOWN_EVIDENCE)
        self.assertRejected(st, tr, env(st, CommandKind.POINT_CONTRADICTION,
                                        evidence=[EvidenceRef(EvidenceType.CLAIM, "cl01")]), RejectCode.MISSING_SLOT)
        npc_cmd = env(st, CommandKind.SILENCE)
        npc_cmd.command.actor = "jumo"
        self.assertRejected(st, tr, npc_cmd, RejectCode.NOT_YOUR_TURN)
        st2, tr2 = drive_to(self, self.engine, st, tr, Phase.INTERROGATION)
        self.assertRejected(st2, tr2, env(st2, CommandKind.INTERROGATE, addressee="jumo",
                                          interrogation_kind=InterrogationKind.WHEREABOUTS, night=1,
                                          slot=Slot.MIDNIGHT), RejectCode.ILLEGAL_TARGET)

    def test_game_ended_rejects(self):
        engine = PaldoEngine(ScriptedPolicy(votes={(1, "main"): {
            "hunjang": "blacksmith", "jumo": "blacksmith", "meosum": "blacksmith", "yakcho": "blacksmith",
            "blacksmith": "jumo"}}))
        st, tr = game_base6(engine)
        st, tr = drive_to(self, engine, st, tr, Phase.VOTE)
        st, tr, _ = step(self, engine, st, tr, CommandKind.VOTE, target="blacksmith")
        self.assertIs(st.phase, Phase.ENDED)
        self.assertRejected(st, tr, env(st, CommandKind.ADVANCE), RejectCode.GAME_ENDED)
        self.assertEqual(engine.player_view(st, CONTENT).legal_kinds, [])

    def test_accepted_does_not_mutate_input(self):
        st, tr = self.st0, self.tr0
        for kind in (CommandKind.ADVANCE, CommandKind.SILENCE, CommandKind.ADVANCE, CommandKind.SILENCE):
            before = (dump(st), dump(tr))
            st2, tr2, _ = step(self, self.engine, st, tr, kind)
            self.assertEqual((dump(st), dump(tr)), before)
            st, tr = st2, tr2

    def test_deterministic(self):
        def run():
            st, tr = game_base6(PaldoEngine(), seed=7)
            st, tr = drive_to(self, self.engine, st, tr, Phase.VOTE)
            st, tr, r = step(self, self.engine, st, tr, CommandKind.VOTE, target="jumo")
            return dump(st), dump(tr), dump(r.events)
        self.assertEqual(run(), run())

    def _day2_round1(self):
        st, tr = drive_to(self, self.engine, self.st0, self.tr0, Phase.VOTE)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.VOTE, target="yakcho")
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.DIVINE, target="blacksmith")
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.ADVANCE)
        self.assertEqual((st.day, st.phase), (2, Phase.ROUND1))
        return st, tr


# ---------------------------------------------------------------- 조건 3·4: 투표·종료 판정·감금 공개
class Voting(unittest.TestCase):
    """조건 3: 동시 비공개·자기 투표 금지·결선·재동점 무감금, 조건 4: 감금 시 씐 자 여부 공개."""

    TIE = {"hunjang": "jumo", "jumo": "yakcho", "blacksmith": "yakcho", "meosum": "hunjang", "yakcho": "hunjang"}

    def at_vote(self, votes=None, nights=None):
        engine = PaldoEngine(ScriptedPolicy(votes, nights))
        st, tr = game_base6(engine)
        st, tr = drive_to(self, engine, st, tr, Phase.VOTE)
        return engine, st, tr

    def test_simultaneous_secret(self):
        engine, st, tr = self.at_vote()
        self.assertEqual(sorted(st.pending_votes), sorted(n for n in st.active if n != PLAYER_ID))
        self.assertEqual(st.public.votes, [])
        view = engine.player_view(st, CONTENT)
        self.assertEqual(view.public.votes, [])
        self.assertNotIn("pending_votes", serde.to_jsonable(view))
        npc_votes = []
        for target in ("jumo", "yakcho", "meosum"):
            _, _, r = step(self, engine, st, tr, CommandKind.VOTE, target=target)
            rv = next(e for e in r.events if e.event_type is EventType.VOTES_REVEALED)
            self.assertEqual(rv.payload["votes"][PLAYER_ID], target)
            npc_votes.append({k: x for k, x in rv.payload["votes"].items() if k != PLAYER_ID})
        self.assertEqual(npc_votes[0], npc_votes[1])
        self.assertEqual(npc_votes[0], npc_votes[2])
        self.assertEqual(npc_votes[0], st.pending_votes)

    def test_self_vote_forbidden(self):
        engine, st, tr = self.at_vote()
        r = engine.apply(st, tr, env(st, CommandKind.VOTE, target=PLAYER_ID), DEPS)
        self.assertIs(r.reject, RejectCode.ILLEGAL_TARGET)
        for npc, target in st.pending_votes.items():
            self.assertNotEqual(npc, target)

    def test_runoff_then_tie_no_lock(self):
        engine, st, tr = self.at_vote({(1, "main"): self.TIE, (1, "runoff"): self.TIE})
        st, tr, r = step(self, engine, st, tr, CommandKind.VOTE, target="jumo")
        self.assertIs(st.phase, Phase.RUNOFF)
        self.assertEqual(st.runoff_candidates, ["hunjang", "jumo", "yakcho"])
        self.assertEqual(engine.player_view(st, CONTENT).runoff_candidates, ["hunjang", "jumo", "yakcho"])
        self.assertEqual(end_payload(r.events), [])
        bad = engine.apply(st, tr, env(st, CommandKind.VOTE, target="meosum"), DEPS)
        self.assertIs(bad.reject, RejectCode.ILLEGAL_TARGET)
        st, tr, r = step(self, engine, st, tr, CommandKind.VOTE, target="jumo")
        self.assertEqual(end_payload(r.events), [(None, None, None)])
        self.assertEqual(st.public.locks[-1].locked, None)
        self.assertEqual(len(st.active), 6)
        self.assertIs(st.phase, Phase.NIGHT)
        self.assertEqual([v.round for v in st.public.votes], ["main"] * 6 + ["runoff"] * 6)

    def test_runoff_locks_single_top(self):
        runoff = {"hunjang": "jumo", "jumo": "yakcho", "blacksmith": "jumo", "meosum": "jumo", "yakcho": "hunjang"}
        engine, st, tr = self.at_vote({(1, "main"): self.TIE, (1, "runoff"): runoff})
        st, tr, _ = step(self, engine, st, tr, CommandKind.VOTE, target="jumo")
        st, tr, r = step(self, engine, st, tr, CommandKind.VOTE, target="jumo")
        self.assertEqual(end_payload(r.events), [("jumo", False, None)])
        self.assertIn("jumo", st.locked)

    def test_lock_reveal_event(self):
        """조건 4: 감금 시 씐 자 여부가 공개 이벤트·공개 기록에 남는다."""
        for target, possessed in (("yakcho", False), ("blacksmith", True)):
            votes = {n: target for n in ("hunjang", "jumo", "blacksmith", "meosum", "yakcho") if n != target}
            votes[target] = "jumo"
            engine, st, tr = self.at_vote({(1, "main"): votes})
            st, tr, r = step(self, engine, st, tr, CommandKind.VOTE, target=target)
            with self.subTest(target=target):
                (locked, revealed, _), = end_payload(r.events)
                self.assertEqual((locked, revealed), (target, possessed))
                self.assertEqual((st.public.locks[-1].locked, st.public.locks[-1].revealed_possessed),
                                 (target, possessed))
                self.assertIs(tr.roles[target] is Role.POSSESSED, possessed)


class EndOrder(unittest.TestCase):
    """조건 3: 종료 판정 순서 G=0 → 무당 감금 → G≥V+M, 종료 사유 기록. 감금 직후와 밤 직후 같은 판정."""

    def finish(self, preset_id, keep_g, keep_v, target_role, seed=0):
        st, tr = _compose(PaldoEngine(), self, preset_id, keep_g, keep_v, True)
        target = PLAYER_ID if target_role is Role.MUDANG else next(n for n in st.active if tr.roles[n] is target_role)
        other = next(n for n in st.active if n not in (PLAYER_ID, target))
        votes = {n: (target if n != target else other) for n in st.active if n != PLAYER_ID}
        engine = PaldoEngine(ScriptedPolicy(votes={(1, "main"): votes}))
        st, tr = drive_to(self, engine, st, tr, Phase.VOTE)
        st, tr, r = step(self, engine, st, tr, CommandKind.VOTE, target=other if target == PLAYER_ID else target)
        return st, tr, r

    def reasons(self, r):
        return ([e.payload["end"] for e in r.events if e.event_type is EventType.LOCK_RESOLVED],
                [(e.payload["winner"], e.payload["reason"]) for e in r.events if e.event_type is EventType.GAME_ENDED])

    def test_all_possessed_locked_first(self):
        st, tr, r = self.finish("base6", 1, 1, Role.POSSESSED)   # 감금 뒤 G=0, V=1 (G≥V+M 아님)
        self.assertEqual(self.reasons(r), (["all_possessed_locked"], [("village", "all_possessed_locked")]))
        self.assertEqual((st.result.reason.value, st.result.day), ("all_possessed_locked", 1))
        self.assertNotIn(1, tr.night_plans)   # 마지막 씐 자 감금이면 밤을 실행하지 않는다

    def test_last_possessed_duo(self):
        st, tr, r = self.finish("duo9", 1, 3, Role.POSSESSED)
        self.assertEqual(self.reasons(r)[1], [("village", "all_possessed_locked")])
        self.assertIs(st.phase, Phase.ENDED)

    def test_mudang_locked_before_parity(self):
        st, tr, r = self.finish("base6", 1, 1, Role.MUDANG)      # 무당 감금 뒤 G=1 ≥ V+M=1 도 참
        self.assertEqual(self.reasons(r)[1], [("changgwi", "mudang_locked")])
        self.assertEqual(end_reason(st, tr).value, "mudang_locked")

    def test_parity_after_lock(self):
        st, tr, r = self.finish("base6", 1, 2, Role.VILLAGER)    # 감금 뒤 G=1, V=1, M=1 → 1 ≥ 2 아님
        self.assertIs(st.phase, Phase.NIGHT)
        st, tr, r = self.finish("duo9", 2, 2, Role.VILLAGER)     # 감금 뒤 G=2, V=1, M=1 → 2 ≥ 2
        self.assertEqual(self.reasons(r)[1], [("changgwi", "possessed_parity")])

    def test_parity_after_night(self):
        """밤 처리 직후에도 같은 판정: 감금 뒤 G=1, V=1, M=1 → 밤에 마을 주민 실종 → G=1 ≥ V+M=1."""
        st, tr, r = self.finish("base6", 1, 2, Role.VILLAGER)    # 감금 뒤 G=1, V=1, M=1
        engine = PaldoEngine()
        npc = next(n for n in st.active if n != PLAYER_ID)
        st, tr, r = step(self, engine, st, tr, CommandKind.DIVINE, target=npc)
        kinds = [e.event_type for e in r.events]
        self.assertEqual(kinds, [EventType.NIGHT_RESOLVED, EventType.DIVINATION_DONE, EventType.GAME_ENDED])
        self.assertEqual(r.events[0].payload["end"], "possessed_parity")
        self.assertEqual(st.result.reason.value, "possessed_parity")
        self.assertIn(npc, st.player.divinations[1])   # 대상이 그 밤에 실종돼도 점 결과는 전달


class Interrogation(unittest.TestCase):
    """캐묻기: 낮마다 2회 소비, 행적 질문은 같은 밤·시간대의 기존 주장을 되풀이, 렌더 요청 생성."""

    def test_whereabouts_answer(self):
        engine = PaldoEngine()
        st, tr = game_base6(engine)
        st, tr = drive_to(self, engine, st, tr, Phase.INTERROGATION)
        n_claims = len(st.public.claims)
        prior = next(c for c in st.public.claims if c.speaker == "jumo" and c.claim_type is ClaimType.WHEREABOUTS
                     and c.night == 0 and c.slot is Slot.MIDNIGHT)
        st, tr, r = step(self, engine, st, tr, CommandKind.INTERROGATE, addressee="jumo",
                         interrogation_kind=InterrogationKind.WHEREABOUTS, night=0, slot=Slot.MIDNIGHT)
        sp = st.public.speeches[-1]
        self.assertEqual((sp.speaker, sp.addressee, sp.claim_ids), ("jumo", PLAYER_ID, [prior.claim_id]))
        self.assertEqual(len(st.public.claims), n_claims)
        (req,) = r.render_requests
        self.assertEqual((req.speech_id, req.npc_id, req.fact_sentence, req.state_version),
                         (sp.speech_id, "jumo", sp.fact_sentence, st.state_version))
        self.assertTrue(req.fallback_text)
        self.assertEqual(engine.player_view(st, CONTENT).pending_render_jobs, [req.job_id])
        self.assertEqual([e.event_type for e in r.events], [EventType.INTERROGATION_ANSWERED])

    def test_executor_uses_agreed_alibi(self):
        """밤 명령에 합의 알리바이가 있으면 실행자는 그 밤 한밤 행적 질문에 그 장소로 답한다."""
        engine = PaldoEngine(ScriptedPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO},
                                            nights={1: NightPlan("blacksmith", "hunjang", {"blacksmith": "shrine"})}))
        st, tr = game_base6(engine)
        st, tr = drive_to(self, engine, st, tr, Phase.VOTE)
        st, tr, _ = step(self, engine, st, tr, CommandKind.VOTE, target="yakcho")
        st, tr, _ = step(self, engine, st, tr, CommandKind.DIVINE, target="jumo")
        st, tr = drive_to(self, engine, st, tr, Phase.INTERROGATION)
        st, tr, _ = step(self, engine, st, tr, CommandKind.INTERROGATE, addressee="blacksmith",
                         interrogation_kind=InterrogationKind.WHEREABOUTS, night=1, slot=Slot.MIDNIGHT)
        (cid,) = st.public.speeches[-1].claim_ids          # A-06-01: 2일째 아침 진술을 되풀이한다
        c = next(x for x in st.public.claims if x.claim_id == cid)
        self.assertEqual((c.speaker, c.night, c.slot, c.place.value, c.day),
                         ("blacksmith", 1, Slot.MIDNIGHT, "shrine", 2))
        self.assertEqual(tr.claim_truths[c.claim_id].is_true, tr.whereabouts[1]["blacksmith"][Slot.MIDNIGHT].value == "shrine")


if __name__ == "__main__":
    unittest.main()
