"""Causal boundaries for previous-day MaaS instance calibration."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_maas_day_ahead_instance_transfer import (  # noqa: E402
    hourly_anchors, predictions_for_day, preceding,
)


class MaaSDayAheadTransferTest(unittest.TestCase):
    def test_hourly_selection_uses_time_not_label(self):
        rows = pd.DataFrame({
            "instance_hash": ["a", "a", "a"],
            "decision_s": [144 * 86400 + 3600, 144 * 86400 + 3900,
                           145 * 86400],
            "day": [144, 144, 145],
            "current_threshold": [0, 0, 0],
            "y": [0, 1, 0],
        })
        selected = hourly_anchors(rows)
        self.assertEqual(selected.decision_s.tolist(),
                         [144 * 86400 + 3600, 145 * 86400])

    def test_maturity_and_same_day_target_label_independence(self):
        day = 145
        before = day * 86400 - 3600
        too_late = day * 86400 - 1800
        anchors = pd.DataFrame({
            "instance_hash": ["a", "a"], "day": [144, 144],
            "decision_s": [before, too_late], "any_recent_threshold": [0, 0],
            "y": [1, 0],
        })
        self.assertEqual(preceding(anchors, day).decision_s.tolist(), [before])
        target = pd.DataFrame({"instance_hash": ["a", "b"],
                               "any_recent_threshold": [0, 0], "y": [0, 1]})
        parameters = {"any_recent_threshold": {"0": 0.2, "1": 0.8}}
        first = predictions_for_day(target, anchors, parameters, day)
        changed = target.copy()
        changed["y"] = [1, 0]
        second = predictions_for_day(changed, anchors, parameters, day)
        np.testing.assert_array_equal(first[0], second[0])
        np.testing.assert_array_equal(first[1], second[1])
        self.assertEqual(first[2], 1)


if __name__ == "__main__":
    unittest.main()
