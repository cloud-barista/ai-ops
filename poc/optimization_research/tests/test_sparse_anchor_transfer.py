"""Verify the sparse model-wide profiling information boundary."""
import json
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sparse_anchor_transfer import (add_full_anchor_reference, group_design,
                                    predict_fold_methods, scenario_metrics,
                                    select_probes, select_slo_weighted_probes,
                                    static_features)


class ConstantTwoOutput:
    def predict(self, frame):
        return np.zeros((len(frame), 2), dtype=float)


class ConstantCost:
    def predict(self, frame):
        return np.zeros(len(frame), dtype=float)


def heldout_slates():
    rows = []
    for i in range(6):
        for gpu in (1, 2):
            for cores in (2, 4, 8, 16):
                rows.append({
                    'campaign': 'test', 'model_id': 'new',
                    'workload_group': f'group_{i}',
                    'execution_id': f'execution_{i}_{gpu}_{cores}',
                    'workload_task': 'inference',
                    'batch_size': 2 ** (i % 3), 'max_length': 32 * (2 ** (i % 2)),
                    'request_concurrency': 2 ** (i % 4), 'cpu_threads': 2,
                    'cpu_core_limit': cores, 'replica_count': gpu,
                    'repeats': 8, 'latency_slo_ms': 100.0,
                    'target_throughput_rps': 2.0,
                    'latency_p95_ms': 50.0 / gpu + i,
                    'examples_per_second': 10.0 * gpu,
                    'result_json': json.dumps({'model_parameter_bytes': 123456}),
                })
    return pd.DataFrame(rows)


class SparseAnchorTransferTests(unittest.TestCase):
    def test_unprobed_outcomes_and_json_cannot_change_predictions(self):
        held = heldout_slates()
        dummy = ConstantTwoOutput()
        observed = ['group_0', 'group_2']
        original = predict_fold_methods(held, observed, dummy, dummy)
        changed = held.copy()
        unobserved = ~((changed.workload_group.isin(observed)) &
                       (changed.replica_count == 1) &
                       (changed.cpu_core_limit == 2))
        changed.loc[unobserved, 'latency_p95_ms'] *= 200
        changed.loc[unobserved, 'examples_per_second'] /= 200
        changed.loc[unobserved, 'result_json'] = json.dumps({
            'model_parameter_bytes': 123456,
            'unprobed_performance_field': 'deliberately changed',
        })
        again = predict_fold_methods(changed, observed, dummy, dummy)
        pd.testing.assert_frame_equal(original, again)

    def test_observed_anchor_outcome_changes_transfer(self):
        held = heldout_slates()
        dummy = ConstantTwoOutput()
        first = predict_fold_methods(held, ['group_0'], dummy, dummy)
        changed = held.copy()
        selected = ((changed.workload_group == 'group_0') &
                    (changed.replica_count == 1) &
                    (changed.cpu_core_limit == 2))
        changed.loc[selected, 'latency_p95_ms'] *= 2
        second = predict_fold_methods(changed, ['group_0'], dummy, dummy)
        self.assertFalse(np.allclose(first.sparse_conditional_latency_log,
                                     second.sparse_conditional_latency_log))
        np.testing.assert_allclose(first.zero_shot_latency_log,
                                   second.zero_shot_latency_log)

    def test_probe_selection_is_deterministic_and_distinct(self):
        held = heldout_slates()
        design = group_design(static_features(held, 123456))
        for method in ('medoids', 'integrated_variance', 'random'):
            a = select_probes(design, 4, method, seed=42)
            b = select_probes(design, 4, method, seed=42)
            self.assertEqual(a, b)
            self.assertEqual(len(set(a)), 4)
            self.assertTrue(set(a).issubset(set(design.index)))

    def test_slo_selection_uses_only_registered_metadata(self):
        held = heldout_slates()
        design = group_design(static_features(held, 123456))
        dummy = ConstantTwoOutput()
        original = select_slo_weighted_probes(held, design, 4, dummy, seed=42)
        changed = held.copy()
        changed['latency_p95_ms'] *= 200
        changed['examples_per_second'] /= 200
        changed['result_json'] = json.dumps({
            'model_parameter_bytes': 123456,
            'unprobed_performance_field': 'deliberately changed',
        })
        again = select_slo_weighted_probes(changed, design, 4, dummy, seed=42)
        self.assertEqual(original, again)
        cost_original = select_slo_weighted_probes(
            held, design, 4, dummy, cost_model=ConstantCost(), seed=42)
        cost_changed = select_slo_weighted_probes(
            changed, design, 4, dummy, cost_model=ConstantCost(), seed=42)
        self.assertEqual(cost_original, cost_changed)

    def test_external_outcomes_cannot_change_transfer(self):
        main = heldout_slates()
        main['campaign'] = 'main'
        external = heldout_slates()
        external['campaign'] = 'external'
        external['workload_group'] = 'external_' + external.workload_group
        external['execution_id'] = 'external_' + external.execution_id
        combined = pd.concat([main, external], ignore_index=True)
        dummy = ConstantTwoOutput()
        first = predict_fold_methods(combined, ['group_0', 'group_2'],
                                     dummy, dummy)
        changed = combined.copy()
        mask = changed.campaign == 'external'
        changed.loc[mask, 'latency_p95_ms'] *= 200
        changed.loc[mask, 'examples_per_second'] /= 200
        changed.loc[mask, 'result_json'] = json.dumps({
            'model_parameter_bytes': 123456,
            'unprobed_performance_field': 'deliberately changed',
        })
        second = predict_fold_methods(changed, ['group_0', 'group_2'],
                                      dummy, dummy)
        pd.testing.assert_frame_equal(first[first.campaign == 'external'],
                                      second[second.campaign == 'external'])

    def test_all_selectors_scored_on_same_workload_population(self):
        held = heldout_slates()
        dummy = ConstantTwoOutput()
        summaries = []
        for groups in (['group_0'], ['group_1', 'group_2']):
            predicted = predict_fold_methods(held, groups, dummy, dummy)
            predicted = add_full_anchor_reference(predicted, held, dummy, dummy)
            scores = scenario_metrics(predicted, held)
            summaries.append(scores)
            for record in scores.values():
                self.assertEqual(record['scored_groups'], 6)
                self.assertEqual(record['scored_candidates'], 42)
                self.assertEqual(record['feasible_groups'], 6)
        self.assertEqual(summaries[0]['zero_shot'], summaries[1]['zero_shot'])


if __name__ == '__main__':
    unittest.main()
