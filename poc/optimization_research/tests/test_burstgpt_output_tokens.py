"""Causal cohort, feedback, scoring, and validation-selection checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_burstgpt_output_tokens", SCRIPTS / "evaluate_burstgpt_output_tokens.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BurstGptOutputTokensTest(unittest.TestCase):
    def test_eligibility_uses_request_tokens_not_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trace.csv"
            source.write_text(
                "Timestamp,Session ID,Elapsed time,Model,Request tokens,Response tokens,Total tokens,Log Type\n"
                "19440110,,0,ChatGPT,0,17,17,API log\n"
                "19440111,,0,ChatGPT,8,0,8,API log\n"
                "19440112,,0,ChatGPT,9,4,13,API log\n"
            )
            frame = MODULE.load_source(source, check_pin=False)
        self.assertEqual(frame.row_id.tolist(), [1, 2])
        self.assertEqual(frame["Response tokens"].tolist(), [0, 4])

    def test_batch_feedback_excludes_yesterday_and_today(self) -> None:
        self.assertEqual(MODULE.history_window(303), (295, 301))
        frame = pd.DataFrame({"day": [224, 225, 293, 294, 295]})
        self.assertEqual(MODULE.available_fit_mask(frame).tolist(), [False, True, True, False, False])

    def test_future_labels_cannot_change_day_prediction(self) -> None:
        rows = []
        for day in range(295, 304):
            for _ in range(2):
                rows.append({
                    "day": day, "cohort": "A | api", "input_bin": 1,
                    "Response tokens": day - 294, "base_pred": 2.0,
                })
        original = pd.DataFrame(rows)
        changed_future = original.copy()
        changed_future.loc[changed_future.day >= 302, "Response tokens"] = 1000
        bundle = {"static_global_p90": 5.0, "static_cohort_p90": {"A | api": 5.0}}
        old = MODULE.MIN_SUPPORT
        MODULE.MIN_SUPPORT = 2
        try:
            current = original.loc[original.day == 303]
            p1 = MODULE.rolling_predictions_for_day(current, original, original, 303, bundle)
            p2 = MODULE.rolling_predictions_for_day(
                changed_future.loc[changed_future.day == 303], changed_future, changed_future, 303, bundle
            )
            for key in MODULE.METHODS:
                np.testing.assert_array_equal(p1[key], p2[key])

            changed_history = original.copy()
            changed_history.loc[changed_history.day == 301, "Response tokens"] = 1000
            p3 = MODULE.rolling_predictions_for_day(current, changed_history, changed_history, 303, bundle)
            self.assertGreater(p3["rolling_cohort"][0], p1["rolling_cohort"][0])
            self.assertGreater(p3["hgb_adaptive"][0], p1["hgb_adaptive"][0])
        finally:
            MODULE.MIN_SUPPORT = old

    def test_pinball_and_selection_use_declared_day_metric(self) -> None:
        scored = MODULE.score_arrays(np.array([1.0, 4.0]), np.array([2.0, 2.0]))
        self.assertAlmostEqual(scored["pinball_tokens"], 0.95)
        self.assertEqual(scored["coverage"], 0.5)
        scores = {
            method: {"equal_day_pinball_tokens": 3.0, "pinball_tokens": 3.0}
            for method in MODULE.METHODS
        }
        scores["hgb_adaptive"] = {"equal_day_pinball_tokens": 2.0, "pinball_tokens": 2.5}
        scores["rolling_bin"] = {"equal_day_pinball_tokens": 2.1, "pinball_tokens": 1.0}
        self.assertEqual(MODULE.select_method(scores), "hgb_adaptive")


if __name__ == "__main__":
    unittest.main()
