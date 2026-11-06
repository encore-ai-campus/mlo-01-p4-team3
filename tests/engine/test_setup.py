"""A-05-01 판 생성·배정·아침 진술 테스트(태스크 문서 완료 조건 1~5)."""
from __future__ import annotations

import json
import unittest
from collections import Counter

from engine.domain import params, serde
from engine.domain.content import validate_assignment
from engine.domain.engine import AssignmentError, PaldoEngine, assignment_of, make_setup
from engine.domain.events import DomainEvent, EventType
from engine.domain.model import (
    PLAYER_ID, PRESETS, PUBLIC_PLACES, ClaimType, DataSource, GameMeta, GameState, GameTruth,
    Phase, Polarity, Preset, Role, Slot,
)
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource

SEEDS = range(200)
CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
ENGINE = PaldoEngine()


def meta(preset_id: str, seed: int) -> GameMeta:
    return GameMeta(f"g_{preset_id}_{seed:03d}", PRESETS[preset_id], seed, DataSource.BOT,
                    params.RULE_PARAMS_VERSION, "test", CONTENT.content_version)


def new_game(preset_id: str, seed: int):
    return ENGINE.new_game(meta(preset_id, seed), DEPS)


class AllPresets(unittest.TestCase):
    """6개 프리셋 × 시드 200개."""

    @classmethod
    def setUpClass(cls):
        cls.games = {(p, s): new_game(p, s) for p in PRESETS for s in SEEDS}

    def test_assignment_valid(self):
        """조건 1: 배정 검증기 위반 0건."""
        bad = Counter()
        for (p, _), r in self.games.items():
            for v in validate_assignment(PRESETS[p], CONTENT, assignment_of(r.state, r.truth)):
                bad[(p, v.code)] += 1
        self.assertEqual(len(self.games), 6 * 200)
        self.assertEqual(sum(bad.values()), 0, dict(bad))

    def test_day0_missing_and_role_counts(self):
        """조건 2: 0일째 실종자 1명, 역할 수가 기획서 4.1 프리셋 표와 같다."""
        for (p, s), r in self.games.items():
            pr, st, tr = PRESETS[p], r.state, r.truth
            with self.subTest(preset=p, seed=s):
                self.assertTrue(r.accepted)
                self.assertEqual(len(st.missing), 1)
                self.assertEqual([d.victim for d in st.public.disappearances], st.missing)
                self.assertEqual(len(st.active), pr.n_active)
                self.assertEqual(st.active[0], PLAYER_ID)
                self.assertNotIn(st.missing[0], st.active)
                npcs = [n for n in st.active if n != PLAYER_ID]
                roles = Counter(tr.roles[n] for n in npcs)
                secret = sum(1 for n in npcs if n in tr.secrets)
                self.assertEqual(tr.roles[PLAYER_ID], Role.MUDANG)
                self.assertEqual(roles[Role.POSSESSED], pr.n_possessed)
                self.assertEqual(secret, pr.n_secret)
                self.assertEqual(roles[Role.VILLAGER] - secret, pr.n_active - 1 - pr.n_possessed - pr.n_secret)
                self.assertIs(tr.roles[st.missing[0]], Role.VILLAGER)
                self.assertEqual(set(st.npcs), set(npcs))

    def test_night0_secret_events_only_and_no_one_at_crime(self):
        """조건 3: 비밀 사건은 0일째 밤에만(확정 결정 ①), 범행 장소 동석 금지(확정 결정 ③)."""
        crowded = 0
        for (p, s), r in self.games.items():
            st, tr = r.state, r.truth
            with self.subTest(preset=p, seed=s):
                self.assertEqual(set(tr.whereabouts), {0})
                for n, npc in st.npcs.items():
                    for h in npc.private.hidden_whereabouts:
                        self.assertEqual(h["night"], "0")
                    if n in tr.secrets:
                        self.assertEqual(len(npc.private.hidden_whereabouts), 1)
                lies = [c for c in st.public.claims if tr.claim_truths[c.claim_id].deliberate_lie]
                self.assertTrue(all(c.night == 0 for c in lies))
                d = st.public.disappearances[0]
                plan = tr.night_plans[0]
                w = tr.whereabouts[0]
                self.assertIs(w[plan.executor][Slot.MIDNIGHT], d.place)
                self.assertIs(w[d.victim][Slot.MIDNIGHT], d.place)
                if d.place in PUBLIC_PLACES:
                    crowded += sum(1 for n, role in tr.roles.items()
                                   if role is Role.VILLAGER and n != d.victim
                                   and w[n][Slot.MIDNIGHT] is d.place)
        self.assertEqual(crowded, 0)

    def test_morning_claims_follow_schema(self):
        """조건 4: 아침 진술이 주장 스키마(기획서 5.1)를 따른다."""
        for (p, s), r in self.games.items():
            st, tr = r.state, r.truth
            with self.subTest(preset=p, seed=s):
                ids = [c.claim_id for c in st.public.claims]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(set(ids), set(tr.claim_truths))
                speakers = {c.speaker for c in st.public.claims}
                self.assertEqual(speakers, set(st.npcs))           # 활동 주민 전원이 진술
                for c in st.public.claims:
                    self.assertIn(c.claim_type, (ClaimType.WHEREABOUTS, ClaimType.SIGHTING))
                    self.assertEqual((c.subject, c.night, c.day, c.polarity), (c.speaker, 0, 1, Polarity.AFFIRM))
                    self.assertIsNotNone(c.slot)
                    self.assertIsNotNone(c.place)
                    self.assertIsNone(c.corrects)
                    self.assertIsNone(c.divination_result)
                    if c.claim_type is ClaimType.SIGHTING:
                        self.assertTrue(c.seen)
                        self.assertIn(c.place, PUBLIC_PLACES)
                        self.assertNotIn(c.speaker, c.seen)
                    else:
                        self.assertEqual(c.seen, [])
                    truth = tr.claim_truths[c.claim_id]
                    real = tr.whereabouts[0][c.speaker][c.slot]
                    if c.claim_type is ClaimType.WHEREABOUTS:
                        self.assertEqual(truth.is_true, c.place is real)
                    else:
                        self.assertTrue(truth.is_true)
                        self.assertIs(c.place, real)
                        for x in c.seen:
                            self.assertIs(tr.whereabouts[0][x][c.slot], c.place)
                    self.assertEqual(truth.deliberate_lie, not truth.is_true)
                    if truth.deliberate_lie:
                        self.assertIn(c.claim_id, st.npcs[c.speaker].private.my_false_claims)
                # 거짓말하는 사람: 비밀 보유자(비밀 시간대) + 실행자(한밤)
                liars = {(c.speaker, c.slot) for c in st.public.claims if tr.claim_truths[c.claim_id].deliberate_lie}
                expect = {(n, st.npcs[n].private.hidden_whereabouts[0]["slot"]) for n in tr.secrets}
                expect = {(n, Slot(sl)) for n, sl in expect} | {(tr.night_plans[0].executor, Slot.MIDNIGHT)}
                self.assertEqual(liars, expect)

    def test_executor_alibi_not_refutable_by_own_sightings(self):
        """PM 결정(11/5): 실행자 거짓 행적은 범행 장소·자기 한밤 목격 장소가 아닌 공공장소."""
        places = Counter()
        for (p, s), r in self.games.items():
            st, tr = r.state, r.truth
            ex = tr.night_plans[0].executor
            with self.subTest(preset=p, seed=s):
                claim = next(c for c in st.public.claims if c.speaker == ex
                             and c.claim_type is ClaimType.WHEREABOUTS and c.slot is Slot.MIDNIGHT)
                own = {w.place for w in st.npcs[ex].private.witnessed if w.slot is Slot.MIDNIGHT}
                self.assertIn(claim.place, PUBLIC_PLACES)
                self.assertIsNot(claim.place, st.public.disappearances[0].place)
                self.assertNotIn(claim.place, own)
                if PRESETS[p].n_possessed == 2:
                    self.assertEqual(tr.night_plans[0].agreed_alibis, {ex: claim.place.value})
                places[claim.place] += 1
        self.assertEqual(len(places), len(PUBLIC_PLACES))   # 시드로 고르므로 네 곳 모두 나온다

    def test_confession_threshold_only_for_secret_holders(self):
        """A-07-01: 비밀 보유자만 비밀의 기본 고백 문턱값을 받는다(성향 보정은 정책). 나머지는 None.

        A-05-01 때는 고백 범위 밖이라 비워 두는 것을 검사했다(11/5 PM 결정). A-07-01이 고백 판정을 넣으며 바꿨다.
        """
        secrets = {s.secret_id: s for s in CONTENT.secrets}
        for r in self.games.values():
            for npc in r.state.npcs.values():
                sid = r.truth.secrets.get(npc.npc_id)
                want = secrets[sid].confession_threshold if sid else None
                self.assertEqual(npc.private.confession_threshold, want)

    def test_serde_round_trip(self):
        """조건 4: 상태·진실·이벤트의 serde 왕복이 손실 없다."""
        for (p, s), r in self.games.items():
            with self.subTest(preset=p, seed=s):
                for tp, obj in ((GameState, r.state), (GameTruth, r.truth)):
                    back = serde.loads(tp, serde.dumps(obj))
                    self.assertEqual(back, obj)
                    self.assertEqual(serde.dumps(back), serde.dumps(obj))
                for e in r.events:
                    self.assertEqual(serde.loads(DomainEvent, serde.dumps(e)), e)

    def test_events(self):
        """아침 이벤트: 게임 시작, 실종 발표(피해 장소·시간대, 진술 ID)."""
        for (p, s), r in self.games.items():
            st = r.state
            with self.subTest(preset=p, seed=s):
                self.assertEqual([e.event_type for e in r.events],
                                 [EventType.GAME_STARTED, EventType.MORNING_ANNOUNCED])
                ann = r.events[1].payload
                d = st.public.disappearances[0]
                self.assertEqual(ann["missing"], st.missing)
                self.assertEqual(ann["crime_scene"], {"place": d.place.value, "slot": "midnight"})
                self.assertEqual(ann["claims"], [c.claim_id for c in st.public.claims])
                self.assertEqual((st.state_version, st.day, st.phase), (0, 1, Phase.MORNING))


