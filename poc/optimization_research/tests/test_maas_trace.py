"""Check the MaaS forecast's provenance, aggregation, and time availability."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_maas_trace.py"
SPEC = importlib.util.spec_from_file_location("evaluate_maas_trace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MaasTraceTest(unittest.TestCase):
    def test_pinned_sources(self):
        hashes = MODULE.verify_sources(MODULE.SOURCE)
        self.assertEqual(hashes, MODULE.SOURCE_SHA256)

    def test_saved_result_identifies_current_evaluation_code(self):
        artifact = SCRIPT.parents[1] / "artifacts/maas_trace_v1/metrics.json"
        metrics = json.loads(artifact.read_text())
        self.assertEqual(metrics["evaluation_script_sha256"], MODULE.sha256(SCRIPT))

    def test_qps_aggregates_series_without_mixing_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for i in range(168):
                for app, series, value in (("a", "a0", 0.2), ("a", "a1", 0.3), ("b", "b0", 0.4)):
                    rows.append({"time_s": i * 3600, "app_id": app, "series_id": series,
                                 "metric": "qps_norm", "value": value})
            pd.DataFrame(rows).to_csv(root / "app_metrics.csv", index=False)
            (root / "app_cases.json").write_text(json.dumps({
                "figures": {"8": {"apps": [{"app_id": "a", "label": "A"},
                                          {"app_id": "b", "label": "B"}]}}
            }))
            qps, labels, diagnostics = MODULE.load_qps(root)
            self.assertAlmostEqual(float(qps.loc[0, "a"]), 0.5)
            self.assertAlmostEqual(float(qps.loc[0, "b"]), 0.4)
            self.assertEqual(labels["a"], "A")
            self.assertEqual(diagnostics["qps_series_per_app"], {"a": 2, "b": 1})

    def test_future_qps_cannot_change_features_at_an_earlier_origin(self):
        values = np.clip(0.2 + 0.15 * np.sin(np.arange(168) * 2 * np.pi / 24), 0, 1)
        frame = pd.DataFrame({"a": values}, index=np.arange(168) * 3600)
        altered = frame.copy()
        altered.loc[100 * 3600, "a"] = 0.9
        for horizon in MODULE.HORIZONS:
            before = MODULE.build_examples(frame, horizon)
            after = MODULE.build_examples(altered, horizon)
            target = before.loc[before.target_idx.eq(100)].iloc[0]
            changed = after.loc[after.target_idx.eq(100)].iloc[0]
            self.assertLess(int(target.decision_idx), 100)
            self.assertNotEqual(target.observed_qps_norm, changed.observed_qps_norm)
            for column in MODULE.feature_columns(before) + list(MODULE.STATIC_BASELINES) + [
                "level_corrected_seasonal", "level_ratio"
            ]:
                self.assertAlmostEqual(float(target[column]), float(changed[column]), msg=column)

    def test_forecast_origins_follow_completed_prior_split(self):
        qps = pd.DataFrame({"a": np.full(168, 0.4), "b": np.full(168, 0.3),
                            "c": np.full(168, 0.2)}, index=np.arange(168) * 3600)
        for horizon in MODULE.HORIZONS:
            frame = MODULE.build_examples(qps, horizon)
            train, validation, test = MODULE.chronological_split(frame)
            self.assertEqual(int(train.target_idx.max()), MODULE.TRAIN_END - 1)
            self.assertEqual(int(validation.decision_idx.min()), MODULE.TRAIN_END)
            self.assertEqual(int(validation.target_idx.max()), MODULE.VALID_END - 1)
            self.assertEqual(int(test.decision_idx.min()), MODULE.VALID_END)
            self.assertEqual(int(test.target_idx.min()), MODULE.VALID_END + horizon)
            self.assertTrue((validation.decision_idx >= MODULE.TRAIN_END).all())
            self.assertTrue((test.decision_idx >= MODULE.VALID_END).all())


if __name__ == "__main__":
    unittest.main()
