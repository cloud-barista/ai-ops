"""Boundary and label-availability checks for a read-only support audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_gentd26_pipeline_inference_support.py"
SPEC = importlib.util.spec_from_file_location("audit_gentd26_pipeline_inference_support", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PipelineInferenceSupportTests(unittest.TestCase):
    def test_lag_and_neighboring_future_windows_exclude_decision_sample(self):
        times = np.array([0, 1000, 2999, 3000, 3300, 3301, 3600, 3601, 4200, 4201, 4800])
        self.assertEqual(MODULE.window_counts(times, 3600, 600), (5, 2))
        self.assertEqual(MODULE.window_counts(times, 4200, 600), (7, 2))
        # The exact 4200 sample is counted once: in the first outcome, never
        # in the next outcome, and cannot enter either decision's lagged input.
        self.assertEqual(
            MODULE.window_counts(times, 3600, 600)[1]
            + MODULE.window_counts(times, 4200, 600)[1],
            4,
        )

    def test_fixed_grid_is_nonoverlapping_and_respects_sample_gate(self):
        times = [0, 1000, 2999, 3000, 3300, 3301, 3600, 3601, 4200, 4201, 4800]
        frame = pd.DataFrame({
            "timestamp_anon": times,
            "value": [100] * len(times),
            "container_ip": ["a"] * len(times),
        })
        summary = MODULE.support_by_split(frame, horizon=600)
        train = summary["train"]
        self.assertEqual(train["history_supported_windows"], 2)
        self.assertEqual(train["future_n_ge_1"], {
            "windows": 2, "containers": 1, "nonoverlapping_time_blocks": 2,
        })
        self.assertEqual(train["future_n_ge_3"]["windows"], 0)
        self.assertEqual(summary["validation"]["history_supported_windows"], 0)

    def test_global_blocks_are_counted_once_per_fixed_interval(self):
        times = [0] + list(range(3601, 3611)) + [4200] + list(range(4201, 4211)) + [4800]
        frame = pd.DataFrame({"timestamp_anon": times})
        self.assertEqual(MODULE.global_future_blocks(frame, horizon=600), {
            "train": 2, "validation": 0, "test": 0,
        })

    def test_future_crossing_split_boundary_is_purged(self):
        first = 100.0
        train_end = first + MODULE.TRAIN_HOURS * 3600
        valid_end = train_end + MODULE.VALID_HOURS * 3600
        self.assertEqual(MODULE.split_of(train_end - 600, train_end, first), "train")
        self.assertIsNone(MODULE.split_of(train_end - 300, train_end + 300, first))
        self.assertEqual(MODULE.split_of(train_end, train_end + 600, first), "validation")
        self.assertIsNone(MODULE.split_of(valid_end - 300, valid_end + 300, first))
        self.assertEqual(MODULE.split_of(valid_end, valid_end + 600, first), "test")

    def test_exact_key_pair_does_not_create_loading_overhead_label(self):
        pipeline = pd.DataFrame({
            "timestamp_anon": [1, 2, 3], "container_ip": ["a", "a", "b"],
            "value": [10, 20, 30],
        })
        pure = pd.DataFrame({
            "timestamp_anon": [1, 2, 4], "container_ip": ["a", "a", "b"],
            "value": [40, 5, 100],
        })
        self.assertEqual(MODULE.join_diagnostic(pipeline, pure), {
            "matched_keys": 2,
            "pipeline_less_than_pure": 1,
            "pipeline_equal_pure": 0,
            "pipeline_greater_than_pure": 1,
        })

    def test_changed_source_fails_before_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in MODULE.EXPECTED_SHA256:
                (root / name).write_text("altered source", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                MODULE.audit(root)


if __name__ == "__main__":
    unittest.main()
