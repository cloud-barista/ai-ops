"""Information-boundary and support-rule tests for resource-response transfer."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from physical_response_transfer import (CONDITION, diagnose_resource_response,
                                        predict_response)
from sparse_anchor_transfer import static_features


class ConstantResponse:
    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.tile([0.2, 0.1], (len(frame), 1))


def slate() -> pd.DataFrame:
    rows = []
    for group, length in (('known', 64), ('new', 96), ('causal', 96)):
        task = 'causal_lm_forward' if group == 'causal' else 'inference'
        for gpu in (1, 2):
            for cpu in (2, 4, 8, 16):
                rows.append({'campaign': 'synthetic', 'model_id': 'held',
                             'workload_group': group,
                             'execution_id': f'{group}-{gpu}-{cpu}',
                             'workload_task': task, 'batch_size': 4,
                             'max_length': length, 'request_concurrency': 2,
                             'cpu_threads': cpu, 'cpu_core_limit': cpu,
                             'replica_count': gpu, 'repeats': 2,
                             'latency_slo_ms': 100., 'target_throughput_rps': 1.,
                             'latency_p95_ms': 30. + cpu + gpu,
                             'examples_per_second': 50. + gpu})
    return pd.DataFrame(rows)


def frozen(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[['execution_id']].copy()
    for method in ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge'):
        out[f'{method}_latency_log'] = 1.0
        out[f'{method}_rps_log'] = 2.0 + np.log(frame.replica_count.to_numpy())
    return out


class PhysicalResponseTransferTest(unittest.TestCase):
    def setUp(self):
        self.held = slate()
        self.static = static_features(self.held, parameter_bytes=123456.)
        self.pred = frozen(self.held)
        known = self.static[self.static.workload_group == 'known'].iloc[0]
        self.support = ({tuple(known[list(CONDITION)]): 2},
                        {'inference': 2, 'causal_lm_forward': 0})

    def test_support_gate_changes_only_unsupported_same_task_latency(self):
        result = predict_response(self.static, self.pred,
                                  ConstantResponse(), self.support)
        self.assertEqual(int(result.uses_smooth_latency.sum()), 8)
        self.assertTrue(result.loc[result.workload_group == 'known',
                                   'support_smooth_latency_latency_log'].eq(1.).all())
        self.assertTrue(result.loc[result.workload_group == 'causal',
                                   'support_smooth_latency_latency_log'].eq(1.).all())
        np.testing.assert_allclose(result.loc[result.workload_group == 'new',
                                              'support_smooth_latency_latency_log'], 1.1)
        np.testing.assert_allclose(result.support_smooth_latency_rps_log,
                                   result.space_cov_transfer_rps_log)

    def test_unprobed_heldout_outcomes_cannot_change_predictions(self):
        before = predict_response(self.static, self.pred,
                                  ConstantResponse(), self.support)
        changed = self.held.copy()
        mask = ~((changed.workload_group == 'known') &
                 (changed.replica_count == 1) & (changed.cpu_core_limit == 2))
        changed.loc[mask, 'latency_p95_ms'] *= 200
        changed.loc[mask, 'examples_per_second'] /= 200
        after = predict_response(static_features(changed, 123456.), self.pred,
                                 ConstantResponse(), self.support)
        pd.testing.assert_frame_equal(before, after)

    def test_diagnostics_do_not_impose_cpu_monotonicity(self):
        held = self.held[self.held.workload_group == 'known'].copy()
        metrics = diagnose_resource_response(held)
        cpu = next(row for row in metrics if row['axis'] == 'cpu' and
                   row['from_gpu'] == 1 and row['from_cpu'] == 2 and
                   row['to_cpu'] == 4)
        self.assertEqual(cpu['latency_decrease_count'], 0)
        self.assertGreater(cpu['median_latency_ratio'], 1)


if __name__ == '__main__':
    unittest.main()
