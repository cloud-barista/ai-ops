"""Behavioral checks for the zero-probe policy's isolation and selection."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cold_start_policy import (COVERAGES, DIRECT, STATIC, evaluate_scores,
                               expand_direct_features, fit_predict, fold_partitions,
                               select_at_coverage)


class ColdStartPolicyTest(unittest.TestCase):
    def test_direct_features_ignore_outcomes_and_identity(self):
        row = {feature: 1.0 for feature in STATIC}
        frame = pd.DataFrame([row, row])
        scenarios = [{'latency_slo_ms': 10.0, 'target_rps': 20.0},
                     {'latency_slo_ms': 20.0, 'target_rps': 40.0}]
        first = expand_direct_features(frame, scenarios)
        frame['model_id'] = ['a', 'b']
        frame['latency_p95_ms'] = [1.0, 9999.0]
        frame['examples_per_second'] = [9999.0, 1.0]
        second = expand_direct_features(frame, scenarios)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(first.shape, (4, len(DIRECT)))
        np.testing.assert_allclose(first[:2, -2:],
                                   np.tile(np.log([10, 20]), (2, 1)), rtol=1e-6)
        np.testing.assert_allclose(first[2:, -2:],
                                   np.tile(np.log([20, 40]), (2, 1)), rtol=1e-6)

    def test_exact_coverage_uses_predictions_only(self):
        scores = np.array([[.9] * 8, [.2] * 8, [.8] * 8, [.4] * 8])
        chosen = select_at_coverage(scores, .5, .5)
        self.assertEqual(chosen.tolist(), [0, -1, 0, -1])
        natural = select_at_coverage(scores, None, .5)
        self.assertEqual(natural.tolist(), [0, -1, 0, -1])
        self.assertEqual(COVERAGES, (.25, .5, .75))

    def test_matched_selection_and_minimal_resource(self):
        rows = []
        for group, feasible_rank in [('g1', 4), ('g2', -1)]:
            for gpu in (1, 2):
                for cores in (2, 4, 8, 16):
                    rank = 4 * (gpu - 1) + (2, 4, 8, 16).index(cores)
                    good = group == 'g1' and rank >= feasible_rank
                    rows.append({
                        'workload_group': group, 'model_id': 'held',
                        'execution_id': f'{group}-{rank}', 'replica_count': gpu,
                        'cpu_core_limit': cores, 'batch_size': 1,
                        'max_length': 32, 'request_concurrency': 1,
                        'latency_slo_ms': 10, 'target_throughput_rps': 10,
                        'true_log_latency': np.log(1 if good else 100),
                        'true_log_rps': np.log(100 if good else 1),
                    })
        frame = pd.DataFrame(rows).sample(frac=1, random_state=12)
        threshold = [{'latency_slo_ms': 10, 'target_rps': 10}]
        direct = np.zeros((1, 16))
        for i, row in enumerate(frame.itertuples()):
            direct[0, i] = .9 if row.workload_group == 'g1' and row.replica_count == 2 else .1
        scores = {'direct_classifier': direct,
                  'extra_trees_regression': direct * 2 - 1}
        result = evaluate_scores(frame, threshold, scores, 'one', 'local')
        natural = result[(result.method == 'direct_classifier') &
                         result.selection_mode.eq('natural')]
        self.assertEqual(int(natural.selected.sum()), 1)
        self.assertEqual(int(natural.successful.sum()), 1)
        self.assertEqual(int(natural.exact_minimal.sum()), 1)
        self.assertEqual(int(natural.false_approval.sum()), 0)
        self.assertEqual(natural.loc[natural.selected, 'selected_gpu'].iloc[0], 2)

    def test_model_folds_disjoint(self):
        # A smoke check against the current model-id cohort, independent of labels.
        from cold_start_policy import ROOT, CAMPAIGN, prepare
        raw = pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet')
        main = prepare(raw[raw.campaign == CAMPAIGN])
        folds = list(fold_partitions(main))
        self.assertEqual(len(folds), 10)
        for _, donor, held in folds:
            self.assertFalse(set(donor.model_id).intersection(held.model_id))

    def test_heldout_outcomes_cannot_change_forecast(self):
        rows = []
        for name in ['donor_a', 'donor_b', 'target']:
            for j in range(8):
                row = {feature: float(j + 1) for feature in STATIC}
                row.update(model_id=name,
                           true_log_latency=np.log(1 if j < 4 else 100),
                           true_log_rps=np.log(100 if j < 4 else 1))
                rows.append(row)
        frame = pd.DataFrame(rows)
        donors = frame[frame.model_id.ne('target')]
        target = frame[frame.model_id.eq('target')]
        threshold = [{'latency_slo_ms': 10.0, 'target_rps': 10.0}]
        first = fit_predict(donors, target, threshold)
        altered = target.copy()
        altered[['true_log_latency', 'true_log_rps']] = 9999
        second = fit_predict(donors, altered, threshold)
        for method in first:
            np.testing.assert_array_equal(first[method], second[method])
        with self.assertRaises(ValueError):
            fit_predict(donors, donors.iloc[:1], threshold)


if __name__ == '__main__':
    unittest.main()
