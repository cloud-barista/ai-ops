"""Leakage and sparse-group fallback checks for subgroup calibration."""

import importlib.util
import inspect
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts/evaluate_gentd26_subgroup.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_subgroup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SubgroupCalibrationTest(unittest.TestCase):
    def test_group_outcome_enters_only_after_assumed_availability(self):
        history = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:00:00"]),
            "exec_time_seconds": [1.0, 9.0], "base_p90": [0.0, 0.0],
            "predict_type": ["A", "B"],
        })
        evaluation = pd.DataFrame({
            "t": pd.to_datetime([
                "2024-01-01 00:00:02", "2024-01-01 00:00:03",
                "2024-01-01 00:00:04", "2024-01-01 00:01:43",
            ]),
            "exec_time_seconds": [100.0, 1.0, 1.0, 1.0],
            "base_p90": [0.0, 0.0, 0.0, 0.0],
            "predict_type": ["A", "A", "B", "A"],
        })
        offset, count = MODULE.causal_group_offsets(history, evaluation, "predict_type", 10)
        np.testing.assert_array_equal(count, [1, 1, 0, 3])
        np.testing.assert_allclose(offset[:3], [1.0, 1.0, 0.0])
        self.assertGreater(offset[3], 50)
        delayed, delayed_count = MODULE.causal_group_offsets(
            history, evaluation, "predict_type", 10, feedback_lag_seconds=3600
        )
        np.testing.assert_array_equal(delayed_count, [0, 0, 0, 0])
        np.testing.assert_allclose(delayed, 0)
        global_delayed = MODULE.causal_global_offsets(
            history, evaluation, 10, feedback_lag_seconds=3600
        )
        np.testing.assert_allclose(global_delayed, 0)

    def test_sparse_group_falls_back_to_global(self):
        global_offsets = np.array([3.0, 3.0, 3.0])
        local_offsets = np.array([100.0, 8.0, 8.0])
        counts = np.array([0, 20, 100])
        blended, support = MODULE.blend_offsets(
            global_offsets, local_offsets, counts, minimum=20, shrinkage=20
        )
        np.testing.assert_array_equal(support, [False, True, True])
        np.testing.assert_allclose(blended, [3.0, 5.5, 3 + 5 * 100 / 120])

    def test_bad_configuration_rejected(self):
        frame = pd.DataFrame({"t": pd.to_datetime(["2024-01-01"]),
                              "exec_time_seconds": [1.0], "base_p90": [1.0],
                              "predict_type": ["A"]})
        with self.assertRaises(ValueError):
            MODULE.causal_group_offsets(frame, frame, "predict_type", 0)
        with self.assertRaises(ValueError):
            MODULE.causal_group_offsets(frame, frame, "unknown", 10)

    def test_default_reproduction_uses_one_hour_lag(self):
        self.assertEqual(MODULE.DEFAULT_FEEDBACK_LAG_SECONDS, 3600)
        self.assertEqual(
            inspect.signature(MODULE.evaluate).parameters["feedback_lag_seconds"].default,
            MODULE.DEFAULT_FEEDBACK_LAG_SECONDS,
        )

    def test_window_selection_ignores_labels_unavailable_at_test_start(self):
        history = pd.DataFrame({
            "t": pd.to_datetime(["2024-12-01 00:00:00"]),
            "exec_time_seconds": [1.0], "base_p90": [1.0],
        })
        evaluation = pd.DataFrame({
            "t": pd.to_datetime(["2024-12-03 20:00:00", "2024-12-03 23:30:00"]),
            "exec_time_seconds": [1.0, 3600.0],
            "base_p90": [1.0, 1.0],
        })
        available = MODULE.assumed_feedback_time(evaluation, 3600).le(MODULE.VALID_END).to_numpy()
        np.testing.assert_array_equal(available, [True, False])
        _, candidates = MODULE.select_global_window(history, evaluation, available, 3600)
        self.assertTrue(all(row["n"] == 1 for row in candidates))
        self.assertTrue(all(row["pinball_seconds"] == 0 for row in candidates))


if __name__ == "__main__":
    unittest.main()
