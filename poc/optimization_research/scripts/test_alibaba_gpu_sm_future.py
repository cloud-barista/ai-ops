"""Boundary and inference-only checks for frozen future-period confirmation."""
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
import polars as pl

MODULE = Path(__file__).with_name('evaluate_alibaba_gpu_sm_future.py')
spec = importlib.util.spec_from_file_location('sm_future', MODULE)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
FREEZE = Path(__file__).resolve().parents[1] / 'reports/alibaba_gpu_sm_future_freeze_v1.json'


def raw_rows(start):
    return pl.DataFrame([{
        'pod_id': 'p', 'workload_id': 'w', 'server_id': 's',
        'gpu_request': 1., 'server_gpu_count': 1,
        'gpu_spec_public': 'gpu', 'priority_class': 'HP',
        'cpu_request_cores': 4., 'avg_gpu_sm_util': float(i * 10),
        'day': (start + i) // 24, 'hour': (start + i) % 24,
    } for i in range(4)])


class PredictOnlyModel:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return np.full(len(frame), self.value)

    def fit(self, *args, **kwargs):
        raise AssertionError('Confirmation must not fit')


class ConfirmationTests(unittest.TestCase):
    def test_period_boundaries(self):
        self.assertIsNone(study.future_assignment(115 * 24 + 23, 99))
        self.assertEqual(study.future_assignment(116 * 24, 99), 'confirmation')
        self.assertEqual(study.future_assignment(122 * 24 + 22, 0), 'confirmation')
        self.assertIsNone(study.future_assignment(122 * 24 + 23, 99))

    def test_existing_two_hours_are_past_only(self):
        module = study.load_study(json.loads(FREEZE.read_text()))
        frame, _ = module.make_examples(raw_rows(115 * 24 + 22))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.t.iloc[0], 116 * 24)
        self.assertEqual(frame.target_hour_index.iloc[0], 116 * 24 + 1)
        self.assertEqual(frame.sm_lag2.iloc[0], 0)
        self.assertEqual(frame.target_sm.iloc[0], 30)

    def test_last_allowed_target_and_no_following_day(self):
        module = study.load_study(json.loads(FREEZE.read_text()))
        frame, _ = module.make_examples(raw_rows(122 * 24 + 20))
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.target_hour_index.iloc[0], 122 * 24 + 23)
        after, _ = module.make_examples(raw_rows(122 * 24 + 21))
        self.assertEqual(len(after), 0)

    def test_frozen_inference_no_fit_no_alpha_selection(self):
        f = pd.DataFrame({'sm_current': [10., 99.], 'sm_mean3': [8., 90.],
                          'gpu_spec_public': ['known', 'unknown']})
        bundle = {'features': ['sm_current'], 'gpu_median': {'known': 7.},
                  'global_median': 5., 'models': {
                      'direct_hgb': PredictOnlyModel(-5),
                      'residual_hgb': PredictOnlyModel(5)}}
        result = study.fixed_predictions(f, bundle)
        np.testing.assert_array_equal(result['residual_hgb_alpha_1'], [15, 100])
        np.testing.assert_array_equal(result['direct_hgb'], [0, 0])
        np.testing.assert_array_equal(result['train_gpu_median'], [7, 5])
        self.assertEqual(len(result), 5)

    def test_freeze_hash_rejects_changed_code(self):
        freeze = json.loads(FREEZE.read_text())
        freeze['training_script_sha256'] = 'bad-hash'
        with self.assertRaisesRegex(ValueError, 'changed'):
            study.load_study(freeze)

    def test_confirmation_gate_pass_fail_and_insufficient(self):
        freeze = json.loads(FREEZE.read_text())
        module = study.load_study(freeze)
        f = pd.DataFrame([{'workload_id': f'w{w}', 'pod_id': f'p{w}',
                           'day': day, 'target_sm': 30., 'changed_gt10': True}
                          for day in range(116, 123) for w in range(20)
                          for _ in range(10)])
        good = {'persistence': np.full(len(f), 10.),
                'residual_hgb_alpha_1': np.full(len(f), 30.),
                'direct_hgb': np.full(len(f), 29.)}
        passed = study.evaluate_view(f, good, module, freeze)
        self.assertEqual(passed['gate'], 'PASS')
        bad = dict(good, residual_hgb_alpha_1=np.full(len(f), 0.))
        self.assertEqual(study.evaluate_view(f, bad, module, freeze)['gate'], 'NO_GO')
        short = {k: v[:100] for k, v in good.items()}
        insufficient = study.evaluate_view(f.iloc[:100], short, module, freeze)
        self.assertEqual(insufficient['gate'], 'INSUFFICIENT_DATA')


if __name__ == '__main__':
    unittest.main()
