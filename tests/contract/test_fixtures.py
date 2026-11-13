"""모든 픽스처가 계약 타입을 엄격하게 지키는지(알 수 없는 필드·누락·잘못된 열거값 금지)."""
from __future__ import annotations

import unittest
from collections import Counter

from contracts.datasets import IntentExample, Scenario, Split, TrainingRow
from engine.domain import serde
from engine.domain.content import Assignment, ContentBundle, required_counts
from engine.domain.events import EventEnvelope, EventType, topic_of
from engine.domain.model import PRESETS, Command, DataSource, Phase
from engine.ports import PlayerView, RenderRequest

from ._util import FX, load_json, load_jsonl, strict


class SerdeStrictness(unittest.TestCase):
    def test_unknown_field_rejected(self):
        with self.assertRaises(serde.SchemaError):
            serde.from_jsonable(Command, {"kind": "suspect", "actor": "mudang", "oops": 1})

    def test_bad_enum_rejected(self):
        with self.assertRaises(serde.SchemaError):
            serde.from_jsonable(Command, {"kind": "accuse", "actor": "mudang"})

    def test_missing_required_rejected(self):
        with self.assertRaises(serde.SchemaError):
            serde.from_jsonable(Command, {"kind": "suspect"})

    def test_preset_invariants(self):
        with self.assertRaises(serde.SchemaError):
            serde.from_jsonable(type(PRESETS["base6"]), {**serde.to_jsonable(PRESETS["base6"]), "n_possessed": 2})


class ContentFixture(unittest.TestCase):
    def setUp(self):
        self.b = strict(ContentBundle, load_json("fixtures/content_min/bundle.json"))

    def test_counts_cover_all_presets(self):
        for pid, p in PRESETS.items():
            need = required_counts(p)
            self.assertGreaterEqual(len(self.b.characters), need["characters"], pid)
            self.assertGreaterEqual(len(self.b.secrets), need["secrets"], pid)

    def test_references_resolve(self):
        temps = {t.temperament_id for t in self.b.temperaments}
        chars = {c.npc_id for c in self.b.characters}
        clues = {c.clue_id for c in self.b.grudge_clues}
        sids = {s.secret_id for s in self.b.secrets}
        for c in self.b.characters:
            self.assertIn(c.temperament_id, temps)
        for s in self.b.secrets:
            self.assertTrue(set(s.eligible_npcs) <= chars, s.secret_id)
            self.assertNotEqual(s.actual_place, s.false_place)
            self.assertTrue(set(s.conflicts_with) <= sids)
            if s.grudge_clue_id:
                self.assertIn(s.grudge_clue_id, clues)

    def test_aliases_unique(self):
        seen = Counter(a for c in self.b.characters for a in c.aliases)
        self.assertEqual([a for a, n in seen.items() if n > 1], [])


class RecordedGame(unittest.TestCase):
    def test_steps(self):
        rec = load_json("fixtures/api/recorded_game_base6.json")
        self.assertEqual(rec["source"], "scripted")
        for i, s in enumerate(rec["steps"]):
            v = strict(PlayerView, s["view"])
            self.assertEqual(v.state_version, i)
            if i:
                self.assertEqual(s["request"]["expected_state_version"], i - 1)
                strict(Command, s["request"]["command"])
            for e in s["events"]:
                EventType(e["event_type"]); Phase(e["phase"])
        last = serde.from_jsonable(PlayerView, rec["steps"][-1]["view"])
        self.assertIsNotNone(last.result)
        self.assertEqual(last.legal_kinds, [])

    def test_view_has_no_truth(self):
        text = (FX / "api" / "recorded_game_base6.json").read_text(encoding="utf-8")
        for banned in ('"roles"', '"possessed"', '"beliefs"', '"claim_truths"'):
            self.assertNotIn(banned, text)


class EventsFixture(unittest.TestCase):
    def test_envelopes(self):
        envs = [strict(EventEnvelope, d) for d in load_jsonl("fixtures/events/sample_events.jsonl")]
        self.assertEqual([e.seq for e in envs], list(range(len(envs))))
        self.assertEqual(len({e.event_id for e in envs}), len(envs))
        for e in envs:
            self.assertEqual(e.topic, topic_of(EventType(e.event_type)))
        truth = [e for e in envs if e.event_type == "truth_revealed"]
        self.assertEqual(len(truth), 1)
        self.assertEqual(truth[0].topic, "paldo.truth")
        self.assertEqual(truth[0].phase, "ended")


class MlFixture(unittest.TestCase):
    def test_rows(self):
        rows = [strict(TrainingRow, d) for d in load_jsonl("fixtures/ml/observations_sample.jsonl")]
        for snap in {r.snapshot_id for r in rows}:
            group = [r for r in rows if r.snapshot_id == snap]
            cands = [r.candidate for r in group]
            self.assertEqual(cands, sorted(cands))
            self.assertNotIn(group[0].observer, cands)
            self.assertNotIn("mudang", cands)
            self.assertEqual(len(group), group[0].n_candidates)
            self.assertEqual(sum(r.label for r in group), group[0].n_possessed_remaining)
        for r in rows:
            self.assertIsNotNone(r.label_joined_at)
            self.assertNotIn("is_possessed", r.features)


class LlmFixture(unittest.TestCase):
    def test_requests(self):
        reqs = [strict(RenderRequest, d) for d in load_json("fixtures/llm/interrogation_requests.json")]
        for r in reqs:
            self.assertTrue(r.fallback_text.startswith(r.fact_sentence), r.job_id)
            self.assertEqual(r.priority, 0)


class IntentFixture(unittest.TestCase):
    def test_seed(self):
        ex = [strict(IntentExample, d) for d in load_jsonl("fixtures/intent/seed_examples.jsonl")]
        counts = Counter(e.label for e in ex)
        self.assertEqual(len(counts), 10)
        self.assertTrue(all(n >= 5 for n in counts.values()), counts)
        self.assertTrue(all(e.split is not Split.TEST_HUMAN for e in ex))
        groups = Counter(e.contrast_group for e in ex if e.contrast_group)
        self.assertTrue(groups and all(n >= 2 for n in groups.values()), groups)
        # 인물은 자리표시자만(실제 NPC ID 금지)
        for e in ex:
            for npc in ("hunjang", "jumo", "blacksmith", "훈장", "주모"):
                self.assertNotIn(npc, e.text)


class ScenarioAndAssignmentFixtures(unittest.TestCase):
    def test_scenarios_parse(self):
        files = sorted((FX / "scenarios").glob("*.json"))
        self.assertEqual(len(files), 8)
        for f in files:
            s = strict(Scenario, load_json(str(f.relative_to(FX.parent))))
            self.assertEqual(s.source, "scripted")
            self.assertTrue(s.checks)

    def test_assignment_files_parse(self):
        for name in ("valid_base6.json", "valid_duo9.json"):
            strict(Assignment, load_json(f"fixtures/assignment/{name}"))
        for c in load_json("fixtures/assignment/conflicts.json"):
            strict(Assignment, c["assignment"])


class SourceTag(unittest.TestCase):
    def test_all_fixture_data_is_scripted(self):
        for d in load_jsonl("fixtures/events/sample_events.jsonl"):
            self.assertEqual(d["source"], DataSource.SCRIPTED.value)


if __name__ == "__main__":
    unittest.main()
