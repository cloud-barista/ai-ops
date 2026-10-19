"""Information boundary, replica semantics, and paid-probe cost checks."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from relative_response_sentinel import (_probe_cost, predict_target,
                                        response_posterior, select_gate_threshold)


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def slate(groups=10):
    rows = []
    for i in range(groups):
        for gpu in (1, 2):
            for cpu in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'khu-hf-local-exhaustive',
                    'model_id': 'held', 'workload_group': f'held_{i}',
                    'execution_id': f'e_{i}_{gpu}_{cpu}',
                    'workload_task': 'sequence_classification',
                    'batch_size': 2 ** (i % 3), 'max_length': 64 * (i + 1),
                    'request_concurrency': 1 + i, 'cpu_threads': 2,
                    'cpu_core_limit': cpu, 'replica_count': gpu, 'repeats': 8,
                    'latency_slo_ms': 100.0, 'target_throughput_rps': 2.0,
                    'latency_p95_ms': 30.0 + i + gpu,
                    'examples_per_second': 10.0 + gpu,
                    'wall_seconds': 0.2 + i * 0.01 + gpu * 0.1,
                    'result_json': json.dumps({'model_parameter_bytes': 123456}),
                })
    return pd.DataFrame(rows)


class RelativeResponseSentinelTests(unittest.TestCase):
    def test_unpaid_target_outcomes_cannot_change_predictions(self):
        held = slate()
        covariance = np.stack([np.eye(10) + 0.3,
                               np.eye(10) + 0.3], axis=2)
        probes = {'anchors': [f'held_{i}' for i in (0, 2, 5, 9)],
                  'sentinel': 'held_2',
                  'anchor8': [f'held_{i}' for i in range(8)]}
        zero = ZeroPredictor()
        first, _ = predict_target(held, held.iloc[0:0], covariance, None,
                                  zero, zero, {}, probes, -1.0, [])
        changed = held.copy()
        paid_anchors = (changed.workload_group.isin(probes['anchors']) &
                        changed.replica_count.eq(1) &
                        changed.cpu_core_limit.eq(2))
        paid_baseline = (changed.workload_group.isin(probes['anchor8']) &
                         changed.replica_count.eq(1) &
                         changed.cpu_core_limit.eq(2))
        sentinel = (changed.workload_group.eq(probes['sentinel']) &
                    changed.replica_count.eq(2) &
                    changed.cpu_core_limit.eq(2))
        unpaid = ~(paid_anchors | paid_baseline | sentinel)
        changed.loc[unpaid, 'latency_p95_ms'] *= 100.0
        changed.loc[unpaid, 'examples_per_second'] /= 100.0
        second, _ = predict_target(changed, changed.iloc[0:0], covariance, None,
                                   zero, zero, {}, probes, -1.0, [])
        for method in ('anchor_only', 'sentinel_only', 'global_gpu2',
                       'joint_response', 'reliability_gate', 'anchor8'):
            np.testing.assert_allclose(
                first[[f'{method}_latency_log', f'{method}_rps_log']],
                second[[f'{method}_latency_log', f'{method}_rps_log']])

    def test_sentinel_outcome_changes_only_paid_methods(self):
        held = slate()
        covariance = np.stack([np.eye(10) + 0.3,
                               np.eye(10) + 0.3], axis=2)
        probes = {'anchors': [f'held_{i}' for i in (0, 2, 5, 9)],
                  'sentinel': 'held_2',
                  'anchor8': [f'held_{i}' for i in range(8)]}
        zero = ZeroPredictor()
        first, _ = predict_target(held, held.iloc[0:0], covariance, None,
                                  zero, zero, {}, probes, -1.0, [])
        changed = held.copy()
        selected = (changed.workload_group.eq('held_2') &
                    changed.replica_count.eq(2) &
                    changed.cpu_core_limit.eq(2))
        changed.loc[selected, 'latency_p95_ms'] *= 2.0
        second, _ = predict_target(changed, changed.iloc[0:0], covariance, None,
                                   zero, zero, {}, probes, -1.0, [])
        np.testing.assert_allclose(first.anchor_only_latency_log,
                                   second.anchor_only_latency_log)
        np.testing.assert_allclose(first.anchor8_latency_log,
                                   second.anchor8_latency_log)
        self.assertFalse(np.allclose(first.global_gpu2_latency_log,
                                     second.global_gpu2_latency_log))
        self.assertFalse(np.allclose(first.sentinel_only_latency_log,
                                     second.sentinel_only_latency_log))

    def test_unseen_condition_outcomes_are_hidden_and_main_prior_is_exact(self):
        held = slate()
        external = slate(2)
        external['campaign'] = 'khu-hf-local-novel-matrix'
        external['workload_group'] = 'external_' + external.workload_group
        external['execution_id'] = 'external_' + external.execution_id
        external['max_length'] += 2048
        main_prior = np.stack([np.eye(10) + 0.3,
                               np.eye(10) + 0.3], axis=2)
        all_prior = np.stack([np.eye(12) + 0.1,
                              np.eye(12) + 0.1], axis=2)
        probes = {'anchors': [f'held_{i}' for i in (0, 2, 5, 9)],
                  'sentinel': 'held_2',
                  'anchor8': [f'held_{i}' for i in range(8)]}
        zero = ZeroPredictor()
        first, _ = predict_target(held, external, main_prior, all_prior,
                                  zero, zero, {}, probes, -1.0, [])
        changed = external.copy()
        changed['latency_p95_ms'] *= 100.0
        changed['examples_per_second'] /= 100.0
        second, _ = predict_target(held, changed, main_prior, all_prior,
                                   zero, zero, {}, probes, -1.0, [])
        for method in ('anchor_only', 'sentinel_only', 'global_gpu2',
                       'joint_response', 'reliability_gate', 'anchor8'):
            np.testing.assert_allclose(
                first[[f'{method}_latency_log', f'{method}_rps_log']],
                second[[f'{method}_latency_log', f'{method}_rps_log']])
        alone, _ = predict_target(held, held.iloc[0:0], main_prior, None,
                                  zero, zero, {}, probes, -1.0, [])
        combined_main = first[first.campaign.eq('khu-hf-local-exhaustive')]
        np.testing.assert_allclose(combined_main.anchor8_latency_log,
                                   alone.anchor8_latency_log)

    def test_replica_cost_is_charged_twice_and_response_anchor_is_zero(self):
        held = slate()
        groups = ['held_0', 'held_2']
        anchor_wall = held[(held.workload_group.isin(groups)) &
                           held.replica_count.eq(1) &
                           held.cpu_core_limit.eq(2)].wall_seconds.sum()
        sentinel_wall = held[(held.workload_group.eq('held_2')) &
                             held.replica_count.eq(2) &
                             held.cpu_core_limit.eq(2)].wall_seconds.iloc[0]
        self.assertAlmostEqual(_probe_cost(held, groups, 'held_2'),
                               float(anchor_wall + 2 * sentinel_wall))
        rng = np.random.default_rng(3)
        donors = rng.normal(size=(5, 10, 8, 2)) * 0.1
        donors[:, :, 0] = 0.0
        coords = np.column_stack((np.arange(10) / 10, np.zeros((10, 2))))
        observation = np.array([0.1, -0.2])
        posterior = response_posterior(donors, coords, 2, observation)
        np.testing.assert_allclose(posterior[:, 0], 0.0)
        np.testing.assert_allclose(posterior[2, 4], observation)
        threshold = select_gate_threshold([
            {'surprise': 0.2, 'joint_loss': 0.4, 'fallback_loss': 1.0},
            {'surprise': 2.0, 'joint_loss': 1.5, 'fallback_loss': 1.0},
        ])
        self.assertIn(threshold, (-1.0, 0.5, 1.0, 1.5, 2.0, 3.0,
                                  float('inf')))


if __name__ == '__main__':
    unittest.main()
