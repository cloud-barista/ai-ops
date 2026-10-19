"""Donor weights, shared-grid fallback, and held-out outcome isolation."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from donor_similarity_transfer import (
    _evaluate_fold, donor_surfaces, donor_weights,
    selected_anchor_residual, weighted_prior,
)


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def slate():
    rows = []
    for i in range(6):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'test', 'model_id': 'held',
                    'workload_group': f'g{i}',
                    'execution_id': f'e{i}_{gpu}_{cores}',
                    'workload_task': 'inference',
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
    parts = []
    for model, offset in [('donor1', 0.0), ('donor2', 0.5), ('donor3', -0.5)]:
        part = anchors[['workload_task', 'batch_size', 'max_length',
                        'request_concurrency', 'repeats']].copy()
        part['model_id'] = model
        part['residual_latency'] = 0.2 + np.arange(len(part)) * 0.01 + offset
        part['residual_rps'] = -0.3 - np.arange(len(part)) * 0.02 - offset
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


class DonorSimilarityTransferTests(unittest.TestCase):
    def test_weights_favor_closest_donor_and_sum_to_one(self):
        surface = np.zeros((3, 6, 2))
        surface[1] = 0.4
        surface[2] = 1.0
        selected = np.array([0, 2, 5])
        observed = np.zeros((3, 2))
        weights, distances = donor_weights(surface, selected, observed)
        self.assertAlmostEqual(float(weights.sum()), 1.0)
        self.assertTrue(np.all(np.diff(weights) < 0))
        self.assertTrue(np.all(np.diff(distances) > 0))
        self.assertGreater(weights[0], 1 / 3)

    def test_weighted_covariance_is_psd_and_uses_selected_values(self):
        test = slate()
        historical = donors(test)
        selected = np.array([0, 2, 5])
        observed = np.array([[0.2, -0.3], [0.2, -0.3], [0.2, -0.3]])
        priors, mean, info = weighted_prior(test, historical, selected, observed)
        self.assertEqual(info['mode'], 'weighted_aligned_donor_surfaces')
        self.assertEqual(len(info['donor_model_ids']), 3)
        self.assertEqual(mean.shape, (6, 2))
        self.assertTrue(1 <= info['effective_donors'] <= 3)
        for method in ('raw', 'centered', 'pooled_centered'):
            for output in range(2):
                self.assertGreaterEqual(np.linalg.eigvalsh(
                    priors[method][:, :, output]).min(), -1e-9)
        changed = observed + 3
        altered, _, altered_info = weighted_prior(test, historical,
                                                  selected, changed)
        self.assertFalse(np.allclose(priors['raw'], altered['raw']))
        self.assertFalse(np.allclose(info['donor_weights'],
                                     altered_info['donor_weights']))

    def test_task_without_aligned_donors_uses_kernel_fallback(self):
        held = slate()
        held['workload_task'] = 'causal_lm_forward'
        selected = np.array([0, 1])
        observed = np.zeros((2, 2))
        priors, mean, info = weighted_prior(held, donors(slate()), selected,
                                             observed)
        self.assertEqual(info['mode'], 'smooth_kernel_fallback')
        self.assertEqual(info['donor_model_ids'], [])
        np.testing.assert_allclose(priors['raw'], priors['smooth'])
        np.testing.assert_allclose(priors['centered'], priors['smooth'])
        np.testing.assert_allclose(mean, 0)

    def test_exact_surfaces_have_no_heldout_performance_dependency(self):
        held = slate()
        historical = donors(held)
        first, _ = donor_surfaces(held, historical)
        altered = held.copy()
        altered['latency_p95_ms'] *= 100
        altered['examples_per_second'] /= 100
        second, _ = donor_surfaces(altered, historical)
        np.testing.assert_allclose(first, second)

    def test_unprobed_and_nonanchor_outcomes_do_not_change_predictions(self):
        held = slate()
        historical = donors(held)
        probes = ['g0', 'g2', 'g4']
        model = ZeroPredictor()
        size = 123456
        selected, observed = selected_anchor_residual(held, probes, model, size)
        first, _ = _evaluate_fold(held, probes, historical, model,
                                  model, size, interpolate=False)
        changed = held.copy()
        selected_rows = ((changed.workload_group.isin(probes)) &
                         changed.replica_count.eq(1) &
                         changed.cpu_core_limit.eq(2))
        changed.loc[~selected_rows, 'latency_p95_ms'] *= 100
        changed.loc[~selected_rows, 'examples_per_second'] /= 100
        changed.loc[~selected_rows, 'result_json'] = json.dumps({
            'model_parameter_bytes': 123456, 'hidden_outcome': 987,
        })
        selected_again, observed_again = selected_anchor_residual(
            changed, probes, model, size)
        np.testing.assert_array_equal(selected, selected_again)
        np.testing.assert_allclose(observed, observed_again)
        second, _ = _evaluate_fold(changed, probes, historical, model,
                                   model, size, interpolate=False)
        pd.testing.assert_frame_equal(first, second)

    def test_external_outcomes_do_not_affect_interpolated_prediction(self):
        main = slate()
        external = slate().copy()
        external['campaign'] = 'external'
        external['workload_group'] = 'external_' + external.workload_group
        external['execution_id'] = 'external_' + external.execution_id
        external['max_length'] += 8
        both = pd.concat((main, external), ignore_index=True)
        historical = donors(main)
        probes = ['g0', 'g2', 'g4']
        model = ZeroPredictor()
        first, _ = _evaluate_fold(both, probes, historical, model,
                                  model, 123456, interpolate=True)
        changed = both.copy()
        changed.loc[changed.campaign.eq('external'), 'latency_p95_ms'] *= 100
        changed.loc[changed.campaign.eq('external'), 'examples_per_second'] /= 100
        second, _ = _evaluate_fold(changed, probes, historical, model,
                                   model, 123456, interpolate=True)
        pd.testing.assert_frame_equal(first, second)


if __name__ == '__main__':
    unittest.main()
