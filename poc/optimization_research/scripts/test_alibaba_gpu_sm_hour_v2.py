"""Temporal boundaries, combined-period eligibility and inference replay tests."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
import polars as pl

spec = importlib.util.spec_from_file_location('v2', Path(__file__).with_name('train_alibaba_gpu_sm_hour_v2.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def workload_for_bucket(low, high):
    base = study.load_base()
    return next(f'w{i}' for i in range(10000) if low <= base.bucket(f'w{i}') < high)


def rows(start, workload, pod='p'):
    return pl.DataFrame([{
        'pod_id': pod, 'workload_id': workload, 'server_id': 's',
        'gpu_request': 1., 'server_gpu_count': 1,
        'gpu_spec_public': 'gpu', 'priority_class': 'HP',
        'cpu_request_cores': 4., 'avg_gpu_sm_util': float(i * 10),
        'day': (start + i) // 24, 'hour': (start + i) % 24,
    } for i in range(4)])


class ConstantPredictor:
    def predict(self, frame):
        return np.full(len(frame), 5.)

    def fit(self, *args, **kwargs):
        raise AssertionError('Replay must not fit')


class Tests(unittest.TestCase):
    def test_split_edges_and_workload_buckets(self):
        self.assertEqual(study.assign_split(116 * 24, 69), 'train')
        self.assertEqual(study.assign_split(120 * 24 + 22, 69), 'train')
        self.assertIsNone(study.assign_split(120 * 24 + 23, 69))
        self.assertIsNone(study.assign_split(121 * 24, 69))
        self.assertEqual(study.assign_split(121 * 24, 70), 'validation')
        self.assertEqual(study.assign_split(121 * 24 + 22, 84), 'validation')
        self.assertIsNone(study.assign_split(121 * 24 + 23, 84))
        self.assertEqual(study.assign_split(122 * 24 + 22, 99), 'test')
        self.assertIsNone(study.assign_split(122 * 24 + 23, 99))

    def test_cross_download_period_history_is_preserved(self):
        base = study.load_base()
        frame, _ = base.make_examples(rows(115 * 24 + 22, workload_for_bucket(0, 70)))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.t.iloc[0], 116 * 24)
        self.assertEqual(frame.sm_lag2.iloc[0], 0.)
        self.assertEqual(frame.target_sm.iloc[0], 30.)
        self.assertEqual(frame.split.iloc[0], 'train')

    def test_validation_target_and_history_are_separate(self):
        base = study.load_base()
        frame, _ = base.make_examples(rows(120 * 24 + 22, workload_for_bucket(70, 85)))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.split.iloc[0], 'validation')
        self.assertEqual(frame.target_hour_index.iloc[0], 121 * 24 + 1)
        self.assertEqual(frame.measurement_available_hour_index.iloc[0], frame.t.iloc[0] + 1)

    def test_conflicting_workload_across_periods_excluded(self):
        base = study.load_base()
        w = workload_for_bucket(0, 70)
        old = rows(115 * 24 + 20, w)
        new = rows(116 * 24, 'other')
        frame, audit = base.make_examples(pl.concat([old, new]))
        self.assertEqual(len(frame), 0)
        self.assertEqual(audit['unstable_or_unknown_workload_pods_excluded'], 1)

    def test_missing_target_not_replaced_by_zero(self):
        base = study.load_base()
        frame, _ = base.make_examples(rows(116 * 24, workload_for_bucket(0, 70)).head(3))
        self.assertEqual(len(frame), 0)

    def test_predict_selected_alpha_from_saved_bundle_without_fit(self):
        frame = pd.DataFrame({'sm_current': [10., 99.]})
        bundle = {'selected': 'residual_hgb_alpha_0.25', 'features': ['sm_current'],
                  'models': {'residual_hgb': ConstantPredictor()}}
        np.testing.assert_array_equal(study.predict_bundle(frame, bundle), [11.25, 100.])


if __name__ == '__main__':
    unittest.main()
