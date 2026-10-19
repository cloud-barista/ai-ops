"""Nested selection must not see the outer model's high-load outcomes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_scherm_b200_load_curve as base
import evaluate_scherm_b200_load_curve_shrinkage as study


class ShrinkageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selected = base.load_selected()

    def test_outer_target_labels_do_not_change_its_weight_or_predictions(self) -> None:
        before, summary_before = study.run(self.selected)
        held = sorted(self.selected.model.unique())[0]
        changed = self.selected.copy()
        mask = changed.model.eq(held) & changed.concurrency.isin(base.TARGETS)
        for metric in base.METRICS:
            changed.loc[mask, metric] *= 3.0
        after, summary_after = study.run(changed)
        self.assertEqual(summary_before["weights_by_outer_model"][held],
                         summary_after["weights_by_outer_model"][held])
        before_held = before[before.held_out_model.eq(held)].reset_index(drop=True)
        after_held = after[after.held_out_model.eq(held)].reset_index(drop=True)
        for metric in base.METRICS:
            np.testing.assert_array_equal(before_held[f"shrinkage_{metric}"],
                                          after_held[f"shrinkage_{metric}"])

    def test_reported_outer_prediction_is_log_space_blend(self) -> None:
        frame, summary = study.run(self.selected)
        self.assertEqual(len(frame), 42)
        for model, group in frame.groupby("held_out_model"):
            weight = summary["weights_by_outer_model"][model]
            self.assertTrue(group.selected_weight.eq(weight).all())
            for metric in base.METRICS:
                expected = np.exp((1 - weight) * np.log(group[f"donor_median_{metric}"])
                                  + weight * np.log(group[f"ridge_{metric}"]))
                np.testing.assert_allclose(group[f"shrinkage_{metric}"], expected,
                                           rtol=1e-13, atol=0)


if __name__ == "__main__":
    unittest.main()
