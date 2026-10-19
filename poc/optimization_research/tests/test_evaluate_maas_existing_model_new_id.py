"""Causal split, tie budget, and ID-block uncertainty checks for new-ID holdout."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_maas_existing_model_new_id as holdout


class ExistingMaaSNewIdTests(unittest.TestCase):
    def test_train_only_normal_beta_ignores_validation_and_high_state(self):
        rows = pd.DataFrame({
            "split": ["train"] * 4 + ["validation", "test"],
            "cohort": ["complete"] * 6,
            "current_high": [0, 0, 0, 1, 0, 0],
            "any_recent_high": [0, 0, 1, 1, 0, 1],
            "y": [0, 1, 1, 1, 1, 0],
        })
        self.assertEqual(holdout.normal_beta_rates(rows), {"0": .5, "1": 2 / 3})

    def test_top_k_expectation_does_not_choose_labels_inside_ties(self):
        y = np.array([1, 0, 1, 0], dtype=int)
        p = np.array([.9, .9, .1, .1])
        self.assertEqual(holdout.tie_expected_hits(y, p, 1), .5)
        self.assertEqual(holdout.tie_expected_hits(y, p, 2), 1)
        self.assertEqual(holdout.tie_expected_hits(y, p, 3), 1.5)

    def test_support_gate_uses_new_id_test_only_and_observability(self):
        rows = pd.DataFrame({
            "split": ["test"] * 4 + ["train"],
            "cohort": ["complete"] * 5,
            "current_high": [0, 0, 0, 1, 0],
            "day": [145, 145, 146, 145, 140],
            "instance_hash": ["a", "b", "c", "d", "e"],
            "y": [1, 0, 1, 1, 1],
        })
        diagnostics = {"splits": {"test": {"cohorts": {"complete": {
            "by_current_high": [{"current_high": 0, "candidates": 4, "labeled": 3,
                                 "label_observation_rate": .75}],
        }}}}}
        with patch.object(holdout.state, "construct_cohorts", return_value=(rows, diagnostics)):
            report, selected = holdout.support_summary(pd.DataFrame({"unused": [1]}))
        self.assertEqual(len(selected), 3)
        self.assertEqual(report["events"], 2)
        self.assertEqual(report["positive_ids"], 2)
        self.assertEqual(report["days"]["145"]["rows"], 2)
        self.assertTrue(report["checks"]["label_observation_at_least_75pct"])
        self.assertEqual(report["status"], "SUPPORT_NO_GO_NO_SCORE")


if __name__ == "__main__":
    unittest.main()
