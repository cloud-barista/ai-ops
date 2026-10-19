"""Information-boundary tests for logged ERROR risk features."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_swissai_error_status as subject


def rows(created_hours, finished_hours, labels, naive=None, models=None):
    base = subject.utc_us(subject.HISTORY_START)
    n = len(created_hours)
    if naive is None:
        naive = [False]*n
    if models is None:
        models = [0]*n
    return subject.Rows(
        created=base + np.asarray(created_hours, dtype=np.int64)*subject.HOUR_US,
        finished=base + np.asarray(finished_hours, dtype=np.int64)*subject.HOUR_US,
        finished_valid=np.ones(n, dtype=bool),
        finished_naive=np.asarray(naive, dtype=bool),
        model=np.asarray([f"test_{j}" for j in models], dtype=object),
        model_code=np.asarray(models, dtype=np.int32),
        status=np.asarray(labels, dtype=bool),
        source_row=np.arange(n, dtype=np.int32),
        n_models=max(models)+1,
    )


class SwissAIErrorStatusTests(unittest.TestCase):
    def test_feedback_enters_only_after_earlier_completed_hour(self):
        data = rows([0, 2, 3], [1, 10, 11], [True, False, False])
        hist = subject.history(data)
        self.assertEqual(subject.all_past(hist.global_errors, np.asarray([2]))[0], 0)
        self.assertEqual(subject.all_past(hist.global_errors, np.asarray([3]))[0], 1)
        self.assertEqual(subject.all_past(hist.global_completed, np.asarray([3]))[0], 1)

    def test_future_outcome_change_cannot_alter_earlier_feature(self):
        data = rows([0, 3, 6], [5, 7, 8], [False, False, False])
        before = subject.feature_matrix(data, subject.history(data), np.asarray([1]))
        data.status[0] = True
        after = subject.feature_matrix(data, subject.history(data), np.asarray([1]))
        np.testing.assert_array_equal(before, after)

    def test_naive_finish_sensitivity_removes_only_that_feedback(self):
        data = rows([0, 0, 3], [1, 1, 4], [True, False, False], naive=[True, False, False])
        original = subject.history(data)
        strict = subject.history(data, exclude_naive=True)
        h = np.asarray([3])
        self.assertEqual(int(subject.all_past(original.global_completed, h)[0]), 2)
        self.assertEqual(int(subject.all_past(strict.global_completed, h)[0]), 1)
        self.assertEqual(int(subject.all_past(strict.global_errors, h)[0]), 0)

    def test_static_prior_uses_only_fitted_train_outcomes_and_new_model_fallback(self):
        hour_train = (subject.utc_us(subject.TRAIN_START) - subject.utc_us(subject.HISTORY_START)) // subject.HOUR_US
        hour_val = (subject.utc_us(subject.VAL_START) - subject.utc_us(subject.HISTORY_START)) // subject.HOUR_US
        data = rows([hour_train, hour_train+1, hour_val],
                    [hour_train+1, hour_val, hour_val+1],
                    [True, False, False], models=[0, 0, 1])
        global_rate, model_rate, counts, errors = subject.train_static_rates(data)
        self.assertEqual(int(counts.sum()), 1)
        self.assertEqual(int(errors.sum()), 1)
        self.assertAlmostEqual(float(model_rate[1]), global_rate)

    def test_scoring_keeps_error_prevalence_and_brier_separate(self):
        scored = subject.score(np.asarray([False, True]), np.asarray([.2, .6]))
        self.assertEqual(scored["errors"], 1)
        self.assertAlmostEqual(scored["brier"], (.2**2 + .4**2)/2)

    def test_support_gate_rejects_stale_or_sparse_model_history(self):
        # 120 completed events in hour 1 become available in hour 2.
        data = rows([0]*120 + [3, 12], [1]*120 + [15, 16],
                    [False]*122)
        hist = subject.history(data)
        support = np.asarray([120])
        accepted = subject.support_acceptance(data, hist, np.asarray([120, 121]), support)
        np.testing.assert_array_equal(accepted, [True, False])

    def test_support_gate_needs_more_online_evidence_for_new_model(self):
        data = rows([0]*120 + [3], [1]*120 + [4], [False]*121)
        hist = subject.history(data)
        self.assertFalse(subject.support_acceptance(
            data, hist, np.asarray([120]), np.asarray([0]))[0])


if __name__ == "__main__":
    unittest.main()
