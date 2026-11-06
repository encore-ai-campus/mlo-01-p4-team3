"""A-06-01 밤 처리·점괘·고백·기색 테스트(태스크 문서 완료 조건 1~5).

시나리오 `secret_confession`·`false_divination_reveal`의 steps와 checks를 **실제 엔진**(`PaldoEngine.apply`)으로
재생한다. NPC 결정(표·밤 명령·캐묻기 반응)은 `NightPolicy`로 정해 끼우고, 규칙 처리는 엔진이 한다.
"""
from __future__ import annotations

import copy
import random
import unittest

from engine.domain import params, serde
from engine.domain.engine import (
    FACT_CLAIMS, BaselinePolicy, PaldoEngine, can_confess, candidate_pool, judge_tell, night_divination,
)
from engine.domain.events import EventType
from engine.domain.observation import Decision, ReasonCode, observe_npc
from engine.domain.model import (
    PHASE_RULES, PLAYER_ID, ClaimTruth, ClaimType, Command, CommandKind, DivinationPayload, DivinationResult,
    EvidenceRef, EvidenceType, InterrogationKind, NightPlan, Phase, PlayerProgress, ReactionIntent, RejectCode,
    Role, Slot, missing_slots,
)
from tests.engine.test_scenarios_engine import (
    CONTENT, DEPS, DAY1_LOCK_YAKCHO, ScriptedPolicy, drive_to, dump, env, game_base6, scenario, step,
)

DAY1_LOCK_MEOSUM = {"hunjang": "meosum", "jumo": "meosum", "blacksmith": "meosum", "meosum": "jumo",
                    "yakcho": "meosum"}


class NightPolicy(ScriptedPolicy):
    """`ScriptedPolicy` + 캐묻기 반응. reactions[(day, npc)] = ReactionIntent, talks[(day, phase, npc)] = Command."""

    def __init__(self, votes=None, nights=None, reactions=None, talks=None):
        super().__init__(votes, nights)
        self.reactions = reactions or {}
        self.talks = talks or {}

    def react(self, obs, cmd, rng):
        got = self.reactions.get((obs.day, obs.observer))
        return Decision(got, [ReasonCode.SCRIPTED]) if got else super().react(obs, cmd, rng)

    def talk(self, obs, rng):
        got = self.talks.get((obs.day, obs.phase, obs.observer))
        return Decision(got, [ReasonCode.SCRIPTED]) if got else super().talk(obs, rng)


def contradiction(addressee: str, record_id: str) -> dict:
    return {"addressee": addressee, "interrogation_kind": InterrogationKind.CONTRADICTION,
            "evidence": [EvidenceRef(EvidenceType.CONTRADICTION, record_id)]}


def to_night(t, engine, seed=0):
    """1일째 감금(머슴, 씐 자 아님) 뒤 밤 단계."""
    st, tr = game_base6(engine, seed)
    st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
    st, tr, _ = step(t, engine, st, tr, CommandKind.VOTE, target="meosum")
    t.assertIs(st.phase, Phase.NIGHT)
    return st, tr


def confess_day1(t, engine, npc="yakcho", record_id="cl06|cl05"):
    """secret_confession steps: 1일째 캐묻기에서 모순 추궁 → 엔진 반응 고백."""
    st, tr = game_base6(engine)
    st, tr = drive_to(t, engine, st, tr, Phase.INTERROGATION)
    st, tr, r = step(t, engine, st, tr, CommandKind.INTERROGATE, **contradiction(npc, record_id))
    return st, tr, r


def night1_divine(t, engine, st, tr, target):
    st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
    st, tr, _ = step(t, engine, st, tr, CommandKind.VOTE, target="meosum")
    st, tr, r = step(t, engine, st, tr, CommandKind.DIVINE, target=target)
    return st, tr, r


def confess_engine():
    return PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}, nights={1: NightPlan("blacksmith", "hunjang")},
                                   reactions={(1, "yakcho"): ReactionIntent.CONFESS}))


