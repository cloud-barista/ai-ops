"""Checks for paired Philly metrics and deterministic daily alert budgets."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.philly_risk_metrics import (
    binary_metrics, daily_top_five_percent, half_period_brier,
    paired_machine_bootstrap,
)


class PhillyRiskMetricsTest(unittest.TestCase):
    @staticmethod
    def frame() -> pd.DataFrame:
        rows = []
        for day in range(6):
            for index in range(20):
                positive = int(index == 0)
                rows.append({
                    "machine_hash": f"machine-{index % 5}",
                    "gpu_index": index % 2,
                    "decision_utc_s": (100 + day) * 86400 + index * 300,
                    "utc_day": 100 + day,
                    "y": positive,
                    "good": 0.9 if positive else 0.1,
                    "bad": 0.1 if positive else 0.9,
                })
        return pd.DataFrame(rows)

    def test_daily_budget_and_paired_quality(self):
        frame = self.frame()
        alerts = daily_top_five_percent(frame, "good")
        self.assertEqual(alerts["alerts"], 6)
        self.assertEqual(alerts["true_alerts"], 6)
        self.assertEqual(alerts["precision"], 1.0)
        self.assertEqual(alerts["recall"], 1.0)
        good = binary_metrics(frame, "good")
        bad = binary_metrics(frame, "bad")
        self.assertLess(good["brier"], bad["brier"])
        self.assertGreater(good["average_precision"], bad["average_precision"])
        halves = half_period_brier(frame, "good", "bad")
        self.assertEqual([len(part["utc_days"]) for part in halves], [3, 3])
        self.assertTrue(all(part["first_brier"] < part["second_brier"] for part in halves))

    def test_machine_bootstrap_uses_cluster_sums_and_preserves_sign(self):
        frame = self.frame()
        result = paired_machine_bootstrap(frame, "good", "bad", draws=200, seed=7)
        self.assertEqual(result["machines"], 5)
        self.assertEqual(result["draws"], 200)
        self.assertLess(result["point_delta"], 0)
        self.assertLess(result["percentile_95"][1], 0)

    def test_unobserved_label_is_never_scored(self):
        frame = self.frame()
        frame.loc[0, "y"] = pd.NA
        with self.assertRaisesRegex(ValueError, "observed binary"):
            binary_metrics(frame, "good")


if __name__ == "__main__":
    unittest.main()
