"""Mechanistic permutation control and held-out information-boundary tests."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from covariance_permutation_control import (
    permute_donor_conditions, predict_prior, prepare_prediction,
)
from cross_model_covariance import predict_sparse, residual_covariance


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def slate():
    rows = []
    for i in range(10):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'test', 'model_id': 'held',
                    'workload_group': f'g{i}',
                    'execution_id': f'e{i}_{gpu}_{cores}',
                    'workload_task': 'inference',
                    'batch_size': 2 ** (i % 3),
                    'max_length': 64 * (i + 1),
                    'request_concurrency': 1 + i,
                    'cpu_threads': 2, 'cpu_core_limit': cores,
                    'replica_count': gpu, 'repeats': 8,
                    'latency_slo_ms': 100.0,
                    'target_throughput_rps': 2.0,
                    'latency_p95_ms': 30.0 + i + gpu,
                    'examples_per_second': 10.0 + gpu,
                    'result_json': json.dumps({'model_parameter_bytes': 123456}),
                })
    return pd.DataFrame(rows)


def donors(frame):
    anchor = frame[(frame.replica_count == 1) &
                   (frame.cpu_core_limit == 2)]
    pieces = []
    for donor, offset in [('donor1', 0.0), ('donor2', 0.2),
                          ('donor3', -0.1)]:
        one = anchor[['workload_task', 'batch_size', 'max_length',
                      'request_concurrency', 'repeats']].copy()
        one['model_id'] = donor
        one['residual_latency'] = np.arange(len(one)) * 0.1 + offset
        one['residual_rps'] = -np.arange(len(one)) * 0.03 - offset
        pieces.append(one)
    return pd.concat(pieces, ignore_index=True)


class CovariancePermutationControlTests(unittest.TestCase):
    def test_pairs_marginals_and_donor_support_are_preserved(self):
        original = donors(slate())
        shuffled = permute_donor_conditions(original, 1000, 'held')
        self.assertTrue(original.index.equals(shuffled.index))
        pd.testing.assert_frame_equal(
            original.drop(columns=['residual_latency', 'residual_rps']),
            shuffled.drop(columns=['residual_latency', 'residual_rps']),
        )
        for donor in sorted(original.model_id.unique()):
            before = original[original.model_id == donor]
            after = shuffled[shuffled.model_id == donor]
            self.assertCountEqual(
                list(map(tuple, before[['residual_latency', 'residual_rps']].to_numpy())),
                list(map(tuple, after[['residual_latency', 'residual_rps']].to_numpy())),
            )
        pd.testing.assert_frame_equal(
            shuffled, permute_donor_conditions(original, 1000, 'held'))
        self.assertFalse(shuffled[['residual_latency', 'residual_rps']].equals(
            permute_donor_conditions(original, 1001, 'held')[
                ['residual_latency', 'residual_rps']]))

    def test_covariance_changes_but_mean_diagonal_does_not(self):
        held = slate()
        donor = donors(held)
        original, base_info = residual_covariance(held, donor)
        shuffled, shuffled_info = residual_covariance(
            held, permute_donor_conditions(donor, 1000, 'held'))
        self.assertEqual(base_info['aligned_donor_models'],
                         shuffled_info['aligned_donor_models'])
        self.assertFalse(np.allclose(original, shuffled))
        np.testing.assert_allclose(
            np.diagonal(original, axis1=0, axis2=1).mean(axis=1),
            np.diagonal(shuffled, axis1=0, axis2=1).mean(axis=1))

    def test_prediction_matches_original_pipeline_and_ignores_unprobed_labels(self):
        held = slate()
        donor = donors(held)
        covariance, _ = residual_covariance(held, donor)
        probes = [f'g{i}' for i in range(8)]
        predictor = ZeroPredictor()
        state = prepare_prediction(held, probes, predictor)
        own = predict_prior(state, covariance, predictor, 'check')
        original = predict_sparse(held, probes, probes, covariance,
                                  predictor, predictor)
        np.testing.assert_allclose(own.check_latency_log,
                                   original.space_cov_transfer_latency_log)
        np.testing.assert_allclose(own.check_rps_log,
                                   original.space_cov_transfer_rps_log)
        changed = held.copy()
        observed = ((changed.workload_group.isin(probes)) &
                    (changed.replica_count == 1) &
                    (changed.cpu_core_limit == 2))
        changed.loc[~observed, 'latency_p95_ms'] *= 100
        changed.loc[~observed, 'examples_per_second'] /= 100
        perturbed_state = prepare_prediction(changed, probes, predictor)
        perturbed = predict_prior(perturbed_state, covariance,
                                  predictor, 'check')
        pd.testing.assert_frame_equal(own, perturbed)


if __name__ == '__main__':
    unittest.main()