# ---------------------------------------------------------------- 조건 1: 시나리오 2개 실제 엔진 재생
class SecretConfessionReplay(unittest.TestCase):
    """조건 1: `secret_confession`의 steps(모순 추궁 → 고백)와 checks 5개를 실제 엔진으로."""

    def test_steps(self):
        sc = scenario("secret_confession")
        cmd = serde.from_jsonable(Command, sc.steps[0]["command"])
        engine = confess_engine()
        st, tr = drive_to(self, engine, *game_base6(engine), Phase.INTERROGATION)
        st, tr, r = step(self, engine, st, tr, cmd.kind, addressee=cmd.addressee,
                         interrogation_kind=cmd.interrogation_kind, evidence=cmd.evidence)
        want = sc.steps[1]
        sp = st.public.speeches[-1]
        self.assertEqual((sp.speaker, sp.reaction.value), ("yakcho", want["engine_reaction"]))
        (conf,) = st.public.confessions
        self.assertEqual((conf.npc_id, conf.secret_id, conf.day), ("yakcho", want["secret_id"], 1))
        ev = next(e for e in r.events if e.event_type is EventType.CONFESSION)
        self.assertEqual(ev.payload["secret_id"], want["secret_id"])
        self.assertEqual([e.event_type for e in r.events],
                         [EventType.OBSERVATION, EventType.DECISION,          # 답변 의도 결정 직전 관측(A-06-02)
                          EventType.CLAIM_MADE, EventType.CONFESSION, EventType.INTERROGATION_ANSWERED])

    def test_checks(self):
        sc = scenario("secret_confession")
        got = []
        for c in sc.checks:
            with self.subTest(type=c.type, note=c.note):
                out = self.evaluate(c.type, c.input)
                for k, v in c.expect.items():
                    self.assertEqual(out[k], v)
                got.append(c.type)
        self.assertEqual(got, ["divination", "divination", "divination", "missing_slots", "progress_clue"])

    def evaluate(self, kind: str, i: dict) -> dict:
        if kind == "divination":
            return {"result": self.divine_case(i["is_possessed"], i["has_unconfessed_secret"]).value}
        if kind == "missing_slots":
            cmd = serde.from_jsonable(Command, i["command"])
            engine = confess_engine()
            st, tr = drive_to(self, engine, *game_base6(engine), Phase.INTERROGATION)
            r = engine.apply(st, tr, env(st, cmd.kind, addressee=cmd.addressee,
                                         interrogation_kind=cmd.interrogation_kind), DEPS)
            self.assertIs(r.reject, RejectCode.MISSING_SLOT)
            return {"missing": r.reject_detail.split(",")}
        if kind == "progress_clue":
            # 진척 저장은 응용 계층이 confession 이벤트의 grudge_clue_id로 한다(domain.md 9). 같은 판을 두 번 처리.
            _, _, r = confess_day1(self, confess_engine())
            ev = next(e for e in r.events if e.event_type is EventType.CONFESSION)
            self.assertEqual(ev.payload["grudge_clue_id"], i["clue_id"])
            prog = PlayerProgress("p_test")
            return {"added": [prog.add_clue(ev.payload["grudge_clue_id"], "g_base6_001", ev.day)
                              for _ in range(i["times"])]}
        raise AssertionError(kind)

    def divine_case(self, possessed: bool, unconfessed: bool) -> DivinationResult:
        """체크 입력에 맞는 대상을 실제 판에서 고르고 1일째 밤 점 결과를 엔진에서 받는다."""
        engine = confess_engine()
        if possessed:
            st, tr = game_base6(engine)
            target = "blacksmith"
        elif unconfessed:
            st, tr = game_base6(engine)
            target = "yakcho"                               # 고백 전
        else:
            st, tr, _ = confess_day1(self, engine)          # 고백 후
            target = "yakcho"
        self.assertEqual(tr.roles[target] is Role.POSSESSED, possessed)
        st, tr, _ = night1_divine(self, engine, st, tr, target)
        return st.player.divinations[1][target]


