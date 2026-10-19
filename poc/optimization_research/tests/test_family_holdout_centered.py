"""Verify that the centered ablation preserves the prior family stress setup."""
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class FamilyHoldoutCenteredTests(unittest.TestCase):
    def test_raw_and_kernel_predictions_reproduce_original_family_stress(self):
        for suffix, original in [
            ('bert4', 'family_holdout_transfer_v1'),
            ('bert5', 'family_holdout_plus_distilbert_v1'),
        ]:
            with self.subTest(split=suffix):
                fresh_path = ROOT / 'artifacts' / f'family_holdout_centered_{suffix}_v1'
                old_path = ROOT / 'artifacts' / original
                if not fresh_path.exists() or not old_path.exists():
                    self.skipTest('Saved family stress artifacts not present')
                fresh = pd.read_parquet(fresh_path / 'predictions.parquet')
                old = pd.read_parquet(old_path / 'predictions.parquet')
                self.assertEqual(fresh.execution_id.tolist(), old.execution_id.tolist())
                for new_method, old_method in [
                    ('raw_second_moment', 'space_cov_transfer'),
                    ('smooth_kernel', 'space_kernel_transfer'),
                ]:
                    for outcome in ['latency_log', 'rps_log']:
                        np.testing.assert_allclose(
                            fresh[f'{new_method}_{outcome}'],
                            old[f'{old_method}_{outcome}'], rtol=1e-12, atol=1e-12)

    def test_centered_and_raw_use_same_cost_and_exclude_whole_family(self):
        for suffix, original in [
            ('bert4', 'family_holdout_transfer_v1'),
            ('bert5', 'family_holdout_plus_distilbert_v1'),
        ]:
            with self.subTest(split=suffix):
                fresh_path = ROOT / 'artifacts' / f'family_holdout_centered_{suffix}_v1'
                old_path = ROOT / 'artifacts' / original
                if not fresh_path.exists() or not old_path.exists():
                    self.skipTest('Saved family stress artifacts not present')
                fresh = pd.read_csv(fresh_path / 'split_manifest.csv')
                old = pd.read_csv(old_path / 'split_manifest.csv')
                pd.testing.assert_series_equal(fresh.probe_groups_json,
                                               old.probe_groups_json)
                pd.testing.assert_series_equal(fresh.gpu_wall_probe_seconds,
                                               old.gpu_wall_probe_seconds)
                for _, row in fresh.iterrows():
                    self.assertTrue(set(json.loads(row.training_model_ids_json))
                                    .isdisjoint(json.loads(row.excluded_family_model_ids_json)))
                    self.assertEqual(len(json.loads(row.probe_groups_json)), 8)
                    self.assertGreaterEqual(len(json.loads(row.aligned_donor_models_json)), 3)


if __name__ == '__main__':
    unittest.main()
