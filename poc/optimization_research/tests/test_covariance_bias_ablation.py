"""Check residual decomposition and the held-out outcome boundary."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from covariance_bias_ablation import decomposed_prior, predict_ablation
from cross_model_covariance import RHO
from test_cross_model_covariance import ZeroPredictor, donors, slate


class CovarianceBiasAblationTests(unittest.TestCase):
    def test_second_moment_decomposes_into_centered_covariance_and_mean(self):
        held = slate()
        priors, mean, info = decomposed_prior(held, donors(held))
        self.assertEqual(info['mode'], 'aligned_empirical_plus_kernel')
        np.testing.assert_allclose(
            priors['raw'], priors['centered'] +
            (1.0 - RHO) * np.einsum('io,jo->ijo', mean, mean)
        )
        self.assertGreater(float(np.abs(mean).sum()), 0)

    def test_unaligned_task_has_identical_zero_mean_priors(self):
        held = slate('causal_lm_forward')
        priors, mean, info = decomposed_prior(held, donors(slate()))
        self.assertEqual(info['mode'], 'kernel_fallback_no_aligned_donors')
        np.testing.assert_allclose(mean, 0)
        np.testing.assert_allclose(priors['raw'], priors['centered'])
        np.testing.assert_allclose(priors['raw'], priors['smooth'])

    def test_unprobed_target_outcomes_cannot_change_predictions(self):
        held = slate()
        training_donors = donors(held)
        predictor = ZeroPredictor()
        probes = ['g0', 'g2']
        first, _ = predict_ablation(
            held, probes, training_donors, predictor, predictor,
            known_parameter_bytes=123456,
        )
        changed = held.copy()
        measured = (changed.workload_group.isin(probes) &
                    changed.replica_count.eq(1) & changed.cpu_core_limit.eq(2))
        changed.loc[~measured, 'latency_p95_ms'] *= 100
        changed.loc[~measured, 'examples_per_second'] /= 100
        changed.loc[~measured, 'result_json'] = json.dumps({
            'model_parameter_bytes': 987654,
            'unmeasured_private_performance': 1e12,
        })
        second, _ = predict_ablation(
            changed, probes, training_donors, predictor, predictor,
            known_parameter_bytes=123456,
        )
        pd.testing.assert_frame_equal(first, second)


if __name__ == '__main__':
    unittest.main()
