"""복기용 이유 조회 `review_trace`와 템플릿 해설(E-10-10). 도메인 함수만, 실제 엔진 판(규칙 v0 + 가짜 의심 모델)으로 검사한다."""
from __future__ import annotations

import copy
import json
import random
import unittest
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import (
    PRESETS, CommandEnvelope, CommandKind, DataSource, EvidenceRef, EvidenceType, GameMeta,
)
from engine.domain.npc import RuleV0Policy
from engine.domain.observation import ReasonCode
from engine.domain.review import (
    TraceEvent, TraceInput, attach_josa, evidence_speaker, missing_phrases, phrase_id_for, representative_code,
    review_text, review_trace,
)
from engine.domain.serde import dumps
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel
from tests.engine.test_scenarios_engine import CONTENT
from tools.perf.engine_bench import bench_player_command

LINES_PATH = Path(__file__).resolve().parents[2] / "content" / "review_lines_v1.json"


def lines() -> dict[str, list[str]]:
    return json.loads(LINES_PATH.read_text(encoding="utf-8"))["lines"]


def play(seed: int, preset: str = "base6"):
    """규칙 v0 판 하나를 끝까지 진행한다. (최종 상태, TraceEvent 목록)."""
    engine = PaldoEngine(RuleV0Policy(CONTENT))
    deps = EngineDeps(CONTENT, FakeSuspicionModel())
    meta = GameMeta(f"g_review_{seed:03d}", PRESETS[preset], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    "test", CONTENT.content_version)
    r = engine.new_game(meta, deps)
    st, tr, rng = r.state, r.truth, random.Random(seed)
    events: list[TraceEvent] = []

    def add(transition_events, version):
        for e in transition_events:
            events.append(TraceEvent(len(events), e.day, e.phase.value, version, e.event_type.value,
                                     json.loads(dumps(e.payload))))
    add(r.events, st.state_version)
    while st.result is None:
        view = engine.player_view(st, CONTENT)
        cmd = bench_player_command(view, rng)
        r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version}", st.state_version, cmd), deps)
        assert r.accepted, (r.reject, r.reject_detail)
        st, tr = r.state, r.truth
        add(r.events, st.state_version)
    return st, events


