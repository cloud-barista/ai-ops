"""Check that day-ahead calibration cannot use unfinished or future labels."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_philly_day_ahead_calibration_exploratory as study


class DayAheadCalibrationTests(unittest.TestCase):
    def test_uses_completed_previous_day_outcomes_only(self):
        rows = [
            ("m", 0, 0, 0, 1, .2),
            ("m", 0, 23*3600+45*60, 0, 0, .2),
            ("m", 0, 86400, 1, 0, .2),
            ("m", 0, 86400+3600, 1, 1, .2),
        ]
        columns = ["machine_hash", "gpu_index", "decision_utc_s", "utc_day", "y", "p0"]
        original = pd.DataFrame(rows, columns=columns)
        base_machine, base_global, _ = study.predict_day_ahead(original)
        self.assertGreater(base_machine[2], .2)
        altered = original.copy()
        altered.loc[1, "y"] = 1  # 23:45 outcome finishes after day 1 starts
        late_machine, late_global, _ = study.predict_day_ahead(altered)
        self.assertEqual(base_machine[2], late_machine[2])
        self.assertEqual(base_global[2], late_global[2])
        altered = original.copy()
        altered.loc[2:, "y"] = 1  # current-day labels cannot affect current day
        future_machine, future_global, _ = study.predict_day_ahead(altered)
        self.assertEqual(base_machine[2], future_machine[2])
        self.assertEqual(base_global[2], future_global[2])
        altered = original.copy()
        altered.loc[0, "y"] = 0  # completed previous-day label can affect it
        prior_machine, prior_global, _ = study.predict_day_ahead(altered)
        self.assertNotEqual(base_machine[2], prior_machine[2])
        self.assertNotEqual(base_global[2], prior_global[2])


if __name__ == "__main__":
    unittest.main()