class FalseDivinationReplay(unittest.TestCase):
    """조건 1·5: `false_divination_reveal`의 setup(1일째 밤 대장장이 탁함)·steps(맑음으로 공개)·checks 4개."""

    def setUp(self):
        sc = scenario("false_divination_reveal")
        self.sc = sc
        actual = sc.setup["actual"]
        self.engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO},
                                              nights={1: NightPlan("blacksmith", "hunjang")}))
        st, tr = to_night_yakcho(self, self.engine)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.DIVINE, target=actual["target"])
        self.assertEqual(st.player.divinations[actual["night"]][actual["target"]].value, actual["result"])
        self.st, self.tr = drive_to(self, self.engine, st, tr, Phase.ROUND1)

    def reveal(self, st, tr):
        cmd = serde.from_jsonable(Command, self.sc.steps[0]["command"])
        return self.engine.apply(st, tr, env(st, cmd.kind, divination=cmd.divination), DEPS)

    def test_steps_and_checks(self):
        r = self.reveal(self.st, self.tr)
        self.assertTrue(r.accepted, r.reject)
        st = r.state
        c = st.public.claims[-1]
        out = {
            "reveal_record": {"legal": r.accepted, "public_result": c.divination_result.value,
                              "private_result": st.player.divinations[c.night][c.subject].value},
            "phase_legal": None, "limit": None,
        }
        for k, chk in enumerate(self.sc.checks):
            with self.subTest(check=k, type=chk.type):
                if chk.type == "reveal_record":
                    self.assertEqual(chk.input, {"actual": out[chk.type]["private_result"],
                                                 "revealed": out[chk.type]["public_result"]})
                    got = out[chk.type]
                elif chk.type == "phase_legal":
                    got = {"legal": self.phase_legal(chk.input)}
                else:
                    got = {"reject": self.limit(chk.input)}
                for key, want in chk.expect.items():
                    self.assertEqual(got[key], want)

    def phase_legal(self, i) -> bool:
        st, tr = self.st, self.tr
        self.assertEqual(st.phase.value, i["phase"])
        kind = CommandKind(i["kind"])
        if i["is_player"]:
            return self.reveal(st, tr).reject is not RejectCode.ILLEGAL_PHASE
        # NPC가 점괘 공개를 내면 엔진이 받지 않는다(PHASE_RULES NPC 종류 밖).
        bad = PaldoEngine(NightPolicy(talks={(2, Phase.ROUND2, n): Command(kind, n, divination=DivinationPayload(
            "blacksmith", 1, DivinationResult.CLEAR)) for n in st.active if n != PLAYER_ID}))
        st2, tr2 = drive_to(self, self.engine, st, tr, Phase.INTERROGATION)
        try:
            bad.apply(st2, tr2, env(st2, CommandKind.ADVANCE), DEPS)
        except ValueError:
            return False
        return True

    def limit(self, i) -> str:
        st = copy.deepcopy(self.st)
        for k, v in i["player"].items():
            setattr(st.player, k, v)
        return self.reveal(st, self.tr).reject.value


def to_night_yakcho(t, engine):
    st, tr = game_base6(engine)
    st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
    st, tr, _ = step(t, engine, st, tr, CommandKind.VOTE, target="yakcho")
    return st, tr


# ---------------------------------------------------------------- 조건 2: 점은 밤 시작 상태, 실종돼도 전달
class DivinationAtNightStart(unittest.TestCase):
    """조건 2: 점 결과는 밤 시작 시 대상 상태로 정하고, 대상이 그 밤 실종돼도 전달한다(확정 결정 ②)."""

    def test_target_vanishes_same_night(self):
        engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}, nights={1: NightPlan("blacksmith", "jumo")}))
        st, tr = to_night(self, engine)
        before = night_divination(st, tr, "jumo")
        st, tr, r = step(self, engine, st, tr, CommandKind.DIVINE, target="jumo")   # 주모(비밀 tryst) = 피해자
        self.assertIn("jumo", st.missing)
        self.assertEqual(st.player.divinations[1]["jumo"], DivinationResult.MURKY)
        self.assertEqual(before, DivinationResult.MURKY)
        kinds = [e.event_type for e in r.events]
        self.assertLess(kinds.index(EventType.NIGHT_RESOLVED), kinds.index(EventType.DIVINATION_DONE))
        self.assertEqual(next(e for e in r.events if e.event_type is EventType.DIVINATION_DONE).payload,
                         {"target": "jumo", "night": 1, "result": "murky"})

    def test_result_rule(self):
        engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}))
        st, tr = to_night(self, engine)
        want = {"hunjang": "clear", "jumo": "murky", "blacksmith": "murky", "yakcho": "murky"}
        for target, res in want.items():
            with self.subTest(target=target):
                st2, _, _ = step(self, engine, st, tr, CommandKind.DIVINE, target=target)
                self.assertEqual(st2.player.divinations[1][target].value, res)

    def test_divination_is_private(self):
        engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}))
        st, tr = to_night(self, engine)
        st, tr, _ = step(self, engine, st, tr, CommandKind.DIVINE, target="blacksmith")
        self.assertFalse([c for c in st.public.claims if c.claim_type is ClaimType.DIVINATION_REVEAL])
        self.assertEqual(engine.player_view(st, CONTENT).player.divinations[1]["blacksmith"], DivinationResult.MURKY)


