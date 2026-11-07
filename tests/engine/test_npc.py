"""A-07-01 NPC 규칙 v0 테스트(태스크 문서 완료 조건 1~4·7, 조건 5의 판정 함수, A-06-02 인계 목격 근거).

스냅샷을 손으로 만들어 믿음·정책을 단독으로 검사하고, 끝에 `PaldoEngine(RuleV0Policy)`로 판을 돌려 연결을 확인한다.
조건 5(500판)·6(결정 시간 p95)의 수치는 증빙의 측정 명령이 낸다.
"""
from __future__ import annotations

import dataclasses
import json
import random
import unittest
from pathlib import Path

from engine.domain import params, serde
from engine.domain.content import ContentBundle
from engine.domain.engine import BaselinePolicy, PaldoEngine, _Tx
from engine.domain.events import EventType
from engine.domain.model import (
    PLAYER_ID, PRESETS, Beliefs, Claim, ClaimType, Command, CommandEnvelope, CommandKind, DataSource,
    DisappearanceRecord, DivinationResult, EvidenceGroup, EvidenceRef, EvidenceSource, EvidenceType, GameMeta,
    InterrogationKind, ObservationSnapshot, Phase, Place, Polarity, PrivateKnowledge, PropositionKind, PublicRecord,
    ReactionIntent, Role, Sighting, Slot, Speech, TellRecord, VoteRecord,
)
from engine.domain.npc import PossessedPolicy, RuleV0Policy, StrategyMix, VillagerPolicy
from engine.domain.npc import beliefs as bl
from engine.domain.npc.policy_possessed import night_groups
from engine.domain.observation import NO_EVIDENCE_CODES, ReasonCode, decision_payload
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.perf.engine_bench import bench_player_command

CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
ROOT = Path(__file__).resolve().parents[2]
BUNDLE_V1 = serde.from_jsonable(ContentBundle, json.loads((ROOT / "content/bundle_v1.json").read_text(encoding="utf-8")))
TALK = [CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.POINT_CONTRADICTION, CommandKind.EXPLAIN,
        CommandKind.SILENCE]


# ---------------------------------------------------------------- 스냅샷 만들기
def snap(observer="a", role=Role.VILLAGER, public=None, private=None, candidates=("b", "c", "d"),
         legal=TALK, preset="base6", game_id="g1", props=()) -> ObservationSnapshot:
    pk = private or PrivateKnowledge(npc_id=observer, role=role)
    return ObservationSnapshot(
        snapshot_id=f"snap:{game_id}:v1:d000:{observer}", game_id=game_id, observer=observer, day=1,
        phase=Phase.ROUND1, state_version=1, public=public or PublicRecord(), private=pk,
        beliefs=Beliefs(propositions=list(props)), legal_kinds=list(legal), candidates=list(candidates),
        n_possessed_remaining=PRESETS[preset].n_possessed, preset_id=preset)


def where(cid, who, place, night=0, slot=Slot.MIDNIGHT) -> Claim:
    return Claim(cid, who, ClaimType.WHEREABOUTS, who, night, slot, place)


def saw(cid, who, seen, place, night=0, slot=Slot.MIDNIGHT) -> Claim:
    return Claim(cid, who, ClaimType.SIGHTING, who, night, slot, place, seen=list(seen))


def speech(sid, speaker, kind, target=None, evidence=(), reaction=None) -> Speech:
    return Speech(sid, 1, Phase.ROUND1, speaker, kind, target=target, evidence=list(evidence), reaction=reaction)


def contra(a, b) -> EvidenceRef:
    return EvidenceRef(EvidenceType.CONTRADICTION, f"{a}|{b}")


def sus(s: ObservationSnapshot, who: str) -> float:
    return bl.compute_beliefs(s).suspicion[who]


