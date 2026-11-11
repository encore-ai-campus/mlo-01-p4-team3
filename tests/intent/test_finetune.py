"""D-07-03: 계보·대조쌍 격리, 검수 전 데이터 차단, CPU 지표와 BERT 버전."""
import unittest
from dataclasses import replace

from contracts.datasets import LabelSource, Split
from intent.baseline import load_examples
from intent.evaluation import reference_case
from intent.finetune import cpu_usage, prepare_split, select_label
from intent.parser import BaselineIntentParser
from tests.intent.test_parser import context


class FinetuneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_examples("content/intent/examples_v1.jsonl")

    def test_split_preserves_lineages_and_contrast_groups_and_is_reproducible(self):
        approved = [row for row in self.rows if row.label_source is not LabelSource.GENERATED]
        root = next(row for row in approved if row.label_source is LabelSource.AUTHORED)
        reviewed = replace(root, example_id=root.example_id + "_v998", text=root.text + "!",
                           label_source=LabelSource.GENERATED_REVIEWED)
        pending = replace(root, example_id=root.example_id + "_v999", text=root.text + "?",
                          label_source=LabelSource.GENERATED)
        rows = [*self.rows, reviewed, pending]
        train, dev = prepare_split(rows, seed=7)
        self.assertEqual((train, dev), prepare_split([*approved, reviewed], seed=7))
        self.assertEqual(prepare_split(rows, seed=7), (train, dev))
        for key in (lambda r: r.split_key.lineage_root, lambda r: r.contrast_group):
            self.assertFalse({key(r) for r in train if key(r)} & {key(r) for r in dev if key(r)})
        self.assertEqual({r.label for r in train}, {r.label for r in dev})
        self.assertTrue(all(r.split is Split.TRAIN for r in train))
        self.assertTrue(all(r.split is Split.DEV for r in dev))
        self.assertEqual(len(train) + len(dev), len(approved) + 1)
        self.assertIn(reviewed.example_id, {row.example_id for row in train + dev})

    def test_explicit_generated_experiment_uses_all_rows_without_changing_sources(self):
        train, dev = prepare_split(self.rows, seed=7, include_generated=True)
        self.assertEqual(len(train) + len(dev), len(self.rows))
        self.assertEqual({r.example_id for r in train + dev}, {r.example_id for r in self.rows})
        self.assertEqual(
            {r.example_id for r in train + dev if r.label_source is LabelSource.GENERATED},
            {r.example_id for r in self.rows if r.label_source is LabelSource.GENERATED},
        )
        for key in (lambda r: r.split_key.lineage_root, lambda r: r.contrast_group):
            self.assertFalse({key(r) for r in train if key(r)} & {key(r) for r in dev if key(r)})
        self.assertEqual({r.label for r in train}, {r.label for r in dev})
        self.assertEqual((train, dev), prepare_split(self.rows, seed=7, include_generated=True))
        for split in (Split.DEV, Split.TEST_HUMAN):
            with self.assertRaises(ValueError):
                prepare_split([replace(self.rows[0], split=split), *self.rows[1:]],
                              include_generated=True)

    def test_reject_fixed_evaluation_data_even_if_unreviewed(self):
        for row in (replace(self.rows[0], label_source=LabelSource.GENERATED, split=Split.TEST_HUMAN),
                    replace(self.rows[0], split=Split.TEST_HUMAN),
                    replace(self.rows[0], split=Split.DEV)):
            with self.assertRaises(ValueError):
                prepare_split([row, *self.rows[1:]])

    def test_same_threshold_and_nonfinite_probability_rejection(self):
        self.assertEqual(select_label({"suspect": .19, "defend": .18}, .2), ("unsupported", .19))
        self.assertEqual(select_label({"suspect": .8, "defend": .2}, .2), ("suspect", .8))
        with self.assertRaises(ValueError):
            select_label({"suspect": float("nan")}, .2)

    def test_cpu_cores_and_normalized_machine_usage_are_distinct(self):
        result = cpu_usage(8., 4., 16)
        self.assertEqual(result["process_cpu_percent"], 200.)
        self.assertEqual(result["machine_cpu_percent"], 12.5)
        with self.assertRaises(ValueError):
            cpu_usage(1., 0., 16)

    def test_evaluation_respects_authored_context_hints(self):
        row = next(r for r in self.rows if "ctx=last_addressee" in r.notes)
        _, ctx, _ = reference_case(row)
        self.assertIsNone(ctx.selected_npc)
        self.assertEqual(ctx.last_addressee, "hunjang")
        row = next(r for r in self.rows if "ctx=selected_npc" in r.notes)
        _, ctx, _ = reference_case(row)
        self.assertEqual(ctx.selected_npc, "hunjang")

    def test_classifier_supplied_tokenizer_version_preserves_shared_parser(self):
        class Model:
            model_version = "bert-test"
            tokenizer_version = "hf-tokenizer-test"
            threshold = .2
            def predict(self, text):
                return "suspect", .8
        result = BaselineIntentParser(Model()).parse("훈장이 수상하다", context())
        self.assertEqual(result.versions["tokenizer"], "hf-tokenizer-test")
        self.assertEqual(result.versions["model"], "bert-test")
        self.assertTrue(result.needs_confirmation)
