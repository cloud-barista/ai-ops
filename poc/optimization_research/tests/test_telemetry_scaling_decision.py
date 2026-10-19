import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from evaluate_telemetry_scaling_decision import donor_thresholds, score_group


class TelemetryScalingDecisionTests(unittest.TestCase):
    def test_thresholds_ignore_heldout_outcomes(self):
        frame = pd.DataFrame({
            'model_id': ['held', 'donor_a', 'donor_b'],
            'true_log_latency': [100., 1., 3.],
            'true_log_requests_per_second': [-100., 2., 4.],
        })
        before = donor_thresholds(frame, 'held')
        frame.loc[0, ['true_log_latency', 'true_log_requests_per_second']] = [1e9, -1e9]
        after = donor_thresholds(frame, 'held')
        for a, b in zip(before, after):
            np.testing.assert_allclose(a, b)

    def test_false_scale_and_missed_beneficial_are_distinct(self):
        frame = pd.DataFrame({
            'anchor_log_latency': [3., 3.],
            'anchor_log_requests_per_second': [0., 0.],
            'true_log_latency': [1., 3.],
            'true_log_requests_per_second': [2., 0.],
        })
        for name in ('baseline', 'telemetry', 'gpu_telemetry',
                     'no_count', 'count_only'):
            frame[f'{name}_latency_log'] = [1., 1.]
            frame[f'{name}_rps_log'] = [2., 2.]
        rows = score_group(frame, np.array([2.]), np.array([1.]))
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertEqual(row['beneficial_scale'], 1)
            self.assertEqual(row['successful_scale'], 1)
            self.assertEqual(row['false_scale_approval'], 1)
            self.assertEqual(row['missed_beneficial_scale'], 0)


if __name__ == '__main__':
    unittest.main()
