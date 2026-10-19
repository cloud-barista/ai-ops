"""Check that nested calibration cannot read held-out candidate outcomes."""
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from conditional_response import prepare
from crossfit_anchor_calibration import predict_outer


def toy_slates():
    rows = []
    for model_index, model in enumerate(['a', 'b', 'c', 'unseen']):
        for workload in [0, 1]:
            for gpu in [1, 2]:
                for cores in [2, 4, 8, 16]:
                    latency = (40 + 7 * model_index + 5 * workload) / gpu * (1 + 2 / cores)
                    rps = (12 + 3 * model_index + workload) * gpu * (1 + cores / 100)
                    rows.append({
                        'eligible': True, 'device': 'cuda', 'campaign': 'test',
                        'model_id': model, 'workload_group': f'{model}-{workload}',
                        'execution_id': f'{model}-{workload}-{gpu}-{cores}',
                        'replica_count': gpu, 'cpu_core_limit': cores,
                        'batch_size': 2, 'max_length': 128 + 64 * workload,
                        'request_concurrency': 1 + workload, 'repeats': 8,
                        'workload_task': 'inference', 'latency_slo_ms': 100.0,
                        'target_throughput_rps': 5.0,
                        'latency_p95_ms': latency,
                        'examples_per_second': 2 * rps,
                        'result_json': json.dumps({'model_parameter_bytes': 1000 * (model_index + 1)}),
                        'compute_seconds': 1.0,
                    })
    return pd.DataFrame(rows)


class CrossfitAnchorCalibrationTests(unittest.TestCase):
    def test_heldout_nonanchor_outcomes_do_not_change_predictions(self):
        raw = toy_slates()
        training = prepare(raw[raw.model_id != 'unseen'])
        heldout = raw[raw.model_id == 'unseen'].copy()
        changed = heldout.copy()
        nonanchor = ~((changed.replica_count == 1) & (changed.cpu_core_limit == 2))
        changed.loc[nonanchor, 'latency_p95_ms'] *= 10
        changed.loc[nonanchor, 'examples_per_second'] /= 10
        # Smaller deterministic forests keep this leakage test fast while
        # exercising the full outer and inner split/prediction path.
        with patch('crossfit_anchor_calibration.make_regressor',
                   side_effect=lambda: ExtraTreesRegressor(
                       n_estimators=5, min_samples_leaf=2, random_state=42)):
            first = predict_outer(training, prepare(heldout))
            second = predict_outer(training, prepare(changed))
        keys = ['method', 'execution_id']
        values = ['predicted_log_latency', 'predicted_log_requests_per_second']
        a = first.sort_values(keys).reset_index(drop=True)
        b = second.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(a[keys + values], b[keys + values])
        self.assertTrue(np.isfinite(a[values].to_numpy()).all())


if __name__ == '__main__':
    unittest.main()
