"""Check resource-order regret definitions on hand-built complete slates."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_resource_regret import METHODS, score_threshold


def hand_built_slates() -> pd.DataFrame:
    rows = []
    for workload in ('gpu1_possible', 'gpu2_needed', 'infeasible'):
        for gpu in (1, 2):
            for core in (2, 4, 8, 16):
                actual_pass = ((workload == 'gpu1_possible' and
                                (gpu, core) in {(1, 4), (1, 16), (2, 2)}) or
                               (workload == 'gpu2_needed' and
                                (gpu, core) in {(2, 4), (2, 16)}))
                row = {
                    'workload_group': workload, 'replica_count': gpu,
                    'cpu_core_limit': core,
                    'latency_p95_ms': 5.0 if actual_pass else 20.0,
                    'examples_per_second': 6.0 if actual_pass else 2.0,
                    'batch_size': 2,
                }
                for method in METHODS:
                    predicted_pass = {
                        'space_cov_transfer':
                            (workload == 'gpu1_possible' and (gpu, core) == (1, 2)) or
                            (workload == 'gpu2_needed' and (gpu, core) == (2, 16)) or
                            (workload == 'infeasible' and (gpu, core) == (1, 2)),
                        'space_kernel_transfer':
                            workload == 'gpu1_possible' and (gpu, core) == (2, 2),
                        'space_ridge':
                            (workload == 'gpu1_possible' and (gpu, core) == (1, 4)) or
                            (workload == 'gpu2_needed' and (gpu, core) == (2, 4)),
                        'space_global_mean':
                            workload == 'gpu1_possible' and (gpu, core) == (1, 16),
                    }[method]
                    row[f'{method}_latency_log'] = np.log(
                        5.0 if predicted_pass else 20.0)
                    row[f'{method}_rps_log'] = np.log(
                        3.0 if predicted_pass else 1.0)
                rows.append(row)
    return pd.DataFrame(rows)


class ResourceRegretTests(unittest.TestCase):
    def test_oracle_denominators_and_false_approval(self):
        rows = score_threshold(hand_built_slates(),
                               {'latency_slo_ms': 10, 'target_rps': 2})
        by_method = {row['method']: row for row in rows}
        cov = by_method['space_cov_transfer']
        self.assertEqual(cov['workload_queries'], 3)
        self.assertEqual(cov['oracle_feasible'], 2)
        self.assertEqual(cov['oracle_gpu1_feasible'], 1)
        self.assertEqual(cov['selected'], 3)
        self.assertEqual(cov['successful_choices'], 1)
        self.assertEqual(cov['false_approvals'], 2)
        self.assertEqual(cov['missed_feasible'], 1)
        self.assertEqual(cov['oracle_cost_matches'], 0)
        self.assertEqual(cov['extra_cpu_cores_same_gpu_on_success'], 12)
        self.assertEqual(cov['successful_but_extra_core_same_gpu'], 1)

    def test_avoidable_gpu2_and_oracle_cost_match(self):
        rows = score_threshold(hand_built_slates(),
                               {'latency_slo_ms': 10, 'target_rps': 2})
        by_method = {row['method']: row for row in rows}
        kernel = by_method['space_kernel_transfer']
        self.assertEqual(kernel['successful_choices'], 1)
        self.assertEqual(kernel['avoidable_gpu2_recommendations'], 1)
        self.assertEqual(kernel['avoidable_gpu2_successes'], 1)
        self.assertEqual(kernel['extra_gpu_units_on_success'], 1)
        self.assertEqual(kernel['oracle_cost_matches'], 0)
        ridge = by_method['space_ridge']
        self.assertEqual(ridge['successful_choices'], 2)
        self.assertEqual(ridge['oracle_cost_matches'], 2)
        self.assertEqual(ridge['avoidable_gpu2_recommendations'], 0)
        global_mean = by_method['space_global_mean']
        self.assertEqual(global_mean['extra_cpu_cores_same_gpu_on_success'], 12)

    def test_rejects_incomplete_or_duplicate_resource_slate(self):
        frame = hand_built_slates()
        with self.assertRaises(ValueError):
            score_threshold(frame.iloc[1:],
                            {'latency_slo_ms': 10, 'target_rps': 2})
        copy = frame.copy()
        copy.loc[0, 'cpu_core_limit'] = 4
        with self.assertRaises(ValueError):
            score_threshold(copy, {'latency_slo_ms': 10, 'target_rps': 2})


if __name__ == '__main__':
    unittest.main()