# ---------------------------------------------------------------- 조건 3: 고백 → 다음 점 맑음, 후보 제외
class Confession(unittest.TestCase):
    """조건 3: 고백자는 다음 점부터 맑음, 후보에서 제외(domain.md 6-1). 씐 자·비밀 없는 주민은 고백 불가."""

    def test_confession_records(self):
        st, tr, r = confess_day1(self, confess_engine())
        (conf,) = st.public.confessions
        old = next(c for c in st.public.claims if c.speaker == "yakcho" and c.slot is Slot.MIDNIGHT and c.night == 0
                   and c.corrects is None)
        new = st.public.claims[-1]
        self.assertEqual(conf.corrected_claims, [old.claim_id])
        self.assertEqual((new.speaker, new.corrects, new.place.value, new.night, new.slot),
                         ("yakcho", old.claim_id, "tavern", 0, Slot.MIDNIGHT))
        self.assertTrue(tr.claim_truths[new.claim_id].is_true)
        self.assertEqual(new.place, tr.whereabouts[0]["yakcho"][Slot.MIDNIGHT])
        rt = st.npcs["yakcho"].runtime
        self.assertEqual((rt.confessed, rt.confessed_day), (True, 1))
        self.assertEqual(st.public.speeches[-1].claim_ids, [new.claim_id])
        line = next(s.confession_line for s in CONTENT.secrets if s.secret_id == conf.secret_id)
        self.assertIn(line, st.public.speeches[-1].fact_sentence)
        self.assertFalse(can_confess(st, tr, "yakcho"))      # 두 번 고백하지 않는다

    def test_next_divination_clear(self):
        engine = confess_engine()
        st, tr = game_base6(engine)
        self.assertEqual(night_divination(st, tr, "yakcho"), DivinationResult.MURKY)
        st, tr, _ = confess_day1(self, engine)
        self.assertEqual(night_divination(st, tr, "yakcho"), DivinationResult.CLEAR)
        st, tr, _ = night1_divine(self, engine, st, tr, "yakcho")
        self.assertEqual(st.player.divinations[1]["yakcho"], DivinationResult.CLEAR)

    def test_candidate_pool_excludes_confessor(self):
        engine = confess_engine()
        st0, _ = game_base6(engine)
        st, tr, _ = confess_day1(self, engine)
        self.assertEqual(candidate_pool(st0, "hunjang"), ["blacksmith", "jumo", "meosum", "yakcho"])
        self.assertEqual(candidate_pool(st, "hunjang"), ["blacksmith", "jumo", "meosum"])
        self.assertEqual(candidate_pool(st, "yakcho"), ["blacksmith", "hunjang", "jumo", "meosum"])

    def test_baseline_vote_skips_confessor(self):
        st, tr, _ = confess_day1(self, confess_engine())
        st = copy.deepcopy(st)
        for npc in st.npcs.values():
            npc.beliefs.suspicion = {"yakcho": 99.0}
        pol = BaselinePolicy()
        for npc in ("hunjang", "jumo", "meosum", "blacksmith"):
            with self.subTest(voter=npc):
                obs, _ = observe_npc(st, npc, [CommandKind.VOTE], "snap_test")
                d = pol.vote(obs, list(st.active), random.Random(0))
                self.assertNotEqual(d.value, "yakcho")
                self.assertIn(ReasonCode.SKIP_CONFESSED, d.reason_codes)

    def test_vote_fallback_when_only_confessors(self):
        """결선 후보가 고백자뿐이면 합법 후보로 돌아간다(투표는 반드시 한다)."""
        st, tr, _ = confess_day1(self, confess_engine())
        obs, _ = observe_npc(st, "hunjang", [CommandKind.VOTE], "snap_test")
        got = BaselinePolicy().vote(obs, ["yakcho", "hunjang"], random.Random(0))
        self.assertEqual(got.value, "yakcho")
        self.assertIn(ReasonCode.FALLBACK_ALL_LEGAL, got.reason_codes)

    def test_illegal_confession_rejected_by_engine(self):
        for npc in ("blacksmith", "hunjang"):            # 씐 자, 비밀 없는 주민
            engine = PaldoEngine(NightPolicy(reactions={(1, npc): ReactionIntent.CONFESS}))
            st, tr = drive_to(self, engine, *game_base6(engine), Phase.INTERROGATION)
            with self.subTest(npc=npc):
                self.assertFalse(can_confess(st, tr, npc))
                with self.assertRaises(ValueError):
                    engine.apply(st, tr, env(st, CommandKind.INTERROGATE, **contradiction(npc, "cl01|cl02")), DEPS)

    def test_baseline_never_confesses(self):
        engine = PaldoEngine()
        st, tr = drive_to(self, engine, *game_base6(engine), Phase.INTERROGATION)
        st, tr, _ = step(self, engine, st, tr, CommandKind.INTERROGATE, **contradiction("yakcho", "cl12|cl13"))
        self.assertEqual(st.public.speeches[-1].reaction, ReactionIntent.EXPLAIN)
        self.assertEqual(st.public.confessions, [])


