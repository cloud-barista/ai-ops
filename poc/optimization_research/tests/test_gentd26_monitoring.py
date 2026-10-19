"""Guard the GenTD26 monitoring experiment against future-feature leakage."""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/evaluate_gentd26_monitoring.py"
SPEC = importlib.util.spec_from_file_location("evaluate_gentd26_monitoring", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_trace(root: pathlib.Path, *, missing_queue_minute: int | None = None,
               future_qps: float = 0.09) -> None:
    minutes = pd.date_range("2022-09-11 01:12:00", periods=20, freq="min")
    stamps = minutes.astype("int64") / 1e9
    queue = pd.DataFrame({"timestamp_anon": stamps, "value": np.zeros(20),
                          "container_ip": [None] * 20})
    queue.loc[10, "value"] = 1.0
    if missing_queue_minute is not None:
        queue = queue.drop(index=missing_queue_minute)
    queue.to_csv(root / "queue_size_raw_anon.csv", index=False)
    qps = pd.DataFrame({"timestamp_anon": stamps, "value": np.full(20, 0.09),
                        "container_ip": ["pod"] * 20,
                        "request_type": ["Generative Requests"] * 20})
    qps.loc[10, "value"] = future_qps
    qps.to_csv(root / "qps.csv", index=False)
    for name, value in (
        ("queue_rt_raw_anon.csv", 200.0),
        ("pod_gpu_duty_cycle_anon.csv", 20.0),
        ("pod_gpu_memory_used_bytes_anon.csv", 1e9),
        ("pipeline_inference_data_anon.csv", 15000.0),
    ):
        pd.DataFrame({"timestamp_anon": stamps, "value": [value] * 20,
                      "container_ip": ["pod"] * 20}).to_csv(root / name, index=False)


class MonitoringTraceTest(unittest.TestCase):
    def test_future_queue_event_excludes_current_and_features_exclude_future(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            make_trace(pathlib.Path(first), future_qps=0.09)
            make_trace(pathlib.Path(second), future_qps=1000.0)
            features, labels = MODULE.aggregate_features(pathlib.Path(first))
            changed_features, _ = MODULE.aggregate_features(pathlib.Path(second))
            t4, t5, t10 = features.index[[4, 5, 10]]
            self.assertEqual(int(labels.loc[t4, "queue_future_positive"]), 0)
            self.assertEqual(int(labels.loc[t5, "queue_future_positive"]), 1)
            self.assertEqual(int(labels.loc[t10, "queue_future_positive"]), 0)
            pd.testing.assert_series_equal(features.loc[t5], changed_features.loc[t5])

    def test_missing_future_monitoring_is_not_negative_label(self):
        with tempfile.TemporaryDirectory() as directory:
            make_trace(pathlib.Path(directory), missing_queue_minute=10)
            _, labels = MODULE.aggregate_features(pathlib.Path(directory))
            t5 = pd.Timestamp("2022-09-11 01:17:00")
            self.assertFalse(bool(labels.loc[t5, "queue_future_observed"]))

    def test_split_purges_five_minute_future_label_boundary(self):
        index = pd.date_range("2022-09-11 09:54:00", "2022-09-12 00:00:00", freq="min")
        masks = MODULE.chronological_masks(index)
        def allowed(split: str, timestamp: str) -> bool:
            return bool(masks[split][index.get_loc(pd.Timestamp(timestamp))])
        self.assertTrue(allowed("train", "2022-09-11 09:54:00"))
        self.assertFalse(allowed("train", "2022-09-11 09:55:00"))
        self.assertTrue(allowed("validation", "2022-09-11 10:00:00"))
        self.assertTrue(allowed("validation", "2022-09-11 13:54:00"))
        self.assertFalse(allowed("validation", "2022-09-11 13:55:00"))
        self.assertTrue(allowed("test", "2022-09-11 14:00:00"))
        self.assertTrue(allowed("test", "2022-09-11 23:54:00"))
        self.assertFalse(allowed("test", "2022-09-11 23:55:00"))


if __name__ == "__main__":
    unittest.main()