# ---------------------------------------------------------------- 조건 1: 믿음
class Beliefs1(unittest.TestCase):
    def test_suspicion_per_target(self):
        """대상별 의심도: 공개 모순의 행적 쪽이 가장 오르고, 목격 쪽은 조금, 무관한 사람은 기준값."""
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN)])
        b = bl.compute_beliefs(snap(public=pub))
        self.assertGreater(b.suspicion["b"], b.suspicion["c"])
        self.assertGreater(b.suspicion["c"], bl.BASE_SUSPICION)
        self.assertEqual(b.suspicion["d"], bl.BASE_SUSPICION)
        self.assertEqual(set(b.suspicion), {"b", "c", "d"})   # 후보만(무당·자신 제외)

    def test_trust_in_mudang(self):
        """무당 신뢰도: 무당의 모순 지적이 실제 모순이면 오르고, 아니면 내려간다."""
        claims = [where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN), where("cl03", "d", Place.SHRINE)]
        ok = PublicRecord(claims=claims, speeches=[speech("sp01", PLAYER_ID, CommandKind.POINT_CONTRADICTION,
                                                          evidence=[contra("cl01", "cl02")])])
        bad = PublicRecord(claims=claims, speeches=[speech("sp01", PLAYER_ID, CommandKind.POINT_CONTRADICTION,
                                                           evidence=[contra("cl01", "cl03")])])
        self.assertGreater(bl.compute_beliefs(snap(public=ok)).trust_in_mudang, bl.BASE_TRUST)
        self.assertLess(bl.compute_beliefs(snap(public=bad)).trust_in_mudang, bl.BASE_TRUST)

    def test_trust_drops_when_mudang_clears_a_liar_i_saw(self):
        """내가 거짓을 직접 본 사람(거짓말한 사람은 탁함)에게 맑음을 공개하면 무당 신뢰도가 내려간다."""
        pk = PrivateKnowledge("a", Role.VILLAGER, witnessed=[Sighting(0, Slot.MIDNIGHT, Place.TAVERN, ["b"])])
        reveal = Claim("cl02", PLAYER_ID, ClaimType.DIVINATION_REVEAL, "b", 0,
                       divination_result=DivinationResult.CLEAR)
        b = bl.compute_beliefs(snap(public=PublicRecord(claims=[where("cl01", "b", Place.WELL), reveal]), private=pk))
        self.assertLess(b.trust_in_mudang, bl.BASE_TRUST)

    def test_propositions_only_from_own_witness(self):
        """확정 명제는 직접 목격만: 내 목격 → was_at(own_witness). 타인 목격 주장은 명제가 아니라 WITNESS 근거."""
        pk = PrivateKnowledge("a", Role.VILLAGER, witnessed=[Sighting(0, Slot.EVENING, Place.WELL, ["b"])])
        pub = PublicRecord(claims=[saw("cl01", "c", ["d"], Place.SHRINE)],
                           disappearances=[DisappearanceRecord(0, "v", Place.TAVERN)])
        b = bl.compute_beliefs(snap(public=pub, private=pk))
        own = [p for p in b.propositions if p.kind is PropositionKind.WAS_AT]
        self.assertEqual([(p.subject, p.source, p.place) for p in own], [("b", EvidenceSource.OWN_WITNESS, Place.WELL)])
        self.assertFalse(any(p.subject == "d" for p in b.propositions))
        wit = [r for r in b.reasons if r.target == "d" and r.group is EvidenceGroup.WITNESS]
        self.assertEqual([(r.evidence.record_id, r.delta < 0) for r in wit], [("cl01", True)])

    def test_others_claim_and_mutual_vouch_differ(self):
        """타인 주장과 상호 보증은 다른 가중치(상호 보증은 담합일 수 있어 덜 믿는다)."""
        rec = [DisappearanceRecord(0, "v", Place.TAVERN)]
        one = PublicRecord(claims=[saw("cl01", "c", ["d"], Place.SHRINE)], disappearances=rec)
        both = PublicRecord(claims=[saw("cl01", "c", ["d"], Place.SHRINE), saw("cl02", "d", ["c"], Place.SHRINE)],
                            disappearances=rec)
        self.assertLess(sus(snap(public=one), "d"), sus(snap(public=both), "d"))

    def test_not_seeing_is_not_absence(self):
        """"못 봤다"는 부재 증명이 아니다: 비목격 주장·목격 없음은 모순·명제·의심도에 쓰지 않는다."""
        non = Claim("cl02", "c", ClaimType.NON_SIGHTING, "c", 0, Slot.MIDNIGHT, Place.WELL, polarity=Polarity.DENY,
                    seen=["b"])
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), non, where("cl03", "c", Place.WELL)])
        pk = PrivateKnowledge("a", Role.VILLAGER, whereabouts={0: {Slot.MIDNIGHT: Place.WELL}})   # 나도 거기 있었지만 못 봄
        b = bl.compute_beliefs(snap(public=pub, private=pk))
        self.assertEqual(bl.contradictions(pub), [])
        self.assertEqual(b.suspicion, {"b": 50.0, "c": 50.0, "d": 50.0})
        self.assertEqual(b.propositions, [])

    def test_own_witness_lie_raises_most(self):
        """내가 본 장소와 다른 행적을 말한 사람은 크게 오른다(공개 모순보다)."""
        pk = PrivateKnowledge("a", Role.VILLAGER, witnessed=[Sighting(0, Slot.MIDNIGHT, Place.TAVERN, ["b"])])
        mine = sus(snap(public=PublicRecord(claims=[where("cl01", "b", Place.WELL)]), private=pk), "b")
        public = sus(snap(public=PublicRecord(claims=[where("cl01", "b", Place.WELL),
                                                      saw("cl02", "c", ["b"], Place.TAVERN)])), "b")
        self.assertGreater(mine, public)

    def test_cleared_by_own_alibi_is_zero(self):
        """1명 판: 직접 목격 알리바이(not_possessed)인 후보는 의심도 0."""
        p = bl.Proposition(PropositionKind.NOT_POSSESSED, "b", EvidenceSource.OWN_WITNESS, 0, Slot.MIDNIGHT, Place.WELL)
        self.assertEqual(bl.compute_beliefs(snap(props=[p])).suspicion["b"], 0.0)

    def test_idempotent(self):
        """같은 스냅샷을 여러 번 갱신해도 결과가 같다(공개 기록에서 다시 계산)."""
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN)],
                           speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", [contra("cl01", "cl02")])])
        s = snap(public=pub)
        b1 = bl.compute_beliefs(s)
        s2 = dataclasses.replace(s, beliefs=b1)
        self.assertEqual(bl.compute_beliefs(s2), b1)


