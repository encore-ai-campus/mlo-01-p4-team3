"""Real classifier and leakage prevention tests."""
import unittest
from dataclasses import replace

from contracts.datasets import Split
from intent.baseline import IntentBaseline, load_examples, split_examples


class BaselineTests(unittest.TestCase):
    """Fit only training lineages and report genuine model scores."""

    def test_seed_schema_and_split(self):
        rows = load_examples()
        self.assertEqual(len(rows), 50)
        train, dev = split_examples(rows, seed=7)
        self.assertEqual(split_examples(rows, seed=7), (train, dev))
        self.assertEqual({r.label for r in train}, {r.label for r in dev})
        self.assertFalse({r.split_key.lineage_root for r in train} &
                         {r.split_key.lineage_root for r in dev})
        self.assertFalse({r.contrast_group for r in train if r.contrast_group} &
                         {r.contrast_group for r in dev if r.contrast_group})

    def test_derived_examples_follow_root(self):
        rows = load_examples()
        clone = replace(rows[0], example_id="derived_000", text="아주 수상하오")
        train, dev = split_examples(rows + [clone], seed=7)
        self.assertEqual(rows[0] in train, clone in train)

    def test_unseen_text_and_probability(self):
        model = IntentBaseline().fit(load_examples())
        label, score = model.predict("<P1>이 수상하다")
        self.assertEqual(label, "suspect")
        self.assertTrue(0 <= score <= 1)
        self.assertEqual(model.predict("zzzzzzzz")[0], "unsupported")
        self.assertEqual(model.predict("")[0], "unsupported")

    def test_human_test_never_fitted(self):
        rows = load_examples()
        with self.assertRaises(ValueError):
            IntentBaseline().fit(rows + [replace(rows[0], split=Split.TEST_HUMAN)])

    def test_holdout_not_in_vocabulary_or_version(self):
        train, dev = split_examples(load_examples(), seed=7)
        model = IntentBaseline().fit(train)
        self.assertNotIn("zzzz", model.vectorizer.vocabulary_)
        self.assertEqual(model.model_version, IntentBaseline().fit(train).model_version)
        self.assertNotEqual(model.model_version, IntentBaseline().fit(train[:-1]).model_version)
        with self.assertRaises(ValueError):
            IntentBaseline().fit([])
