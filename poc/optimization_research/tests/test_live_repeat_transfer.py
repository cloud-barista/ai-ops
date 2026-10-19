"""Leakage and limited-decision checks for repeated live stress evaluation."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from live_repeat_transfer import (METHODS, OUTCOMES, live_truth_and_static,
                                  mask_heldout, pair_decisions)


class LiveRepeatTransferTest(unittest.TestCase):
    def test_outcomes_removed_from_all_synthetic_resource_rows(self):
        records = []
        for round_id in range(47):
            for gpu in (1, 2):
                records.append({
                    'campaign': 'khu-hf-live', 'model_id': 'm',
                    'workload_group': 'w', 'workload_task': 'inference',
                    'batch_size': 4, 'max_length': 128,
                    'request_concurrency': 2, 'repeats': 8,
                    'replica_count': gpu, 'cpu_core_limit': 2,
                    'cpu_threads': 2, 'latency_slo_ms': 500,
                    'target_throughput_rps': 10,
                    'run_id': f'round-{round_id}',
                    'execution_id': f'raw-{round_id}-{gpu}',
                    'latency_p95_ms': 100 + round_id + gpu,
                    'examples_per_second': 40 + round_id + gpu,
                    'result_json': '{"model_parameter_bytes": 10}',
                    'wall_seconds': 1.0, 'compute_seconds': 0.5,
                })
        truth, static = live_truth_and_static(pd.DataFrame(records))
        self.assertEqual(len(truth), 2)
        self.assertEqual(len(static), 8)
        self.assertTrue(static[list(OUTCOMES)].isna().all().all())
        self.assertEqual(set(static.cpu_core_limit), {2, 4, 8, 16})

    def test_only_selected_main_anchor_remains_visible(self):
        rows = pd.DataFrame([
            {'workload_group': 'a', 'replica_count': 1, 'cpu_core_limit': 2},
            {'workload_group': 'a', 'replica_count': 2, 'cpu_core_limit': 2},
            {'workload_group': 'b', 'replica_count': 1, 'cpu_core_limit': 2},
        ])
        for name in OUTCOMES:
            rows[name] = 'x' if name == 'result_json' else 1.0
        hidden = mask_heldout(rows, ['a'])
        self.assertEqual(int(hidden[list(OUTCOMES)].notna().all(axis=1).sum()), 1)
        self.assertTrue(hidden.loc[0, list(OUTCOMES)].notna().all())
        self.assertTrue(hidden.loc[1:, list(OUTCOMES)].isna().all().all())

    def test_pair_decision_uses_only_two_measured_gpu_options(self):
        rows = pd.DataFrame([
            {'model_id': 'm', 'workload_group': 'paired',
             'execution_id': 'one', 'replica_count': 1,
             'latency_p95_ms': 110, 'examples_per_second': 20,
             'batch_size': 1, 'latency_slo_ms': 100,
             'target_throughput_rps': 30},
            {'model_id': 'm', 'workload_group': 'paired',
             'execution_id': 'two', 'replica_count': 2,
             'latency_p95_ms': 90, 'examples_per_second': 40,
             'batch_size': 1, 'latency_slo_ms': 100,
             'target_throughput_rps': 30},
        ])
        for method in METHODS:
            rows[f'{method}_latency_log'] = np.log([110, 90])
            rows[f'{method}_rps_log'] = np.log([20, 40])
        decision = pair_decisions(rows, rows)
        self.assertEqual(len(decision), len(METHODS))
        self.assertEqual(set(decision.oracle_gpu), {2})
        self.assertEqual(set(decision.selected_gpu), {2})
        self.assertTrue(decision.chosen_truly_feasible.all())


if __name__ == '__main__':
    unittest.main()