# ---------------------------------------------------------------- 조건 2: 반복·해명·모순 지적 실패
class Repeats2(unittest.TestCase):
    def test_repeated_key_has_no_effect(self):
        """같은 (발언자, 대상, 근거)의 의심은 두 번째부터 효과 없음. 근거가 다르면 효과가 있다."""
        ev = [EvidenceRef(EvidenceType.VOTE, "v1_0")]
        once = PublicRecord(speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", ev)])
        twice = PublicRecord(speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", ev),
                                       speech("sp02", "c", CommandKind.SUSPECT, "b", ev)])
        other = PublicRecord(speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", ev),
                                       speech("sp02", "c", CommandKind.SUSPECT, "b", [EvidenceRef(EvidenceType.VOTE, "v1_1")])])
        self.assertGreater(sus(snap(public=once), "b"), bl.BASE_SUSPICION)
        self.assertEqual(sus(snap(public=once), "b"), sus(snap(public=twice), "b"))
        self.assertGreater(sus(snap(public=other), "b"), sus(snap(public=once), "b"))
        self.assertEqual(bl.compute_beliefs(snap(public=twice)).repeat_keys, ["c|b|vote:v1_0"])

    def test_suspect_and_defend_counted_separately(self):
        """같은 발언자·대상·근거라도 의심과 감싸기는 반대 행동이라 둘 다 효과가 있다(반복 키는 종류별)."""
        ev = [EvidenceRef(EvidenceType.VOTE, "v1_0")]
        s_only = PublicRecord(speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", ev)])
        both = PublicRecord(speeches=[speech("sp01", "c", CommandKind.SUSPECT, "b", ev),
                                      speech("sp02", "c", CommandKind.DEFEND, "b", ev)])
        self.assertLess(sus(snap(public=both), "b"), sus(snap(public=s_only), "b"))

    def test_explain_diminishes(self):
        """해명은 반복할수록 효과가 줄어든다: 1·2·3번째 해명의 감소량이 점점 작다."""
        vals = []
        for n in range(4):
            pub = PublicRecord(speeches=[speech(f"sp{i:02d}", "b", CommandKind.EXPLAIN) for i in range(n)])
            vals.append(sus(snap(public=pub), "b"))
        drops = [vals[i] - vals[i + 1] for i in range(3)]
        self.assertTrue(drops[0] > drops[1] > drops[2] > 0, drops)

    def test_failed_contradiction_lowers_credibility(self):
        """모순이 아닌 것을 모순이라 지적하면: 주민은 그 사람 의심도 상승, 무당은 무당 신뢰도 하락."""
        claims = [where("cl01", "b", Place.WELL), where("cl02", "d", Place.SHRINE)]
        npc = PublicRecord(claims=claims, speeches=[speech("sp01", "c", CommandKind.POINT_CONTRADICTION,
                                                           evidence=[contra("cl01", "cl02")])])
        self.assertGreater(sus(snap(public=npc), "c"), bl.BASE_SUSPICION)
        self.assertEqual(sus(snap(public=npc), "b"), bl.BASE_SUSPICION)
        mud = PublicRecord(claims=claims, speeches=[speech("sp01", PLAYER_ID, CommandKind.POINT_CONTRADICTION,
                                                           evidence=[contra("cl01", "cl02")])])
        self.assertLess(bl.compute_beliefs(snap(public=mud)).trust_in_mudang, bl.BASE_TRUST)

    def test_pointing_stays_valid_after_correction(self):
        """실제 모순을 지적한 뒤 고백으로 그 주장이 정정돼도 지적은 실패로 바뀌지 않는다(신뢰 하락·의심 상승 없음)."""
        fix = Claim("cl03", "b", ClaimType.WHEREABOUTS, "b", 0, Slot.MIDNIGHT, Place.TAVERN, corrects="cl01")
        claims = [where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN), fix]
        for who in (PLAYER_ID, "d"):
            pub = PublicRecord(claims=claims, speeches=[speech("sp01", who, CommandKind.POINT_CONTRADICTION,
                                                               evidence=[contra("cl01", "cl02")])])
            b = bl.compute_beliefs(snap(public=pub))
            self.assertGreaterEqual(b.trust_in_mudang, bl.BASE_TRUST)
            self.assertEqual(b.suspicion["d"], bl.BASE_SUSPICION)

    def test_corrected_claim_is_not_contradiction(self):
        """고백으로 정정된 주장은 모순으로 세지 않는다."""
        fix = Claim("cl03", "b", ClaimType.WHEREABOUTS, "b", 0, Slot.MIDNIGHT, Place.TAVERN, corrects="cl01")
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN), fix])
        self.assertEqual(bl.contradictions(pub), [])
        self.assertFalse(bl.is_contradiction(pub, "cl01", "cl02"))


