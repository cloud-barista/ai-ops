"""Leakage, calibration, and inference checks for conditional VRAM peaks."""
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
import evaluate_vram_conditional as study
from evaluate_vram_peak import CAMPAIGNS, selected_measurements
from evaluate_vram_upper import donor_calibration, forecast_fold


ROOT = Path(__file__).resolve().parents[1]


class ConditionalVramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaigns = selected_measurements(
            pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet'))
        cls.main = cls.campaigns[CAMPAIGNS[0]]
        cls.held = 'google/bert_uncased_L-2_H-128_A-2'
        cls.donor = cls.main.loc[cls.main.model_id != cls.held]
        cls.external = cls.campaigns[CAMPAIGNS[2]].loc[
            cls.campaigns[CAMPAIGNS[2]].model_id == cls.held]

    def test_equal_model_votes_and_parameter_distance(self):
        scores = pd.DataFrame({
            'model_id': ['many'] * 100 + ['one'],
            'parameter_gib': [1.0] * 100 + [100.0],
            'ratio': [1.0] * 100 + [2.0],
        })
        self.assertEqual(study.model_balanced_quantile(scores, None), 2.0)
        self.assertEqual(study.model_balanced_quantile(scores, 0.5, 1.0), 1.0)
        with self.assertRaises(ValueError):
            study.model_balanced_quantile(scores, -1.0, 1.0)

    def test_target_outcomes_do_not_change_point_or_calibration(self):
        scores = study.crossfit_scores(self.donor)
        chosen, _ = study.select_bandwidth(self.donor)
        original = study.forecasts_from_scores(self.donor, self.external,
                                               scores, chosen)
        changed = self.external.copy()
        changed['reserved_peak_gib'] *= 1000
        changed['gpu_memory_reserved_bytes'] *= 1000
        changed['result_json'] = '{}'
        new = study.forecasts_from_scores(self.donor, changed, scores, chosen)
        for method in study.METHODS:
            np.testing.assert_allclose(original[method], new[method], atol=1e-12)

    def test_held_model_excluded_from_inner_selection(self):
        original = study.point_predict
        seen = []

        def checking_predict(train, target, method):
            self.assertFalse(set(train.model_id) & set(target.model_id))
            self.assertNotIn(self.held, set(train.model_id))
            self.assertNotIn(self.held, set(target.model_id))
            seen.append(target.model_id.iloc[0])
            return original(train, target, method)

        with patch.object(study, 'point_predict', side_effect=checking_predict):
            selected, scores = study.select_bandwidth(self.donor)
        self.assertIn(selected, study.BANDWIDTHS)
        self.assertTrue(seen)
        self.assertNotIn(self.held, set(scores.inner_held_model))

    def test_global_baseline_matches_prior_implementation(self):
        scores = study.crossfit_scores(self.donor)
        new = study.forecasts_from_scores(self.donor, self.external,
                                          scores, 2.0)['global_ratio_q90']
        old = forecast_fold(self.donor, self.external, {self.held},
                            donor_calibration(self.donor))['ridge_ratio_oof_q90']
        np.testing.assert_allclose(new, old, atol=1e-12)

    def test_unsupported_task_abstains_and_leak_is_rejected(self):
        causal_id = 'Qwen/Qwen2.5-0.5B-Instruct'
        donor = self.main.loc[self.main.model_id != causal_id]
        target = self.main.loc[self.main.model_id == causal_id]
        with self.assertRaisesRegex(ValueError, 'Unsupported target task'):
            study.forecasts_from_scores(donor, target,
                                        study.crossfit_scores(donor), 2.0)
        with self.assertRaisesRegex(ValueError, 'excluded'):
            study.forecasts_from_scores(self.main, self.external,
                                        study.crossfit_scores(self.main), 2.0)

    def test_estimator_roundtrip_static_fields_and_profile_gate(self):
        bundle = study.fit_research_estimator(self.main)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.joblib'
            joblib.dump(bundle, path)
            bundle = joblib.load(path)
        target = self.external.copy()
        target['model_id'] = 'new/encoder'
        first = study.forecast_with_research_estimator(bundle, target)
        target['reserved_peak_gib'] *= 1000
        np.testing.assert_allclose(first,
            study.forecast_with_research_estimator(bundle, target), atol=1e-12)
        self.assertTrue((first >= target.parameter_gib.to_numpy()).all())
        changed = target.copy()
        changed['cpu_core_limit'] = 4
        with self.assertRaisesRegex(ValueError, 'Unsupported hardware profile'):
            study.forecast_with_research_estimator(bundle, changed)
        changed = target.copy()
        changed['workload_task'] = 'causal_lm_forward'
        with self.assertRaisesRegex(ValueError, 'Unsupported task'):
            study.forecast_with_research_estimator(bundle, changed)
        with self.assertRaisesRegex(ValueError, 'new model'):
            study.forecast_with_research_estimator(bundle, self.external)

    def test_pinball_and_model_macro_aggregation(self):
        self.assertEqual(study.pinball90_mib(np.array([2.0]),
                                             np.array([1.0])), 921.6)
        example = pd.DataFrame([
            dict(fold='loomo:a', campaign=CAMPAIGNS[0], method='m',
                 model_id='a', observed_peak_gib=2.0, forecast_peak_gib=1.0),
            dict(fold='loomo:a', campaign=CAMPAIGNS[0], method='m',
                 model_id='a', observed_peak_gib=1.0, forecast_peak_gib=2.0),
        ])
        per_model, _, loomo = study.summarize(example)
        self.assertEqual(per_model.coverage.iloc[0], 0.5)
        self.assertAlmostEqual(loomo.pinball90_mib_macro.iloc[0], 512.0)

    def test_full_evaluation_abstains_on_unsupported_qwen(self):
        forecasts, selection, abstentions = study.evaluate(self.campaigns)
        self.assertEqual(set(forecasts.method), set(study.METHODS))
        self.assertEqual(len(abstentions), 3)
        self.assertTrue(abstentions.model_id.str.contains('Qwen').all())
        self.assertEqual(len(forecasts.loc[
            forecasts.fold.str.startswith('loomo:') &
            forecasts.campaign.eq(CAMPAIGNS[0])]), 512 * len(study.METHODS))
        self.assertFalse(selection.fold.isna().any())


if __name__ == '__main__':
    unittest.main()
