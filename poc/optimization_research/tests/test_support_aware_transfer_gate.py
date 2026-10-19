"""Information-boundary tests for the retrospective support gate."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from support_aware_transfer_gate import (  # noqa: E402
    _probe_residual, choose_methods, exact_donor_support,
    merge_direct_ridge, probe_loo_scores)


class SupportAwareGateTests(unittest.TestCase):
    def test_exact_support_counts_distinct_donor_models(self):
        donor = pd.DataFrame({
            'model_id': ['a', 'a', 'b', 'a'],
            'workload_task': ['text'] * 4,
            'batch_size': [1, 1, 1, 2],
            'max_length': [16] * 4,
            'request_concurrency': [1] * 4,
            'repeats': [8] * 4,
        })
        target = donor.iloc[[0, 3]].copy()
        self.assertEqual(exact_donor_support(target, donor).tolist(), [True, False])

    def test_ineligible_empirical_covariance_cannot_win(self):
        scores = {'cov': np.array([0.01, 0.01]),
                  'kernel': np.array([0.20, 0.30]),
                  'ridge': np.array([0.30, 0.20])}
        self.assertEqual(choose_methods(scores, np.array([True, False])),
                         [['cov', 'cov'], ['kernel', 'ridge']])

    def test_probe_residual_ignores_unselected_outcomes(self):
        groups = ['p', 'q', 'u']
        truth = pd.DataFrame({
            'workload_group': groups,
            'replica_count': [1] * 3,
            'cpu_core_limit': [2] * 3,
            'latency_p95_ms': [10.0, 20.0, 30.0],
            'examples_per_second': [100.0, 200.0, 300.0],
            'batch_size': [1] * 3,
        })
        prediction = truth[['workload_group', 'replica_count', 'cpu_core_limit']].copy()
        prediction['zero_shot_latency_log'] = 0.0
        prediction['zero_shot_rps_log'] = 0.0
        first = _probe_residual(truth, prediction, ['p', 'q'], groups)
        modified = truth.copy()
        modified.loc[modified.workload_group == 'u',
                     ['latency_p95_ms', 'examples_per_second']] *= 1000
        second = _probe_residual(modified, prediction, ['p', 'q'], groups)
        np.testing.assert_array_equal(first[0], second[0])
        np.testing.assert_allclose(first[1], second[1])

    def test_loo_uses_only_selected_residuals(self):
        anchors = pd.DataFrame({'batch_size': [1, 2, 4, 8],
                                'max_length': [16] * 4,
                                'request_concurrency': [1] * 4})
        cov = np.stack([np.eye(4), np.eye(4)], axis=2)
        design = np.column_stack((np.ones(4), np.arange(4, dtype=float)))
        selected = np.array([0, 1, 2])
        residual = np.array([[0.2, -0.1], [0.3, -0.1], [0.4, 0.2]])
        a = probe_loo_scores(cov, design, selected, residual, anchors)
        changed = anchors.copy()
        changed.loc[3, 'batch_size'] = 100000
        # A changed unselected query coordinate may affect the kernel matrix,
        # but it cannot affect probe-to-probe predictions or their LOO score.
        b = probe_loo_scores(cov, design, selected, residual, changed)
        for method in a:
            np.testing.assert_allclose(a[method], b[method])

    def test_mismatched_zero_shot_model_is_rejected(self):
        first = pd.DataFrame({'execution_id': ['x'],
                              'zero_shot_latency_log': [1.0],
                              'zero_shot_rps_log': [2.0]})
        second = pd.DataFrame({'execution_id': ['x'],
                               'zero_shot_latency_log': [1.1],
                               'zero_shot_rps_log': [2.0],
                               'ridge_shift_latency_log': [1.0],
                               'ridge_shift_rps_log': [2.0]})
        with self.assertRaises(ValueError):
            merge_direct_ridge(first, second)


if __name__ == '__main__':
    unittest.main()