# ---------------------------------------------------------------- 마을 주민 정책
class Villager(unittest.TestCase):
    def setUp(self):
        self.pol = VillagerPolicy(CONTENT)

    def test_points_unpointed_contradiction(self):
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN)])
        d = self.pol.talk(snap(observer="hunjang", public=pub), random.Random(0))
        self.assertEqual((d.value.kind, d.value.target), (CommandKind.POINT_CONTRADICTION, "b"))
        self.assertEqual(d.evidence, [contra("cl01", "cl02")])

    def test_no_repeat_of_own_suspicion(self):
        """이미 같은 근거로 의심했으면 같은 키를 다시 내지 않는다."""
        pub = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN)],
                           speeches=[speech("sp01", "x", CommandKind.POINT_CONTRADICTION, "b", [contra("cl01", "cl02")])],
                           tells=[TellRecord("cl01", 1, True)])
        s = snap(observer="blacksmith", public=pub, legal=[CommandKind.SUSPECT, CommandKind.SILENCE])
        keys = set()
        for seed in range(30):
            d = self.pol.talk(s, random.Random(seed))
            if d.value.kind is CommandKind.SUSPECT:
                keys.add(bl.repeat_key("blacksmith", d.value.target, d.value.evidence))
        self.assertTrue(keys)
        for k in keys:
            t, rid = k.split("|")[2].split(":", 1)
            pub2 = dataclasses.replace(pub, speeches=pub.speeches + [
                speech("sp02", "blacksmith", CommandKind.SUSPECT, "b", [EvidenceRef(EvidenceType(t), rid)])])
            for seed in range(30):
                d = self.pol.talk(dataclasses.replace(s, public=pub2), random.Random(seed))
                if d.value.kind is CommandKind.SUSPECT:
                    self.assertNotEqual(bl.repeat_key("blacksmith", d.value.target, d.value.evidence), k)

    def test_belief_cache_is_per_snapshot_object(self):
        """같은 snapshot_id라도 다른 객체(분기·재연)면 다시 계산한다."""
        pol = VillagerPolicy()
        s1 = snap(public=PublicRecord(tells=[TellRecord("cl01", 1, True)], claims=[where("cl01", "b", Place.WELL)]))
        s2 = snap()
        self.assertEqual(s1.snapshot_id, s2.snapshot_id)
        self.assertGreater(pol.update_beliefs(s1).suspicion["b"], bl.BASE_SUSPICION)
        self.assertEqual(pol.update_beliefs(s2).suspicion["b"], bl.BASE_SUSPICION)

    def test_vote_excludes_cleared(self):
        """투표 안전 층: 확정 명제(씐 자 아님)인 후보는 의심도와 무관하게 표를 받지 않는다(마을 추론 위반 0)."""
        p = bl.Proposition(PropositionKind.NOT_POSSESSED, "b", EvidenceSource.OWN_WITNESS, 0, Slot.MIDNIGHT, Place.WELL)
        pub = PublicRecord(tells=[TellRecord("cl01", 1, True)], claims=[where("cl01", "b", Place.SHRINE)])
        s = snap(public=pub, props=[p], legal=[CommandKind.VOTE])
        for seed in range(50):
            d = self.pol.vote(s, ["a", "b", "c", "d", PLAYER_ID], random.Random(seed))
            self.assertNotEqual(d.value, "b")
            self.assertFalse(bl.vote_conflicts(bl.compute_beliefs(s), d.value))
            self.assertIn(ReasonCode.EXCLUDE_PROPOSITION, d.reason_codes)

    def test_vote_fallback_when_all_cleared(self):
        """결선 후보가 모두 확정 무고면 합법 후보 전체로 돌아간다(규칙상 기권 없음)."""
        p = bl.Proposition(PropositionKind.NOT_POSSESSED, "b", EvidenceSource.OWN_WITNESS, 0, Slot.MIDNIGHT, Place.WELL)
        d = self.pol.vote(snap(props=[p], legal=[CommandKind.VOTE]), ["b"], random.Random(0))
        self.assertEqual(d.value, "b")
        self.assertIn(ReasonCode.FALLBACK_ALL_LEGAL, d.reason_codes)

    def test_vote_mudang_only_when_trust_low(self):
        s = snap(legal=[CommandKind.VOTE], candidates=("b",))
        self.assertNotEqual(self.pol.vote(s, ["a", "b", PLAYER_ID], random.Random(0)).value, PLAYER_ID)
        bad = [speech(f"sp{i:02d}", PLAYER_ID, CommandKind.POINT_CONTRADICTION, evidence=[contra(f"cl{i}", f"cx{i}")])
               for i in range(3)]
        low = snap(public=PublicRecord(speeches=bad), legal=[CommandKind.VOTE], candidates=("b",))
        self.assertLess(bl.compute_beliefs(low).trust_in_mudang, 20.0)
        self.assertEqual(self.pol.vote(low, ["a", PLAYER_ID], random.Random(0)).value, PLAYER_ID)


# ---------------------------------------------------------------- A-06-02 인계: 목격 근거 표시
class WitnessCodes(unittest.TestCase):
    def test_witness_codes_only_when_used(self):
        """타인 목격 근거를 의심도에 쓴 대상에 대한 결정에만 witness_* 코드와 주장 근거가 붙는다."""
        rec = [DisappearanceRecord(0, "v", Place.TAVERN)]
        pub = PublicRecord(claims=[saw("cl01", "c", ["d"], Place.SHRINE), where("cl02", "b", Place.WELL),
                                   saw("cl03", "c", ["b"], Place.SHRINE)], disappearances=rec,
                           tells=[TellRecord("cl02", 1, True)])
        pol = VillagerPolicy()
        s = snap(public=pub, legal=[CommandKind.VOTE])
        d_b = pol.vote(s, ["b"], random.Random(0))
        self.assertIn(ReasonCode.WITNESS_OTHERS_CLAIM, d_b.reason_codes)
        self.assertIn(EvidenceRef(EvidenceType.CLAIM, "cl03"), d_b.evidence)
        d_c = pol.vote(s, ["c"], random.Random(0))
        self.assertFalse({ReasonCode.WITNESS_OTHERS_CLAIM, ReasonCode.WITNESS_MUTUAL_VOUCH} & set(d_c.reason_codes))
        b = bl.compute_beliefs(s)
        self.assertFalse(any(p.source in (EvidenceSource.OTHERS_CLAIM, EvidenceSource.MUTUAL_VOUCH)
                             for p in b.propositions))   # 목격 근거는 reasons에만


