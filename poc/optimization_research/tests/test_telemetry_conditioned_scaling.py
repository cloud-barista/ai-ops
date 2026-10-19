import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from conditional_response import CONDITIONAL_FEATURES
from telemetry_conditioned_scaling import (TELEMETRY_FEATURES,
                                           add_anchor_telemetry,
                                           predict_telemetry)


def summary(value: float) -> str:
    return json.dumps({
        'gpu_utilization_percent_mean': value,
        'gpu_utilization_percent_max': value,
        'gpu_memory_used_mib_max': value,
        'cpu_percent_mean': value,
        'running_sample_count': value,
    })


class TelemetryConditioningTests(unittest.TestCase):
    def test_nonanchor_telemetry_cannot_enter_features(self):
        frame = pd.DataFrame({
            'workload_group': ['a', 'a', 'b', 'b'],
            'replica_count': [1, 2, 1, 2],
            'cpu_core_limit': [2, 2, 2, 2],
            'node_summary_json': [summary(1), summary(1000),
                                  summary(2), summary(2000)],
        })
        first = add_anchor_telemetry(frame)
        changed = frame.copy()
        changed.loc[changed.replica_count == 2, 'node_summary_json'] = summary(1e7)
        second = add_anchor_telemetry(changed)
        np.testing.assert_array_equal(first[list(TELEMETRY_FEATURES)],
                                      second[list(TELEMETRY_FEATURES)])
        self.assertAlmostEqual(first.loc[1, TELEMETRY_FEATURES[0]], np.log(2))
        self.assertAlmostEqual(first.loc[3, TELEMETRY_FEATURES[0]], np.log(3))

    def test_invalid_anchor_telemetry_is_rejected(self):
        frame = pd.DataFrame({
            'workload_group': ['a'], 'replica_count': [1],
            'cpu_core_limit': [2], 'node_summary_json': [summary(-1)],
        })
        with self.assertRaises(ValueError):
            add_anchor_telemetry(frame)

    def test_prediction_uses_current_anchor_and_replica_prior(self):
        class Constant:
            def predict(self, features):
                return np.zeros((len(features), 2))

        frame = pd.DataFrame({
            'anchor_log_latency': [1.2],
            'anchor_log_requests_per_second': [2.3],
            'prior_log_throughput_gain': [np.log(2)],
        })
        for feature in list(CONDITIONAL_FEATURES) + list(TELEMETRY_FEATURES):
            if feature not in frame:
                frame[feature] = 0.0
        predicted = predict_telemetry(Constant(), frame)
        np.testing.assert_allclose(predicted[0], [1.2, 2.3 + np.log(2)])


if __name__ == '__main__':
    unittest.main()
