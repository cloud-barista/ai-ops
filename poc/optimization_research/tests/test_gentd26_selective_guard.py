"""Checks for causal selection partitioning and selective prediction gates."""

import importlib.util
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_gentd26_selective_guard",
    ROOT / "scripts/evaluate_gentd26_selective_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SelectiveGuardTest(unittest.TestCase):
    def test_policy_fit_and_audit_use_disjoint_available_labels(self):
        selection = pd.DataFrame({
            "t": pd.to_datetime([
                "2024-01-01 00:00", "2024-01-01 22:00",
                "2024-01-02 00:00", "2024-01-02 23:50",
            ]),
            "exec_time_seconds": [10., 7200., 10., 3600.],
        })
        early, fit, audit, available = MODULE.partition_selection(
            selection, pd.Timestamp("2024-01-03"), 3600,
        )
        self.assertEqual(early.index.tolist(), [0, 1])
        self.assertEqual(fit.index.tolist(), [0])
        self.assertEqual(audit.index.tolist(), [2])
        self.assertEqual(available.index.tolist(), [0, 1, 2])
        self.assertTrue(set(fit.index).isdisjoint(audit.index))

    def test_unseen_and_unsupported_groups_abstain_without_test_outcomes(self):
        group_record = {
            "n": 60, "hits": 57, "coverage": .95,
            "wilson_90pct_lower": MODULE.wilson_lower(57, 60),
            "policy_fit_support": 100,
            "selection_support_at_origin": 120,
        }
        evidence = {
            "overall_audit": {
                "n": 300, "hits": 285, "coverage": .95,
                "wilson_90pct_lower": MODULE.wilson_lower(285, 300),
            },
            "groups": {
                "predict_type": {"A": group_record},
                "checkpoint_model_version_id": {"M0": group_record},
            },
        }
        future = pd.DataFrame({
            "predict_type": ["A", "B", "A"],
            "checkpoint_model_version_id": ["M0", "M0", "M1"],
            "exec_time_seconds": [1., 2., 3.],
        })
        baseline = MODULE.decide(future, evidence, "audit_empirical")
        np.testing.assert_array_equal(baseline, [True, False, False])
        future["exec_time_seconds"] = 10_000.0
        np.testing.assert_array_equal(
            MODULE.decide(future, evidence, "audit_empirical"), baseline,
        )
        evidence["groups"]["predict_type"]["A"] = {
            **group_record, "policy_fit_support": 99,
        }
        self.assertFalse(MODULE.decide(future, evidence, "support")[0])

    def test_recent_coverage_failure_abstains_even_with_large_support(self):
        low = {
            "n": 100, "hits": 89, "coverage": .89,
            "wilson_90pct_lower": MODULE.wilson_lower(89, 100),
            "policy_fit_support": 500,
            "selection_support_at_origin": 500,
        }
        good = {
            "n": 100, "hits": 98, "coverage": .98,
            "wilson_90pct_lower": MODULE.wilson_lower(98, 100),
            "policy_fit_support": 500,
            "selection_support_at_origin": 500,
        }
        evidence = {
            "overall_audit": {
                "n": 250, "hits": 240, "coverage": .96,
                "wilson_90pct_lower": MODULE.wilson_lower(240, 250),
            },
            "groups": {
                "predict_type": {"A": good},
                "checkpoint_model_version_id": {"M0": low},
            },
        }
        future = pd.DataFrame({
            "predict_type": ["A"], "checkpoint_model_version_id": ["M0"],
        })
        self.assertTrue(MODULE.decide(future, evidence, "support")[0])
        self.assertFalse(MODULE.decide(future, evidence, "audit_empirical")[0])

    def test_wilson_bound_and_empty_accepted_metrics(self):
        self.assertEqual(MODULE.wilson_lower(0, 0), 0.0)
        self.assertLess(MODULE.wilson_lower(9, 10), .9)
        with self.assertRaises(ValueError):
            MODULE.wilson_lower(11, 10)
        frame = pd.DataFrame({
            "exec_time_seconds": [10., 20.],
            "nested_guard92_p90": [12., 12.],
        })
        result = MODULE.selective_metrics(frame, "nested_guard92_p90", np.zeros(2, bool))
        self.assertEqual(result["abstention_fraction"], 1.0)
        self.assertIsNone(result["accepted_metrics"])


if __name__ == "__main__":
    unittest.main()