# ---------------------------------------------------------------- 조건 3: 씐 자 전략
class _Tripwire(PrivateKnowledge):
    """비밀 보유 정보 필드를 읽으면 실패한다."""
    SECRET_FIELDS = {"secret_id", "hidden_whereabouts", "confession_threshold"}

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, "SECRET_FIELDS"):
            raise AssertionError(f"씐 자 정책이 비밀 필드를 읽음: {name}")
        return object.__getattribute__(self, name)


class CountingMix:
    def __init__(self, mix: StrategyMix):
        self.mix, self.calls = mix, []

    def mix_for(self, game_id: str) -> StrategyMix:
        self.calls.append(game_id)
        return self.mix


def possessed_snap(public=None, legal=TALK, game_id="g1", partner=None, false_claims=(), observer="jumo"):
    pk = _Tripwire(npc_id=observer, role=Role.POSSESSED, partner_id=partner, my_false_claims=list(false_claims))
    return snap(observer=observer, role=Role.POSSESSED, public=public, private=pk, legal=legal, game_id=game_id,
                candidates=("b", "c", "d") + ((partner,) if partner else ()))


class Possessed3(unittest.TestCase):
    PUB = PublicRecord(claims=[where("cl01", "b", Place.WELL), saw("cl02", "c", ["b"], Place.TAVERN),
                               Claim("cl03", PLAYER_ID, ClaimType.DIVINATION_REVEAL, "d", 0,
                                     divination_result=DivinationResult.MURKY)],
                       votes=[VoteRecord("v1_0", 1, "main", PLAYER_ID, "b")],
                       speeches=[speech("sp01", "d", CommandKind.SUSPECT, "jumo", [EvidenceRef(EvidenceType.VOTE, "v1_0")])])

    def pol(self, **mix):
        return PossessedPolicy(CONTENT, CountingMix(StrategyMix(**mix)))

    def test_does_not_read_secret_fields(self):
        """씐 자 결정 전체(말·투표·밤·답변)가 비밀 보유 정보를 읽지 않는다(읽으면 실패하는 개인 지식으로 확인)."""
        pol = self.pol()
        s = possessed_snap(public=self.PUB)
        for seed in range(20):
            rng = random.Random(seed)
            pol.talk(s, rng)
            pol.vote(dataclasses.replace(s, legal_kinds=[CommandKind.VOTE]), ["b", "c", "d", PLAYER_ID], rng)
            pol.night_plan(dataclasses.replace(s, legal_kinds=[CommandKind.NIGHT_PLAN]), ["jumo"], ["b", "c", "d"], rng)
            pol.react(s, Command(CommandKind.INTERROGATE, PLAYER_ID, addressee="jumo",
                                 interrogation_kind=InterrogationKind.CONTRADICTION), rng)
        RuleV0Policy(CONTENT).talk(s, random.Random(0))

    def test_frame_contradicted(self):
        quiet = dataclasses.replace(self.PUB, speeches=[])            # 나에 대한 의심이 없어 해명하지 않는 상황
        d = next(d for seed in range(20) if (d := self.pol(frame_target={"contradicted": 1.0}).talk(
            possessed_snap(public=quiet), random.Random(seed))).value.kind is not CommandKind.SILENCE)
        self.assertIn(ReasonCode.FRAME_CONTRADICTED, d.reason_codes)
        self.assertEqual(d.value.target, "b")
        self.assertEqual(d.evidence, [contra("cl01", "cl02")])

    def test_frame_mudang(self):
        """무당 의심 유도: 무당의 점괘 공개·투표 기록을 근거로 무당을 의심한다."""
        for seed in range(10):
            d = self.pol(frame_target={"mudang": 1.0}).talk(possessed_snap(public=self.PUB), random.Random(seed))
            if d.value.kind is CommandKind.SUSPECT:
                self.assertEqual(d.value.target, PLAYER_ID)
                self.assertIn(d.evidence[0].type, (EvidenceType.DIVINATION, EvidenceType.VOTE))
                break
        else:
            self.fail("무당 의심 발언이 나오지 않음")

    def test_never_frames_partner(self):
        pub = PublicRecord(claims=[where("cl01", "yakcho", Place.WELL), saw("cl02", "c", ["yakcho"], Place.TAVERN)])
        pol = self.pol(frame_target={"contradicted": 1.0, "suspected": 1.0})
        for seed in range(30):
            d = pol.talk(possessed_snap(public=pub, partner="yakcho"), random.Random(seed))
            self.assertNotEqual(d.value.target, "yakcho")
            v = pol.vote(possessed_snap(public=pub, partner="yakcho", legal=[CommandKind.VOTE]),
                         ["yakcho", "b", "c"], random.Random(seed))
            self.assertNotEqual(v.value, "yakcho")

    def test_lie_types(self):
        """거짓 행적: routine = 평소 한밤 공공장소를 합의 알리바이로, unrefuted = 비워 엔진이 반박되지 않는 곳을 고름."""
        s = possessed_snap(legal=[CommandKind.NIGHT_PLAN])
        r = self.pol(lie_type={"routine": 1.0}).night_plan(s, ["jumo"], ["b", "c"], random.Random(0))
        self.assertEqual(r.value.agreed_alibis, {"jumo": Place.TAVERN.value})   # jumo 한밤 동선: tavern 0.5
        self.assertIn(ReasonCode.LIE_ROUTINE, r.reason_codes)
        u = self.pol(lie_type={"unrefuted": 1.0}).night_plan(s, ["jumo"], ["b", "c"], random.Random(0))
        self.assertEqual(u.value.agreed_alibis, {})
        self.assertIn(ReasonCode.LIE_UNREFUTED, u.reason_codes)
        self.assertNotIn(Place.HOME.value, r.value.agreed_alibis.values())

    def test_agreed_alibi_equal_to_crime_scene_is_replaced(self):
        """합의 알리바이(routine)가 실제 범행 장소와 같으면 엔진이 반박 회피 공공장소로 대신한다(거짓 행적이 사실이 되지 않게)."""
        meta = GameMeta("g_alibi", PRESETS["base6"], 5, DataSource.BOT, params.RULE_PARAMS_VERSION, "t",
                        CONTENT.content_version)
        r = PaldoEngine().new_game(meta, DEPS)
        tx = _Tx(r.state, r.truth, CONTENT, BaselinePolicy())
        plan = tx.tr.night_plans[0]
        actual = tx.tr.whereabouts[0][plan.executor][Slot.MIDNIGHT]
        plan.agreed_alibis = {plan.executor: actual.value}
        self.assertIsNot(tx.executor_lie(0, plan.executor), actual)
        other = next(p for p in (Place.WELL, Place.SHRINE) if p is not actual)
        plan.agreed_alibis = {plan.executor: other.value}
        self.assertIs(tx.executor_lie(0, plan.executor), other)

    def test_night_target_mix(self):
        """밤 대상: 묶음(나를 지목한 사람·거짓을 봤을 수 있는 사람·무당을 따르는 사람)을 비율로 섞어 고른다."""
        pub = PublicRecord(claims=[where("cl01", "jumo", Place.SHRINE), where("cl02", "c", Place.SHRINE)],
                           speeches=[speech("sp01", "b", CommandKind.SUSPECT, "jumo")],
                           votes=[VoteRecord("v1_0", 1, "main", PLAYER_ID, "x"), VoteRecord("v1_1", 1, "main", "d", "x")])
        s = possessed_snap(public=pub, legal=[CommandKind.NIGHT_PLAN], false_claims=["cl01"])
        self.assertEqual(night_groups(s, ["b", "c", "d"]), {"accuser": {"b"}, "witness": {"c"}, "trusts_mudang": {"d"}})
        for arm, who, code in (("accuser", "b", ReasonCode.NIGHT_ACCUSER), ("witness", "c", ReasonCode.NIGHT_WITNESS),
                               ("trusts_mudang", "d", ReasonCode.NIGHT_TRUSTS_MUDANG)):
            d = self.pol(night_target={arm: 1.0}).night_plan(s, ["jumo"], ["b", "c", "d"], random.Random(0))
            self.assertEqual(d.value.victim, who)
            self.assertIn(code, d.reason_codes)
        mixed = {self.pol().night_plan(s, ["jumo"], ["b", "c", "d"], random.Random(seed)).value.victim
                 for seed in range(40)}
        self.assertEqual(mixed, {"b", "c", "d"})                     # 고정 규칙이 아니라 섞어 고른다

    def test_mix_injected_once_per_game_and_fixed(self):
        """혼합 비율은 판마다 처음 한 번만 주입받고, 공급자가 나중에 다른 값을 줘도 그 판 동안 바꾸지 않는다."""
        src = CountingMix(StrategyMix(answer_intent={"evade": 1.0}, mix_id="arm-evade"))
        pol = PossessedPolicy(CONTENT, src)
        q = Command(CommandKind.INTERROGATE, PLAYER_ID, addressee="jumo", interrogation_kind=InterrogationKind.WHEREABOUTS)
        s1 = possessed_snap(game_id="g1")
        self.assertIs(pol.react(s1, q, random.Random(0)).value, ReactionIntent.EVADE)
        src.mix = StrategyMix(answer_intent={"counter": 1.0}, mix_id="arm-counter")
        for seed in range(5):
            self.assertIs(pol.react(s1, q, random.Random(seed)).value, ReactionIntent.EVADE)
            pol.talk(s1, random.Random(seed))
        self.assertEqual(src.calls, ["g1"])
        self.assertIs(pol.react(possessed_snap(game_id="g2"), q, random.Random(0)).value, ReactionIntent.COUNTER)
        self.assertEqual(src.calls, ["g1", "g2"])

    def test_mix_validation(self):
        with self.assertRaises(ValueError):
            StrategyMix(lie_type={"home": 1.0})
        with self.assertRaises(ValueError):
            StrategyMix(frame_target={"mudang": 0.0})


