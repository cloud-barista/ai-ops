"""Check telemetry shuffles preserve measured values and joint vectors."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from telemetry_permutation_control import FEATURES, shuffle_joint_telemetry


def fixture() -> pd.DataFrame:
    out = pd.DataFrame({
        'workload_group': [f'g{i}' for i in range(8)],
        'batch_size': [1] * 4 + [2] * 4,
        'anchor_log_latency': np.arange(8, dtype=float),
        'anchor_log_requests_per_second': np.arange(8, dtype=float) + 10,
        'true_log_requests_per_second': np.arange(8, dtype=float) + 20,
    })
    for j, feature in enumerate(FEATURES):
        out[feature] = np.arange(8, dtype=float) * 10 + j
    return out


class TelemetryPermutationControlTest(unittest.TestCase):
    def test_global_shuffle_preserves_joint_vectors_and_performance(self):
        source = fixture()
        out = shuffle_joint_telemetry(source, seed=43)
        self.assertFalse(out[list(FEATURES)].equals(source[list(FEATURES)]))
        original_vectors = sorted(map(tuple, source[list(FEATURES)].to_numpy()))
        shuffled_vectors = sorted(map(tuple, out[list(FEATURES)].to_numpy()))
        self.assertEqual(original_vectors, shuffled_vectors)
        pd.testing.assert_frame_equal(source.drop(columns=list(FEATURES)),
                                      out.drop(columns=list(FEATURES)))

    def test_batch_stratification_keeps_batch_telemetry_marginals(self):
        source = fixture()
        out = shuffle_joint_telemetry(source, seed=44, mode='within_batch')
        for batch in (1, 2):
            original = source[source.batch_size == batch]
            permuted = out[out.batch_size == batch]
            self.assertEqual(sorted(map(tuple, original[list(FEATURES)].to_numpy())),
                             sorted(map(tuple, permuted[list(FEATURES)].to_numpy())))
        pd.testing.assert_frame_equal(source.drop(columns=list(FEATURES)),
                                      out.drop(columns=list(FEATURES)))

    def test_invalid_or_repeated_group_rejected(self):
        source = fixture()
        with self.assertRaises(ValueError):
            shuffle_joint_telemetry(source, 42, mode='unknown')
        repeated = pd.concat([source, source.iloc[[0]]], ignore_index=True)
        with self.assertRaises(ValueError):
            shuffle_joint_telemetry(repeated, 42)


if __name__ == '__main__':
    unittest.main()
