"""Information-boundary and accounting checks for the dual-axis pilot."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dual_axis_sparse_probe import (_probe_cost, center_cross_probe,
                                    dual_correction, predict_fold)


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


class ZeroCostPredictor:
    def predict(self, frame):
        return np.zeros(len(frame))


def slate(groups=10):
    rows = []
    for i in range(groups):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'synthetic', 'model_id': 'held',
                    'workload_group': f'g{i}',
                    'execution_id': f'e{i}_{gpu}_{cores}',
                    'workload_task': 'sequence_classification',
                    'batch_size': 2 ** (i % 3), 'max_length': 64 * (i + 1),
                    'request_concurrency': 1 + i,
                    'cpu_threads': 2, 'cpu_core_limit': cores,
                    'replica_count': gpu, 'repeats': 8,
                    'latency_slo_ms': 100.0,
                    'target_throughput_rps': 2.0,
                    'latency_p95_ms': 30.0 + i + gpu,
                    'examples_per_second': 10.0 + gpu,
                    'wall_seconds': 0.2 + i * 0.01 + gpu * 0.1,
                    'result_json': json.dumps({'model_parameter_bytes': 123456}),
                })
    return pd.DataFrame(rows)


class DualAxisPilotTests(unittest.TestCase):
    def test_unobserved_performance_does_not_affect_dual_prediction(self):
        held = slate()
        covariance = np.stack([np.eye(10) + 0.3,
                               np.eye(10) + 0.3], axis=2)
        zero = ZeroPredictor()
        first, manifest = predict_fold(held, covariance, zero, zero,
                                       ZeroCostPredictor(),
                                       budgets=(6, 8))
        changed = held.copy()
        anchor6 = manifest['anchor_groups'][6]
        crossing = set(manifest['cross_groups'].values())
        observed_anchor = ((changed.workload_group.isin(anchor6)) &
                           (changed.replica_count == 1) &
                           (changed.cpu_core_limit == 2))
        observed_cross = ((changed.workload_group.isin(crossing)) &
                          (changed.replica_count == 2) &
                          (changed.cpu_core_limit == 2))
        changed.loc[~(observed_anchor | observed_cross), 'latency_p95_ms'] *= 100
        changed.loc[~(observed_anchor | observed_cross), 'examples_per_second'] /= 100
        second, second_manifest = predict_fold(changed, covariance, zero, zero,
                                               ZeroCostPredictor(), budgets=(6, 8))
        self.assertEqual(manifest, second_manifest)
        for method in ('dual_center', 'dual_cheapest', 'anchor6'):
            np.testing.assert_allclose(first[[f'{method}_latency_log',
                                              f'{method}_rps_log']],
                                       second[[f'{method}_latency_log',
                                               f'{method}_rps_log']])

    def test_cross_measurement_changes_only_gpu2_predictions(self):
        held = slate()
        covariance = np.stack([np.eye(10) + 0.3,
                               np.eye(10) + 0.3], axis=2)
        zero = ZeroPredictor()
        first, manifest = predict_fold(held, covariance, zero, zero,
                                       ZeroCostPredictor(),
                                       budgets=(6, 8))
        group = manifest['cross_groups']['dual_center']
        changed = held.copy()
        selected = ((changed.workload_group == group) &
                    (changed.replica_count == 2) &
                    (changed.cpu_core_limit == 2))
        changed.loc[selected, 'latency_p95_ms'] *= 2
        changed.loc[selected, 'examples_per_second'] /= 2
        second, _ = predict_fold(changed, covariance, zero, zero,
                                 ZeroCostPredictor(),
                                 budgets=(6, 8))
        gpu1 = first.replica_count == 1
        np.testing.assert_allclose(first.loc[gpu1, 'dual_center_latency_log'],
                                   second.loc[gpu1, 'dual_center_latency_log'])
        self.assertFalse(np.allclose(first.loc[~gpu1, 'dual_center_latency_log'],
                                     second.loc[~gpu1, 'dual_center_latency_log']))
        np.testing.assert_allclose(first.anchor6_latency_log,
                                   second.anchor6_latency_log)

    def test_probe_cost_charges_two_gpu_wall_seconds(self):
        held = slate()
        expected = float(held[(held.workload_group == 'g0') &
                              (held.replica_count == 1) &
                              (held.cpu_core_limit == 2)].wall_seconds.iloc[0] +
                         2 * held[(held.workload_group == 'g0') &
                                  (held.replica_count == 2) &
                                  (held.cpu_core_limit == 2)].wall_seconds.iloc[0])
        self.assertAlmostEqual(_probe_cost(held, ['g0'], 'g0'), expected)

    def test_center_uses_only_workload_coordinates(self):
        design = pd.DataFrame({'x': [0.0, 1.0, 2.0], 'y': [0.0, 0.0, 0.0]},
                              index=['a', 'b', 'c'])
        self.assertEqual(center_cross_probe(design, ['a', 'b', 'c']), 'b')


if __name__ == '__main__':
    unittest.main()