# ---------------------------------------------------------------- 조건 4: 고백
class Confession4(unittest.TestCase):
    Q = Command(CommandKind.INTERROGATE, PLAYER_ID, addressee="a", interrogation_kind=InterrogationKind.WHEREABOUTS,
                night=0, slot=Slot.MIDNIGHT)

    def secret_snap(self, n_prior: int, role=Role.VILLAGER, threshold=60.0):
        pk = PrivateKnowledge("meosum", role, secret_id="s1" if role is Role.VILLAGER else None,
                              hidden_whereabouts=[{"night": "0", "slot": "midnight"}] if role is Role.VILLAGER else [],
                              confession_threshold=threshold if role is Role.VILLAGER else None)
        sp = [Speech(f"sp{i:02d}", 1, Phase.INTERROGATION, "meosum", CommandKind.INTERROGATE, addressee=PLAYER_ID,
                     reaction=ReactionIntent.EVADE) for i in range(n_prior)]
        sp += [speech(f"sq{i:02d}", f"x{i}", CommandKind.SUSPECT, "meosum") for i in range(n_prior)]
        return snap(observer="meosum", role=role, public=PublicRecord(speeches=sp), private=pk, legal=[])

    def test_confess_over_threshold(self):
        """비밀 주민: 압박 < 문턱값이면 고백하지 않고, 넘으면 고백한다(성향 보정 문턱값)."""
        pol = VillagerPolicy(CONTENT)
        temp = pol.temperament("meosum")
        lo = self.secret_snap(0)
        self.assertLess(bl.pressure(lo, InterrogationKind.WHEREABOUTS, 0, Slot.MIDNIGHT), bl.confession_threshold(lo, temp))
        self.assertIsNot(pol.react(lo, self.Q, random.Random(0)).value, ReactionIntent.CONFESS)
        hi = self.secret_snap(3)
        self.assertGreaterEqual(bl.pressure(hi, InterrogationKind.WHEREABOUTS, 0, Slot.MIDNIGHT),
                                bl.confession_threshold(hi, temp))
        d = pol.react(hi, self.Q, random.Random(0))
        self.assertIs(d.value, ReactionIntent.CONFESS)
        self.assertEqual(d.reason_codes, [ReasonCode.PRESSURE_CONFESS])

    def test_threshold_follows_temperament(self):
        s = self.secret_snap(0, threshold=60.0)
        easy, hard = CONTENT.temperaments[3], CONTENT.temperaments[0]   # timid 0.8, scholar 0.3
        self.assertLess(bl.confession_threshold(s, easy), bl.confession_threshold(s, hard))

    def test_possessed_never_confesses(self):
        """씐 자는 압박이 아무리 커도 고백하지 않는다."""
        pol = RuleV0Policy(CONTENT)
        s = self.secret_snap(10, role=Role.POSSESSED)
        for seed in range(30):
            self.assertIsNot(pol.react(s, self.Q, random.Random(seed)).value, ReactionIntent.CONFESS)

    def test_no_secret_never_confesses(self):
        pk = PrivateKnowledge("hunjang", Role.VILLAGER)
        s = snap(observer="hunjang", private=pk, public=self.secret_snap(10).public, legal=[])
        self.assertIsNot(VillagerPolicy(CONTENT).react(s, self.Q, random.Random(0)).value, ReactionIntent.CONFESS)


