"""A-06-02 관측 투영·스냅샷·진실 이벤트·이유 코드 테스트(태스크 문서 완료 조건 1~6).

엔진 판 200개(프리셋 6종을 시드 0~199에 돌려 배정)는 `PaldoEngine`으로 이 테스트 안에서 끝까지 돌린다.
판 실행 도구(`tools/run_engine_games.py`)는 A-06-03 산출물이라 쓰지 않는다. 플레이어 명령은
`tools.perf.engine_bench.bench_player_command`(고정 선택, 캐묻기·의심·투표·점 포함)를 쓴다.
"""
from __future__ import annotations

import dataclasses
import json
import random
import typing
import unittest
from unittest import mock

from engine.domain import engine as engine_mod
from engine.domain import params, serde
from engine.domain.engine import BaselinePolicy, NpcPolicy, PaldoEngine, _Tx
from engine.domain.events import EventType, topic_of
from engine.domain.model import (
    PLAYER_ID, PRESETS, Claim, ClaimType, CommandEnvelope, CommandKind, DataSource, DisappearanceRecord,
    EvidenceRef, EvidenceSource, EvidenceType, GameMeta, GameState, GameTruth, ObservationSnapshot, Phase, Place,
    PropositionKind, PublicRecord, Role, Sighting, Slot, alibi_propositions, crime_scene_of,
)
from engine.domain.observation import (
    NO_EVIDENCE_CODES, Decision, ReasonCode, decision_payload, observe_npc, update_beliefs,
    witness_basis, witness_evidence,
)
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.perf.engine_bench import bench_player_command

CONTENT = FixtureContentSource().load("*")
DEPS = EngineDeps(CONTENT, FakeSuspicionModel())
N_GAMES = 200
PRESET_IDS = sorted(PRESETS)
TRUTH_FIELDS = {f.name for f in dataclasses.fields(GameTruth)}


# ---------------------------------------------------------------- 진실 읽기 추적
class _Reads:
    """결정 지점(`_Tx.decide`: 관측 투영 → 믿음 갱신 → 정책) 안팎에서 진실 필드를 읽은 횟수."""
    inside = False
    during_decide = 0
    outside = 0


class TrackedTruth(GameTruth):
    """진실 필드 접근을 센다. 엔진의 deepcopy가 같은 클래스를 만들므로 판 내내 추적된다."""

    def __getattribute__(self, name):
        if name in TRUTH_FIELDS:
            if _Reads.inside:
                _Reads.during_decide += 1
            else:
                _Reads.outside += 1
        return object.__getattribute__(self, name)


def tracked(truth: GameTruth) -> TrackedTruth:
    return TrackedTruth(**{f.name: object.__getattribute__(truth, f.name) for f in dataclasses.fields(GameTruth)})


_real_decide = _Tx.decide
_real_observe = engine_mod.observe_npc


def _flagged_decide(self, *a, **kw):
    _Reads.inside = True
    try:
        return _real_decide(self, *a, **kw)
    finally:
        _Reads.inside = False


def meta(preset_id: str, seed: int) -> GameMeta:
    return GameMeta(f"g_proj_{preset_id}_{seed:03d}", PRESETS[preset_id], seed, DataSource.BOT,
                    params.RULE_PARAMS_VERSION, "test", CONTENT.content_version)


def play(engine: PaldoEngine, preset_id: str, seed: int):
    """판 1개를 끝까지 돌린다. 반환: (meta, 1일째 아침 결과, [(전이 전 상태, 결과)], 마지막 상태, 마지막 진실)."""
    m = meta(preset_id, seed)
    first = engine.new_game(m, DEPS)
    st, tr, rng = first.state, tracked(first.truth), random.Random(seed)
    steps = []
    while st.result is None:
        cmd = bench_player_command(engine.player_view(st, CONTENT), rng)
        r = engine.apply(st, tr, CommandEnvelope(m.game_id, f"c{st.state_version:04d}", st.state_version, cmd), DEPS)
        if not r.accepted:
            raise AssertionError(f"{preset_id}/{seed} 거부: {cmd.kind.value} {r.reject} {r.reject_detail}")
        steps.append((st, r))
        st, tr = r.state, r.truth
        if len(steps) > 400:
            raise AssertionError(f"{preset_id}/{seed} 판이 끝나지 않음")
    return m, first, steps, st, tr


