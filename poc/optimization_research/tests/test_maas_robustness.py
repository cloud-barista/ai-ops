"""Critical time and comparison checks for the MaaS one-hour stress test."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts/evaluate_maas_robustness.py"
SPEC = importlib.util.spec_from_file_location("evaluate_maas_robustness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MaasRobustnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qps, _, _ = MODULE.load_qps(MODULE.SOURCE)
        cls.frame = MODULE.build_examples(qps, 1)

    def test_rolling_origins_use_completed_validation_labels_and_disjoint_tests(self):
        all_test_keys = []
        for origin in MODULE.ORIGINS:
            train, validation, test = MODULE.split_origin(self.frame, origin)
            self.assertLess(int(train.target_idx.max()), origin - 24)
            self.assertLess(int(validation.target_idx.max()), origin)
            self.assertGreaterEqual(int(test.decision_idx.min()), origin)
            self.assertEqual(len(validation), 23 * 3)
            for row in test.itertuples():
                all_test_keys.append((row.app_id, row.target_idx))
        self.assertEqual(len(all_test_keys), len(set(all_test_keys)))

    def test_validation_choice_does_not_inspect_test_targets(self):
        _, validation, test = MODULE.split_origin(self.frame, 120)
        selected, choice = MODULE.with_selected_static(validation, test)
        pair_selected, pair_choice = MODULE.with_selected_pair(validation, test)
        changed_test = test.copy()
        changed_test["observed_qps_norm"] = 1 - changed_test.observed_qps_norm
        selected_changed, choice_changed = MODULE.with_selected_static(validation, changed_test)
        pair_changed, pair_choice_changed = MODULE.with_selected_pair(validation, changed_test)
        self.assertEqual(choice, choice_changed)
        self.assertEqual(pair_choice, pair_choice_changed)
        np.testing.assert_array_equal(
            selected.validation_selected_static_by_app.to_numpy(),
            selected_changed.validation_selected_static_by_app.to_numpy(),
        )
        np.testing.assert_array_equal(
            pair_selected.validation_selected_persistence_or_corrected.to_numpy(),
            pair_changed.validation_selected_persistence_or_corrected.to_numpy(),
        )

    def test_original_last_two_day_forecast_rows_are_reproduced(self):
        original = pd.read_parquet(ROOT / "artifacts/maas_trace_v1/test_predictions.parquet")
        original = original.loc[original.horizon_hours.eq(1)]
        robust = pd.read_parquet(ROOT / "artifacts/maas_robustness_v1/test_predictions.parquet")
        robust = robust.loc[robust.test_day.ge(5)]
        keys = ["app_id", "target_idx"]
        joined = original.merge(robust, on=keys, suffixes=("_original", "_robust"), validate="one_to_one")
        self.assertEqual(len(joined), len(original))
        for field in ("observed_qps_norm", "last_value", "level_corrected_seasonal"):
            np.testing.assert_allclose(joined[f"{field}_original"], joined[f"{field}_robust"], rtol=0, atol=0)
        error = np.abs(original.observed_qps_norm - original.level_corrected_seasonal).mean()
        self.assertAlmostEqual(float(error), 0.03350638586531623)

    def test_paired_day_resampling_preserves_day_blocks(self):
        toy = pd.DataFrame({
            "test_day": [1, 1, 2, 2],
            "observed_qps_norm": [0.0] * 4,
            "first": [1.0, 1.0, 0.0, 0.0],
            "second": [0.0, 0.0, 1.0, 1.0],
        })
        result = MODULE.paired_day_interval(toy, "first", "second")
        self.assertEqual(result["exact_resamples"], 4)
        self.assertEqual(result["first_minus_second_mae"], 0.0)
        self.assertAlmostEqual(result["interval_95_within_week"][0], -0.925)
        self.assertAlmostEqual(result["interval_95_within_week"][1], 0.925)

    def test_saved_result_is_tied_to_current_code_and_source(self):
        artifact = json.loads((ROOT / "artifacts/maas_robustness_v1/metrics.json").read_text())
        self.assertEqual(artifact["evaluation_script_sha256"], MODULE.sha256(SCRIPT))
        self.assertEqual(artifact["upstream_script_sha256"],
                         MODULE.sha256(ROOT / "scripts/evaluate_maas_trace.py"))
        self.assertEqual(artifact["source_sha256"], MODULE.verify_sources(MODULE.SOURCE))


if __name__ == "__main__":
    unittest.main()