# ---------------------------------------------------------------- 조건 7: 템플릿 성향 선택(E-06-01 bundle_v1)
class Templates7(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meta = GameMeta("g_tpl", PRESETS["base6"], 3, DataSource.BOT, params.RULE_PARAMS_VERSION, "test",
                        BUNDLE_V1.content_version)
        r = PaldoEngine().new_game(meta, EngineDeps(BUNDLE_V1, FakeSuspicionModel()))
        cls.tx = _Tx(r.state, r.truth, BUNDLE_V1, BaselinePolicy())
        cls.by_temp = {}
        for c in BUNDLE_V1.characters:
            cls.by_temp.setdefault(c.temperament_id, c.npc_id)

    def _tpl(self, kind, reaction, temp):
        return next(t.variants[0] for t in BUNDLE_V1.templates
                    if t.kind is kind and t.reaction is reaction and t.temperament_id == temp)

    def test_temperament_match(self):
        """화자 성향과 같은 템플릿이 그 (kind, reaction)에 있으면 그것을 쓴다."""
        for temp, npc in self.by_temp.items():
            for kind, reaction in ((CommandKind.SUSPECT, None), (CommandKind.SILENCE, None),
                                   (CommandKind.INTERROGATE, ReactionIntent.EVADE)):
                self.assertEqual(self.tx.template(kind, reaction, speaker=npc), self._tpl(kind, reaction, temp))

    def test_common_fallback(self):
        """성향 템플릿이 없는 조합은 공용을 쓴다(점괘 공개는 성향 템플릿이 없다)."""
        npc = self.by_temp["scholar"]
        self.assertEqual(self.tx.template(CommandKind.REVEAL_DIVINATION, speaker=npc),
                         self._tpl(CommandKind.REVEAL_DIVINATION, None, None))
        thin = dataclasses.replace(BUNDLE_V1, templates=[t for t in BUNDLE_V1.templates
                                                         if not (t.kind is CommandKind.SUSPECT and t.temperament_id)])
        tx = _Tx(self.tx.st, self.tx.tr, thin, BaselinePolicy())
        self.assertEqual(tx.template(CommandKind.SUSPECT, speaker=npc), self._tpl(CommandKind.SUSPECT, None, None))

    def test_player_and_whereabouts_stay_common(self):
        """무당(플레이어)·화자 없음은 공용, 행적 진술 사실 문장은 공용 `t_whereabouts`."""
        self.assertEqual(self.tx.template(CommandKind.SUSPECT, speaker=PLAYER_ID),
                         self._tpl(CommandKind.SUSPECT, None, None))
        self.assertEqual(self.tx.template(CommandKind.EXPLAIN), self._tpl(CommandKind.EXPLAIN, None, None))
        c = Claim("clx", "a", ClaimType.WHEREABOUTS, "a", 0, Slot.MIDNIGHT, Place.WELL)
        t_where = next(t.variants[0] for t in BUNDLE_V1.templates if t.template_id == "t_whereabouts")
        self.assertEqual(self.tx.whereabouts_sentence(c).split()[-1], t_where.split()[-1])

    def test_explain_templates_have_no_placeholders(self):
        """캐묻기 해명 사실 문장 자리에 성향별 해명 템플릿을 그대로 써도 된다(자리표시자 없음)."""
        for t in BUNDLE_V1.templates:
            if t.kind is CommandKind.EXPLAIN and t.template_id != "t_whereabouts":
                self.assertNotIn("{", t.variants[0], t.template_id)


# ---------------------------------------------------------------- 엔진 연결(조건 5 판정 함수 포함)
def _play(engine: PaldoEngine, preset: str, seed: int, content=CONTENT):
    meta = GameMeta(f"g_npc_{preset}_{seed:03d}", PRESETS[preset], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    "test", content.content_version, npc_policy_versions={})
    deps = EngineDeps(content, FakeSuspicionModel())
    r = engine.new_game(meta, deps)
    st, tr, rng, events = r.state, r.truth, random.Random(seed), list(r.events)
    while st.result is None:
        cmd = bench_player_command(engine.player_view(st, content), rng)
        r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version}", st.state_version, cmd), deps)
        assert r.accepted, (r.reject, r.reject_detail)
        st, tr = r.state, r.truth
        events += r.events
    return st, tr, events


