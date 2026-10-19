"""Temporal leakage check for hourly previous-day slot correction."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from explore_philly_slot_day_calibration import slot_day_predictions  # noqa: E402


class PhillySlotDayTest(unittest.TestCase):
    def test_slot_residual_and_no_today_label_leakage(self):
        rows = []
        for hour in range(4):
            for gpu in (0, 1):
                rows.append({"machine_hash": "m", "gpu_index": gpu,
                             "decision_utc_s": 86400 + hour * 3600,
                             "utc_day": 1, "y": int(gpu == 0), "p0": 0.2})
        for gpu in (0, 1):
            rows.append({"machine_hash": "m", "gpu_index": gpu,
                         "decision_utc_s": 172800, "utc_day": 2,
                         "y": 0, "p0": 0.2})
        frame = pd.DataFrame(rows).sort_values(
            ["decision_utc_s", "machine_hash", "gpu_index"]).reset_index(drop=True)
        machine, slot = slot_day_predictions(frame)
        self.assertAlmostEqual(machine[-2], machine[-1])
        self.assertGreater(slot[-2], slot[-1])
        changed = frame.copy()
        changed.loc[changed.utc_day.eq(2), "y"] = 1
        changed_machine, changed_slot = slot_day_predictions(changed)
        np.testing.assert_array_equal(machine, changed_machine)
        np.testing.assert_array_equal(slot, changed_slot)


if __name__ == "__main__":
    unittest.main()