class ReviewTraceOnRealGames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = {seed: play(seed) for seed in range(4)}

    def trace(self, seed):
        st, events = self.games[seed]
        return st, events, review_trace(TraceInput(st.meta.game_id, st.meta.rule_version, st.meta.seed, events,
                                                   st.public))

    def test_every_npc_decision_becomes_one_item(self):
        """NPC decision 이벤트 수 = 복기 항목 수, 순서·ID(d<seq>)·이유 코드가 이벤트 그대로."""
        for seed in self.games:
            _, events, trace = self.trace(seed)
            decisions = [e for e in events if e.event_type == EventType.DECISION.value
                         and e.payload["actor"] != "mudang"]
            self.assertTrue(decisions)
            self.assertEqual([i.item_id for i in trace.items], [f"d{e.seq}" for e in decisions])
            for item, ev in zip(trace.items, decisions):
                self.assertEqual(item.reason_codes, ev.payload["reason_codes"])
                self.assertEqual(item.phrase_id, phrase_id_for(item.reason_codes))
            self.assertIsNone(trace.win_prob)

    def test_all_npc_votes_have_reasons(self):
        """공개된 NPC 표마다 같은 날·투표자의 vote 결정이 있다(누락 0)."""
        for seed in self.games:
            st, _, trace = self.trace(seed)
            voted = {(v.day, v.voter) for v in st.public.votes if v.voter != "mudang"}
            decided = {(i.day, i.actor) for i in trace.items if i.kind == CommandKind.VOTE.value}
            self.assertLessEqual(voted, decided, seed)
            for item in trace.items:
                if item.kind == CommandKind.VOTE.value:
                    self.assertTrue(item.target_npc and item.reason_codes)

    def test_talk_and_answers_link_to_their_speech(self):
        """회의 말은 같은 화자·같은 종류의 발언, 캐묻기 답은 그 NPC의 답변 발언과 이어진다."""
        for seed in self.games:
            st, _, trace = self.trace(seed)
            speeches = {s.speech_id: s for s in st.public.speeches}
            talks = [i for i in trace.items if i.kind in {"suspect", "defend", "point_contradiction", "explain",
                                                         "silence"}]
            self.assertTrue(talks)
            for item in talks:
                sp = speeches[item.speech_id]
                self.assertEqual((sp.speaker, sp.kind.value), (item.actor, item.kind))
            for item in (i for i in trace.items if i.kind == "react"):
                sp = speeches[item.speech_id]
                self.assertEqual((sp.speaker, sp.kind), (item.actor, CommandKind.INTERROGATE))
                self.assertEqual(sp.reaction.value, item.reaction)
            linked = [i.speech_id for i in trace.items if i.speech_id]
            self.assertEqual(len(linked), len(set(linked)))

    def test_night_and_vote_items_have_no_speech(self):
        for seed in self.games:
            _, _, trace = self.trace(seed)
            for item in trace.items:
                if item.kind in ("vote", "night_plan"):
                    self.assertIsNone(item.speech_id)
                if item.kind == "night_plan":
                    self.assertTrue(item.executor and item.victim)

    def test_result_ignores_state_changes_after_the_game(self):
        """이벤트만 입력이다: 종료 뒤 공개 기록·상태를 바꿔도 이유·행동은 같다(사후 계산 금지)."""
        st, events, trace = self.trace(0)
        public = copy.deepcopy(st.public)
        public.votes.clear()
        for npc in st.npcs.values():
            npc.beliefs = None
        again = review_trace(TraceInput(st.meta.game_id, st.meta.rule_version, st.meta.seed, events, public))
        self.assertEqual([(i.item_id, i.kind, i.target_npc, i.reason_codes) for i in again.items],
                         [(i.item_id, i.kind, i.target_npc, i.reason_codes) for i in trace.items])

    def test_input_events_are_not_modified(self):
        st, events, _ = self.trace(1)
        before = copy.deepcopy(events)
        review_trace(TraceInput(st.meta.game_id, st.meta.rule_version, st.meta.seed, events, st.public))
        self.assertEqual(events, before)

    def test_every_item_has_template_text(self):
        """실제 판의 모든 항목이 이름으로 채운 템플릿 문장을 갖고 자리표시자·괄호 조사가 남지 않는다."""
        names = {c.npc_id: c.name_ko for c in CONTENT.characters}
        for seed in self.games:
            st, _, trace = self.trace(seed)
            for item in trace.items:
                text = review_text(item, lines(), names, st.meta.seed)
                self.assertTrue(text, item.phrase_id)
                for bad in ("{", "}", "(는)", "(를)", "(가)"):
                    self.assertNotIn(bad, text)
                if "{actor}" in lines()[item.phrase_id][0]:
                    self.assertIn(names[item.actor], text)


class ReviewTraceRules(unittest.TestCase):
    def ev(self, seq, version, etype, payload, day=1, phase="round1"):
        return TraceEvent(seq, day, phase, version, etype, payload)

    def decision(self, seq, version, actor, kind, target, codes=("rule_silence",)):
        return self.ev(seq, version, "decision", {"actor": actor, "snapshot_id": "s", "kind": kind, "target": target,
                                                  "reason_codes": list(codes), "evidence": [],
                                                  "policy_version": "rule_v0"})

    def test_talk_link_stops_at_transition_boundary(self):
        """다음 전이의 발언과는 잇지 않는다."""
        events = [self.decision(0, 3, "jumo", "silence", None), self.ev(1, 4, "speech_made", {"speech_id": "sp9"})]
        item = review_trace(TraceInput("g", "r", 1, events)).items[0]
        self.assertIsNone(item.speech_id)

    def test_player_decisions_and_other_events_are_skipped(self):
        events = [self.decision(0, 1, "mudang", "suspect", "jumo"), self.ev(1, 1, "speech_made", {"speech_id": "a"}),
                  self.decision(2, 1, "jumo", "suspect", "hunjang", ("rule_suspect", "witness_others_claim")),
                  self.ev(3, 1, "speech_made", {"speech_id": "b"})]
        items = review_trace(TraceInput("g", "r", 1, events)).items
        self.assertEqual([(i.item_id, i.actor, i.target_npc, i.speech_id) for i in items],
                         [("d2", "jumo", "hunjang", "b")])

    def test_old_or_unknown_evidence_rows_are_tolerated(self):
        ev = self.decision(0, 1, "jumo", "vote", "hunjang", ("max_suspicion",))
        ev.payload["evidence"] = [{"type": "claim", "record_id": "c1"}, {"type": "???"}, "bad"]
        item = review_trace(TraceInput("g", "r", 1, [ev])).items[0]
        self.assertEqual(item.evidence, [EvidenceRef(EvidenceType.CLAIM, "c1")])

    def test_target_shapes_by_kind(self):
        events = [self.decision(0, 1, "jumo", "react", "evade", ("react_evade",)),
                  self.decision(1, 2, "jumo", "night_plan", {"executor": "jumo", "victim": "gwabu"},
                                ("night_accuser",))]
        react, night = review_trace(TraceInput("g", "r", 1, events)).items
        self.assertEqual((react.reaction, react.target_npc), ("evade", None))
        self.assertEqual((night.executor, night.victim, night.target_npc), ("jumo", "gwabu", None))

    def test_representative_code_prefers_evidence_codes(self):
        self.assertEqual(representative_code(["max_suspicion", "rule_suspect"]), "rule_suspect")
        self.assertEqual(representative_code(["model_estimate", "vote_conform"]), "model_estimate")
        with self.assertRaises(ValueError):
            representative_code([])


