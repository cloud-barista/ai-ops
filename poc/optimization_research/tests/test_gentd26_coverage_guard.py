"""Small contract checks for validation-only coverage guards."""

import importlib.util
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_gentd26_coverage_guard",
    ROOT / "scripts/evaluate_gentd26_coverage_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CoverageGuardTest(unittest.TestCase):
    def test_margin_is_smallest_nonnegative_empirical_coverage_fix(self):
        y = np.arange(1, 11, dtype=float)
        pred = np.zeros(10)
        self.assertEqual(MODULE.minimum_margin(y, pred, .9), 9.0)
        self.assertEqual(MODULE.minimum_margin(y, pred, .92), 10.0)
        self.assertEqual(MODULE.minimum_margin(y, y + 1, .9), 0.0)

    def test_supported_subgroups_meet_selection_coverage_target(self):
        selection = pd.DataFrame({
            "exec_time_seconds": np.array([10.] * 10 + [20.] * 10),
            "predict_type": ["A"] * 10 + ["B"] * 10,
            "checkpoint_model_version_id": ["M0", "M1"] * 10,
        })
        global_pred = np.zeros(20)
        group_pred = np.ones(20)
        policy = MODULE.fit_guard(
            selection, global_pred, group_pred, .5, .9, min_group_support=5,
        )
        out = MODULE.predict_guard(selection, global_pred, group_pred, policy)
        self.assertGreaterEqual(np.mean(selection.exec_time_seconds <= out), .9)
        for column in MODULE.GROUPS:
            for key in policy["group_support"][column]:
                mask = selection[column] == key
                self.assertGreaterEqual(np.mean(selection.loc[mask, "exec_time_seconds"] <= out[mask]), .9)

    def test_prediction_does_not_use_evaluation_outcome(self):
        selection = pd.DataFrame({
            "exec_time_seconds": [10., 20., 30., 40.],
            "predict_type": ["A", "A", "B", "B"],
            "checkpoint_model_version_id": ["M0", "M1", "M0", "M1"],
        })
        policy = MODULE.fit_guard(
            selection, np.ones(4), np.ones(4), 0., .9, min_group_support=2,
        )
        future = selection.copy()
        first = MODULE.predict_guard(future, np.ones(4), np.ones(4), policy)
        future["exec_time_seconds"] = 100000.
        second = MODULE.predict_guard(future, np.ones(4), np.ones(4), policy)
        np.testing.assert_array_equal(first, second)


if __name__ == "__main__":
    unittest.main()
