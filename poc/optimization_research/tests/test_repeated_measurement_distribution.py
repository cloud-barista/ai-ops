"""Leakage, chronology, cost, and score checks for repeated-run forecasts."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import quad
from scipy.stats import t

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from repeated_measurement_distribution import (donor_prior, forecast_holdout,
                                               prepare_panel, round_slices,
                                               score_forecasts, student_t_crps)


ROOT = Path(__file__).resolve().parents[1]
V1 = 'khu-hf-direct-collector/v1'
V2 = 'khu-hf-direct-collector/v2'


class RepeatedDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = prepare_panel(pd.read_parquet(
            ROOT / 'data/audit_v1/measurements.parquet'))
        cls.held = sorted(cls.panel.model_id.unique())[0]

    def test_actual_chronology_and_splits(self):
        v1 = self.panel[self.panel.collector_version == V1]
        ordered = v1[['round_rank', 'run_id']].drop_duplicates().sort_values('round_rank')
        self.assertTrue(ordered.run_id.iloc[0].endswith('round-100'))
        self.assertTrue(ordered.run_id.iloc[42].endswith('round-042'))
        self.assertTrue(ordered.run_id.iloc[43].endswith('round-043'))
        for version in [V1, V2]:
            for scenario in ['immediate_first_k', 'cutoff_last_k']:
                for k in [2, 3, 5, 10]:
                    donor, target, future = round_slices(version, scenario, k)
                    self.assertEqual(len(target), k)
                    self.assertEqual(len(future), 20)
                    self.assertLess(max(donor + target), min(future))
                    if scenario == 'immediate_first_k':
                        self.assertEqual(donor, target)

    def test_future_and_unselected_target_outcomes_do_not_enter_forecast(self):
        original = forecast_holdout(self.panel, V2, self.held, 'immediate_first_k', 5)
        changed = self.panel.copy()
        mask = ((changed.collector_version == V2) &
                (changed.model_id == self.held) & (changed.round_rank >= 5))
        changed.loc[mask, 'log_latency'] += 10.0
        changed.loc[mask, 'log_rps'] -= 10.0
        changed.loc[mask, 'latency_p95_ms'] *= 100.0
        changed.loc[mask, 'examples_per_second'] /= 100.0
        after = forecast_holdout(changed, V2, self.held, 'immediate_first_k', 5)
        pd.testing.assert_frame_equal(original, after, check_exact=True)

        future_rounds = round_slices(V2, 'immediate_first_k', 5)[2]
        original_scores = score_forecasts(self.panel[self.panel.collector_version == V2],
                                          original.head(1), future_rounds)
        changed_scores = score_forecasts(changed[changed.collector_version == V2],
                                         after.head(1), future_rounds)
        self.assertFalse(np.array_equal(original_scores.nll.to_numpy(),
                                        changed_scores.nll.to_numpy()))

    def test_entire_held_model_excluded_from_donor_prior(self):
        first = self.panel[self.panel.collector_version == V2]
        before = donor_prior(first, self.held, list(range(5)), 'log_latency')
        changed = first.copy()
        changed.loc[changed.model_id == self.held, 'log_latency'] += 100.0
        after = donor_prior(changed, self.held, list(range(5)), 'log_latency')
        self.assertEqual(before, after)

    def test_other_collector_version_cannot_affect_forecast(self):
        original = forecast_holdout(self.panel, V2, self.held, 'cutoff_last_k', 5)
        changed = self.panel.copy()
        mask = changed.collector_version == V1
        changed.loc[mask, 'log_latency'] += 100.0
        changed.loc[mask, 'log_rps'] -= 100.0
        after = forecast_holdout(changed, V2, self.held, 'cutoff_last_k', 5)
        pd.testing.assert_frame_equal(original, after, check_exact=True)

    def test_missing_selected_probe_is_rejected(self):
        v2 = self.panel[(self.panel.collector_version == V2) &
                        (self.panel.model_id == self.held) &
                        (self.panel.round_rank == 0)]
        changed = self.panel.drop(v2.index[0])
        with self.assertRaises(ValueError):
            forecast_holdout(changed, V2, self.held, 'immediate_first_k', 5)

    def test_training_after_future_start_is_rejected(self):
        changed = self.panel.copy()
        donor = changed[(changed.collector_version == V2) &
                        (changed.model_id != self.held) & (changed.round_rank < 5)]
        future = changed[(changed.collector_version == V2) &
                         (changed.round_rank == 27)]
        changed.loc[future.index[0], 'start_time'] = donor.finish_time.max() - pd.Timedelta(seconds=1)
        with self.assertRaises(ValueError):
            forecast_holdout(changed, V2, self.held, 'immediate_first_k', 5)

    def test_gpu_wall_cost_counts_replicas(self):
        forecast = forecast_holdout(self.panel, V2, self.held, 'immediate_first_k', 5)
        row = forecast[forecast.replica_count == 2].iloc[0]
        ids = json.loads(row.probe_execution_ids_json)
        source = self.panel[self.panel.execution_id.isin(ids)]
        self.assertEqual(len(source), 5)
        self.assertAlmostEqual(row.probe_gpu_wall_seconds,
                               float((source.replica_count * source.wall_seconds).sum()))

    def test_student_t_crps_agrees_with_cdf_integral(self):
        for degrees in [1, 4]:
            for actual in [0.0, .5, -3.0]:
                analytic = float(student_t_crps(np.array([actual]), 0.0, 1.0,
                                                degrees)[0])
                cdf = lambda x: t.cdf(x, degrees)
                integral = (quad(lambda x: cdf(x) ** 2, -np.inf, actual)[0] +
                            quad(lambda x: (1 - cdf(x)) ** 2, actual, np.inf)[0])
                self.assertAlmostEqual(analytic, integral, places=7)

    def test_two_probe_forecast_contains_cauchy_baseline(self):
        forecast = forecast_holdout(self.panel, V2, self.held, 'immediate_first_k', 2)
        local_t = forecast[forecast.method == 'target_local_t']
        self.assertEqual(len(local_t), self.panel[(self.panel.collector_version == V2) &
                                                  (self.panel.model_id == self.held)]
                         .groupby(['workload_task', 'replica_count']).ngroups * 2)
        self.assertTrue(local_t.degrees_freedom.eq(1).all())


if __name__ == '__main__':
    unittest.main()
