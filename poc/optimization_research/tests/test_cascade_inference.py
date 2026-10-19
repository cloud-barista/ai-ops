"""Tests for the static decision cascade's information and margin boundaries."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cascade_inference import (STATIC, decision_radius, first_pass, margin_matrix,
                               measure_choice, prediction_bundle, route_choice,
                               sorted_slates, support_force)


class FixedRegressor:
    def __init__(self, left, right):
        self.left = left
        self.right = right

    def predict(self, x):
        return np.column_stack((np.full(len(x), self.left),
                                np.full(len(x), self.right)))


class CascadeInferenceTest(unittest.TestCase):
    def test_decision_radius_is_first_choice_stability_margin(self):
        # First two candidates fail by 0.3; the third passes by 0.2.
        a = np.array([[[0.9, -0.3, 0.2, -0.5, -0.8, -1.0, -1.2, -1.3]]])
        b = np.array([[[-0.3, 0.9, 0.6, 0.9, 0.9, 0.9, 0.9, 0.9]]])
        choice = first_pass(np.minimum(a, b))
        self.assertEqual(int(choice[0, 0]), 2)
        radius = decision_radius(a, b, choice)
        self.assertAlmostEqual(float(radius[0, 0]), 0.2)
        # All possible coordinate perturbations bounded by <radius keep rank 2.
        perturbed_a = a - 0.199
        perturbed_b = b + 0.199
        self.assertEqual(int(first_pass(np.minimum(perturbed_a, perturbed_b))[0, 0]), 2)
        none = np.full((1, 1, 8), -0.4)
        none_choice = first_pass(none)
        self.assertEqual(int(none_choice[0, 0]), -1)
        self.assertAlmostEqual(float(decision_radius(none, none, none_choice)[0, 0]), 0.4)

    def test_route_uses_support_and_below_threshold(self):
        bundle = {'radius': np.array([[.01, .2, .3]]),
                  'support_force': np.array([False, False, True]),
                  'cheap_choice': np.array([[0, 1, 2]]),
                  'strong_choice': np.array([[3, 4, 5]]),
                  'cheap_margin': np.zeros((1, 3, 8)),
                  'strong_margin': np.ones((1, 3, 8))}
        choice, _, route = route_choice(bundle, .05)
        self.assertEqual(route.tolist(), [[True, False, True]])
        self.assertEqual(choice.tolist(), [[3, 1, 5]])

    def test_outcome_mutation_cannot_change_target_predictions(self):
        rows = []
        for model in ('donor', 'target'):
            for gpu in (1, 2):
                for core in (2, 4, 8, 16):
                    row = {feature: float(gpu + core) for feature in STATIC}
                    row.update(model_id=model, workload_group=model,
                               replica_count=gpu, cpu_core_limit=core,
                               workload_task='inference',
                               log_parameter_bytes=float(8 if model == 'donor' else 9),
                               true_log_latency=1.0, true_log_rps=2.0)
                    rows.append(row)
        frame = pd.DataFrame(rows)
        donors = frame.loc[frame.model_id.eq('donor')]
        target = frame.loc[frame.model_id.eq('target')].copy()
        models = (FixedRegressor(1.0, 2.0), FixedRegressor(1.1, 2.1))
        t = [{'latency_slo_ms': np.exp(2.0), 'target_rps': np.exp(1.0)}]
        first = prediction_bundle(donors, target, t, models)
        altered = target.copy()
        altered[['true_log_latency', 'true_log_rps']] = [100.0, -100.0]
        second = prediction_bundle(donors, altered, t, models)
        np.testing.assert_array_equal(first['cheap_choice'], second['cheap_choice'])
        np.testing.assert_array_equal(first['strong_choice'], second['strong_choice'])
        np.testing.assert_array_equal(first['radius'], second['radius'])
        self.assertTrue(first['support_force'][0])

    def test_truth_is_not_an_input_to_choice(self):
        feasible = np.zeros((1, 1, 8), dtype=bool)
        feasible[0, 0, 1] = True
        choice = np.array([[0]], dtype=np.int16)
        oracle = np.array([[1]], dtype=np.int16)
        measured = measure_choice(choice, feasible, oracle)
        self.assertEqual(measured['false_approval'], 1)
        self.assertEqual(measured['exact_minimal'], 0)
        self.assertEqual(measured['approved'], 1)


if __name__ == '__main__':
    unittest.main()
