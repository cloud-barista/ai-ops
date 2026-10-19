import unittest

import numpy as np
import pandas as pd

from scripts.evaluate_chutes_demand_p90_v1 import (
    CountBins, STEP, decision_row, design, metrics, scales, score_gate, support_gate,
)


T = pd.Timestamp("1970-12-28 12:00")


def frame(ids, starts, models=None):
    if models is None:
        models = ["m"] * len(ids)
    return pd.DataFrame({
        "invocation_id": ids,
        "function_name": ["chat_stream"] * len(ids),
        "chute_id": models,
        "started_at": starts,
    })


class ChutesDemandCausalTests(unittest.TestCase):
    def test_target_starts_are_not_in_current_decision_inputs(self):
        bins = CountBins()
        starts = [T - STEP + pd.Timedelta(seconds=i) for i in range(30)]
        starts += [T + pd.Timedelta(seconds=i) for i in range(100)]
        bins.add_frame(frame(list(range(130)), starts))
        bins.finish()
        current = decision_row(bins, "m", T)
        self.assertTrue(current["eligible"])
        self.assertEqual(current["m5"], 30)
        self.assertEqual(current["m30"], 30)
        self.assertEqual(current["g5"], 30)
        self.assertEqual(current["y"], 100)
        self.assertNotIn("y", decision_row(bins, "m", T, include_target=False))
        self.assertEqual(decision_row(bins, "m", T + STEP)["m5"], 100)

    def test_global_input_includes_other_models_and_prior30_is_six_bin_max(self):
        bins = CountBins()
        for k, n in enumerate((4, 5, 6, 7, 8, 9), start=1):
            bins.model[("m", T - k * STEP)] = n
            bins.global_stream[T - k * STEP] = n + 10
        row = decision_row(bins, "m", T)
        self.assertEqual(row["m30"], 39)
        self.assertEqual(row["g30"], 99)
        self.assertEqual(row["prior30_p90"], 9)
        self.assertEqual(row["last5"], 4)

    def test_duplicate_request_and_out_of_order_group_rejected(self):
        bins = CountBins()
        bins.add_frame(frame([1], [T]))
        bins.add_frame(frame([1], [T + STEP]))
        with self.assertRaisesRegex(ValueError, "duplicate invocation_id"):
            bins.finish()
        other = CountBins()
        other.add_frame(frame([1], [T]))
        with self.assertRaisesRegex(ValueError, "globally ordered"):
            other.add_frame(frame([2], [T - STEP]))

    def test_relative_scale_and_fixed_model_onehot(self):
        row = {
            "model": "m", "m5": 20, "m10": 40, "m30": 120, "m60": 240,
            "g5": 50, "g10": 100, "g30": 300, "g60": 600,
        }
        rows = pd.DataFrame([row, {k: (v * 2 if k != "model" else v) for k, v in row.items()}])
        sm, sg = scales(rows)
        np.testing.assert_array_equal(sm, [20, 40])
        np.testing.assert_array_equal(sg, [50, 100])
        rel = design(rows, ["m", "other"], True)
        np.testing.assert_array_equal(rel[0], rel[1])
        self.assertEqual(rel.shape, (2, 10))
        self.assertEqual(rel[0, -2:].tolist(), [1, 0])

    def test_support_selection_uses_only_prior_eligibility(self):
        train = {"eligible_rows": 4000, "eligible_per_model": {f"m{i}": 70 for i in range(35)},
                 "global_zero_fraction": 0.0}
        test = {"eligible_rows": 900, "eligible_per_model": {f"m{i}": (12 if i < 25 else 0)
                                                        for i in range(35)},
                "global_zero_fraction": 0.0}
        passed, selected, reasons = support_gate({"train": train, "test": test}, "test")
        self.assertTrue(passed, reasons)
        self.assertEqual(len(selected), 25)
        test["eligible_rows"] = 799
        self.assertFalse(support_gate({"train": train, "test": test}, "test")[0])

    def test_metrics_model_balancing_and_gate_prevent_unsafe_candidate(self):
        rows = pd.DataFrame({
            "model": ["a", "b", "b", "b"],
            "t": [T, T, T + STEP, T + 2 * STEP],
            "y": [100, 10, 10, 10],
            "last5": [100, 10, 10, 10],
            "prior30_p90": [100, 10, 10, 10],
            "absolute": [100, 10, 10, 10],
            "relative": [0, 10, 10, 10],
        })
        value = metrics(rows, ["a", "b"])
        self.assertEqual(value["equal_model_pinball"]["absolute"], 0)
        self.assertAlmostEqual(value["equal_model_pinball"]["relative"], 45)
        self.assertAlmostEqual(value["equal_model_coverage"]["relative"], 0.5)
        self.assertFalse(score_gate(value, "val")[0])
        self.assertTrue(any("coverage" in r for r in score_gate(value, "val")[1]))

    def test_test_gate_requires_supported_hourly_wins(self):
        value = {
            "equal_model_pinball": {
                "last5": 100, "prior30_p90": 110, "absolute": 90, "relative": 75,
            },
            "relative_model_win_fraction_over_absolute": 0.8,
            "equal_model_coverage": {"relative": 0.9},
            "equal_model_underprediction_mass": {"absolute": 20, "relative": 18},
            "hour_blocks": [
                {"absolute": 10, "relative": 9} for _ in range(3)
            ],
        }
        passed, reasons = score_gate(value, "test")
        self.assertFalse(passed)
        self.assertTrue(any("hourly" in reason for reason in reasons))
        value["hour_blocks"].append({"absolute": 10, "relative": 9})
        self.assertTrue(score_gate(value, "test")[0])


if __name__ == "__main__":
    unittest.main()
