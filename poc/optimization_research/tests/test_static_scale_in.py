import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from evaluate_static_scale_in import (MAIN, METHODS, STATIC_FEATURES,
                                      TELEMETRY_FEATURES, decision_counts,
                                      predict_fold, prepare_pairs, select_rule)


def measurement(replica: int, latency: float, eps: float, *,
                telemetry: float = 10.0, batch: int = 2) -> dict:
    return {
        'campaign': MAIN, 'eligible': True, 'success': 1, 'device': 'cuda',
        'cpu_core_limit': 2, 'replica_count': replica,
        'workload_group': 'same-workload', 'execution_id': f'run-{replica}',
        'started_at': f'2026-01-01T00:00:0{replica}Z',
        'model_id': 'test/model', 'model_revision': 'abc', 'batch_size': batch,
        'max_length': 128, 'request_concurrency': 1, 'dataset': 'operations',
        'workload_task': 'inference', 'target_throughput_rps': 40.0,
        'latency_slo_ms': 10.0, 'latency_p95_ms': latency,
        'examples_per_second': eps,
        'result_json': json.dumps({'model_parameter_bytes': 1_000_000}),
        'node_summary_json': json.dumps({
            'gpu_utilization_percent_mean': telemetry,
            'gpu_utilization_percent_max': telemetry,
            'gpu_memory_used_mib_max': telemetry,
            'cpu_percent_mean': telemetry,
        }),
    }


class StaticScaleInTests(unittest.TestCase):
    def test_target_outcome_and_telemetry_cannot_enter_features(self):
        raw = pd.DataFrame([measurement(2, 3, 200), measurement(1, 6, 100)])
        first = prepare_pairs(raw, MAIN)
        changed = raw.copy()
        changed.loc[changed.replica_count == 1, 'latency_p95_ms'] = 999
        changed.loc[changed.replica_count == 1, 'examples_per_second'] = 1
        changed.loc[changed.replica_count == 1, 'node_summary_json'] = '{}'
        changed.loc[changed.replica_count == 1, 'result_json'] = '{}'
        second = prepare_pairs(changed, MAIN)
        np.testing.assert_array_equal(first[list(STATIC_FEATURES) +
                                          list(TELEMETRY_FEATURES)].to_numpy(),
                                      second[list(STATIC_FEATURES) +
                                             list(TELEMETRY_FEATURES)].to_numpy())
        self.assertNotEqual(first.target_log_latency.iloc[0],
                            second.target_log_latency.iloc[0])
        self.assertAlmostEqual(first.current_log_rps.iloc[0], np.log(100))
        self.assertAlmostEqual(first.target_log_rps.iloc[0], np.log(50))

    def test_mismatched_pair_is_rejected(self):
        raw = pd.DataFrame([measurement(2, 3, 200), measurement(1, 6, 100, batch=3)])
        with self.assertRaisesRegex(ValueError, 'batch_size'):
            prepare_pairs(raw, MAIN)

    def test_decision_requires_both_constraints_and_current_eligibility(self):
        frame = pd.DataFrame({
            'current_log_latency': np.log([3, 11, 3]),
            'current_log_rps': np.log([100, 100, 100]),
            'target_log_latency': np.log([9, 5, 11]),
            'target_log_rps': np.log([50, 50, 50]),
            'log_latency_slo': np.log([10, 10, 10]),
            'log_rps_min': np.log([40, 40, 40]),
        })
        result = decision_counts(frame, np.log([9, 5, 9]), np.log([50, 50, 50]), 0)
        self.assertEqual((result['eligible'], result['possible_safe'],
                          result['approved'], result['safe_approved'],
                          result['false_approved']), (2, 1, 2, 1, 1))

    def test_rule_selection_respects_donor_false_fraction(self):
        # All four current shapes look safe under the naive ratio. One target
        # fails, so the 25% observed false rate must be rejected.
        frame = pd.DataFrame({
            'current_log_latency': np.log([1, 1, 1, 1]),
            'current_log_rps': np.log([100, 100, 100, 100]),
            'target_log_latency': np.log([2, 2, 2, 20]),
            'target_log_rps': np.log([40, 40, 40, 40]),
            'log_latency_slo': np.log([10, 10, 10, 10]),
            'log_rps_min': np.log([30, 30, 30, 30]),
        })
        rule = select_rule(frame, 'ratio')
        self.assertLessEqual(rule['false_fraction'], 0.05)
        self.assertEqual(rule['false_approved'], 0)

    def test_held_out_gpu1_outcome_cannot_change_prediction_or_rule(self):
        rows = []
        for i, model_id in enumerate(('donor-a', 'donor-b', 'donor-c', 'held')):
            raw = pd.DataFrame([measurement(2, 3 + i / 10, 200 + i),
                                measurement(1, 6 + i / 10, 100 + i)])
            raw['workload_group'] = f'workload-{i}'
            raw['model_id'] = model_id
            pair = prepare_pairs(raw, MAIN)
            rows.append(pair)
        train = pd.concat(rows[:3], ignore_index=True)
        held = rows[3]
        original, rules = predict_fold(train, [held], 'held')
        changed = held.copy()
        changed['target_log_latency'] = np.log(999.0)
        changed['target_log_rps'] = np.log(1.0)
        changed['delta_latency'] = changed.target_log_latency - changed.current_log_latency
        changed['delta_rps'] = changed.target_log_rps - changed.current_log_rps
        altered, altered_rules = predict_fold(train, [changed], 'held')
        self.assertEqual(rules, altered_rules)
        for method in METHODS:
            np.testing.assert_allclose(original[f'{method}_log_latency'],
                                       altered[f'{method}_log_latency'])
            np.testing.assert_allclose(original[f'{method}_log_rps'],
                                       altered[f'{method}_log_rps'])
            np.testing.assert_array_equal(original[f'{method}_approve'],
                                          altered[f'{method}_approve'])


if __name__ == '__main__':
    unittest.main()
