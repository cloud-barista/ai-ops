"""Information-boundary tests for the GenTD26 observed GPU-memory peak study."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_gpu_memory_peak.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_gpu_memory_peak", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def development_id() -> str:
    return next(f"synthetic-{i}" for i in range(100) if not MODULE.held_out(f"synthetic-{i}"))


def write_synthetic_trace(root: Path, memory_values: np.ndarray, timestamps: np.ndarray) -> None:
    cid = development_id()
    pd.DataFrame({
        "timestamp_anon": timestamps,
        "value": memory_values * MODULE.GIB,
        "container_ip": cid,
    }).to_csv(root / "pod_gpu_memory_used_bytes_anon.csv", index=False)
    pd.DataFrame({
        "timestamp_anon": timestamps[:-1],
        "value": np.zeros(len(timestamps) - 1),
        "container_ip": cid,
    }).to_csv(root / "pod_gpu_duty_cycle_anon.csv", index=False)


class GenTD26MemoryPeakTests(unittest.TestCase):
    def test_future_and_hidden_bridge_do_not_change_features(self):
        times = MODULE.T0 + np.arange(102) * MODULE.PERIOD
        original = np.full(102, 10.0)
        changed = original.copy()
        changed[64:70] = 80.0  # Six readings hidden by the availability lag.
        changed[70:] = 40.0  # Future label changes, features must not.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_synthetic_trace(root, original, times)
            first = MODULE.build_episodes(root, hide_heldout_and_test_labels=False)
            write_synthetic_trace(root, changed, times)
            second = MODULE.build_episodes(root, hide_heldout_and_test_labels=False)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first.loc[0, "last_input_time"], MODULE.T0 + 63 * MODULE.PERIOD)
        for name in MODULE.MEMORY_FEATURES + MODULE.DUTY_FEATURES:
            a, b = first.loc[0, name], second.loc[0, name]
            self.assertTrue((np.isnan(a) and np.isnan(b)) or a == b, name)
        self.assertEqual(first.loc[0, "recent_peak"], second.loc[0, "recent_peak"])
        self.assertEqual(first.loc[0, "past4h_peak"], second.loc[0, "past4h_peak"])
        self.assertEqual(first.loc[0, "y"], 10.0)
        self.assertEqual(second.loc[0, "y"], 40.0)

    def test_missing_interval_rejects_target_window(self):
        times = MODULE.T0 + np.arange(102) * MODULE.PERIOD
        times[85:] += MODULE.PERIOD  # A missing 57-second observation in the future.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_synthetic_trace(root, np.full(102, 10.0), times)
            with self.assertRaisesRegex(ValueError, "No eligible episodes"):
                MODULE.build_episodes(root, hide_heldout_and_test_labels=False)

    def test_completed_residual_is_used_only_after_horizon_end(self):
        frame = pd.DataFrame({
            "cid": ["a", "a", "a", "b"],
            "decision_time": [0.0, 299.0, 300.0, 300.0],
            "future_end_time": [300.0, 599.0, 600.0, 600.0],
            "y": [100.0, 20.0, 20.0, 1000.0],
            "recent_peak": [10.0, 10.0, 10.0, 10.0],
        })
        values = MODULE.online_container_residuals(frame, global_residual_q90=3.0)
        self.assertEqual(values[0], 3.0)
        self.assertEqual(values[1], 3.0)
        self.assertEqual(values[2], 3.0)  # One completion is below n=5 fallback.
        self.assertEqual(values[3], 3.0)  # Other container labels do not transfer.
        repeated = pd.DataFrame({
            "cid": ["a"] * 7,
            "decision_time": [0, 100, 200, 300, 400, 500, 600],
            "future_end_time": [100, 200, 300, 400, 500, 600, 700],
            "y": [30] * 7,
            "recent_peak": [10] * 7,
        })
        local = MODULE.online_container_residuals(repeated, global_residual_q90=3.0)
        self.assertEqual(local[4], 3.0)
        self.assertGreater(local[5], 3.0)  # Five completed outcomes become available.

    def test_split_purges_future_windows_at_boundaries(self):
        self.assertEqual(MODULE.split_of(MODULE.FIT_END - 100, MODULE.FIT_END + 100), "boundary")
        self.assertEqual(MODULE.split_of(MODULE.FIT_END, MODULE.VAL_END), "validation")
        self.assertEqual(MODULE.split_of(MODULE.VAL_END - 1, MODULE.VAL_END + 1), "boundary")
        self.assertEqual(MODULE.split_of(MODULE.VAL_END, MODULE.VAL_END + 1), "test")

    def test_duplicate_container_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            times = np.array([MODULE.T0, MODULE.T0], dtype=float)
            write_synthetic_trace(root, np.array([10.0, 11.0]), times)
            with self.assertRaisesRegex(ValueError, "duplicate container/time"):
                MODULE.load_measurements(root)


if __name__ == "__main__":
    unittest.main()
