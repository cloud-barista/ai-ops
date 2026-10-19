"""Behavioral checks for the retrospective shape-confirmation replay."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_sequential_shape_confirmation import replay


class SequentialShapeConfirmationTests(unittest.TestCase):
    def test_nonmonotone_observation_does_not_trigger_binary_search(self):
        # Shape 1 passes, shape 2 fails, and a bigger shape 4 also passes.
        observed = np.array([0, 1, 0, 0, 1, 0, 0, 0], dtype=bool)
        predicted = np.array([0, 0, 0, 0, 1, 0, 0, 0], dtype=bool)
        costs = np.arange(1, 9, dtype=float)
        ascending = replay(predicted, observed, costs, "ascending")
        model_first = replay(predicted, observed, costs, "model_first_fallback")
        self.assertEqual(ascending["probed_indices"], [0, 1])
        self.assertEqual(ascending["selected_index"], 1)
        self.assertEqual(model_first["probed_indices"], [4])
        self.assertEqual(model_first["selected_index"], 4)
        self.assertEqual(model_first["gpu_seconds"], 5.0)

    def test_failed_first_probe_falls_back_without_using_unprobed_truth(self):
        predicted = np.array([0, 0, 0, 0, 1, 0, 0, 0], dtype=bool)
        observed = np.array([0, 1, 0, 0, 0, 0, 0, 1], dtype=bool)
        costs = np.arange(1, 9, dtype=float)
        fallback = replay(predicted, observed, costs, "model_first_fallback")
        verify = replay(predicted, observed, costs, "verify_first")
        self.assertEqual(fallback["probed_indices"], [4, 0, 1])
        self.assertEqual(fallback["selected_index"], 1)
        self.assertEqual(fallback["gpu_seconds"], 8.0)
        self.assertEqual(verify["probed_indices"], [4])
        self.assertIsNone(verify["selected_index"])
        # Different outcomes beyond the stopping point cannot change the action.
        changed_tail = observed.copy()
        changed_tail[2:] = ~changed_tail[2:]
        changed_tail[4] = False
        self.assertEqual(replay(predicted, changed_tail, costs, "model_first_fallback"),
                         fallback)

    def test_no_predicted_pass_and_invalid_measurement_cost(self):
        predicted = np.zeros(8, dtype=bool)
        observed = np.array([0, 0, 1, 0, 0, 0, 0, 0], dtype=bool)
        costs = np.ones(8, dtype=float)
        self.assertEqual(replay(predicted, observed, costs, "model_first_fallback")
                         ["probed_indices"], [0, 1, 2])
        self.assertEqual(replay(predicted, observed, costs, "verify_first")
                         ["probed_indices"], [])
        costs[0] = float("nan")
        with self.assertRaisesRegex(ValueError, "positive and finite"):
            replay(predicted, observed, costs, "ascending")


if __name__ == "__main__":
    unittest.main()
