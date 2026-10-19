"""Information-boundary and behavior checks for adaptive anchor profiling."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from adaptive_probe_transfer import (METHODS, choose_adaptive_probes,
                                     decision_subset_counts, posterior_state)


def two_regions():
    block = np.full((3, 3), 0.8)
    np.fill_diagonal(block, 1.0)
    one = np.zeros((6, 6))
    one[:3, :3] = block
    one[3:, 3:] = block
    return np.stack((one, one), axis=2)


class AdaptiveProbeTests(unittest.TestCase):
    def test_observations_change_later_probe_choice(self):
        covariance = two_regions()
        left = {0: np.array([3.0, 0.0]), 3: np.zeros(2)}
        right = {0: np.zeros(2), 3: np.array([3.0, 0.0])}
        a, _ = choose_adaptive_probes(covariance, [0, 3],
                                      lambda index: left.get(index, np.zeros(2)),
                                      budget=3)
        b, _ = choose_adaptive_probes(covariance, [0, 3],
                                      lambda index: right.get(index, np.zeros(2)),
                                      budget=3)
        self.assertEqual(a, [0, 3, 1])
        self.assertEqual(b, [0, 3, 4])
        a_static, _ = choose_adaptive_probes(
            covariance, [0, 3], lambda index: left.get(index, np.zeros(2)),
            budget=3, emphasis=0.0)
        b_static, _ = choose_adaptive_probes(
            covariance, [0, 3], lambda index: right.get(index, np.zeros(2)),
            budget=3, emphasis=0.0)
        self.assertEqual(a_static, b_static)

    def test_unselected_residuals_are_never_read(self):
        covariance = two_regions()
        observations = {index: np.array([float(index) / 10, -0.2])
                        for index in range(6)}
        accessed = []

        def observe(index):
            accessed.append(index)
            return observations[index]

        selected, trace = choose_adaptive_probes(covariance, [0, 3], observe,
                                                  budget=4)
        self.assertEqual(accessed, selected)
        self.assertEqual(len(set(selected)), 4)
        self.assertEqual([row['index'] for row in trace], selected)
        changed = observations.copy()
        for index in set(range(6)) - set(selected):
            changed[index] = np.array([1e6, -1e6])
        second, _ = choose_adaptive_probes(
            covariance, [0, 3], lambda index: changed[index], budget=4)
        self.assertEqual(selected, second)

    def test_posterior_conditions_only_on_selected_values(self):
        covariance = two_regions()
        mean, conditional = posterior_state(covariance, [0, 3],
                                            np.array([[2.0, 1.0], [0.0, 0.0]]))
        self.assertGreater(mean[1, 0], mean[4, 0])
        self.assertLess(conditional[1, 1, 0], covariance[1, 1, 0])
        self.assertTrue(np.isfinite(conditional).all())

    def test_invalid_budget_or_duplicate_initial_is_rejected(self):
        covariance = two_regions()
        with self.assertRaises(ValueError):
            choose_adaptive_probes(covariance, [0, 0], lambda _: np.zeros(2), 3)
        with self.assertRaises(ValueError):
            choose_adaptive_probes(covariance, [0], lambda _: np.zeros(2), 7)

    def test_subset_scoring_respects_generated_per_row_thresholds(self):
        rows = []
        for group in ('mixed', 'easy'):
            for gpu in (1, 2):
                for core in (2, 4, 8, 16):
                    latency = 8.0
                    rps = 2.0
                    if group == 'mixed' and gpu == 1 and core == 2:
                        latency = 12.0
                    if group == 'mixed' and gpu == 1 and core == 4:
                        rps = 0.5
                    row = {'workload_group': group, 'replica_count': gpu,
                           'cpu_core_limit': core, 'latency_p95_ms': latency,
                           'examples_per_second': rps, 'batch_size': 1,
                           'latency_slo_ms': 10.0 if group == 'mixed' else 100.0,
                           'target_throughput_rps': 1.0 if group == 'mixed' else 0.1}
                    for method in METHODS:
                        row[f'{method}_latency_log'] = np.log(latency)
                        row[f'{method}_rps_log'] = np.log(rps)
                    rows.append(row)
        frame = pd.DataFrame(rows).iloc[::-1].copy()
        result = decision_subset_counts(frame, frame.latency_slo_ms,
                                        frame.target_throughput_rps)
        adaptive = {row['subset']: row for row in result
                    if row['method'] == 'adaptive_cov_transfer'}
        self.assertEqual(adaptive['all']['successful_choices'], 2)
        self.assertEqual(adaptive['nontrivial']['workload_queries'], 1)
        self.assertEqual(adaptive['both_constraints_active']['workload_queries'], 1)
        self.assertEqual(adaptive['both_constraints_active']['successful_choices'], 1)


if __name__ == '__main__':
    unittest.main()
