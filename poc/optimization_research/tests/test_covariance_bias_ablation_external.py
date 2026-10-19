"""Check external-condition prior decomposition and zero-probe boundary."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from covariance_bias_ablation_external import (
    decomposed_continuous_prior, predict_external,
)
from cross_model_covariance import RHO
from test_cross_model_covariance import ZeroPredictor, donors, slate


def main_and_new():
    main = slate()
    extra = slate().iloc[:8].copy()
    extra['campaign'] = 'new'
    extra['workload_group'] = 'new_g'
    extra['execution_id'] = 'new_' + extra.execution_id
    extra['max_length'] = 777
    return main, pd.concat((main, extra), ignore_index=True)


class ExternalBiasAblationTests(unittest.TestCase):
    def test_interpolated_second_moment_decomposes(self):
        main, all_test = main_and_new()
        priors, mean, info = decomposed_continuous_prior(
            all_test, donors(main)
        )
        self.assertEqual(info['mode'], 'interpolated_donor_covariance_plus_kernel')
        np.testing.assert_allclose(
            priors['raw'], priors['centered'] +
            (1.0 - RHO) * np.einsum('io,jo->ijo', mean, mean)
        )

    def test_external_outcomes_cannot_change_predictions(self):
        main, all_test = main_and_new()
        priors, mean, _ = decomposed_continuous_prior(
            all_test, donors(main)
        )
        zero = ZeroPredictor()
        probes = ['g0', 'g2']
        first = predict_external(
            all_test, probes, priors, mean, zero, zero, 123456,
        )
        changed = all_test.copy()
        external = changed.campaign.eq('new')
        changed.loc[external, 'latency_p95_ms'] *= 100
        changed.loc[external, 'examples_per_second'] /= 100
        changed.loc[external, 'result_json'] = json.dumps({
            'model_parameter_bytes': 987654,
            'private_external_outcome': 1e12,
        })
        second = predict_external(
            changed, probes, priors, mean, zero, zero, 123456,
        )
        pd.testing.assert_frame_equal(first[first.campaign == 'new'],
                                      second[second.campaign == 'new'])


if __name__ == '__main__':
    unittest.main()
