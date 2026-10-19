"""Checks for delayed outcome availability and stable offset calibration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_queue_calibration.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_queue_calibration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class QueueCalibrationTest(unittest.TestCase):
    def test_offset_moves_overprediction_toward_observed_rate(self):
        probability = np.full(20, 0.5)
        outcome = np.array([1] * 5 + [0] * 15)
        offset = MODULE.fit_intercept(probability, outcome, ridge=1.0)
        changed = MODULE.shifted_probability(probability, offset)
        self.assertLess(offset, 0)
        self.assertLess(abs(changed.mean() - outcome.mean()), 0.25)
        self.assertTrue(np.isfinite(MODULE.fit_intercept(probability, np.zeros(20), ridge=1.0)))

    def test_rolling_waits_until_entire_five_minute_future_window_matures(self):
        index = pd.date_range("2022-09-11 10:00", periods=17, freq="min")
        probability = np.full(len(index), 0.5)
        empty = np.zeros(len(index), dtype=int)
        first_changed = empty.copy()
        first_changed[0] = 1
        later_changed = empty.copy()
        later_changed[1] = 1
        original, _, original_count = MODULE.causal_rolling_intercept(
            index, probability, empty, window_minutes=30, ridge=1.0
        )
        first, _, first_count = MODULE.causal_rolling_intercept(
            index, probability, first_changed, window_minutes=30, ridge=1.0
        )
        later, _, later_count = MODULE.causal_rolling_intercept(
            index, probability, later_changed, window_minutes=30, ridge=1.0
        )
        np.testing.assert_array_equal(original_count[:7], [0, 0, 0, 0, 0, 0, 1])
        np.testing.assert_array_equal(original_count, first_count)
        np.testing.assert_array_equal(original_count, later_count)
        np.testing.assert_allclose(first[:6], original[:6], rtol=0, atol=0)
        self.assertGreater(first[6], original[6])
        self.assertEqual(later[6], original[6])
        self.assertGreater(later[7], original[7])

    def test_future_test_outcome_cannot_change_earlier_predictions(self):
        index = pd.date_range("2022-09-11 10:00", periods=25, freq="min")
        probability = np.linspace(0.2, 0.7, len(index))
        outcome = np.zeros(len(index), dtype=int)
        changed = outcome.copy()
        changed[10] = 1
        first, _, _ = MODULE.causal_rolling_intercept(
            index, probability, outcome, window_minutes=30, ridge=4.0
        )
        second, _, _ = MODULE.causal_rolling_intercept(
            index, probability, changed, window_minutes=30, ridge=4.0
        )
        np.testing.assert_allclose(first[:16], second[:16], rtol=0, atol=0)
        self.assertGreater(second[16], first[16])

    def test_reliability_bins_report_actual_observed_rates(self):
        outcome = np.array([0, 0, 1, 1])
        probability = np.array([0.1, 0.2, 0.8, 0.9])
        result = MODULE.reliability_bins(outcome, probability, count=2)
        self.assertEqual([row["observed_rate"] for row in result["equal_count_bins"]],
                         [0.0, 1.0])
        self.assertAlmostEqual(result["expected_calibration_error"], 0.15)


if __name__ == "__main__":
    unittest.main()