class EngineGames(unittest.TestCase):
    """엔진 판 200개를 한 번 돌려 조건 2~6을 확인한다."""

    @classmethod
    def setUpClass(cls):
        _Reads.during_decide = _Reads.outside = 0
        engine = PaldoEngine()
        cls.games = []
        cls.snaps = {}                     # snapshot_id -> 엔진이 만든 스냅샷 객체(이벤트 JSON 역직렬화는 표본만, 느림)

        def record(*a, **kw):
            snap, beliefs = _real_observe(*a, **kw)
            cls.snaps[snap.snapshot_id] = snap
            return snap, beliefs

        with mock.patch.object(_Tx, "decide", _flagged_decide), mock.patch.object(engine_mod, "observe_npc", record):
            for seed in range(N_GAMES):
                cls.games.append(play(engine, PRESET_IDS[seed % len(PRESET_IDS)], seed))
        cls.reads_during_decide, cls.reads_outside = _Reads.during_decide, _Reads.outside
        cls.all_events = [[e for _, r in steps for e in r.events] for _, _, steps, _, _ in cls.games]
        n_obs = sum(1 for evs in cls.all_events for e in evs if e.event_type is EventType.OBSERVATION)
        n_dec = sum(1 for evs in cls.all_events for e in evs if e.event_type is EventType.DECISION)
        print(f"\n[A-06-02] games={len(cls.games)} observations={n_obs} decisions={n_dec} "
              f"truth_reads_during_decide={cls.reads_during_decide} truth_reads_outside={cls.reads_outside}")

    def test_observation_payload_is_snapshot(self):
        """observation 이벤트 본문의 스냅샷 JSON = 엔진이 정책에 넘긴 스냅샷(표본 50개는 역직렬화까지)."""
        obs = [e for evs in self.all_events for e in evs if e.event_type is EventType.OBSERVATION]
        self.assertEqual(len(obs), len(self.snaps))
        for i, e in enumerate(obs):
            snap = self.snaps[e.payload["snapshot_id"]]
            self.assertEqual(e.payload["snapshot"], serde.to_jsonable(snap))
            if i % (len(obs) // 50) == 0:
                self.assertEqual(serde.from_jsonable(ObservationSnapshot, e.payload["snapshot"]), snap)

    # ---- 조건 6: 관측·믿음·정책 경로에서 진실을 읽지 않음
    def test_no_truth_read_in_decision_path(self):
        self.assertEqual(self.reads_during_decide, 0)
        self.assertGreater(self.reads_outside, 0)       # 추적 장치가 실제로 진실 읽기를 센다(엔진 규칙 처리)

    def test_tracker_counts_reads_inside_decide(self):
        """추적 장치 자체 검사: 결정 지점 안에서 진실 필드를 읽으면 during_decide가 오른다."""
        tr = self.games[0][4]
        before = _Reads.during_decide
        _Reads.inside = True
        try:
            tr.whereabouts
        finally:
            _Reads.inside = False
        self.assertEqual(_Reads.during_decide, before + 1)
        _Reads.during_decide = before

    def test_alibi_propositions_only_from_public_scene_and_own_witness(self):
        """모든 스냅샷의 확정 명제 = 공개 실종 기록 × 자기 목격의 `alibi_propositions`, 출처는 직접 목격뿐."""
        n_checked = 0
        for (m, _, _, _, _), evs in zip(self.games, self.all_events):
            for e in evs:
                if e.event_type is not EventType.OBSERVATION:
                    continue
                snap = self.snaps[e.payload["snapshot_id"]]
                want = [p for rec in snap.public.disappearances
                        for p in alibi_propositions(rec, snap.private.witnessed, m.preset.n_possessed, snap.observer)]
                key = lambda p: (p.kind, p.subject, p.night, p.place)
                self.assertEqual(sorted(map(key, snap.beliefs.propositions)), sorted(set(map(key, want))))
                self.assertTrue(all(p.source is EvidenceSource.OWN_WITNESS for p in snap.beliefs.propositions))
                n_checked += len(snap.beliefs.propositions)
        self.assertGreater(n_checked, 0)

    def test_two_possessed_not_executor_only_and_candidates_kept(self):
        n_props = 0
        n_kept = 0
        for (m, _, _, _, _), evs in zip(self.games, self.all_events):
            if m.preset.n_possessed != 2:
                continue
            for e in evs:
                if e.event_type is not EventType.OBSERVATION:
                    continue
                snap = self.snaps[e.payload["snapshot_id"]]
                kinds = {p.kind for p in snap.beliefs.propositions}
                self.assertLessEqual(kinds, {PropositionKind.NOT_EXECUTOR})
                n_props += len(snap.beliefs.propositions)
                # 후보에서 빼지 않는다(비실행 공범일 수 있음): 공개 기록상 활동 중이고 고백하지 않은 명제 대상은 후보에 남는다
                gone = ({d.victim for d in snap.public.disappearances} | {lk.locked for lk in snap.public.locks}
                        | {c.npc_id for c in snap.public.confessions} | {snap.observer, PLAYER_ID})
                for p in snap.beliefs.propositions:
                    if p.subject not in gone:
                        self.assertIn(p.subject, snap.candidates)
                        n_kept += 1
        self.assertGreater(n_props, 0)
        self.assertGreater(n_kept, 0)

    def test_others_claims_only_as_evidence(self):
        """타인의 공개 목격 주장은 확정 명제에 들어가지 않고 WITNESS 근거(Reason)로만 남는다."""
        n = 0
        for (m, _, _, _, _), evs in zip(self.games, self.all_events):
            for e in evs:
                if e.event_type is not EventType.OBSERVATION:
                    continue
                snap = self.snaps[e.payload["snapshot_id"]]
                got = witness_evidence(snap.public, snap.observer, m.preset.n_possessed)
                reasons = {(r.target, r.evidence.record_id) for r in snap.beliefs.reasons}
                for p, cid in got:
                    self.assertIn(p.source, (EvidenceSource.OTHERS_CLAIM, EvidenceSource.MUTUAL_VOUCH))
                    self.assertIn((p.subject, cid), reasons)
                    n += 1
        self.assertGreater(n, 0)

    # ---- 조건 2: 행동 직전 observation, 같은 snapshot_id
    def test_observation_right_before_each_decision(self):
        n = 0
        for evs in self.all_events:
            ids = set()
            for i, e in enumerate(evs):
                if e.event_type is EventType.DECISION:
                    prev = evs[i - 1]
                    self.assertIs(prev.event_type, EventType.OBSERVATION)
                    self.assertEqual(prev.payload["snapshot_id"], e.payload["snapshot_id"])
                    self.assertEqual(prev.payload["observer"], e.payload["actor"])
                    self.assertNotIn(e.payload["snapshot_id"], ids)
                    ids.add(e.payload["snapshot_id"])
                    n += 1
                if e.event_type is EventType.OBSERVATION:
                    self.assertIs(evs[i + 1].event_type, EventType.DECISION)
        self.assertGreater(n, 0)

    def test_decision_not_on_game_topic(self):
        """결정·관측은 paldo.observations로만 간다. 판 도중 paldo.game에는 결정(씐 자 밤 명령 등)이 실리지 않는다."""
        self.assertEqual(topic_of(EventType.DECISION), "paldo.observations")
        self.assertEqual(topic_of(EventType.OBSERVATION), "paldo.observations")
        game_types = {e.event_type for evs in self.all_events for e in evs if topic_of(e.event_type) == "paldo.game"}
        self.assertFalse(game_types & {EventType.DECISION, EventType.OBSERVATION, EventType.TRUTH_REVEALED})

    def test_every_npc_action_has_decision(self):
        """NPC 말·표·캐묻기 답·밤 명령 수 = 종류별 decision 수."""
        for (m, first, steps, st, tr), evs in zip(self.games, self.all_events):
            dec = [e.payload for e in evs if e.event_type is EventType.DECISION]
            talk = sum(1 for d in dec if d["kind"] in {k.value for k in engine_mod._TALK_KINDS})
            npc_talks = sum(1 for s in st.public.speeches if s.speaker != PLAYER_ID and s.kind is not CommandKind.INTERROGATE)
            answers = sum(1 for s in st.public.speeches if s.kind is CommandKind.INTERROGATE)
            npc_votes = sum(1 for v in st.public.votes if v.voter != PLAYER_ID)
            with self.subTest(game=m.game_id):
                self.assertEqual(talk, npc_talks)
                self.assertEqual(sum(1 for d in dec if d["kind"] == "react"), answers)
                self.assertEqual(sum(1 for d in dec if d["kind"] == "vote"), npc_votes)
                self.assertEqual(sum(1 for d in dec if d["kind"] == "night_plan"), len(tr.night_plans) - 1)

    def test_snapshot_has_no_truth(self):
        """스냅샷 JSON에는 진실 필드가 없고, private은 관찰자 자기 것, 씐 자 정보는 씐 자 관찰자에게만."""
        for (m, _, steps, st, tr), evs in zip(self.games[:30], self.all_events[:30]):
            roles = object.__getattribute__(tr, "roles")
            for e in evs:
                if e.event_type is not EventType.OBSERVATION:
                    continue
                body = e.payload["snapshot"]
                self.assertFalse({"roles", "claim_truths", "secrets", "truth", "npcs"} & _keys(body))
                self.assertEqual(body["private"]["npc_id"], body["observer"])
                if roles[body["observer"]] is Role.VILLAGER:
                    self.assertIsNone(body["private"]["partner_id"])
                    self.assertEqual(body["private"]["night_plans"], {})

    # ---- 조건 3: truth_revealed는 ended 뒤에만
    def test_truth_revealed_only_after_ended(self):
        for (m, first, steps, st, tr), evs in zip(self.games, self.all_events):
            self.assertNotIn(EventType.TRUTH_REVEALED, [e.event_type for e in first.events])
            with self.subTest(game=m.game_id):
                truth_ev = [e for e in evs if e.event_type is EventType.TRUTH_REVEALED]
                self.assertEqual(len(truth_ev), 1)
                self.assertIs(truth_ev[0].phase, Phase.ENDED)
                last = steps[-1][1].events
                self.assertIs(last[-1].event_type, EventType.TRUTH_REVEALED)
                self.assertIs(last[-2].event_type, EventType.GAME_ENDED)
                for _, r in steps[:-1]:
                    self.assertNotIn(EventType.TRUTH_REVEALED, [e.event_type for e in r.events])
                body = truth_ev[0].payload
                self.assertEqual(body["roles"], {k: v.value for k, v in object.__getattribute__(tr, "roles").items()})
                self.assertEqual(set(body), {"roles", "secrets", "lies", "whereabouts", "night_plans"})

    # ---- 조건 4: 이유 코드·근거 ID
    def test_every_decision_has_reason_codes(self):
        valid = {c.value for c in ReasonCode}
        no_ev = {c.value for c in NO_EVIDENCE_CODES}
        seen_codes = set()
        for evs in self.all_events:
            claims_by_snap = {}
            for e in evs:
                if e.event_type is EventType.OBSERVATION:
                    claims_by_snap[e.payload["snapshot_id"]] = {c["claim_id"] for c in e.payload["snapshot"]["public"]["claims"]}
                if e.event_type is not EventType.DECISION:
                    continue
                d = e.payload
                self.assertTrue(d["reason_codes"])
                self.assertLessEqual(set(d["reason_codes"]), valid)
                seen_codes |= set(d["reason_codes"])
                if not d["evidence"]:
                    self.assertLessEqual(set(d["reason_codes"]), no_ev)
                for ref in d["evidence"]:            # 근거 ID는 그 스냅샷의 공개 주장
                    self.assertEqual(ref["type"], EvidenceType.CLAIM.value)
                    self.assertIn(ref["record_id"], claims_by_snap[d["snapshot_id"]])
                self.assertTrue(d["policy_version"])
        self.assertIn(ReasonCode.MAX_SUSPICION.value, seen_codes)
        # 기본 규칙은 목격 근거를 쓰지 않으므로 witness_* 코드를 붙이지 않는다(쓰지 않은 근거 기록 금지)
        self.assertFalse({ReasonCode.WITNESS_OTHERS_CLAIM.value, ReasonCode.WITNESS_MUTUAL_VOUCH.value} & seen_codes)

    # ---- 조건 5: 아침 발표에 피해 장소·시간대
    def test_morning_announcement_has_crime_scene(self):
        homes = 0
        for (m, first, steps, st, tr), evs in zip(self.games, self.all_events):
            announced = [e for e in first.events + evs if e.event_type is EventType.MORNING_ANNOUNCED]
            recs = st.public.disappearances
            night_end = any(e.event_type is EventType.NIGHT_RESOLVED and e.payload["end"] for e in evs)
            # 0일째 포함 밤마다 1회. 밤 처리로 판이 끝나면 아침이 없어 마지막 실종은 발표되지 않는다
            self.assertEqual(len(announced), len(recs) - (1 if night_end else 0))
            where = object.__getattribute__(tr, "whereabouts")
            for e, rec in zip(announced, recs):
                self.assertEqual(e.payload["crime_scene"], {"place": rec.place.value, "slot": rec.slot.value})
                self.assertEqual(e.payload["missing"], [rec.victim])
                self.assertIs(rec.place, where[rec.night][rec.victim][Slot.MIDNIGHT])
                self.assertIs(rec.slot, Slot.MIDNIGHT)
                if rec.place is Place.HOME:
                    self.assertEqual(crime_scene_of(rec), (Place.HOME, rec.victim))
                    homes += 1
            self.assertEqual(recs[0].night, 0)
        self.assertGreater(homes, 0)


def _keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys(v)
    return out





# ---------------------------------------------------------------- 조건 1: 처리 순서와 진실 차단
class TruthBlindPolicy(BaselinePolicy):
    """결정마다 스냅샷의 진실·남의 상태 필드에 접근해 보고(모두 실패해야 한다), 믿음이 이미 갱신됐는지 기록한다."""

    FORBIDDEN = ("truth", "roles", "secrets", "claim_truths", "night_plans", "npcs", "whereabouts", "active")

    def __init__(self):
        self.calls = 0
        self.leaks: list[str] = []
        self.stale_beliefs = 0

    def _probe(self, obs):
        self.calls += 1
        for name in self.FORBIDDEN:
            try:
                getattr(obs, name)
            except AttributeError:
                continue
            self.leaks.append(name)
        want = update_beliefs(obs.beliefs, obs.public, obs.private, PRESETS[obs.preset_id].n_possessed, obs.observer)
        if want != obs.beliefs:                     # 스냅샷의 믿음이 이미 갱신 끝난 상태여야 한다
            self.stale_beliefs += 1

    def talk(self, obs, rng):
        self._probe(obs)
        return super().talk(obs, rng)

    def vote(self, obs, candidates, rng):
        self._probe(obs)
        return super().vote(obs, candidates, rng)

    def react(self, obs, cmd, rng):
        self._probe(obs)
        return super().react(obs, cmd, rng)

    def night_plan(self, obs, executors, victims, rng):
        self._probe(obs)
        assert obs.private.role is Role.POSSESSED
        return super().night_plan(obs, executors, victims, rng)


class ProcessingOrder(unittest.TestCase):
    def test_policy_signature_has_no_truth_or_state(self):
        for cls in (NpcPolicy, BaselinePolicy):
            for name in ("talk", "vote", "react", "night_plan"):
                hints = typing.get_type_hints(getattr(cls, name), vars(engine_mod))
                with self.subTest(cls=cls.__name__, method=name):
                    self.assertIs(hints["obs"], ObservationSnapshot)
                    self.assertNotIn(GameTruth, hints.values())
                    self.assertNotIn(GameState, hints.values())
                    self.assertIs(hints["return"], Decision)

    def test_snapshot_fields_exclude_truth(self):
        fields = {f.name for f in dataclasses.fields(ObservationSnapshot)}
        self.assertFalse(fields & {"truth", "roles", "claim_truths", "npcs", "secrets"})
        self.assertNotIn("truth", typing.get_type_hints(observe_npc))

    def test_truth_access_fails_and_beliefs_updated_first(self):
        for preset_id, seed in (("base6", 3), ("duo9", 4), ("duo11", 5)):
            pol = TruthBlindPolicy()
            play(PaldoEngine(pol), preset_id, seed)
            with self.subTest(preset=preset_id):
                self.assertGreater(pol.calls, 0)
                self.assertEqual(pol.leaks, [])
                self.assertEqual(pol.stale_beliefs, 0)

    def test_observation_input_ignores_truth(self):
        """`PaldoEngine.observe`는 진실을 쓰지 않는다: 진실 대신 None을 넘겨도 같은 스냅샷."""
        engine = PaldoEngine()
        r = engine.new_game(meta("base6", 1), DEPS)
        npc = next(n for n in r.state.active if n != PLAYER_ID)
        a = engine.observe(r.state, r.truth, npc)
        b = engine.observe(r.state, None, npc)
        self.assertEqual(serde.to_jsonable(a), serde.to_jsonable(b))
        self.assertIn(":q000:", a.snapshot_id)          # 결정 스냅샷(":d000:")과 겹치지 않는 조회용 ID

    def test_night_snapshot_only_own_private(self):
        """밤 명령 스냅샷에는 그 씐 자의 private(partner_id·night_plans)만, 다른 NPC의 private은 없다."""
        _, first, steps, st, tr = play(PaldoEngine(), "duo9", 7)
        roles = object.__getattribute__(tr, "roles")
        evs = [e for _, r in steps for e in r.events]
        nights = [i for i, e in enumerate(evs) if e.event_type is EventType.DECISION and e.payload["kind"] == "night_plan"]
        self.assertTrue(nights)
        for i in nights:
            body = evs[i - 1].payload["snapshot"]
            obs = body["observer"]
            self.assertIs(roles[obs], Role.POSSESSED)
            self.assertEqual(body["private"]["npc_id"], obs)
            self.assertIsNotNone(body["private"]["partner_id"])
            self.assertTrue(body["private"]["night_plans"])
            others = [n for n in roles if n not in (obs, PLAYER_ID)]
            self.assertNotIn("npcs", body)
            text = json.dumps(body["private"], ensure_ascii=False)
            for n in others:      # 남의 개인 지식 필드(npc_id 키)가 private에 없다
                self.assertNotIn(f'"npc_id": "{n}"', text)


# ---------------------------------------------------------------- 조건 6 단위: 근거 출처 구분
class WitnessEvidence(unittest.TestCase):
    def _public(self):
        rec = DisappearanceRecord(0, "gwabu", Place.WELL)
        claims = [
            Claim("cl01", "jumo", ClaimType.SIGHTING, "jumo", 0, Slot.MIDNIGHT, Place.TAVERN, seen=["yakcho"]),
            Claim("cl02", "yakcho", ClaimType.SIGHTING, "yakcho", 0, Slot.MIDNIGHT, Place.TAVERN, seen=["jumo"]),
            Claim("cl03", "hunjang", ClaimType.SIGHTING, "hunjang", 0, Slot.MIDNIGHT, Place.SHRINE, seen=["meosum"]),
            Claim("cl04", "blacksmith", ClaimType.SIGHTING, "blacksmith", 0, Slot.MIDNIGHT, Place.WELL, seen=["gwabu"]),
            Claim("cl05", "meosum", ClaimType.SIGHTING, "meosum", 0, Slot.EVENING, Place.SHRINE, seen=["hunjang"]),
        ]
        return PublicRecord(claims=claims, disappearances=[rec])

    def test_sources(self):
        got = [(p.subject, p.source, cid) for p, cid in witness_evidence(self._public(), "me", 1)]
        self.assertEqual(got, [("yakcho", EvidenceSource.MUTUAL_VOUCH, "cl01"),
                               ("jumo", EvidenceSource.MUTUAL_VOUCH, "cl02"),
                               ("meosum", EvidenceSource.OTHERS_CLAIM, "cl03")])

    def test_kind_follows_preset(self):
        kinds = {p.kind for p, _ in witness_evidence(self._public(), "me", 2)}
        self.assertEqual(kinds, {PropositionKind.NOT_EXECUTOR})

    def test_not_a_proposition(self):
        from engine.domain.model import Beliefs, PrivateKnowledge
        b = update_beliefs(Beliefs(), self._public(), PrivateKnowledge("me", Role.VILLAGER), 1, "me")
        self.assertEqual(b.propositions, [])
        self.assertEqual([(r.target, r.evidence.record_id, r.delta) for r in b.reasons],
                         [("yakcho", "cl01", 0.0), ("jumo", "cl02", 0.0), ("meosum", "cl03", 0.0)])
        again = update_beliefs(b, self._public(), PrivateKnowledge("me", Role.VILLAGER), 1, "me")
        self.assertEqual(again, b)                  # 같은 근거는 한 번만

    def test_own_witness_becomes_proposition(self):
        from engine.domain.model import Beliefs, PrivateKnowledge
        pk = PrivateKnowledge("me", Role.VILLAGER, witnessed=[Sighting(0, Slot.MIDNIGHT, Place.TAVERN, ["jumo"])])
        b = update_beliefs(Beliefs(), self._public(), pk, 1, "me")
        self.assertEqual([(p.subject, p.kind, p.source) for p in b.propositions],
                         [("jumo", PropositionKind.NOT_POSSESSED, EvidenceSource.OWN_WITNESS)])

    def test_witness_basis_codes(self):
        """출처별 이유 코드와 주장 근거. 프리셋 목록에 없는 프리셋 ID여도 동작한다(창귀 수를 읽지 않음)."""
        from engine.domain.model import Beliefs, PrivateKnowledge
        snap = ObservationSnapshot("s", "g", "me", 1, Phase.VOTE, 1, self._public(), PrivateKnowledge("me", Role.VILLAGER),
                                   Beliefs(), [CommandKind.VOTE], ["jumo", "meosum", "yakcho"], 1, "custom_preset")
        self.assertEqual(witness_basis(snap, "yakcho"), ([ReasonCode.WITNESS_MUTUAL_VOUCH], [EvidenceRef(EvidenceType.CLAIM, "cl01")]))
        self.assertEqual(witness_basis(snap, "meosum"), ([ReasonCode.WITNESS_OTHERS_CLAIM], [EvidenceRef(EvidenceType.CLAIM, "cl03")]))
        self.assertEqual(witness_basis(snap, "hunjang"), ([], []))

    def test_decision_requires_evidence_unless_listed(self):
        with self.assertRaises(ValueError):
            decision_payload("jumo", "s", "vote", "yakcho", Decision("yakcho", []), "v")
        with self.assertRaises(ValueError):
            decision_payload("jumo", "s", "vote", "yakcho", Decision("yakcho", [ReasonCode.WITNESS_OTHERS_CLAIM]), "v")
        ok = decision_payload("jumo", "s", "silence", None, Decision(None, [ReasonCode.BASELINE_SILENCE]), "v")
        self.assertEqual(ok["reason_codes"], ["baseline_silence"])


if __name__ == "__main__":
    unittest.main()
