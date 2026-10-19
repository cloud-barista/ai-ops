"""Causal feedback ordering and frozen guard policy checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_burstgpt_observed_duration", SCRIPTS / "evaluate_burstgpt_observed_duration.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BurstGptObservedDurationTest(unittest.TestCase):
    def test_late_training_result_is_excluded(self) -> None:
        frame = pd.DataFrame({"t": [5.0, 9.0, 9.5],
                              "available_at": [7.0, 9.5, 12.0]})
        self.assertEqual(MODULE.training_mask(frame, 10.0).tolist(), [True, True, False])

    def test_future_or_unavailable_label_cannot_change_earlier_prediction(self) -> None:
        times = np.array([0.0, 1.0, 2.0, 4.0, 7.0])
        base = np.full(len(times), 2.0)
        models = np.array(["a", "a", "a", "a", "a"])
        original = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        changed = original.copy()
        changed[2] = 100.0
        p1 = MODULE.causal_rolling_predictions(times, original, base, models, delay_seconds=5)
        p2 = MODULE.causal_rolling_predictions(times, changed, base, models, delay_seconds=5)
        # At t=7 neither version of row 2 can have delivered feedback yet.
        for a, b in zip(p1, p2):
            np.testing.assert_array_equal(a, b)

    def test_guard_uses_only_given_selection_and_preserves_group_targets(self) -> None:
        y = np.array([1, 3, 4, 8, 1, 3, 4, 8], float)
        pred = np.full(len(y), 2.0)
        kinds = np.array(["api"] * 4 + ["conversation"] * 4)
        models = np.array(["a", "b", "a", "b"] * 2)
        old_minimum = MODULE.GUARD_MINIMUM
        MODULE.GUARD_MINIMUM = 2
        try:
            policy = MODULE.fit_guard(y, pred, kinds, models)
            guarded = MODULE.apply_guard(pred, kinds, models, policy)
            self.assertGreaterEqual(np.mean(y <= guarded), MODULE.GUARD_TARGET)
            for key in np.unique(kinds):
                self.assertGreaterEqual(np.mean(y[kinds == key] <= guarded[kinds == key]),
                                        MODULE.GUARD_TARGET)
            for key in np.unique(models):
                self.assertGreaterEqual(np.mean(y[models == key] <= guarded[models == key]),
                                        MODULE.GUARD_TARGET)
        finally:
            MODULE.GUARD_MINIMUM = old_minimum


if __name__ == "__main__":
    unittest.main()