class TemplateLines(unittest.TestCase):
    def test_no_reason_code_is_missing(self):
        """E-10-10 4번: 모든 `ReasonCode`에 문구가 있다(rv_skip_trusted_mudang 포함, 빠진 코드 0건)."""
        self.assertEqual(missing_phrases(lines()), [])
        self.assertIn("rv_skip_trusted_mudang", lines())
        self.assertEqual(set(lines()), {"rv_" + c.value for c in ReasonCode})

    def test_placeholders_are_allowed_slots_only(self):
        import string
        allowed = {"actor", "target", "victim", "executor"}
        for pid, variants in lines().items():
            self.assertTrue(variants, pid)
            for v in variants:
                used = {f for _, f, _, _ in string.Formatter().parse(v) if f}
                self.assertLessEqual(used, allowed, pid)

    def test_variant_choice_is_deterministic(self):
        from engine.domain.review import ReviewTraceItem
        item = ReviewTraceItem("d5", 1, "vote", "jumo", "vote", ["max_suspicion"], [], "v", "rv_x", target_npc="gwabu")
        many = {"rv_x": ["{actor}이(가) {target}을(를) 찍음 1", "{actor}이(가) {target}을(를) 찍음 2"]}
        names = {"jumo": "주모", "gwabu": "과부"}
        first = review_text(item, many, names, 7)
        self.assertEqual(first, review_text(item, many, names, 7))
        self.assertIn(first, {"주모가 과부를 찍음 1", "주모가 과부를 찍음 2"})
        seen = {review_text(item, many, names, s) for s in range(30)}
        self.assertEqual(len(seen), 2)
        self.assertEqual(review_text(item, {}, names, 7), "")

    def test_missing_slot_uses_default_word(self):
        from engine.domain.review import ReviewTraceItem
        item = ReviewTraceItem("d1", 1, "round1", "jumo", "silence", ["rule_silence"], [], "v", "rv_y")
        self.assertEqual(review_text(item, {"rv_y": ["{actor}은(는) {target}을(를) 봄"]}, {"jumo": "주모"}, 1),
                         "주모는 다른 사람을 봄")

    def test_attach_josa(self):
        self.assertEqual(attach_josa("훈장은(는) 대장장이을(를) 의심"), "훈장은 대장장이를 의심")
        self.assertEqual(attach_josa("과부이(가) 머슴과(와)"), "과부가 머슴과")
        self.assertEqual(attach_josa("약초꾼은(는)"), "약초꾼은")


class EvidenceSpeaker(unittest.TestCase):
    def test_resolves_claim_and_contradiction_speaker(self):
        st, _ = play(2)
        claim = st.public.claims[0]
        self.assertEqual(evidence_speaker(EvidenceRef(EvidenceType.CLAIM, claim.claim_id), st.public), claim.speaker)
        self.assertEqual(evidence_speaker(EvidenceRef(EvidenceType.CONTRADICTION, claim.claim_id + "|x"), st.public),
                         claim.speaker)
        self.assertIsNone(evidence_speaker(EvidenceRef(EvidenceType.VOTE, "v1"), st.public))
        self.assertIsNone(evidence_speaker(EvidenceRef(EvidenceType.CLAIM, "nope"), st.public))


if __name__ == "__main__":
    unittest.main()