class EngineRuleV0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = CountingMix(StrategyMix())
        cls.engine = PaldoEngine(RuleV0Policy(CONTENT, cls.src))
        cls.games = {(p, s): _play(cls.engine, p, s) for p, n in (("base6", 12), ("short5", 8), ("duo9", 5))
                     for s in range(n)}

    def test_games_finish_and_decisions_valid(self):
        """규칙 v0로 판이 끝까지 돈다(엔진의 합법 검사에 걸리지 않음). 결정은 정책 버전 rule-v0, 이유 코드·근거 규칙을 지킨다."""
        for (_p, _s), (st, _tr, events) in self.games.items():
            self.assertIsNotNone(st.result)
            for e in events:
                if e.event_type is EventType.DECISION:
                    self.assertEqual(e.payload["policy_version"], "rule-v0")
                    codes = {ReasonCode(c) for c in e.payload["reason_codes"]}
                    self.assertTrue(codes)
                    if not e.payload["evidence"]:
                        self.assertTrue(codes <= NO_EVIDENCE_CODES, codes)

    def test_no_inference_violation(self):
        """마을 주민의 표가 그 직전 관측 스냅샷의 확정 명제와 충돌하지 않는다(대안이 있을 때)."""
        n_votes = 0
        for st, _tr, events in self.games.values():
            snaps = {}
            for e in events:
                if e.event_type is EventType.OBSERVATION:
                    snaps[e.payload["snapshot_id"]] = serde.from_jsonable(ObservationSnapshot, e.payload["snapshot"])
                elif e.event_type is EventType.DECISION and e.payload["kind"] == "vote":
                    s = snaps[e.payload["snapshot_id"]]
                    if s.private.role is Role.VILLAGER and ReasonCode.FALLBACK_ALL_LEGAL.value not in e.payload["reason_codes"]:
                        n_votes += 1
                        self.assertFalse(bl.vote_conflicts(s.beliefs, e.payload["target"]))
        self.assertGreater(n_votes, 100)

    def test_beliefs_stored_and_in_snapshot(self):
        """엔진이 결정 직전 정책의 믿음 갱신을 상태와 관측 스냅샷에 넣는다(의심도가 채워짐)."""
        n = 0
        for st, tr, events in self.games.values():
            for e in events:
                if e.event_type is EventType.OBSERVATION:   # 모든 관측: 의심도 키 = 후보(투표 판단과 같은 믿음이 기록됨)
                    snap_ = e.payload["snapshot"]
                    self.assertEqual(set(snap_["beliefs"]["suspicion"]), set(snap_["candidates"]))
                    n += 1
        self.assertGreater(n, 500)
        st, tr, events = self.games[("base6", 0)]
        r = PaldoEngine(RuleV0Policy(CONTENT)).new_game(
            GameMeta("g_obs", PRESETS["base6"], 1, DataSource.BOT, params.RULE_PARAMS_VERSION, "t",
                     CONTENT.content_version), DEPS)
        npc = next(x for x in r.state.npcs if x in r.state.active)
        q = PaldoEngine(RuleV0Policy(CONTENT)).observe(r.state, r.truth, npc)
        self.assertEqual(set(q.beliefs.suspicion), set(q.candidates))                   # 포트 observe도 같은 갱신
        self.assertEqual(PaldoEngine().observe(r.state, r.truth, npc).beliefs.suspicion, {})   # baseline은 갱신 없음

    def test_confessions_happen_only_by_villagers(self):
        n = 0
        for st, tr, _events in self.games.values():
            for c in st.public.confessions:
                n += 1
                self.assertIs(tr.roles[c.npc_id], Role.VILLAGER)
        self.assertGreater(n, 0)

    def test_mix_called_once_per_game(self):
        """엔진 판에서도 혼합 비율 공급자는 판마다 한 번만 불린다."""
        from collections import Counter
        self.assertTrue(self.src.calls)
        self.assertEqual(set(Counter(self.src.calls).values()), {1})

    def test_decision_payload_accepts_all_rule_codes(self):
        """규칙 v0 이유 코드는 근거 없는 결정이 허용 목록 안이거나 근거를 싣는다(decision_payload 검사 통과)."""
        for st, _tr, events in self.games.values():
            for e in events:
                if e.event_type is EventType.DECISION:
                    self.assertIn("snapshot_id", e.payload)
        d = VillagerPolicy().talk(snap(), random.Random(0))
        decision_payload("a", "snap:x", d.value.kind.value, None, d, "rule-v0")


if __name__ == "__main__":
    unittest.main()
