"""Contract and leakage checks for the CPU-to-GPU exploratory study."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from cpu_preprofile_transfer import (  # noqa: E402
    CAMPAIGN, cpu_frame, paired_cpu2, predict_fold, pseudo_posterior,
)
from cross_model_covariance import KEY_COLUMNS  # noqa: E402
from evaluate_cpu_preprofile_resource import partial_workload_groups  # noqa: E402


class _ZeroPredictor:
    def predict(self, rows):
        return np.zeros((len(rows), 2))


class _IdentityTransfer:
    def predict(self, rows):
        return np.asarray(rows)


class CpuPreprofileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = pd.read_parquet(ROOT / 'data/audit_v1/measurements.parquet')
        cls.gpu = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
                      raw.device.eq('cuda')].copy()
        cls.cpu = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
                      raw.device.eq('cpu')].copy()

    def test_exact_pairs_and_observed_order(self):
        paired = paired_cpu2(self.gpu, self.cpu)
        self.assertEqual(len(paired), 64)
        self.assertEqual(paired.groupby('model_id').size().tolist(), [8] * 8)
        self.assertTrue((paired.finished_at < paired.gpu_started_at).all())
        # CPU8-first is a *reordered* offline protocol: in the actual stream
        # each model saw its first GPU before its final CPU2 measurement.
        for model_id, group in paired.groupby('model_id'):
            first_gpu = self.gpu[self.gpu.model_id == model_id].started_at.min()
            self.assertGreater(group.finished_at.max(), first_gpu)
        malformed = self.cpu.copy()
        ix = malformed[malformed.cpu_core_limit.eq(2)].index[0]
        malformed.loc[ix, 'finished_at'] = '2099-01-01T00:00:00Z'
        with self.assertRaises(ValueError):
            paired_cpu2(self.gpu, malformed)

    def test_pseudo_observations_are_not_treated_as_exact_gpu_labels(self):
        covariance = np.zeros((2, 2, 2))
        covariance[:, :, 0] = np.eye(2)
        covariance[:, :, 1] = np.eye(2)
        inferred = pseudo_posterior(covariance, np.asarray([0]),
                                    np.asarray([[3.0, -3.0]]),
                                    noise_fraction=0.5)
        np.testing.assert_allclose(inferred[0], [2.0, -2.0])
        np.testing.assert_allclose(inferred[1], [0.0, 0.0])

    def test_cpu_only_predictions_ignore_heldout_gpu_outcomes(self):
        held = sorted(self.cpu.model_id.unique())[0]
        target_gpu = self.gpu[self.gpu.model_id == held].copy()
        target_cpu = cpu_frame(self.gpu, paired_cpu2(self.gpu, self.cpu))
        target_cpu = target_cpu[target_cpu.model_id == held]
        donor = self.gpu[(self.gpu.model_id != held) &
                         self.gpu.model_id.ne('Qwen/Qwen2.5-0.5B-Instruct') &
                         self.gpu.replica_count.eq(1) &
                         self.gpu.cpu_core_limit.eq(2)]
        donor = donor[['model_id', *KEY_COLUMNS]].copy()
        donor['residual_latency'] = 0.0
        donor['residual_rps'] = 0.0
        base, _ = predict_fold(target_gpu, target_cpu, _ZeroPredictor(),
                               _ZeroPredictor(), _ZeroPredictor(),
                               _IdentityTransfer(), donor)
        altered = target_gpu.copy()
        altered['latency_p95_ms'] *= 10
        altered['examples_per_second'] *= 0.1
        altered['result_json'] = '{}'
        changed, _ = predict_fold(altered, target_cpu, _ZeroPredictor(),
                                  _ZeroPredictor(), _ZeroPredictor(),
                                  _IdentityTransfer(), donor)
        for target in ('latency', 'rps'):
            np.testing.assert_allclose(base[f'cpu8_transfer_{target}_log'],
                                       changed[f'cpu8_transfer_{target}_log'])
        self.assertFalse(np.allclose(base.gpu8_covariance_latency_log,
                                     changed.gpu8_covariance_latency_log))

    def test_partial_oracle_excludes_zero_and_eight_feasible_cells(self):
        x = pd.DataFrame({
            'workload_group': ['none'] * 8 + ['partial'] * 8 + ['all'] * 8,
            'latency_p95_ms': [2.0] * 8 + [0.5] * 4 + [2.0] * 4 + [0.5] * 8,
            'examples_per_second': [2.0] * 24,
            'batch_size': [1] * 24,
        })
        selected = partial_workload_groups(x, {'latency_slo_ms': 1.0,
                                                'target_rps': 1.0})
        self.assertEqual(selected, {'partial'})


if __name__ == '__main__':
    unittest.main()
