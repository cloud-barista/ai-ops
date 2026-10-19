"""Causal-history and source-key guards for the update-latency study."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_update_latency.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_update_latency", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class UpdateLatencyTest(unittest.TestCase):
    def test_history_excludes_current_and_last_five_minutes(self):
        frame = pd.DataFrame({
            "t": [0.0, 100.0, 300.0, 301.0, 600.0],
            "y_ms": [10.0, 1000.0, 20.0, 30.0, 40.0],
            "ip": ["a", "a", "a", "a", "b"],
        })
        features = MODULE.past_update_features(frame)
        self.assertEqual(features.loc[0, "global_n4"], 0)
        self.assertEqual(features.loc[2, "ip_last"], 10)
        self.assertEqual(features.loc[3, "ip_last"], 10)
        self.assertEqual(features.loc[4, "global_n4"], 3)
        self.assertEqual(features.loc[4, "ip_n4"], 0)
        self.assertTrue(np.isnan(features.loc[4, "ip_last"]))

    def test_state_join_uses_pre_event_reading_and_preserves_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in MODULE.STATE_FILES:
                pd.DataFrame({
                    "timestamp_anon": [0.0, 301.0],
                    "value": [10.0, 1000.0],
                    "container_ip": ["a", "a"],
                }).to_csv(root / filename, index=False)
            updates = pd.DataFrame({"t": [600.0, 901.0, 2000.0],
                                    "ip": ["a", "a", "b"]})
            result = MODULE.add_preobserved_state(
                pd.DataFrame(index=range(len(updates))), updates, root
            )
            self.assertEqual(result.loc[0, "gpu_duty"], 10)
            self.assertEqual(result.loc[1, "gpu_duty"], 1000)
            self.assertTrue(np.isnan(result.loc[2, "gpu_duty"]))
            self.assertEqual(result.loc[0, "gpu_duty_age_seconds"], 300)

    def test_split_purges_unavailable_boundary_labels(self):
        frame = pd.DataFrame({"t": [MODULE.T0, MODULE.TRAIN_END - 301,
                                    MODULE.TRAIN_END - 299, MODULE.TRAIN_END,
                                    MODULE.VALID_END - 299, MODULE.VALID_END]})
        mask = MODULE.split_masks(frame)
        self.assertEqual(mask["train_fit"].tolist(), [True, True, False, False, False, False])
        self.assertEqual(mask["validation_selection"].tolist(),
                         [False, False, False, True, False, False])
        self.assertEqual(mask["test"].tolist(), [False, False, False, False, False, True])

    def test_duplicate_pipeline_key_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "updates.csv"
            pd.DataFrame({
                "timestamp_anon": [1, 1], "value": [10, 20],
                "container_ip": ["a", "a"],
            }).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                MODULE.load_updates(path)


if __name__ == "__main__":
    unittest.main()
