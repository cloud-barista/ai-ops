"""Predict-time worker changes leave the fitted research model unchanged."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from predict_performance import configure_predict_workers, predict_records
from train_performance import CATEGORICAL, FEATURES, NUMERIC, make_model


class PredictWorkersTests(unittest.TestCase):
    def test_fitted_extra_trees_predictions_are_unchanged(self):
        frame = pd.DataFrame([
            dict(model_id='a', model_revision='v1', device='cuda',
                 batch_size=1 + i, max_length=32, request_concurrency=1,
                 cpu_threads=2, cpu_core_limit=2 + i, replica_count=1,
                 repeats=8)
            for i in range(8)
        ])
        model = make_model('extra_trees', 42)
        self.assertEqual(model.named_steps['regressor'].n_jobs, 2)
        targets = np.column_stack((np.arange(8.0) + 1, np.arange(8.0) + 3))
        model.fit(frame[FEATURES], targets)
        before = model.predict(frame[FEATURES])
        bundle = {'selected': 'extra_trees', 'model': model,
                  'features': FEATURES, 'targets': ['latency', 'throughput'],
                  'categorical_support': {k: frame[k].unique().tolist() for k in CATEGORICAL},
                  'numeric_support': {k: [float(frame[k].min()), float(frame[k].max())]
                                      for k in NUMERIC},
                  'support_combination_columns': ['model_id'],
                  'support_combinations': [('a',)],
                  'training_condition_support': {
                      ('a', 'v1', 'cuda'): {
                          'batch_size': (1, 8), 'max_length': (32, 32),
                          'request_concurrency': (1, 1),
                          'repeats': frozenset({8}),
                      },
                  }}
        result = predict_records(bundle, frame.iloc[:1].to_dict('records'))
        self.assertEqual(result[0]['status'], 'PREDICTED')
        np.testing.assert_allclose(list(result[0]['prediction'].values()),
                                   np.exp(before[0]), rtol=0, atol=1e-12)
        self.assertEqual(model.named_steps['regressor'].n_jobs, 1)
        np.testing.assert_allclose(before, model.predict(frame[FEATURES]), rtol=0,
                                   atol=1e-12)
        self.assertIs(configure_predict_workers(bundle), bundle)
        self.assertEqual(model.named_steps['regressor'].n_jobs, 1)
        del bundle['training_condition_support']
        self.assertEqual(predict_records(bundle, frame.iloc[:1].to_dict('records')),
                         [{'status': 'ABSTAIN',
                           'reasons': ['missing_training_condition_support']}])

    def test_many_records_use_one_model_call_and_preserve_abstain_order(self):
        class CountingModel:
            calls = 0

            def predict(self, frame):
                self.calls += 1
                batch = frame['batch_size'].to_numpy(dtype=float)
                return np.log(np.column_stack((batch + 1, batch + 2)))

        model = CountingModel()
        bundle = {
            'selected': 'test_regressor', 'model': model,
            'features': ['batch_size'], 'targets': ['latency', 'throughput'],
            'categorical_support': {'model_id': ['a']},
            'numeric_support': {'batch_size': [1, 8]},
            'support_combination_columns': ['model_id'],
            'support_combinations': [('a',)],
        }
        records = [{'model_id': 'a', 'batch_size': batch} for batch in range(1, 9)]
        records[3]['model_id'] = 'unknown'
        batched = predict_records(bundle, records)
        self.assertEqual(model.calls, 1)
        self.assertEqual(batched[3]['status'], 'ABSTAIN')
        self.assertEqual(batched[3]['reasons'], [
            'unsupported_category:model_id',
            'unsupported_model_hardware_replica_combination',
        ])
        serial = [predict_records(bundle, [record])[0] for record in records]
        self.assertEqual(batched, serial)
        self.assertEqual(model.calls, 8)
        self.assertEqual(predict_records(bundle, []), [])
        self.assertEqual(model.calls, 8)

    def test_baseline_batch_preserves_fallback_and_abstain_order(self):
        lookup = pd.DataFrame(
            [[np.log(10.0), np.log(20.0)]],
            index=pd.MultiIndex.from_tuples([('a', 'cuda')], names=['model_id', 'device']),
            columns=['latency', 'throughput'],
        )
        bundle = {
            'selected': 'model_device_median',
            'baseline': (lookup, np.log(np.array([30.0, 40.0]))),
            'targets': ['latency', 'throughput'],
            'categorical_support': {'model_id': ['a', 'b'], 'device': ['cuda']},
            'numeric_support': {'batch_size': [1, 4]},
            'support_combination_columns': ['model_id', 'device'],
            'support_combinations': [('a', 'cuda'), ('b', 'cuda')],
            'training_condition_support': {
                ('a', None, 'cuda'): {'batch_size': (1, 4),
                                      'max_length': (1, 1),
                                      'request_concurrency': (1, 1),
                                      'repeats': frozenset({1})},
                ('b', None, 'cuda'): {'batch_size': (1, 4),
                                      'max_length': (1, 1),
                                      'request_concurrency': (1, 1),
                                      'repeats': frozenset({1})},
            },
        }
        records = [
            {'model_id': model_id, 'model_revision': None, 'device': 'cuda',
             'batch_size': batch_size, 'max_length': 1,
             'request_concurrency': 1, 'repeats': 1}
            for model_id, batch_size in [('a', 1), ('b', 2), ('c', 3), ('a', 4)]
        ]
        batched = predict_records(bundle, records)
        serial = [predict_records(bundle, [record])[0] for record in records]
        self.assertEqual(batched, serial)
        self.assertEqual([row['status'] for row in batched],
                         ['PREDICTED', 'PREDICTED', 'ABSTAIN', 'PREDICTED'])
        self.assertAlmostEqual(batched[0]['prediction']['latency'], 10.0)
        self.assertAlmostEqual(batched[1]['prediction']['throughput'], 40.0)


if __name__ == '__main__':
    unittest.main()