class Deterministic(unittest.TestCase):
    """조건 5: 같은 시드 → 같은 판."""

    def test_same_seed_same_game(self):
        for p in PRESETS:
            for s in (0, 7, 199):
                a, b = new_game(p, s), new_game(p, s)
                self.assertEqual(serde.dumps(a.state), serde.dumps(b.state))
                self.assertEqual(serde.dumps(a.truth), serde.dumps(b.truth))
                self.assertEqual(serde.dumps(a.events), serde.dumps(b.events))

    def test_different_seeds_differ(self):
        dumps = {json.dumps(serde.to_jsonable(new_game("base6", s).truth), sort_keys=True) for s in range(20)}
        self.assertGreater(len(dumps), 15)

    def test_input_meta_not_mutated(self):
        m = meta("base6", 3)
        before = serde.dumps(m)
        ENGINE.new_game(m, DEPS)
        self.assertEqual(serde.dumps(m), before)


class ContentShortage(unittest.TestCase):
    def test_too_few_characters_rejected(self):
        small = serde.from_jsonable(type(CONTENT), serde.to_jsonable(CONTENT))
        small.characters = small.characters[:5]
        with self.assertRaises(AssignmentError):
            make_setup(PRESETS["base6"], small, 1, "g")

    def test_unsatisfiable_secrets_rejected(self):
        bad = serde.from_jsonable(type(CONTENT), serde.to_jsonable(CONTENT))
        for sec in bad.secrets:
            sec.eligible_npcs = []
        with self.assertRaises(AssignmentError):
            make_setup(Preset("t6", 6, 1, 2), bad, 1, "g")


if __name__ == "__main__":
    unittest.main()
