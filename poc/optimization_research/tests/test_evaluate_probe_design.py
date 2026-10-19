"""Checks for donor-only weighted probe selection."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cross_model_covariance import choose_covariance_probes
from evaluate_probe_design import (donor_threshold_importance,
                                   select_weighted_covariance_probes)


class ProbeDesignEvaluationTests(unittest.TestCase):
    def test_uniform_weight_and_cost_matches_existing_covariance_selector(self):
        x = np.arange(6, dtype=float)
        kernel = np.exp(-0.5 * (x[:, None] - x[None, :]) ** 2)
        covariance = np.stack([kernel, 0.5 * kernel], axis=2)
        selected = select_weighted_covariance_probes(
            covariance, 3, np.ones(6), np.ones(6))
        np.testing.assert_array_equal(
            selected, choose_covariance_probes(covariance, 3))
        self.assertEqual(len(set(selected.tolist())), 3)

    def test_weight_and_cost_can_change_probe_choice(self):
        identity = np.stack([np.eye(3), np.eye(3)], axis=2)
        baseline = select_weighted_covariance_probes(
            identity, 1, np.ones(3), np.ones(3))
        important = select_weighted_covariance_probes(
            identity, 1, np.array([1., 1., 5.]), np.ones(3))
        cheap = select_weighted_covariance_probes(
            identity, 1, np.ones(3), np.array([4., 1., 2.]))
        self.assertEqual(int(baseline[0]), 0)
        self.assertEqual(int(important[0]), 2)
        self.assertEqual(int(cheap[0]), 1)

    def test_importance_uses_only_supplied_zero_shot_and_donor_thresholds(self):
        rows = []
        for group in ('a', 'b'):
            for gpu in (1, 2):
                for cores in (2, 4, 8, 16):
                    rows.append({'workload_group': group, 'replica_count': gpu,
                                 'cpu_core_limit': cores,
                                 'latency_p95_ms': 500.,
                                 'examples_per_second': 1.})
        static = pd.DataFrame(rows)
        predictions = np.tile(np.log([100., 10.]), (16, 1))
        thresholds = [{'latency_slo_ms': 100., 'target_rps': 10.}]
        before = donor_threshold_importance(
            static, predictions, thresholds, ['b', 'a'])
        modified = static.copy()
        modified['latency_p95_ms'] *= 100
        modified['examples_per_second'] /= 100
        after = donor_threshold_importance(
            modified, predictions, thresholds, ['b', 'a'])
        np.testing.assert_allclose(before, after)
        self.assertTrue((before > 1).all())

    def test_invalid_cost_rejected(self):
        identity = np.stack([np.eye(2), np.eye(2)], axis=2)
        with self.assertRaises(ValueError):
            select_weighted_covariance_probes(
                identity, 1, np.ones(2), np.array([0., 1.]))


if __name__ == '__main__':
    unittest.main()
