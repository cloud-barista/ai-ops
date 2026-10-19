"""Post-score size-law baseline: held-out isolation and artifact row matching."""

import unittest

import numpy as np

from scripts import evaluate_h100_readiness_eta as original
from scripts import evaluate_h100_readiness_eta_size_baseline_posthoc as followup


class SizeBaselinePosthocTest(unittest.TestCase):
    def test_heldout_actual_never_enters_size_line_or_margin(self):
        donor = [
            {
                "model": f"donor-{size}",
                "model_size_b": float(size),
                "actual_remaining_s": float(10 + 3 * size + repeat),
            }
            for size in (1, 2, 4)
            for repeat in range(10)
        ]
        target = [{"model": "heldout", "model_size_b": 8.0,
                   "actual_remaining_s": 1.0}]
        first = followup.fit_size_linear_residual_p90(donor, target)
        target[0]["actual_remaining_s"] = 10000.0
        second = followup.fit_size_linear_residual_p90(donor, target)
        np.testing.assert_array_equal(first, second)
        self.assertGreater(first[0], 30.0)

    def test_original_1200_rows_join_without_label_or_key_change(self):
        records = original.read_trials(original.DEFAULT_INPUT)
        rows = original.build_stage_rows(records)
        previous = followup.load_original_predictions(followup.ORIGINAL_PREDICTIONS)
        joined = followup.generate(rows, previous)
        self.assertEqual(len(joined), 1200)
        self.assertEqual({followup.key(row) for row in joined}, set(previous))
        for row in joined:
            self.assertTrue(np.isfinite(row[followup.NO_CACHE]))
            self.assertTrue(np.isfinite(row[followup.ORACLE_CACHE]))
            self.assertGreaterEqual(row[followup.NO_CACHE], 0)
            self.assertGreaterEqual(row[followup.ORACLE_CACHE], 0)


if __name__ == "__main__":
    unittest.main()
