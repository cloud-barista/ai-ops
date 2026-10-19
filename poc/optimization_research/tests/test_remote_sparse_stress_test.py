"""Ensure remote resampling keeps fixed complete resource slates."""
import sys
from pathlib import Path
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from remote_sparse_stress_test import first_complete_remote_slate


def remote_fixture():
    rows = []
    for model in ('new_a', 'new_b'):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                group = model + '_condition'
                rows.append({
                    'campaign': 'khu-hf-remote-216', 'eligible': True,
                    'run_status': 'complete', 'device': 'cuda',
                    'replica_count': gpu, 'cpu_core_limit': cores,
                    'started_at': '2026-01-01T00:00:01Z',
                    'execution_id': f'{model}_{gpu}_{cores}',
                    'workload_group': group, 'model_id': model,
                    'batch_size': 1, 'max_length': 64,
                    'request_concurrency': 1, 'latency_slo_ms': 100,
                    'target_throughput_rps': 1, 'host': 'remote',
                    'device_name': 'new GPU', 'latency_p95_ms': 20,
                    'examples_per_second': 10, 'wall_seconds': 2,
                })
    first = rows[0].copy()
    first.update({'execution_id': 'later_duplicate',
                  'started_at': '2026-01-01T00:00:02Z',
                  'latency_p95_ms': 100,
                  'examples_per_second': 2, 'wall_seconds': 10})
    rows.insert(0, first)
    return pd.DataFrame(rows)


class RemoteSlateTests(unittest.TestCase):
    def test_first_valid_and_median_are_deterministic(self):
        rows = remote_fixture()
        first, detail = first_complete_remote_slate(rows, 'first')
        median, _ = first_complete_remote_slate(rows, 'median')
        self.assertEqual(detail['repeated_rows_removed'], 1)
        self.assertEqual(len(first), 16)
        cell = (first.model_id == 'new_a') & (first.replica_count == 1) & (first.cpu_core_limit == 2)
        self.assertEqual(first.loc[cell, 'latency_p95_ms'].iloc[0], 20)
        self.assertEqual(median.loc[cell, 'latency_p95_ms'].iloc[0], 60)
        self.assertEqual(median.loc[cell, 'examples_per_second'].iloc[0], 6)
        self.assertEqual(median.loc[cell, 'wall_seconds'].iloc[0], 6)

    def test_missing_resource_cell_is_rejected(self):
        rows = remote_fixture()
        rows = rows[~((rows.model_id == 'new_b') &
                      (rows.replica_count == 2) &
                      (rows.cpu_core_limit == 16))]
        with self.assertRaisesRegex(ValueError, 'Incomplete or duplicated slate'):
            first_complete_remote_slate(rows)


if __name__ == '__main__':
    unittest.main()
