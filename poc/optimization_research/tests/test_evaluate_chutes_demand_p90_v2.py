"""Synthetic causality, calibration, support and gate checks for Chutes demand v2."""

import math
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_chutes_demand_p90_v2 as v2


class ChutesDemandV2Tests(unittest.TestCase):
    def row(self, t, y, model="m1", absolute=10., relative=30., prior30_p90=20.):
        return {
            "model": model, "t": pd.Timestamp(t), "y": y, "m30": 60,
            "last5": 15., "absolute": absolute, "relative": relative,
            "prior30_p90": prior30_p90,
        }

    def prior(self):
        return {"m1": {e: .1 for e in v2.EXPERTS}}

    def test_future_and_current_labels_cannot_affect_current_prediction(self):
        start = pd.Timestamp("1970-01-01 00:00")
        rows = pd.DataFrame([self.row(start, 10), self.row(start + v2.v1.STEP, 10)])
        first = v2.online_mixture(rows, self.prior(), start)
        future_changed = rows.copy()
        future_changed.loc[1, "y"] = 1000
        changed = v2.online_mixture(future_changed, self.prior(), start)
        self.assertEqual(first.loc[0, "candidate"], changed.loc[0, "candidate"])
        self.assertEqual(first.loc[1, "candidate"], changed.loc[1, "candidate"])
        self.assertEqual(first.loc[1, "prior_observations"], 1)
        past_changed = rows.copy()
        past_changed.loc[0, "y"] = 40
        changed_past = v2.online_mixture(past_changed, self.prior(), start)
        self.assertEqual(first.loc[0, "candidate"], changed_past.loc[0, "candidate"])
        self.assertNotEqual(first.loc[1, "candidate"], changed_past.loc[1, "candidate"])
        self.assertEqual(first.loc[1, "static"], changed_past.loc[1, "static"])

    def test_observation_outside_one_hour_is_ignored(self):
        start = pd.Timestamp("1970-01-01 00:00")
        rows = pd.DataFrame([self.row(start, 10), self.row(start + pd.Timedelta(minutes=65), 40)])
        predicted = v2.online_mixture(rows, self.prior(), start)
        self.assertEqual(predicted.loc[1, "prior_observations"], 0)
        self.assertAlmostEqual(predicted.loc[1, "candidate"], predicted.loc[1, "static"])

    def test_duplicate_decision_time_rejects_same_window_label(self):
        start = pd.Timestamp("1970-01-01 00:00")
        rows = pd.DataFrame([self.row(start, 10), self.row(start, 40)])
        with self.assertRaisesRegex(ValueError, "future outcome"):
            v2.online_mixture(rows, self.prior(), start)

    def test_calibration_shrinks_small_model_to_global(self):
        start = pd.Timestamp("1970-01-01 00:00")
        rows = pd.DataFrame([
            self.row(start, 10, "m1"), self.row(start, 40, "m2"),
            self.row(start + v2.v1.STEP, 40, "m2"),
        ])
        calibrated = v2.calibration(rows, ["m1", "m2"])
        self.assertTrue(calibrated["m1"]["absolute"] > 0.)
        self.assertTrue(calibrated["m1"]["absolute"] < calibrated["m2"]["absolute"])

    def test_test_support_uses_only_prior_eligibility(self):
        values = {f"m{i:02d}": 12 for i in range(35)}
        old = v2.registry
        try:
            v2.registry = lambda: list(values)
            passed, models, reasons = v2.test_support_gate({
                "eligible_rows": 800, "eligible_per_model": values, "global_zero_fraction": 0.,
            })
            self.assertTrue(passed)
            self.assertEqual(len(models), 35)
            values["m00"] = 11
            values["m01"] = 11
            passed, models, reasons = v2.test_support_gate({
                "eligible_rows": 800, "eligible_per_model": values, "global_zero_fraction": 0.,
            })
            self.assertTrue(passed)
            self.assertEqual(len(models), 33)
            self.assertEqual(reasons, [])
        finally:
            v2.registry = old

    def test_leave_one_model_out_and_hourly_gates_are_enforced(self):
        metrics = {
            "models": 35,
            "equal_model_pinball": {m: 100. for m in v2.ORIGINAL} | {
                "static": 96., "candidate": 90.,
            },
            "candidate_model_wins_over_best_original": 30,
            "equal_model_coverage": {"candidate": .90},
            "equal_model_underprediction_mass": {"absolute": 10., "candidate": 9.},
            "leave_one_model_out_min_gain": .03,
            "hour_blocks": [
                {"candidate": 1., "static": 2.} for _ in range(4)
            ],
        }
        passed, reasons = v2.score_gate(metrics, "G")
        self.assertTrue(passed, reasons)
        metrics["leave_one_model_out_min_gain"] = .01
        passed, reasons = v2.score_gate(metrics, "G")
        self.assertFalse(passed)
        self.assertTrue(any("leave-one" in reason for reason in reasons))
        metrics["leave_one_model_out_min_gain"] = .03
        metrics["hour_blocks"] = [{"candidate": 3., "static": 2.} for _ in range(4)]
        passed, reasons = v2.score_gate(metrics, "G")
        self.assertFalse(passed)
        self.assertTrue(any("hourly" in reason for reason in reasons))

    def test_test_bounds_have_internal_warmup_and_complete_labels(self):
        self.assertGreaterEqual(v2.T[0] - pd.Timedelta(hours=1),
                                pd.Timestamp(v2.TEST_GROUPS[77][0]))
        self.assertLessEqual(v2.T[1], pd.Timestamp(v2.TEST_GROUPS[79][1]))
        self.assertEqual(int((v2.T[1] - v2.T[0]) / v2.v1.STEP), 68)
        self.assertEqual(set(v2.TEST_GROUPS), {77, 78, 79})
        self.assertTrue(set(v2.TEST_GROUPS).isdisjoint({74, 75, 76}))


if __name__ == "__main__":
    unittest.main()
