"""Behavioral and leakage checks for the observed-VRAM upper forecast."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import evaluate_vram_upper as study
from evaluate_vram_peak import CAMPAIGNS, selected_measurements


ROOT = Path(__file__).resolve().parents[1]


class VramUpperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaigns = selected_measurements(
            pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet'))
        cls.main = cls.campaigns[CAMPAIGNS[0]]
        cls.held = 'google/bert_uncased_L-2_H-128_A-2'
        cls.donor = cls.main.loc[cls.main.model_id != cls.held]
        cls.external = cls.campaigns[CAMPAIGNS[2]].loc[
            cls.campaigns[CAMPAIGNS[2]].model_id == cls.held]

    def test_weighted_quantile_equalizes_models(self):
        values = np.array([0.0] * 100 + [2.0])
        ids = np.array(['large'] * 100 + ['small'])
        self.assertEqual(study.weighted_model_quantile(values, ids, .75), 2.0)
        with self.assertRaises(ValueError):
            study.weighted_model_quantile(np.array([np.nan]), np.array(['x']))

    def test_crossfit_excludes_the_calibration_model(self):
        original = study.point_predict
        seen = []

        def checking_predict(train, target, method):
            self.assertFalse(set(train.model_id) & set(target.model_id))
            self.assertNotIn(self.held, set(train.model_id))
            self.assertNotIn(self.held, set(target.model_id))
            seen.append((method, target.model_id.iloc[0]))
            return original(train, target, method)

        with patch.object(study, 'point_predict', side_effect=checking_predict):
            result = study.donor_calibration(self.donor)
        self.assertEqual(result['donor_models'], 8)
        self.assertEqual(len(seen), 16)
        self.assertGreaterEqual(result['direct_ratio'], 1)

    def test_target_outcomes_cannot_change_forecasts(self):
        original = study.forecast_fold(self.donor, self.external, {self.held})
        changed = self.external.copy()
        changed['reserved_peak_gib'] *= 10000
        changed['gpu_memory_reserved_bytes'] *= 10000
        changed['result_json'] = '{}'
        new = study.forecast_fold(self.donor, changed, {self.held})
        for method in study.METHODS:
            np.testing.assert_allclose(original[method], new[method], atol=1e-12)
        lower = self.external.parameter_gib.to_numpy()
        for result in original.values():
            self.assertTrue((result >= lower).all())

    def test_held_model_never_enters_donors(self):
        target = self.external
        with self.assertRaisesRegex(ValueError, 'Held model included'):
            study.forecast_fold(self.main, target, {self.held})
        with self.assertRaisesRegex(ValueError, 'Target model included'):
            study.point_predict(self.main, target, 'direct')

    def test_mixed_hardware_snapshot_is_rejected(self):
        campaigns = dict(self.campaigns)
        changed = campaigns[CAMPAIGNS[2]].copy()
        changed.loc[changed.index[0], 'gpu_indices_json'] = '[1]'
        campaigns[CAMPAIGNS[2]] = changed
        with self.assertRaisesRegex(ValueError, 'one local host'):
            study.evaluate(campaigns)

    def test_full_estimator_roundtrip_uses_static_inputs(self):
        bundle = study.fit_research_estimator(self.main)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.joblib'
            joblib.dump(bundle, path)
            loaded = joblib.load(path)
        first = study.forecast_with_research_estimator(loaded, self.external)
        changed = self.external.copy()
        changed['reserved_peak_gib'] *= 1000
        after = study.forecast_with_research_estimator(loaded, changed)
        np.testing.assert_allclose(first, after, atol=1e-12)
        self.assertTrue((first >= self.external.parameter_gib.to_numpy()).all())
        unsupported = self.external.copy()
        unsupported['host'] = 'another-host'
        with self.assertRaisesRegex(ValueError, 'Unsupported hardware profile'):
            study.forecast_with_research_estimator(loaded, unsupported)
        unsupported = self.external.copy()
        unsupported['cpu_core_limit'] = 4
        with self.assertRaisesRegex(ValueError, 'Unsupported hardware profile'):
            study.forecast_with_research_estimator(loaded, unsupported)
        causal = self.main.loc[self.main.workload_task == 'causal_lm_forward'].head(1)
        with self.assertRaisesRegex(ValueError, 'Unsupported task'):
            study.forecast_with_research_estimator(loaded, causal)

    def test_fold_calibration_never_includes_held_model(self):
        forecasts, calibration = study.evaluate(self.campaigns)
        self.assertEqual(len(calibration), 11)
        self.assertEqual(set(forecasts.method), set(study.METHODS))
        for row in calibration.itertuples():
            self.assertFalse(set(row.held_models.split('|')) &
                             set(row.donor_model_ids.split('|')))
        self.assertEqual(len(forecasts.loc[
            (forecasts.fold.str.startswith('loomo:')) &
            (forecasts.campaign == CAMPAIGNS[0])]), 575 * 5)
        qwen = forecasts.loc[forecasts.fold ==
                             'loomo:Qwen/Qwen2.5-0.5B-Instruct']
        self.assertFalse(qwen.supported_task.any())
        per_model, _ = study.summarize(forecasts)
        supported = study.cross_fold_summary(per_model, supported_only=True,
                                             forecasts=forecasts)
        self.assertEqual(set(supported.models), {8})

    def test_pinball_and_coverage_are_consistent(self):
        sample = pd.DataFrame([
            dict(fold='x', campaign='y', method='m', model_id='a',
                 observed_peak_gib=2.0, forecast_peak_gib=1.0),
            dict(fold='x', campaign='y', method='m', model_id='a',
                 observed_peak_gib=1.0, forecast_peak_gib=2.0),
        ])
        model, aggregate = study.summarize(sample)
        self.assertEqual(model.coverage.iloc[0], .5)
        self.assertAlmostEqual(model.pinball90_mib.iloc[0], 512.0)
        self.assertAlmostEqual(aggregate.coverage_macro.iloc[0], .5)


if __name__ == '__main__':
    unittest.main()