# ---------------------------------------------------------------- 조건 4: 기색
class Tells(unittest.TestCase):
    """조건 4: 기색은 사실 주장이 거짓일 때만 판정, 주장 ID당 1회 고정(재질문해도 같은 결과)."""

    def test_judge_rule(self):
        from engine.domain.model import Claim
        rng = random.Random(0)
        for ct in ClaimType:
            c = Claim("x", "jumo", ct, "jumo", 0, Slot.MIDNIGHT)
            with self.subTest(claim_type=ct.value):
                self.assertIsNone(judge_tell(rng, c, ClaimTruth("x", True, False)))
                got = judge_tell(rng, c, ClaimTruth("x", False, True))
                self.assertIs(got is None, ct not in FACT_CLAIMS)
        self.assertEqual(params.TELL_PROB, 0.5)

    def test_tells_only_on_false_fact_claims(self):
        """시드 60개 판(1일째 아침 진술 + 2일째 아침 진술)에서 공개 기색은 모두 거짓 사실 주장에만, 판정은 1회."""
        judged = shown = 0
        for seed in range(60):
            engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}))
            st, tr = to_night(self, engine, seed)
            st, tr, _ = step(self, engine, st, tr, CommandKind.DIVINE, target="jumo")
            claims = {c.claim_id: c for c in st.public.claims}
            for t in st.public.tells:
                self.assertTrue(t.shown)
                self.assertFalse(tr.claim_truths[t.claim_id].is_true)
                self.assertIn(claims[t.claim_id].claim_type, FACT_CLAIMS)
            self.assertEqual(len({t.claim_id for t in st.public.tells}), len(st.public.tells))
            false_fact = {cid for cid, c in claims.items()
                          if c.claim_type in FACT_CLAIMS and not tr.claim_truths[cid].is_true}
            runtime = {cid: v for n in st.npcs.values() for cid, v in n.runtime.tells.items()}
            self.assertEqual(set(runtime), false_fact)                       # 거짓 사실 주장마다 정확히 1회 판정
            self.assertEqual({t.claim_id for t in st.public.tells}, {c for c, v in runtime.items() if v})
            judged += len(runtime)
            shown += len(st.public.tells)
        self.assertGreater(shown, 0)
        self.assertLess(shown, judged)                                        # 확률 판정(항상 보이지 않음)

    def test_reask_same_result(self):
        """같은 밤·시간대를 다시 캐물어도 같은 주장 ID를 되풀이하고 기색을 다시 판정하지 않는다."""
        engine = PaldoEngine()
        st, tr = drive_to(self, engine, *game_base6(engine), Phase.INTERROGATION)
        lie = next(c for c in st.public.claims if c.speaker == "blacksmith" and c.slot is Slot.MIDNIGHT)
        before = (dump(st.public.tells), dict(st.npcs["blacksmith"].runtime.tells))
        q = {"addressee": "blacksmith", "interrogation_kind": InterrogationKind.WHEREABOUTS, "night": 0,
             "slot": Slot.MIDNIGHT}
        for _ in range(2):
            st, tr, r = step(self, engine, st, tr, CommandKind.INTERROGATE, **q)
            self.assertEqual(st.public.speeches[-1].claim_ids, [lie.claim_id])
            self.assertEqual((dump(st.public.tells), dict(st.npcs["blacksmith"].runtime.tells)), before)
            self.assertNotIn(EventType.CLAIM_MADE, [e.event_type for e in r.events])
        self.assertIn(lie.claim_id, st.npcs["blacksmith"].runtime.tells)

    def test_new_lie_judged_once_with_event(self):
        """캐묻기로 처음 나온 거짓 주장은 말한 직후 판정, 보였으면 공개 기록과 claim_made에 남는다."""
        found = set()
        for seed in range(40):
            engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM},
                                             nights={1: NightPlan("blacksmith", "hunjang")}))
            st, tr = to_night(self, engine, seed)
            st, tr, _ = step(self, engine, st, tr, CommandKind.DIVINE, target="jumo")
            # 2일째 아침 진술에 이미 1일째 밤 주장이 있으므로 직접 새 주장을 만든다: 아침 주장을 지우고 캐묻는다.
            # 주장 ID는 공개 주장 수로 매기므로 지운 자리는 다른 ID의 자리표시로 채워 번호가 겹치지 않게 한다.
            st = copy.deepcopy(st)
            for k, c in enumerate(st.public.claims):
                if c.speaker == "blacksmith" and c.night == 1 and c.slot is Slot.MIDNIGHT:
                    st.public.claims[k] = copy.replace(c, claim_id="removed", night=0, speaker="mudang")
            st.public.tells = [t for t in st.public.tells if t.claim_id in {c.claim_id for c in st.public.claims}]
            st, tr = drive_to(self, engine, st, tr, Phase.INTERROGATION)
            st, tr, r = step(self, engine, st, tr, CommandKind.INTERROGATE, addressee="blacksmith",
                             interrogation_kind=InterrogationKind.WHEREABOUTS, night=1, slot=Slot.MIDNIGHT)
            c = st.public.claims[-1]
            self.assertFalse(tr.claim_truths[c.claim_id].is_true)
            shown = st.npcs["blacksmith"].runtime.tells[c.claim_id]
            ev = next(e for e in r.events if e.event_type is EventType.CLAIM_MADE)
            self.assertEqual(ev.payload.get("tell", False), shown)
            self.assertEqual(any(t.claim_id == c.claim_id for t in st.public.tells), shown)
            found.add(shown)
        self.assertEqual(found, {True, False})

    def test_tell_evidence_usable(self):
        """공개된 기색은 근거(EvidenceType.TELL)로 쓸 수 있고, 판정만 있고 안 보인 주장은 근거가 아니다."""
        engine = PaldoEngine()
        st, tr = drive_to(self, engine, *game_base6(engine), Phase.ROUND1)
        (t,) = st.public.tells
        ok = engine.apply(st, tr, env(st, CommandKind.SUSPECT, target="yakcho",
                                      evidence=[EvidenceRef(EvidenceType.TELL, t.claim_id)]), DEPS)
        self.assertTrue(ok.accepted)
        hidden = next(cid for n in st.npcs.values() for cid, v in n.runtime.tells.items() if not v)
        bad = engine.apply(st, tr, env(st, CommandKind.SUSPECT, target="jumo",
                                       evidence=[EvidenceRef(EvidenceType.TELL, hidden)]), DEPS)
        self.assertIs(bad.reject, RejectCode.UNKNOWN_EVIDENCE)


