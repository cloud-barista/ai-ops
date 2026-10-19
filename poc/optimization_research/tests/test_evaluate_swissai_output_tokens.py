import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import evaluate_swissai_output_tokens as evaluator
from evaluate_swissai_output_tokens import (
    feature_matrix, gate, sample_training,
)
from swissai_output_tokens_core import DAY_US, build_day_snapshots


class SwissaiOutputTokenEvaluatorTests(unittest.TestCase):
    def test_joined_parquet_eligibility_and_source_row_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compact = root / 'compact.parquet'
            outputs = root / 'outputs.parquet'
            pl.DataFrame({
                'source_row': [1, 0, 2],
                'created_utc_us': [10, 20, 30],
                'finished_utc_us': [20, 40, None],
                'finished_naive': [False, True, False],
                'model': ['model-a', 'model-a', '\\N'],
                'status': ['DEFAULT', 'ERROR', 'DEFAULT'],
            }).write_parquet(compact)
            pl.DataFrame({
                'source_row': [1, 0, 2],
                'reported_output_tokens': [8, 9, None],
                'temperature': [0.5, None, None],
                'top_p': [None, None, None],
                'frequency_penalty': [None, None, None],
                'presence_penalty': [None, None, None],
                'max_tokens': [None, None, None],
            }).write_parquet(outputs)
            with patch.object(evaluator, 'verify_pins', lambda: None), \
                 patch.object(evaluator, 'COMPACT', compact), \
                 patch.object(evaluator, 'EXTRACTED', outputs), \
                 patch.object(evaluator, 'EXPECTED_ROWS', 3):
                data = evaluator.load_data()
            self.assertEqual(data['eligible'].tolist(), [True, False, False])
            self.assertEqual(data['output_observed'].tolist(), [True, True, False])
            self.assertEqual(data['params'][0, 0], .5)
            self.assertEqual(data['available'][0], 20 + evaluator.HOUR_US)

    def test_early_history_is_missing_and_unseen_model_is_unknown(self):
        days = np.array([10, 10], dtype=np.int32)
        data = {
            'created': np.array([10 * DAY_US + 10, 10 * DAY_US + 20]),
            'day': days,
            'codes': np.array([0, 1], dtype=np.int32),
            'params': np.full((2, 5), np.nan),
        }
        history = build_day_snapshots(
            days, np.array([10 * DAY_US + 1]),
            np.array([0]), np.array([100]), 2)
        x = feature_matrix(data, np.array([0, 1]), history,
                           np.array([0], dtype=np.int32))
        self.assertEqual(x[0, 0], 0)
        self.assertTrue(np.isnan(x[1, 0]))
        self.assertEqual(x[0, 8], 0)  # No same-day feedback.
        self.assertTrue(np.isnan(x[0, 10]))
        self.assertTrue(np.isnan(x[1, 14]))

    def test_training_sampling_has_daily_cap_and_is_repeatable(self):
        days = np.array([1] * 5_100 + [2] * 100, dtype=np.int32)
        indices = np.arange(len(days))
        first = sample_training(indices, days)
        second = sample_training(indices, days)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(first), 5_100)
        self.assertEqual(np.sum(days[first] == 1), 5_000)

    def test_validation_gate_checks_loss_coverage_and_model_majority(self):
        def method(loss, coverage):
            return {'complete_7d_block_equal_pinball_tokens': loss,
                    'overall': {'coverage': coverage}}
        validation = {
            'scores': {
                'static_global': method(10, .90),
                'static_model': method(9, .90),
                'recent_global': method(9, .90),
                'recent_model': method(8, .90),
                'hgb': method(7.5, .90),
            },
            'by_model_at_least_1000': {
                'a': {'recent_model': {'mean_pinball_tokens': 8,
                                       'coverage': .9},
                      'hgb': {'mean_pinball_tokens': 7,
                              'coverage': .9}},
                'b': {'recent_model': {'mean_pinball_tokens': 8,
                                       'coverage': .9},
                      'hgb': {'mean_pinball_tokens': 7,
                              'coverage': .9}},
                'c': {'recent_model': {'mean_pinball_tokens': 8,
                                       'coverage': .9},
                      'hgb': {'mean_pinball_tokens': 9,
                              'coverage': .9}},
            },
        }
        result = gate(validation)
        self.assertTrue(result['passes'])
        self.assertEqual(result['selected_baseline'], 'recent_model')
        validation['by_model_at_least_1000']['a']['hgb']['coverage'] = .8
        self.assertFalse(gate(validation)['passes'])


if __name__ == '__main__':
    unittest.main()
