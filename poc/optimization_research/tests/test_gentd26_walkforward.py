"""Chronology and feedback-availability checks for rolling-origin audit."""

import importlib.util
import pathlib
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "evaluate_gentd26_walkforward",
    ROOT / "scripts/evaluate_gentd26_walkforward.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WalkForwardLeakageTest(unittest.TestCase):
    def test_fit_filters_labels_unavailable_at_fit_cutoff(self):
        # Fit cutoff is Jan 5; an extremely long Jan 4 request cannot train
        # the model even though the request itself predates the fit cutoff.
        dates = pd.date_range("2024-01-01", "2024-01-12", freq="10min", inclusive="left")
        data = pd.DataFrame({"t": dates, "exec_time_seconds": 10.0})
        late = data.index[data.t.eq(pd.Timestamp("2024-01-04 23:00"))][0]
        data.loc[late, "exec_time_seconds"] = 7200.0
        frames, boundaries = MODULE.split_origin(data, pd.Timestamp("2024-01-09"))
        self.assertEqual(boundaries["fit_end_exclusive"], "2024-01-05 00:00:00")
        self.assertNotIn(late, frames["fit"].index)
        self.assertLessEqual(
            MODULE.assumed_availability(frames["fit"], 3600).max(),
            pd.Timestamp("2024-01-05"),
        )
        self.assertLess(frames["calibration"].t.max(), frames["selection"].t.min())
        self.assertLess(frames["selection"].t.max(), frames["test"].t.min())

    def test_static_offset_uses_only_assumed_available_feedback(self):
        history = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:00"]),
            "exec_time_seconds": [10.0, 100.0],
            "base_p90": [5.0, 0.0],
        })
        at_two = MODULE.static_offset(history, pd.Timestamp("2024-01-01 02:00"), 3600)
        self.assertEqual(at_two, 5.0)
        at_three = MODULE.static_offset(history, pd.Timestamp("2024-01-01 03:00"), 3600)
        self.assertGreater(at_three, at_two)

    def test_policy_choice_excludes_selection_labels_arriving_after_test_start(self):
        selection = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:59"]),
            "exec_time_seconds": [10.0, 5000.0],
            "base_p90": [5.0, 5.0],
        })
        scored = MODULE.selectable_feedback(
            selection, pd.Timestamp("2024-01-01 02:00"), 3600,
        )
        self.assertEqual(len(scored), 1)
        self.assertEqual(scored.exec_time_seconds.iloc[0], 10.0)

    def test_policy_selection_never_uses_later_test_labels(self):
        history = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 00:01"]),
            "exec_time_seconds": [5.0, 10.0], "base_p90": [3.0, 3.0],
        })
        selection = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 02:00", "2024-01-01 02:01"]),
            "exec_time_seconds": [8.0, 9.0], "base_p90": [3.0, 3.0],
        })
        future = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01 04:00"]),
            "exec_time_seconds": [100000.0], "base_p90": [3.0],
        })
        selected, candidates = MODULE.select_rolling_window(history, selection, 3600)
        contaminated_history = pd.concat([history, future], ignore_index=True)
        selected_again, candidates_again = MODULE.select_rolling_window(
            contaminated_history, selection, 3600
        )
        self.assertEqual(selected, selected_again)
        np.testing.assert_allclose(
            [x["pinball_seconds"] for x in candidates],
            [x["pinball_seconds"] for x in candidates_again],
        )

    def test_overlapping_test_windows_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            MODULE.evaluate(origins=(pd.Timestamp("2024-11-29"),
                                     pd.Timestamp("2024-11-30")))

    def test_day_bootstrap_keeps_pairing(self):
        frame = pd.DataFrame({
            "t": pd.to_datetime(["2024-01-01", "2024-01-01",
                                 "2024-01-02", "2024-01-02"]),
            "exec_time_seconds": [10.0] * 4,
            "global_rolling_p90": [5.0] * 4,
            "selected_group_p90": [10.0] * 4,
        })
        result = MODULE.paired_day_bootstrap(frame, repeats=100)
        self.assertEqual(result["days"], 2)
        self.assertLess(result["group_minus_global_pinball_seconds"], 0)
        self.assertLess(result["pinball_95pct_interval_seconds"][1], 0)


if __name__ == "__main__":
    unittest.main()
