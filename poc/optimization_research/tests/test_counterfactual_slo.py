"""Guard workload grouping and fixed-threshold scoring against row-key errors."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_counterfactual_slo import (
    METHODS, pareto_summary, score_query, training_threshold_grid,
)


def two_workloads() -> pd.DataFrame:
    rows = []
    for group in ('feasible', 'impossible'):
        for gpu in (1, 2):
            for core in (2, 4, 8, 16):
                latency, rps = (20.0, 1.0)
                if group == 'feasible' and gpu == 1 and core == 2:
                    latency, rps = (20.0, 3.0)  # Latency alone fails.
                if group == 'feasible' and gpu == 1 and core == 4:
                    latency, rps = (5.0, 1.0)  # Throughput alone fails.
                if group == 'feasible' and gpu == 2 and core == 2:
                    latency, rps = (5.0, 3.0)  # Dominates all seven.
                row = {
                    'workload_group': group, 'replica_count': gpu,
                    'cpu_core_limit': core, 'latency_p95_ms': latency,
                    'examples_per_second': rps * 2, 'batch_size': 2,
                }
                for method in METHODS:
                    row[f'{method}_latency_log'] = np.log(latency)
                    row[f'{method}_rps_log'] = np.log(rps)
                rows.append(row)
    return pd.DataFrame(rows)


class CounterfactualSLOTests(unittest.TestCase):
    def test_uses_workload_group_and_both_constraints(self):
        table = two_workloads()
        info, methods = score_query(
            table, {'latency_slo_ms': 10.0, 'target_rps': 2.0})
        self.assertEqual(info, {
            'oracle_feasible': 1, 'nontrivial_feasibility': 1,
            'both_constraints_active': 1,
        })
        for method in methods:
            self.assertEqual(method['selected'], 1)
            self.assertEqual(method['successful_choices'], 1)
            self.assertEqual(method['false_approvals'], 0)

    def test_pareto_front_is_within_each_workload(self):
        summary = pareto_summary(two_workloads())
        self.assertEqual(summary['front_size_counts'], {'1': 1, '8': 1})
        self.assertEqual(summary['single_front_config_counts'],
                         {'gpu2_core2': 1})

    def test_training_threshold_grid_uses_only_passed_training_rows(self):
        train = pd.DataFrame({
            'latency_p95_ms': [1.0, 2.0, 3.0, 4.0],
            'examples_per_second': [2.0, 4.0, 6.0, 8.0],
            'batch_size': [2, 2, 2, 2],
        })
        grid = training_threshold_grid(train)
        self.assertEqual(len(grid), 25)
        self.assertAlmostEqual(
            next(x['latency_slo_ms'] for x in grid
                 if x['latency_quantile'] == 0.50), 2.5)
        self.assertAlmostEqual(
            next(x['target_rps'] for x in grid
                 if x['rps_quantile'] == 0.50), 2.5)


if __name__ == '__main__':
    unittest.main()
