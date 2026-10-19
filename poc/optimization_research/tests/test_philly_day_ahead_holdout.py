"""Source isolation and equal-row scoring checks for the future Philly test."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_philly_day_ahead_holdout as collector
import evaluate_philly_day_ahead_holdout as study


class PhillyDayAheadHoldoutTests(unittest.TestCase):
    def test_bucket_is_disjoint_from_all_previously_collected_ids(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "data/external/msr_philly_2017/gpu_util_audit.json",
            "data/external/msr_philly_2017_holdout/gpu_util_holdout_audit.json",
            "data/external/msr_philly_2017_calibration_holdout/gpu_util_calibration_holdout_audit.json",
        ):
            previous = json.loads((root/relative).read_text(encoding="utf-8"))
            for machine in previous["gpu_csv"]["selected_machine_ids"]:
                self.assertFalse(collector.selected(machine))
        found = [f"candidate-{i}" for i in range(1000)
                 if collector.selected(f"candidate-{i}")]
        self.assertTrue(found)
        self.assertTrue(all(25 <= collector.prior_collector.machine_bucket(machine) < 35
                            for machine in found))

    def test_collector_does_not_contact_source_without_freeze(self):
        with patch.object(collector, "verified_freeze", side_effect=ValueError("not frozen")):
            with patch.object(collector.requests, "Session",
                              side_effect=AssertionError("HTTP called")):
                with self.assertRaisesRegex(ValueError, "not frozen"):
                    collector.collect()

    def test_equal_predictions_cannot_pass_comparison_gate(self):
        frame = pd.DataFrame({
            "machine_hash": ["a", "b"]*15,
            "utc_day": np.repeat(np.arange(study.START_DAY, study.END_DAY_EXCLUSIVE), 2),
            "y": [0, 1]*15,
        })
        probabilities = np.full(len(frame), .5)
        result = study.evaluate_scores(frame, probabilities, probabilities.copy(),
                                       probabilities.copy())
        self.assertFalse(result["gate"]["all_passed"])
        self.assertFalse(result["gate"]["checks"]["row_brier_3pct_below_frozen_pooled"])
        self.assertFalse(result["gate"]["checks"]["row_brier_1pct_below_global_day_ahead"])


if __name__ == "__main__":
    unittest.main()
