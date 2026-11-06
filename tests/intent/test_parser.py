"""Five-stage parser behavior and role resolution."""
import copy
import unittest

from engine.domain.model import CommandKind, EvidenceRef, EvidenceType, InterrogationKind, Speech
from engine.domain.serde import from_jsonable, to_jsonable
from engine.ports import IntentParser, ParseFailure, ParseResult, UtteranceForm
from intent.parser import BaselineIntentParser
from intent.matcher import normalize_people
from tests.intent.test_matcher import claim, context


class ParserTests(unittest.TestCase):
    """The real parser preserves context and requires confirmation."""

    @classmethod
    def setUpClass(cls):
        """Train one seed baseline for functional tests, not evaluation metrics."""
        cls.parser = BaselineIntentParser()

    def test_port_versions_serialization_and_immutable_context(self):
        ctx = context()
        before = copy.deepcopy(ctx)
        self.assertIsInstance(self.parser, IntentParser)
        result = self.parser.parse("훈장이 수상하다", ctx)
        self.assertEqual(ctx, before)
        self.assertEqual(set(result.versions), {"model", "tokenizer", "alias_dict", "matcher", "threshold"})
        self.assertTrue(all(result.versions.values()))
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(from_jsonable(ParseResult, to_jsonable(result)), result)

    def test_context_priority_and_missing_target(self):
        ctx = context()
        ctx.selected_npc, ctx.last_addressee = "jumo", "meosum"
        self.assertEqual(self.parser.parse("훈장이 수상하다", ctx).candidates[0].command.target, "hunjang")
        self.assertEqual(self.parser.parse("수상하다", ctx).candidates[0].command.target, "jumo")
        ctx.selected_npc = None
        self.assertEqual(self.parser.parse("수상하다", ctx).candidates[0].command.target, "meosum")
        ctx.last_addressee = None
        self.assertEqual(self.parser.parse("수상하다", ctx).failure, ParseFailure.MISSING_TARGET)

    def test_recipient_mentioned_and_quote(self):
        ctx = context()
        ctx.last_addressee = "jumo"
        ctx.public.speeches = [Speech("s1", 1, ctx.phase, "meosum", CommandKind.SUSPECT, target="hunjang")]
        result = self.parser.parse("훈장이 수상하다고?", ctx)
        candidate = result.candidates[0]
        self.assertEqual(candidate.command.interrogation_kind, InterrogationKind.SUSPICION_REASON)
        self.assertEqual((candidate.command.addressee, candidate.command.target), ("meosum", "hunjang"))
        self.assertEqual(candidate.form, UtteranceForm.QUOTE)
        text = "훈장이 수상하다고? " + "훈장은 결백하오 " * 20
        candidate = self.parser.parse(text, ctx).candidates[0]
        probabilities = self.parser.model.classifier.predict_proba(
            self.parser.model.vectorizer.transform([normalize_people(text, ctx.aliases)]))[0]
        index = list(self.parser.model.classifier.classes_).index("interrogate_suspicion_reason")
        self.assertAlmostEqual(candidate.intent_score, float(probabilities[index]))
        result = self.parser.parse("주모님, 훈장을 왜 의심하오?", ctx)
        self.assertEqual((result.candidates[0].command.addressee, result.candidates[0].command.target),
                         ("jumo", "hunjang"))

    def test_negation_and_multi_intent(self):
        result = self.parser.parse("훈장이 수상하지 않다", context())
        self.assertEqual(result.candidates[0].command.kind, CommandKind.DEFEND)
        result = self.parser.parse("훈장은 수상하지만 머슴은 결백해", context())
        self.assertEqual({c.command.kind: c.command.target for c in result.candidates},
                         {CommandKind.SUSPECT: "hunjang", CommandKind.DEFEND: "meosum"})
        self.assertEqual([c.intent_score for c in result.candidates],
                         sorted((c.intent_score for c in result.candidates), reverse=True))

    def test_ambiguous_evidence_and_missing_slots(self):
        ctx = context()
        ctx.public.claims = [claim("c1"), claim("c2", "jumo"), claim("c3", "meosum")]
        result = self.parser.parse("서낭당 한밤 진술들이 앞뒤가 맞지 않소", ctx)
        self.assertEqual(result.failure, ParseFailure.AMBIGUOUS_EVIDENCE)
        self.assertEqual(len(result.candidates[0].evidence_alternatives), 3)
        ctx.selected_cards = [EvidenceRef(EvidenceType.CLAIM, "c1")] * 2
        result = self.parser.parse("훈장, 주모 말과 왜 다르오?", ctx)
        self.assertIn("evidence", result.candidates[0].missing_slots)

    def test_unsupported_empty_and_inactive_context(self):
        for text, failure in [(" ", ParseFailure.EMPTY_INPUT), ("배고프다", ParseFailure.UNSUPPORTED_INTENT),
                              ("훈장의 점괘는 어땠소?", ParseFailure.UNSUPPORTED_INTENT)]:
            with self.subTest(text=text):
                result = self.parser.parse(text, context())
                self.assertEqual(result.failure, failure)
                self.assertTrue(result.needs_confirmation)
        ctx = context()
        ctx.selected_npc = "inactive"
        self.assertEqual(self.parser.parse("수상하다", ctx).failure, ParseFailure.MISSING_TARGET)
