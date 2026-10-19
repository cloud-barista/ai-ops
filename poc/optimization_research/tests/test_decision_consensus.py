"""Selection rules must use frozen predictions before seeing target outcomes."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_decision_consensus import (
    METHODS, conservative_prediction, dominators, evaluate_scenario,
    ordered_slate, paired_model_comparison, score_selection, select,
)


def example_slate() -> pd.DataFrame:
    rows = []
    for group in ('a', 'b'):
        for gpu in (1, 2):
            for core in (2, 4, 8, 16):
                cheap = gpu == 1 and core == 2
                row = {
                    'workload_group': group,
                    'replica_count': gpu,
                    'cpu_core_limit': core,
                    'latency_p95_ms': 5.0 if not cheap else 50.0,
                    'examples_per_second': 6.0,
                    'batch_size': 2,
                }
                for method in set(sum((list(x) for x in METHODS.values()), [])):
                    row[f'{method}_latency_log'] = np.log(8.0)
                    row[f'{method}_rps_log'] = np.log(3.0)
                # Kernel rejects the cheap candidate; consensus must do so.
                if cheap:
                    row['space_kernel_transfer_latency_log'] = np.log(12.0)
                rows.append(row)
    return pd.DataFrame(rows)


class DecisionConsensusTests(unittest.TestCase):
    def test_agreement_selects_cheapest_jointly_approved_candidate(self):
        table = ordered_slate(example_slate())
        single = conservative_prediction(table, METHODS['covariance'])
        joint = conservative_prediction(table, METHODS['covariance_and_kernel'])
        self.assertEqual(select(single, 2, 10, 2, 0).tolist(), [0, 0])
        self.assertEqual(select(joint, 2, 10, 2, 0).tolist(), [1, 1])
        self.assertEqual(select(joint, 2, 10, 2, 0.3).tolist(), [-1, -1])

    def test_truth_affects_score_but_cannot_affect_choice(self):
        table = ordered_slate(example_slate())
        known = conservative_prediction(table, METHODS['all_three'])
        chosen = select(known, 2, 10, 2, 0)
        before = score_selection(chosen, np.ones((2, 8), dtype=bool))
        after = score_selection(chosen, np.zeros((2, 8), dtype=bool))
        self.assertEqual(chosen.tolist(), [1, 1])
        self.assertEqual(before['selected'], after['selected'])
        self.assertEqual(before['successful_choices'], 2)
        self.assertEqual(after['false_approvals'], 2)

    def test_scenario_rejects_duplicated_or_incomplete_slates(self):
        table = example_slate()
        with self.assertRaisesRegex(ValueError, 'eight candidates'):
            evaluate_scenario(table.iloc[:-1],
                              {'latency_slo_ms': 10, 'target_rps': 2})
        duplicate = table.copy()
        duplicate.loc[0, 'cpu_core_limit'] = 4
        with self.assertRaisesRegex(ValueError, 'Repeated candidate'):
            ordered_slate(duplicate)

    def test_consensus_and_single_margin_dominance(self):
        aggregate = pd.DataFrame([
            {'policy': 'all_three', 'margin_log': 0.0,
             'successful_choices': 9, 'false_approvals': 2},
            {'policy': 'covariance', 'margin_log': 0.05,
             'successful_choices': 10, 'false_approvals': 1},
            {'policy': 'ridge', 'margin_log': 0.0,
             'successful_choices': 8, 'false_approvals': 1},
        ])
        result = dominators(aggregate)
        self.assertTrue(result[0]['dominated_by_single_fixed_margin'])
        self.assertEqual(result[0]['dominating_single_policies'][0]['policy'],
                         'covariance')

    def test_paired_comparison_uses_models_as_units(self):
        rows = []
        for i in range(9):
            for policy, margin, success, false in (
                    ('all_three', 0.0, 10 + i, 2),
                    ('covariance', 0.05, 9 + i, 3)):
                rows.append({
                    'heldout_model_id': f'model_{i}', 'policy': policy,
                    'margin_log': margin, 'selected': success + false,
                    'successful_choices': success, 'false_approvals': false,
                })
        comparison = paired_model_comparison(
            pd.DataFrame(rows), ('all_three', 0.0), ('covariance', 0.05))
        self.assertEqual(comparison['successful_choices']['difference_total'], 9)
        self.assertEqual(comparison['false_approvals']['difference_total'], -9)
        self.assertEqual(comparison['successful_choices'][
            'descriptive_model_bootstrap_95_interval_for_mean'], [1.0, 1.0])


if __name__ == '__main__':
    unittest.main()
