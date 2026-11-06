"""Check benchmark percentile and epoch extrapolation, without downloading models."""
import unittest

from intent.bert_bench import estimate_epoch_ms, percentile


class BenchmarkMath(unittest.TestCase):
    """Preserve a partial last batch and count first-step overhead only once."""

    def test_nearest_rank_p95(self):
        self.assertEqual(percentile(list(range(1, 101)), 95), 95)
        self.assertEqual(percentile([3.0], 95), 3.0)
        with self.assertRaises(ValueError):
            percentile([], 95)

    def test_epoch_partial_batch_and_first_step(self):
        self.assertEqual(estimate_epoch_ms(41, 4, 20, 10, 1), 161)
        self.assertEqual(estimate_epoch_ms(40, 4, 20, 10, 1), 150)
        self.assertEqual(estimate_epoch_ms(1, 4, 20, 10, 1), 21)

    def test_invalid_extrapolation_inputs(self):
        for values in [(0, 4, 20, 10, 1), (40, 0, 20, 10, 1), (40, 4, -1, 10, 1)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                estimate_epoch_ms(*values)
