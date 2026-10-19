"""Check SLO case accounting, measured-anchor handling, and donor isolation."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_slo_case_mix import matched_donor_rows, score_slate
from evaluate_counterfactual_slo import training_threshold_grid


def example_slate() -> pd.DataFrame:
    rows = []
    for gpu in (1, 2):
        for core in (2, 4, 8, 16):
            good = gpu == 1 and core == 2
            latency, rps = (5.0, 3.0) if good else (20.0, 1.0)
            rows.append({
                'workload_group': 'target', 'replica_count': gpu,
                'cpu_core_limit': core, 'latency_p95_ms': latency,
                'examples_per_second': rps * 2, 'batch_size': 2,
                'space_cov_transfer_latency_log': np.log(latency),
                'space_cov_transfer_rps_log': np.log(rps),
                # Ridge mistakenly approves the next-cheapest candidate.
                'space_ridge_latency_log': np.log(5.0),
                'space_ridge_rps_log': np.log(3.0),
            })
    # Ridge misses the known-good measured anchor, forcing a false approval.
    rows[0]['space_ridge_latency_log'] = np.log(20.0)
    # Neither of these other candidates meets both targets, yet each fails a
    # different target alone; this makes the dual-constraint diagnostic real.
    rows[1]['examples_per_second'] = 6.0
    rows[2]['latency_p95_ms'] = 5.0
    return pd.DataFrame(rows)


class SLOCaseMixTests(unittest.TestCase):
    def test_three_cases_and_measured_anchor(self):
        slate = example_slate()
        methods = ('space_cov_transfer', 'space_ridge')
        partial = score_slate(
            slate, {'latency_slo_ms': 10, 'target_rps': 2}, {'target'}, methods)
        self.assertEqual([row['case_bucket'] for row in partial],
                         ['partial', 'partial'])
        self.assertEqual(partial[0]['feasible_count'], 1)
        self.assertEqual(partial[0]['both_constraints_active'], 1)
        self.assertEqual((partial[0]['successful_choices'],
                          partial[0]['measured_anchor_selected_success']), (1, 1))
        self.assertEqual((partial[1]['successful_choices'],
                          partial[1]['false_approvals']), (0, 1))

        none = score_slate(
            slate, {'latency_slo_ms': 1, 'target_rps': 2}, {'target'}, methods)
        all_eight = score_slate(
            slate, {'latency_slo_ms': 100, 'target_rps': 0.1}, {'target'}, methods)
        self.assertEqual(none[0]['case_bucket'], 'none')
        self.assertEqual(all_eight[0]['case_bucket'], 'all_eight')
        self.assertEqual(all_eight[0]['feasible_count'], 8)

    def test_exact_workload_donors_come_only_from_passed_training_rows(self):
        base = {
            'workload_task': 'classification', 'batch_size': 4,
            'max_length': 128, 'request_concurrency': 2, 'repeats': 3,
        }
        donor_rows = [
            {**base, 'model_id': 'donor_a', 'latency_p95_ms': 5.,
             'examples_per_second': 20.},
            {**base, 'model_id': 'donor_b', 'latency_p95_ms': 10.,
             'examples_per_second': 40.},
            {**base, 'model_id': 'different_length', 'max_length': 256,
             'latency_p95_ms': 1000., 'examples_per_second': 1.},
        ]
        train = pd.DataFrame(donor_rows)
        key = ('classification', 4, 128, 2, 3)
        donor = matched_donor_rows(train, key)
        self.assertEqual(set(donor.model_id), {'donor_a', 'donor_b'})
        grid = training_threshold_grid(donor)
        self.assertAlmostEqual(grid[12]['latency_slo_ms'], 7.5)
        # An unmatched condition with one model cannot establish an
        # exact-workload threshold distribution.
        self.assertTrue(matched_donor_rows(
            train, ('classification', 4, 256, 2, 3)).empty)


if __name__ == '__main__':
    unittest.main()
