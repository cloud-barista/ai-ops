"""Chronology and held-model isolation for nested donor calibration."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from learned_repeat_distribution import (
    calibration_cells, candidate_crps, fit_full_models, gpu_variances,
    learned_forecast, predict_from_trained_model, select_hyperparameters,
)
from repeated_measurement_distribution import (
    KEY, forecast_holdout, prepare_panel,
)

ROOT = Path(__file__).resolve().parents[1]
V1 = 'khu-hf-direct-collector/v1'
V2 = 'khu-hf-direct-collector/v2'


class LearnedRepeatDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = prepare_panel(pd.read_parquet(
            ROOT / 'data/audit_v1/measurements.parquet'))
        cls.held = sorted(cls.panel.model_id.unique())[0]

    def test_inner_calibration_excludes_held_model_entirely(self):
        v2 = self.panel[self.panel.collector_version == V2]
        before, meta = calibration_cells(v2, self.held, 'log_latency', 5)
        changed = v2.copy()
        changed.loc[changed.model_id == self.held, 'log_latency'] += 100.0
        after, _ = calibration_cells(changed, self.held, 'log_latency', 5)
        self.assertEqual(meta['inner_origin_round_rank'], 7)
        pd.testing.assert_frame_equal(before, after, check_exact=True)
        self.assertNotIn(self.held, set(before.inner_target_model))

    def test_future_outer_target_does_not_change_selected_hyperparameters(self):
        v2 = self.panel[self.panel.collector_version == V2]
        base, _ = calibration_cells(v2, self.held, 'log_rps', 2)
        best, _ = select_hyperparameters(base, 2)
        changed = v2.copy()
        changed.loc[changed.round_rank >= 27, 'log_rps'] += 20.0
        after, _ = calibration_cells(changed, self.held, 'log_rps', 2)
        changed_best, _ = select_hyperparameters(after, 2)
        self.assertEqual(best, changed_best)

    def test_calibration_uses_actual_order_and_prior_precedes_future(self):
        v1 = self.panel[self.panel.collector_version == V1]
        cells, meta = calibration_cells(v1, self.held, 'log_latency', 5)
        self.assertEqual(meta['inner_origin_round_rank'], 23)
        self.assertEqual(meta['inner_validation_last_round_rank'], 42)
        self.assertEqual(meta['inner_validation_rows'], len(cells) * 20)
        ordered = v1[['run_id', 'round_rank']].drop_duplicates().sort_values('round_rank')
        self.assertTrue(ordered.run_id.iloc[0].endswith('round-100'))
        self.assertLess(v1[v1.round_rank < 23].finish_time.max(),
                        v1[v1.round_rank >= 23].start_time.min())

    def test_inner_validation_mutation_changes_training_score(self):
        v2 = self.panel[self.panel.collector_version == V2]
        cells, _ = calibration_cells(v2, self.held, 'log_latency', 5)
        original = candidate_crps(cells, 5, 5, 1)
        changed = v2.copy()
        selected = ((changed.model_id != self.held) &
                    changed.round_rank.between(7, 26))
        changed.loc[selected, 'log_latency'] += 1.0
        other, _ = calibration_cells(changed, self.held, 'log_latency', 5)
        self.assertGreater(candidate_crps(other, 5, 5, 1), original)

    def test_only_selected_target_probes_affect_outer_prediction(self):
        v2 = self.panel[self.panel.collector_version == V2]
        fixed = forecast_holdout(self.panel, V2, self.held, 'cutoff_last_k', 5)
        baseline = fixed[fixed.target == 'log_latency']
        learned = learned_forecast(baseline, 2, 1.25, 5)
        changed = self.panel.copy()
        hidden = ((changed.collector_version == V2) &
                  (changed.model_id == self.held) &
                  ((changed.round_rank < 22) | (changed.round_rank >= 27)))
        changed.loc[hidden, 'log_latency'] += 99.0
        refit = forecast_holdout(changed, V2, self.held, 'cutoff_last_k', 5)
        after = learned_forecast(refit[refit.target == 'log_latency'], 2, 1.25, 5)
        pd.testing.assert_frame_equal(learned, after, check_exact=True)

    def test_fit_full_model_never_uses_outer_future(self):
        baseline = fit_full_models(self.panel)
        changed = self.panel.copy()
        mask = ((changed.collector_version == V2) &
                (changed.round_rank >= 27))
        changed.loc[mask, ['log_latency', 'log_rps']] += 99.0
        after = fit_full_models(changed)
        self.assertEqual(baseline, after)
        self.assertEqual(len(baseline), 16)

    def test_serialized_model_predictor_rejects_unsupported_inputs(self):
        artifact = json.loads((ROOT / 'artifacts/learned_repeat_v1/trained_models.json').read_text())
        model = next(x for x in artifact['fitted_models']
                     if x['collector_version'] == V2 and x['k'] == 5 and
                     x['target'] == 'log_latency' and
                     x['method'] == 'nested_donor_nll')
        result = predict_from_trained_model(model, 1, [0.0] * 5)
        self.assertLess(result['lower90_log'], result['mean_log'])
        self.assertLess(result['mean_log'], result['upper90_log'])
        self.assertTrue(np.isfinite(result['scale_log']))
        with self.assertRaises(ValueError):
            predict_from_trained_model(model, 3, [0.0] * 5)
        with self.assertRaises(ValueError):
            predict_from_trained_model(model, 1, [0.0] * 4)


if __name__ == '__main__':
    unittest.main()
