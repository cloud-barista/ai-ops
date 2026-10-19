"""Exact-event and freeze tests for the SwissAI idle-return signal."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import evaluate_swissai_idle_return as idle  # noqa: E402


def minute(value: int) -> int:
    return int(idle.SPLIT_STARTS[0] + value * idle.MIN_US)


class IdleReturnTests(unittest.TestCase):
    def test_duplicate_arrivals_are_one_gap_but_request_counts_keep_rows(self) -> None:
        # Two requests at s, one decision at s+5.  A different model keeps
        # the global observation window populated after the target closes.
        own = np.array([minute(0), minute(0), minute(14), minute(14)], dtype=np.int64)
        global_us = np.sort(np.r_[own, minute(17), minute(30)])
        rows = idle.build_episodes(idle.Trace(global_us, {"a": own}, {}), include_test_labels=True)
        first = rows[rows.t_us == minute(5)]
        self.assertEqual(len(first), 1)
        self.assertEqual(int(first.y.iloc[0]), 1)  # r=t+9m
        self.assertEqual(int(first.own10.iloc[0]), 2)  # original rows, not distinct times
        self.assertTrue(bool(first.future_global_observed.iloc[0]))

    def test_at_decision_arrival_excludes_and_horizon_end_is_negative(self) -> None:
        own = np.array([minute(0), minute(5), minute(20)], dtype=np.int64)
        global_us = np.sort(np.r_[own, minute(40)])
        rows = idle.build_episodes(idle.Trace(global_us, {"a": own}, {}), include_test_labels=True)
        self.assertNotIn(minute(5), rows.t_us.to_list())  # next arrival at t
        at10 = rows[rows.t_us == minute(10)]
        self.assertEqual(len(at10), 1)
        self.assertEqual(int(at10.y.iloc[0]), 0)  # r=t+10m

    def test_split_boundary_requires_complete_label_window(self) -> None:
        end = int(idle.SPLIT_ENDS[0])
        own = np.array([end - 16 * idle.MIN_US, end - 14 * idle.MIN_US,
                        end - 13 * idle.MIN_US, end - 12 * idle.MIN_US], dtype=np.int64)
        # Last two arrivals create candidate t=end-7m, which is censored.
        global_us = np.sort(np.r_[own, end + 20 * idle.MIN_US])
        rows = idle.build_episodes(idle.Trace(global_us, {"a": own}, {}), include_test_labels=True)
        self.assertFalse(((rows.t_us > end - 10 * idle.MIN_US) & (rows.t_us < end)).any())

    def test_known_six_hour_gap_only_cools_down_after_ending_arrival(self) -> None:
        early = minute(0)
        gap_end = early + 7 * idle.HOUR_US
        later = gap_end + 130 * idle.MIN_US
        own = np.array([early, gap_end, later], dtype=np.int64)
        global_us = np.sort(np.r_[own, later + 30 * idle.MIN_US])
        rows = idle.build_episodes(idle.Trace(global_us, {"a": own}, {}), include_test_labels=True)
        self.assertIn(early + idle.IDLE_US, rows.t_us.to_list())
        self.assertNotIn(gap_end + idle.IDLE_US, rows.t_us.to_list())
        self.assertIn(later + idle.IDLE_US, rows.t_us.to_list())

    def test_episode_history_waits_for_strictly_completed_window(self) -> None:
        rows = pd.DataFrame({"model_alias": ["a"] * 4,
                             "t_us": np.array([minute(0), minute(10), minute(11), minute(25)]),
                             "y": [1, 0, 1, 0]})
        count, successes = idle._history(rows)
        self.assertEqual(count.tolist(), [0, 0, 1, 3])
        self.assertEqual(successes.tolist(), [0, 0, 1, 2])

    def test_gap_mean_transformed_after_averaging_minutes(self) -> None:
        unique = np.array([minute(0), minute(1), minute(10)], dtype=np.int64)
        mean, p90, count = idle._gap_features(unique, np.array([2]))
        self.assertAlmostEqual(float(mean[0]), np.log1p(5.0))
        self.assertAlmostEqual(float(p90[0]), np.log1p(9.0))
        self.assertEqual(float(count[0]), 2.0)

    def test_fit_mode_does_not_materialize_test_labels(self) -> None:
        test_start = int(idle.SPLIT_STARTS[2])
        own = np.array([test_start + minute_offset * idle.MIN_US for minute_offset in (0, 14)], dtype=np.int64)
        global_us = np.sort(np.r_[own, test_start + 30 * idle.MIN_US])
        rows = idle.build_episodes(idle.Trace(global_us, {"a": own}, {}), include_test_labels=False)
        self.assertTrue((rows.y == -1).all())
        self.assertFalse(rows.future_global_observed.any())

    def test_test_summary_uses_raw_brier_and_pre_fit_arrival_for_new_model(self) -> None:
        test_start = int(idle.SPLIT_STARTS[2])
        dates = np.array([test_start + (k * 7 + 1) * idle.DAY_US for k in range(5)], dtype=np.int64)
        rows = pd.DataFrame({
            "model_alias": ["old", "old", "old", "old", "new", "new"],
            "t_us": np.r_[int(idle.SPLIT_STARTS[0] + idle.DAY_US), dates],
            "split": [0, 2, 2, 2, 2, 2],
            "y": [1, 1, 0, 1, 0, 1],
            "first_seen_us": [int(idle.SPLIT_STARTS[0])] * 4 + [test_start] * 2,
            "global10": [1, 2, 1, 2, 1, 2],
            "own10": [1] * 6,
            "future_global_observed": [True, True, False, True, False, True],
        })
        p = np.array([1., 0., 1., 0., 1.])
        result = idle._test_metrics(rows, p, np.full(5, .5), np.full(5, .4),
                                    np.full(5, .4), np.full(5, .3))
        self.assertEqual(result["overall"]["selected_model"]["brier"], 0.)
        self.assertGreater(result["overall"]["selected_model"]["log_loss"], 0.)
        self.assertEqual(result["by_cohort"]["first_seen_since_fit_end"]["rows"], 2)
        self.assertEqual(result["by_cohort"]["zero_fit_eligible_episodes"]["rows"], 2)
        self.assertEqual(result["calibration"]["selected_model"][4]["rows"], 3)

    def test_unfrozen_protocol_cannot_unlock_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            protocol = path / "protocol.md"
            spec = path / "model_spec.json"
            dry = path / "dry_run.json"
            protocol.write_text("**상태: DRAFT**\n")
            spec.write_text("{}")
            dry.write_text("{}")
            manifest = path / "selection_manifest.json"
            manifest.write_text("{}")
            original = idle.PROTOCOL_SHA256
            try:
                idle.PROTOCOL_SHA256 = idle.sha256(protocol)
                with self.assertRaisesRegex(ValueError, "not marked FROZEN"):
                    idle._require_frozen(protocol, idle.sha256(protocol), spec, idle.sha256(spec),
                                         dry, idle.sha256(dry), manifest, idle.sha256(manifest))
            finally:
                idle.PROTOCOL_SHA256 = original

    def test_selection_manifest_locks_protocol_data_model_and_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            protocol = path / "protocol.md"
            protocol.write_text("**상태: FROZEN — synthetic test**\n")
            dry_path = path / "dry_run.json"
            spec_path = path / "model_spec.json"
            model_path = path / "selected_model.joblib"
            manifest_path = path / "selection_manifest.json"
            model_path.write_bytes(b"synthetic fitted model")
            protocol_sha = idle.sha256(protocol)
            code_sha = idle.sha256(Path(idle.__file__))
            original = idle.PROTOCOL_SHA256
            try:
                idle.PROTOCOL_SHA256 = protocol_sha
                idle._json(dry_path, {"support_gate_pass": True,
                                      "protocol_sha256": protocol_sha,
                                      "code_sha256": code_sha,
                                      "fit_validation_labeled_sha256": "abc"})
                dry_sha = idle.sha256(dry_path)
                idle._json(spec_path, {"dry_run_sha256": dry_sha,
                                       "code_sha256": code_sha,
                                       "model_sha256": idle.sha256(model_path),
                                       "fit_validation_labeled_sha256": "abc",
                                       "source_hashes": {}, "selected_model": "logistic",
                                       "selected_shrinkage_k": 5,
                                       "selected_poisson_width_minutes": 120})
                spec_sha = idle.sha256(spec_path)
                idle.freeze_selection(protocol_path=protocol, dry_run_path=dry_path,
                                      model_spec_path=spec_path,
                                      expected_protocol_sha256=protocol_sha,
                                      expected_dry_run_sha256=dry_sha,
                                      expected_model_spec_sha256=spec_sha,
                                      manifest_path=manifest_path)
                manifest_sha = idle.sha256(manifest_path)
                locked = idle._require_frozen(protocol, protocol_sha, spec_path, spec_sha,
                                              dry_path, dry_sha, manifest_path, manifest_sha)
                self.assertEqual(locked["selected_model"], "logistic")
                with self.assertRaises(FileExistsError):
                    idle.freeze_selection(protocol_path=protocol, dry_run_path=dry_path,
                                          model_spec_path=spec_path,
                                          expected_protocol_sha256=protocol_sha,
                                          expected_dry_run_sha256=dry_sha,
                                          expected_model_spec_sha256=spec_sha,
                                          manifest_path=manifest_path)
                manifest_path.write_text(manifest_path.read_text() + " ")
                with self.assertRaisesRegex(ValueError, "manifest SHA256 differs"):
                    idle._require_frozen(protocol, protocol_sha, spec_path, spec_sha,
                                         dry_path, dry_sha, manifest_path, manifest_sha)
            finally:
                idle.PROTOCOL_SHA256 = original


if __name__ == "__main__":
    unittest.main()
