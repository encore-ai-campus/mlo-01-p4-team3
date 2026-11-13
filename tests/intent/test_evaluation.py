"""Evaluation checks must catch slot errors, not just label errors."""
import unittest
from dataclasses import replace

from engine.domain.model import Command, CommandKind
from intent.baseline import load_examples, split_examples
from intent.evaluation import command_equal, evaluate, reference_case
from intent.parser import BaselineIntentParser


class EvaluationTests(unittest.TestCase):
    """Whole command comparison and fixed reference context."""

    def test_whole_command_detects_every_slot(self):
        expected = Command(CommandKind.SUSPECT, "mudang", target="hunjang")
        self.assertTrue(command_equal(expected, replace(expected)))
        for change in ({"target": "jumo"}, {"night": 0}, {"addressee": "jumo"}, {"kind": CommandKind.DEFEND}):
            self.assertFalse(command_equal(expected, replace(expected, **change)))

    def test_reference_roles_and_time(self):
        rows = {r.example_id: r for r in load_examples()}
        text, ctx, expected = reference_case(rows["seed_043"])
        self.assertEqual((expected.addressee, expected.target), ("jumo", "hunjang"))
        _, _, expected = reference_case(rows["seed_030"])
        self.assertEqual(expected.night, 0)
        _, _, expected = reference_case(rows["seed_031"])
        self.assertIsNone(expected.night)

    def test_metrics_are_held_out_and_bounded(self):
        train, dev = split_examples(load_examples())
        from intent.baseline import IntentBaseline
        report = evaluate(BaselineIntentParser(IntentBaseline().fit(train)), dev)
        self.assertEqual(report["metrics"]["evaluation_rows"]["value"], len(dev))
        for key in ("macro_f1", "command_exact_match", "unsupported_recall"):
            self.assertTrue(0 <= report["metrics"][key]["value"] <= 1)
        self.assertEqual(len(report["cases"]), len(dev))
