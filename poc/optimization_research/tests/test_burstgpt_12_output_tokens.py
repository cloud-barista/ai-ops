"""Held-out boundary, request eligibility, and predeclared-gate checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_burstgpt_12_output_tokens.py"
SPEC = importlib.util.spec_from_file_location("evaluate_burstgpt_12_output_tokens", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DummyEstimator:
    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return np.full(len(matrix), 5.0)


class BurstGptEarlierOutputTokensTest(unittest.TestCase):
    def test_input_eligibility_keeps_zero_output(self) -> None:
        rows = pd.DataFrame({"Request tokens": [0, 8, 9], "Response tokens": [42, 0, 4]})
        selected = rows.loc[MODULE.eligible_request_mask(rows)]
        self.assertEqual(selected["Response tokens"].tolist(), [0, 4])

    def test_day_60_enters_on_day_62(self) -> None:
        self.assertEqual(MODULE.history_window(61), (53, 59))
        self.assertEqual(MODULE.history_window(62), (54, 60))
        self.assertEqual(MODULE.history_window(63), (55, 61))

    def test_fixed_bin_edges(self) -> None:
        observed = np.searchsorted(MODULE.BIN_EDGES, np.array([1, 64, 65, 256, 257, 1024, 1025, 4096, 4097]), side="left")
        np.testing.assert_array_equal(observed, [0, 0, 1, 1, 2, 2, 3, 3, 4])

    def test_recent_day_predictions_do_not_read_yesterday_or_today(self) -> None:
        rows = []
        for day in range(53, 121):
            rows.append({
                "source_file": "BurstGPT_1.csv" if day <= 60 else "BurstGPT_2.csv",
                "file_row": day, "Timestamp": day * 86400.0, "day": day,
                "Model": "ChatGPT", "Log Type": "API log", "cohort": "ChatGPT | API log",
                "Request tokens": 10, "input_bin": 0, "Response tokens": 10,
            })
        original = pd.DataFrame(rows)
        changed = original.copy()
        changed.loc[changed.day == 61, "Response tokens"] = 1000
        bundle = {
            "estimator": DummyEstimator(),
            "feature_state": {"Model": {"ChatGPT": 0}, "Log Type": {"API log": 0}},
            "static_global_p90": 10.0,
            "static_cohort_p90": {"ChatGPT | API log": 10.0},
        }
        first = MODULE.predict_all(original, bundle)
        second = MODULE.predict_all(changed, bundle)
        for day in (61, 62):
            row_a = first.loc[first.day == day].iloc[0]
            row_b = second.loc[second.day == day].iloc[0]
            for method in MODULE.METHODS:
                self.assertEqual(row_a[method], row_b[method])
        later_a = first.loc[first.day == 63].iloc[0]
        later_b = second.loc[second.day == 63].iloc[0]
        self.assertGreater(later_b["rolling_bin"], later_a["rolling_bin"])
        self.assertGreater(later_b["rolling_cohort"], later_a["rolling_cohort"])

    def test_pinball_and_predeclared_gates_are_separate(self) -> None:
        scored = MODULE.score_arrays(np.array([1.0, 4.0]), np.array([2.0, 2.0]))
        self.assertAlmostEqual(scored["pinball_tokens"], 0.95)
        self.assertEqual(scored["coverage"], 0.5)
        blocks = {f"{start}-{start+9}": {"equal_day_pinball_tokens": 1.0, "coverage": .91} for start in range(61, 112, 10)}
        cohorts = {str(i): {"equal_day_pinball_tokens": 1.0, "coverage": .91} for i in range(4)}
        main = {"equal_day_pinball_tokens": 1.0, "coverage": .91, "by_10_day_block": blocks, "by_cohort": cohorts}
        other = {
            "equal_day_pinball_tokens": 2.0,
            "by_10_day_block": {key: {"equal_day_pinball_tokens": 2.0} for key in blocks},
            "by_cohort": {key: {"equal_day_pinball_tokens": 2.0} for key in cohorts},
        }
        result = MODULE.gates({"rolling_bin": main, **{key: other for key in MODULE.METHODS if key != "rolling_bin"}})
        self.assertTrue(result["relative_loss_transfer"])
        self.assertTrue(result["safety_upper_bound"])
        main["by_10_day_block"]["61-70"]["coverage"] = .89
        result = MODULE.gates({"rolling_bin": main, **{key: other for key in MODULE.METHODS if key != "rolling_bin"}})
        self.assertTrue(result["relative_loss_transfer"])
        self.assertFalse(result["safety_upper_bound"])

    def test_summary_equal_day_and_six_blocks(self) -> None:
        rows = []
        for day in range(61, 121):
            for cohort in ("A", "B"):
                row = {"day": day, "cohort": cohort, "Response tokens": 10.0}
                row.update({method: 10.0 for method in MODULE.METHODS})
                rows.append(row)
        scored = MODULE.summarize(pd.DataFrame(rows))
        main = scored["rolling_bin"]
        self.assertEqual(main["rows"], 120)
        self.assertEqual(main["equal_day_pinball_tokens"], 0.0)
        self.assertEqual(main["coverage"], 1.0)
        self.assertEqual(len(main["by_10_day_block"]), 6)
        self.assertEqual(list(main["by_10_day_block"]), [
            "61-70", "71-80", "81-90", "91-100", "101-110", "111-120",
        ])
        self.assertEqual(main["by_cohort"]["A"]["days_present"], 60)
        self.assertEqual(main["by_10_day_block"]["61-70"]["equal_day_pinball_tokens"], 0.0)


if __name__ == "__main__":
    unittest.main()
