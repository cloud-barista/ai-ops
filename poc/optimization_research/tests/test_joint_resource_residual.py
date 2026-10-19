"""Focused information-boundary tests for the joint residual experiment."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from joint_resource_residual import (RESOURCES, joint_prior, ordered_grid,
                                     posterior_joint, predict_joint)


class ZeroPredictor:
    def predict(self, frame):
        return np.zeros((len(frame), 2))


def slate() -> pd.DataFrame:
    rows = []
    for i in range(10):
        for gpu, cpu in RESOURCES:
            rows.append({
                'campaign': 'synthetic', 'model_id': 'heldout',
                'workload_group': f'g{i}', 'execution_id': f'g{i}-{gpu}-{cpu}',
                'workload_task': 'encoder_forward', 'batch_size': 2 ** (i % 4),
                'max_length': 64 * (1 + i // 4),
                'request_concurrency': 1 + i % 3,
                'cpu_threads': cpu, 'cpu_core_limit': cpu,
                'replica_count': gpu, 'repeats': 2,
                'latency_slo_ms': 100.0, 'target_throughput_rps': 1.0,
                'latency_p95_ms': float(10 + i + gpu + cpu),
                'examples_per_second': float(20 + i + gpu + cpu),
                'result_json': json.dumps({'model_parameter_bytes': 123456}),
            })
    return pd.DataFrame(rows)


def fake_donors(held: pd.DataFrame) -> list[dict]:
    _, keys = ordered_grid(held)
    grid = np.arange(len(keys) * 8 * 2, dtype=float).reshape(len(keys), 8, 2) / 100
    return [{'model_id': 'donor_a', 'task': 'encoder_forward', 'keys': keys,
             'residual': grid},
            {'model_id': 'donor_b', 'task': 'encoder_forward', 'keys': keys,
             'residual': -0.5 * grid}]


class JointResidualTest(unittest.TestCase):
    def test_joint_prior_uses_only_donors_and_static_heldout_fields(self):
        held = slate()
        static, keys = ordered_grid(held)
        donors = fake_donors(held)
        first = joint_prior(static, keys, donors)
        changed = held.copy()
        changed.latency_p95_ms *= 100
        changed.examples_per_second /= 100
        second_static, second_keys = ordered_grid(changed)
        second = joint_prior(second_static, second_keys, donors)
        for a, b in zip(first[:4], second[:4]):
            np.testing.assert_allclose(a, b)
        self.assertEqual(first[4]['mode'], 'aligned_joint_empirical_plus_kernel')
        self.assertEqual(first[0].shape, (80, 10, 2))
        self.assertEqual(first[1].shape, (10, 10, 2))

    def test_joint_posterior_reproduces_selected_anchor_residuals(self):
        static, keys = ordered_grid(slate())
        cross, anchor, _, _, _ = joint_prior(static, keys, fake_donors(slate()))
        idx = np.array([0, 2, 3, 5])
        measured = np.array([[0.3, -0.2], [-0.1, 0.2],
                             [0.0, 0.1], [0.4, 0.3]])
        posterior = posterior_joint(cross, anchor, idx, measured)
        np.testing.assert_allclose(posterior[8 * idx], measured)

    def test_all_unprobed_outcomes_are_ignored(self):
        held = slate()
        probes = [f'g{i}' for i in range(8)]
        donors = fake_donors(held)
        first, _ = predict_joint(held, probes, donors,
                                 ZeroPredictor(), ZeroPredictor())
        changed = held.copy()
        observed = changed.workload_group.isin(probes) & (changed.replica_count == 1) & (changed.cpu_core_limit == 2)
        changed.loc[~observed, 'latency_p95_ms'] *= 200
        changed.loc[~observed, 'examples_per_second'] /= 200
        changed.loc[~observed, 'result_json'] = json.dumps({
            'model_parameter_bytes': 123456, 'private_performance': 777})
        second, _ = predict_joint(changed, probes, donors,
                                  ZeroPredictor(), ZeroPredictor())
        pd.testing.assert_frame_equal(first, second)


if __name__ == '__main__':
    unittest.main()