# ---------------------------------------------------------------- 조건 5: 점괘 공개
class Reveal(unittest.TestCase):
    """조건 5: 점괘 공개 하루 1회, 거짓 공개 합법, 실제값(무당 개인)·공개값(공개 기록) 분리 저장."""

    def setUp(self):
        tie = {"hunjang": "jumo", "jumo": "hunjang", "blacksmith": "hunjang"}   # 무당 → jumo 이면 2:2, 결선도 2:2
        self.engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO, (2, "main"): tie,
                                                     (2, "runoff"): tie},
                                              nights={1: NightPlan("blacksmith", "meosum")}))
        st, tr = to_night_yakcho(self, self.engine)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.DIVINE, target="blacksmith")
        self.st, self.tr = drive_to(self, self.engine, st, tr, Phase.ROUND1)

    def test_true_and_false_reveal(self):
        for shown, is_true in ((DivinationResult.MURKY, True), (DivinationResult.CLEAR, False)):
            with self.subTest(revealed=shown.value):
                st, tr, r = step(self, self.engine, self.st, self.tr, CommandKind.REVEAL_DIVINATION,
                                 divination=DivinationPayload("blacksmith", 1, shown))
                c = st.public.claims[-1]
                self.assertEqual((c.claim_type, c.speaker, c.subject, c.night, c.divination_result),
                                 (ClaimType.DIVINATION_REVEAL, PLAYER_ID, "blacksmith", 1, shown))
                self.assertEqual(st.player.divinations[1]["blacksmith"], DivinationResult.MURKY)   # 실제값 불변
                self.assertEqual(tr.claim_truths[c.claim_id].is_true, is_true)
                self.assertNotIn(c.claim_id, [t.claim_id for t in st.public.tells])                # 점괘 공개는 기색 대상 아님
                self.assertEqual(st.player.reveals_left_today, 0)
                view = serde.to_jsonable(self.engine.player_view(st, CONTENT))
                self.assertEqual(view["public"]["claims"][-1]["divination_result"], shown.value)

    def test_once_per_day_resets_next_day(self):
        rv = {"divination": DivinationPayload("blacksmith", 1, DivinationResult.CLEAR)}
        st, tr, _ = step(self, self.engine, self.st, self.tr, CommandKind.REVEAL_DIVINATION, **rv)
        st, tr = drive_to(self, self.engine, st, tr, Phase.ROUND2)
        r = self.engine.apply(st, tr, env(st, CommandKind.REVEAL_DIVINATION, **rv), DEPS)
        self.assertIs(r.reject, RejectCode.LIMIT_EXCEEDED)
        st, tr = drive_to(self, self.engine, st, tr, Phase.VOTE)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.VOTE, target="jumo")
        self.assertIs(st.phase, Phase.RUNOFF)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.VOTE, target="jumo")
        self.assertEqual(st.public.locks[-1].locked, None)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.DIVINE, target="hunjang")
        self.assertEqual((st.day, st.player.reveals_left_today), (3, 1))
        st, tr = drive_to(self, self.engine, st, tr, Phase.ROUND1)
        st, tr, _ = step(self, self.engine, st, tr, CommandKind.REVEAL_DIVINATION, **rv)   # 지난 밤 결과도 공개 가능
        self.assertEqual(st.player.reveals_left_today, 0)

    def test_reveal_kind_not_for_npc(self):
        self.assertNotIn(CommandKind.REVEAL_DIVINATION, PHASE_RULES[Phase.ROUND1].npc_kinds)
        self.assertEqual(missing_slots(Command(CommandKind.REVEAL_DIVINATION, PLAYER_ID)), ["divination"])


