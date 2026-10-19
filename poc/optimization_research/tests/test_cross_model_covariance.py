"""Check sparse-probe information boundaries and covariance calculations."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cross_model_covariance import (choose_covariance_probes, posterior_residual,
                                    predict_sparse, residual_covariance,
                                    training_oof_anchor_residuals)
from conditional_response import prepare


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def slate(task='inference'):
    rows = []
    for i in range(6):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'test', 'model_id': 'held',
                    'workload_group': f'g{i}',
                    'execution_id': f'e{i}_{gpu}_{cores}',
                    'workload_task': task,
                    'batch_size': 2 ** (i % 3), 'max_length': 64 * (i + 1),
                    'request_concurrency': 1 + i, 'cpu_threads': 2,
                    'cpu_core_limit': cores, 'replica_count': gpu,
                    'repeats': 8, 'latency_slo_ms': 100.0,
                    'target_throughput_rps': 2.0,
                    'latency_p95_ms': 30.0 + i + gpu,
                    'examples_per_second': 10.0 + gpu,
                    'result_json': json.dumps({'model_parameter_bytes': 123456}),
                })
    return pd.DataFrame(rows)


def donors(frame):
    anchors = frame[(frame.replica_count == 1) & (frame.cpu_core_limit == 2)]
    out = []
    for model, offset in [('donor1', 0.0), ('donor2', 0.1)]:
        d = anchors[['workload_task', 'batch_size', 'max_length',
                     'request_concurrency', 'repeats']].copy()
        d['model_id'] = model
        d['residual_latency'] = np.arange(len(d)) * 0.01 + offset
        d['residual_rps'] = -np.arange(len(d)) * 0.02 - offset
        out.append(d)
    return pd.concat(out, ignore_index=True)


class CrossModelCovarianceTests(unittest.TestCase):
    def test_inner_residual_for_donor_excludes_its_other_resource_targets(self):
        frames = []
        for i, model in enumerate(('donor1', 'donor2', 'donor3')):
            d = slate().copy()
            d['model_id'] = model
            d['workload_group'] = model + '_' + d.workload_group
            d['execution_id'] = model + '_' + d.execution_id
            d['latency_p95_ms'] *= (i + 1)
            d['examples_per_second'] /= (i + 1)
            d['eligible'] = True
            d['device'] = 'cuda'
            frames.append(d)
        original = pd.concat(frames, ignore_index=True)
        first = training_oof_anchor_residuals(prepare(original))
        changed = original.copy()
        unprobed_donor_target = ((changed.model_id == 'donor1') &
                                 ~((changed.replica_count == 1) &
                                   (changed.cpu_core_limit == 2)))
        changed.loc[unprobed_donor_target, 'latency_p95_ms'] *= 50
        changed.loc[unprobed_donor_target, 'examples_per_second'] /= 50
        second = training_oof_anchor_residuals(prepare(changed))
        a = first[first.model_id == 'donor1'].reset_index(drop=True)
        b = second[second.model_id == 'donor1'].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)

    def test_covariance_uses_aligned_donors_and_held_static_conditions_only(self):
        held = slate()
        donor = donors(held)
        first, info = residual_covariance(held, donor)
        self.assertEqual(info['mode'], 'aligned_empirical_plus_kernel')
        self.assertEqual(len(info['aligned_donor_models']), 2)
        self.assertEqual(first.shape, (6, 6, 2))
        for t in range(2):
            self.assertGreaterEqual(np.linalg.eigvalsh(first[:, :, t]).min(), -1e-9)
        changed = held.copy()
        changed['latency_p95_ms'] *= 20
        changed['examples_per_second'] /= 20
        second, _ = residual_covariance(changed, donor)
        np.testing.assert_allclose(first, second)

    def test_unaligned_causal_lm_has_explicit_kernel_fallback(self):
        held = slate('causal_lm_forward')
        _, info = residual_covariance(held, donors(slate()))
        self.assertEqual(info['mode'], 'kernel_fallback_no_aligned_donors')
        self.assertEqual(info['aligned_donor_models'], [])

    def test_selection_and_posterior_are_deterministic(self):
        x = np.arange(6, dtype=float)
        kernel = np.exp(-0.5 * (x[:, None] - x[None, :]) ** 2)
        covariance = np.stack([kernel, kernel * 0.5], axis=2)
        first = choose_covariance_probes(covariance, 3)
        second = choose_covariance_probes(covariance, 3)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(set(first.tolist())), 3)
        values = np.array([[0.4, -0.1], [0.2, 0.3], [-0.4, 0.2]])
        predicted = posterior_residual(covariance, first, values)
        np.testing.assert_allclose(predicted[first], values)

    def test_unprobed_outcomes_do_not_change_any_sparse_prediction(self):
        held = slate()
        covariance = np.stack([np.eye(6) + 0.3, np.eye(6) + 0.3], axis=2)
        predictor = ZeroPredictor()
        selected = ['g0', 'g2']
        first = predict_sparse(held, selected, selected,
                               covariance, predictor, predictor)
        changed = held.copy()
        observed = (changed.workload_group.isin(selected) &
                    (changed.replica_count == 1) &
                    (changed.cpu_core_limit == 2))
        changed.loc[~observed, 'latency_p95_ms'] *= 100
        changed.loc[~observed, 'examples_per_second'] /= 100
        changed.loc[~observed, 'result_json'] = json.dumps({
            'model_parameter_bytes': 123456,
            'private_unprobed_outcome': 987654,
        })
        second = predict_sparse(changed, selected, selected,
                                covariance, predictor, predictor)
        pd.testing.assert_frame_equal(first, second)

    def test_selected_anchor_changes_transfer(self):
        held = slate()
        covariance = np.stack([np.eye(6) + 0.3, np.eye(6) + 0.3], axis=2)
        predictor = ZeroPredictor()
        before = predict_sparse(held, ['g0'], ['g0'],
                                covariance, predictor, predictor)
        changed = held.copy()
        selected = ((changed.workload_group == 'g0') &
                    (changed.replica_count == 1) &
                    (changed.cpu_core_limit == 2))
        changed.loc[selected, 'latency_p95_ms'] *= 2
        after = predict_sparse(changed, ['g0'], ['g0'],
                               covariance, predictor, predictor)
        self.assertFalse(np.allclose(before.cov_transfer_latency_log,
                                     after.cov_transfer_latency_log))
        np.testing.assert_allclose(before.zero_shot_latency_log,
                                   after.zero_shot_latency_log)


if __name__ == '__main__':
    unittest.main()
