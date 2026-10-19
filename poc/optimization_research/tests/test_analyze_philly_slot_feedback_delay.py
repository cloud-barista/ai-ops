"""Feedback maturity must exclude a late previous-day label for both methods."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_philly_slot_feedback_delay import replay, thin_hourly  # noqa: E402


class FeedbackMaturityTest(unittest.TestCase):
    def test_23h_anchor_matures_with_5min_but_not_1h_extra_delay(self):
        previous_day = 17501
        rows = [
            {"machine_hash": "m", "gpu_index": 0,
             "decision_utc_s": previous_day * 86400 + 23 * 3600,
             "utc_day": previous_day, "y": 1, "p0": 0.2},
            {"machine_hash": "m", "gpu_index": 0,
             "decision_utc_s": (previous_day + 1) * 86400,
             "utc_day": previous_day + 1, "y": 0, "p0": 0.2},
        ]
        frame = pd.DataFrame(rows)
        hourly = thin_hourly(frame)
        zero_machine, zero_slot, zero_counts = replay(frame, hourly, 0)
        five_machine, five_slot, five_counts = replay(frame, hourly, 300)
        hour_machine, hour_slot, hour_counts = replay(frame, hourly, 3600)
        np.testing.assert_allclose(zero_machine, five_machine)
        np.testing.assert_allclose(zero_slot, five_slot)
        self.assertEqual(zero_counts[-1]["completed_previous_day_hourly_labels"], 1)
        self.assertEqual(five_counts[-1]["completed_previous_day_hourly_labels"], 1)
        self.assertEqual(hour_counts[-1]["completed_previous_day_hourly_labels"], 0)
        self.assertNotAlmostEqual(five_machine[-1], hour_machine[-1])
        self.assertAlmostEqual(hour_machine[-1], 0.2)
        self.assertAlmostEqual(hour_slot[-1], 0.2)


if __name__ == "__main__":
    unittest.main()
