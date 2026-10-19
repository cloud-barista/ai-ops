"""Information-boundary checks for the pinned load-curve evaluation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluate_scherm_b200_load_curve as study


class LoadCurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selected = study.load_selected()

    def test_pinned_support_and_grid(self) -> None:
        self.assertEqual(len(self.selected), 64)
        self.assertEqual(self.selected.model.nunique(), 7)
        self.assertEqual(len(self.selected[self.selected.concurrency.isin(study.TARGETS)]), 42)
        self.assertFalse(self.selected.duplicated(["model", "concurrency"]).any())

    def test_held_out_target_labels_cannot_change_its_predictions(self) -> None:
        first, _ = study.evaluate(self.selected)
        held = sorted(self.selected.model.unique())[0]
        changed = self.selected.copy()
        mask = changed.model.eq(held) & changed.concurrency.isin(study.TARGETS)
        for metric in study.METRICS:
            changed.loc[mask, metric] *= 2.7
        second, _ = study.evaluate(changed)
        left = first[first.held_out_model.eq(held)].reset_index(drop=True)
        right = second[second.held_out_model.eq(held)].reset_index(drop=True)
        for metric in study.METRICS:
            for method in study.METHODS:
                np.testing.assert_array_equal(left[f"{method}_{metric}"], right[f"{method}_{metric}"])
            np.testing.assert_array_equal(left[f"anchor8_{metric}"], right[f"anchor8_{metric}"])


if __name__ == "__main__":
    unittest.main()
