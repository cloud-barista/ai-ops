"""Check continuous prior and zero-external-probe evaluation boundary."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cross_model_covariance_external import continuous_prior
from cross_model_covariance import predict_sparse
from test_cross_model_covariance import ZeroPredictor, donors, slate


class ContinuousPriorTests(unittest.TestCase):
    def test_new_workload_outcomes_cannot_change_prior(self):
        main = slate()
        extra = slate().iloc[:8].copy()
        extra['campaign'] = 'new'
        extra['workload_group'] = 'new_g'
        extra['execution_id'] = 'new_' + extra.execution_id
        extra['max_length'] = 777
        joined = pd.concat((main, extra), ignore_index=True)
        training_donors = donors(main)
        first, info = continuous_prior(joined, training_donors)
        self.assertEqual(info['mode'], 'interpolated_donor_covariance_plus_kernel')
        self.assertEqual(first.shape, (7, 7, 2))
        for channel in range(2):
            self.assertGreaterEqual(np.linalg.eigvalsh(first[:, :, channel]).min(), -1e-8)
        changed = joined.copy()
        changed.loc[changed.campaign == 'new', 'latency_p95_ms'] *= 100
        changed.loc[changed.campaign == 'new', 'examples_per_second'] /= 100
        second, _ = continuous_prior(changed, training_donors)
        np.testing.assert_allclose(first, second)

    def test_new_workload_outcomes_cannot_change_predictions(self):
        main = slate()
        extra = slate().iloc[:8].copy()
        extra['campaign'] = 'new'
        extra['workload_group'] = 'new_g'
        extra['execution_id'] = 'new_' + extra.execution_id
        extra['max_length'] = 777
        joined = pd.concat((main, extra), ignore_index=True)
        prior, _ = continuous_prior(joined, donors(main))
        predictor = ZeroPredictor()
        first = predict_sparse(joined, ['g0', 'g2'], ['g0', 'g2'],
                               prior, predictor, predictor,
                               parameter_bytes=123456)
        changed = joined.copy()
        changed.loc[changed.campaign == 'new', 'latency_p95_ms'] *= 100
        changed.loc[changed.campaign == 'new', 'examples_per_second'] /= 100
        changed.loc[changed.campaign == 'new', 'result_json'] = json.dumps({
            'model_parameter_bytes': 999999,
            'performance_from_unmeasured_condition': 999999,
        })
        second = predict_sparse(changed, ['g0', 'g2'], ['g0', 'g2'],
                                prior, predictor, predictor,
                                parameter_bytes=123456)
        pd.testing.assert_frame_equal(first[first.campaign == 'new'],
                                      second[second.campaign == 'new'])


if __name__ == '__main__':
    unittest.main()
