"""Time boundary and fixed-method checks for the new MaaS ID trial."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_maas_state_day_calibration_holdout as holdout
import explore_maas_state_day_calibration as development


class StateDayNewIdTests(unittest.TestCase):
    def test_matured_previous_day_excludes_late_label(self):
        day = 146
        midnight = day * 86400
        rows = pd.DataFrame({"day": [145, 145, 144],
                             "decision_s": [midnight - 2100, midnight - 1800,
                                            midnight - 86400 - 2100],
                             "y": [0, 1, 1]})
        eligible = development.matured_previous_day(rows, day)
        self.assertEqual(eligible.decision_s.tolist(), [midnight - 2100])

    def test_candidate_never_uses_today_label(self):
        day = 145
        prior = pd.DataFrame({
            "instance_hash": ["old0", "old1"],
            "decision_s": [day * 86400 - 3000] * 2,
            "day": [144, 144], "any_recent_high": [0, 1], "y": [0, 1],
            "pooled": [.1, .7],
        })
        today = pd.DataFrame({
            "instance_hash": ["new0", "new1"],
            "decision_s": [day * 86400] * 2,
            "day": [day, day], "any_recent_high": [0, 1], "y": [0, 1],
            "pooled": [.2, .8],
        })
        answer, _ = development.predict_day(today, prior, holdout.TRAIN_BETA)
        today["y"] = [1, 0]
        changed, _ = development.predict_day(today, prior, holdout.TRAIN_BETA)
        for method in development.METHODS:
            np.testing.assert_array_equal(answer[method], changed[method])
        self.assertTrue((answer.model_last > 0).all())

    def test_support_uses_test_only_and_checks_prior_maturity(self):
        midnight = 146 * 86400
        rows = pd.DataFrame({
            "cohort": ["complete"] * 6,
            "current_high": [0] * 6,
            "day": [144, 144, 145, 145, 145, 146],
            "decision_s": [145 * 86400 - 3000, 145 * 86400 - 3000,
                           midnight - 3000, midnight - 3000, midnight - 300,
                           midnight],
            "any_recent_high": [0, 1, 0, 1, 0, 0],
            "instance_hash": ["a", "b", "a", "b", "c", "d"],
            "y": [0, 1, 0, 1, 1, 0],
        })
        diagnostic = {"splits": {"test": {"cohorts": {"complete": {
            "by_current_high": [{"current_high": 0, "label_observation_rate": .8}],
        }}}}}
        with patch.object(holdout.state, "construct_cohorts", return_value=(rows, diagnostic)):
            report, _ = holdout.support_summary(pd.DataFrame({"raw": [1]}))
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["matured_previous_day"]["146"]["rows"], 2)
        self.assertEqual(report["status"], "SUPPORT_NO_GO_NO_SCORE")


if __name__ == "__main__":
    unittest.main()
