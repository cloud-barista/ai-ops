"""Check family exclusion and GPU2 outcome masking for telemetry transfer."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from family_holdout_transfer import FAMILY
from telemetry_family_holdout import (HIDDEN_GPU2_COLUMNS, METHODS,
                                      decision_rows, hide_gpu2_outcomes)


class TelemetryFamilyHoldoutTest(unittest.TestCase):
    def test_registered_family_ids_are_distinct(self):
        self.assertEqual(len(FAMILY), 4)
        self.assertEqual(len(set(FAMILY)), 4)

    def test_gpu2_outcomes_are_hidden_but_current_telemetry_remains(self):
        row = {'replica_count': 2, 'cpu_core_limit': 2,
               'anchor_log_latency': np.log(100),
               'anchor_log_requests_per_second': np.log(20),
               'anchor_log1p_gpu_utilization_percent_mean': np.log1p(10)}
        row.update({name: 1.0 for name in HIDDEN_GPU2_COLUMNS})
        hidden = hide_gpu2_outcomes(pd.DataFrame([row]))
        self.assertTrue(hidden[list(HIDDEN_GPU2_COLUMNS)].isna().all().all())
        self.assertEqual(hidden.anchor_log_latency.iloc[0], row['anchor_log_latency'])
        self.assertEqual(hidden.anchor_log1p_gpu_utilization_percent_mean.iloc[0],
                         row['anchor_log1p_gpu_utilization_percent_mean'])
        with self.assertRaises(ValueError):
            hide_gpu2_outcomes(pd.DataFrame([{**row, 'replica_count': 1}]))

    def test_synthetic_decision_counts_only_beneficial_scale(self):
        row = {'anchor_log_latency': np.log(120),
               'anchor_log_requests_per_second': np.log(10),
               'true_log_latency': np.log(90),
               'true_log_requests_per_second': np.log(30)}
        for method in METHODS:
            row[f'{method}_latency_log'] = np.log(90)
            row[f'{method}_rps_log'] = np.log(30)
        records = decision_rows(pd.DataFrame([row]), np.array([np.log(100)]),
                                np.array([np.log(20)]))
        self.assertEqual(len(records), len(METHODS))
        for item in records:
            self.assertEqual(item['at_risk'], 1)
            self.assertEqual(item['beneficial_scale'], 1)
            self.assertEqual(item['successful_scale'], 1)
            self.assertEqual(item['false_scale_approval'], 0)


if __name__ == '__main__':
    unittest.main()