# ---------------------------------------------------------------- 밤 처리 순서·아침 진술
class NightResolution(unittest.TestCase):
    """밤 처리(domain.md 5절 4): 행적 → 목격 → 실종 → 종료 판정 → 점 결과, 다음 날 아침 진술."""

    def night(self, seed=0, plan=NightPlan("blacksmith", "hunjang")):
        engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_MEOSUM}, nights={1: plan}))
        st0, tr0 = to_night(self, engine, seed)
        st, tr, r = step(self, engine, st0, tr0, CommandKind.DIVINE, target="jumo")
        return engine, st0, tr0, st, tr, r

    def test_morning_statements(self):
        for seed in range(30):
            _, st0, _, st, tr, r = self.night(seed)
            with self.subTest(seed=seed):
                speakers = [n for n in st.active if n != PLAYER_ID]
                day2 = [c for c in st.public.claims if c.day == 2]
                ann = next(e for e in r.events if e.event_type is EventType.MORNING_ANNOUNCED)
                self.assertEqual(ann.payload["claims"], [c.claim_id for c in day2])
                self.assertEqual(ann.payload["missing"], ["hunjang"])
                for n in speakers:
                    own = [c for c in day2 if c.speaker == n and c.claim_type is ClaimType.WHEREABOUTS]
                    self.assertEqual([(c.night, c.slot) for c in own], [(1, Slot.EVENING), (1, Slot.MIDNIGHT)])
                for c in day2:
                    actual = tr.whereabouts[1][c.speaker][c.slot]
                    lie = c.speaker == "blacksmith" and c.slot is Slot.MIDNIGHT and c.claim_type is ClaimType.WHEREABOUTS
                    self.assertEqual(tr.claim_truths[c.claim_id].is_true, not lie)
                    if c.claim_type is ClaimType.WHEREABOUTS:
                        self.assertEqual(c.place is actual, not lie)
                    else:
                        self.assertEqual(c.place, actual)
                        self.assertNotIn((c.place, c.slot), [(tr.whereabouts[1]["hunjang"][Slot.MIDNIGHT], Slot.MIDNIGHT)]
                                         if tr.roles[c.speaker] is Role.POSSESSED else [])
                ex = next(c for c in day2 if c.speaker == "blacksmith" and c.slot is Slot.MIDNIGHT)
                self.assertIn(ex.claim_id, st.npcs["blacksmith"].private.my_false_claims)

    def test_sightings_private(self):
        hits = 0
        for seed in range(30):
            _, _, _, st, tr, _ = self.night(seed)
            for n, npc in st.npcs.items():
                for s in (x for x in npc.private.witnessed if x.night == 1):
                    hits += 1
                    self.assertIs(tr.whereabouts[1][n][s.slot], s.place)
                    for o in s.seen:
                        self.assertIs(tr.whereabouts[1][o][s.slot], s.place)
        self.assertGreater(hits, 0)

    def test_agreed_alibi_in_morning(self):
        _, _, _, st, tr, _ = self.night(plan=NightPlan("blacksmith", "hunjang", {"blacksmith": "shrine"}))
        ex = next(c for c in st.public.claims if c.day == 2 and c.speaker == "blacksmith" and c.slot is Slot.MIDNIGHT
                  and c.claim_type is ClaimType.WHEREABOUTS)
        self.assertEqual(ex.place.value, "shrine")

    def test_secret_holders_truthful_after_night0(self):
        _, _, _, st, tr, _ = self.night()
        for c in st.public.claims:
            if c.night >= 1 and c.speaker in tr.secrets:
                self.assertTrue(tr.claim_truths[c.claim_id].is_true)

    def test_deterministic_and_pure(self):
        a = self.night(5)
        b = self.night(5)
        self.assertEqual((dump(a[3]), dump(a[4]), dump(a[5].events)), (dump(b[3]), dump(b[4]), dump(b[5].events)))
        engine, st0, tr0, *_ = a
        before = (dump(st0), dump(tr0))
        engine.apply(st0, tr0, env(st0, CommandKind.DIVINE, target="jumo"), DEPS)
        self.assertEqual((dump(st0), dump(tr0)), before)

    def test_serde_round_trip(self):
        from engine.domain.model import GameState, GameTruth
        _, _, _, st, tr, _ = self.night()
        for tp, obj in ((GameState, st), (GameTruth, tr)):
            self.assertEqual(dump(serde.from_jsonable(tp, serde.to_jsonable(obj))), dump(obj))


if __name__ == "__main__":
    unittest.main()
