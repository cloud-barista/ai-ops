"""Exact numerics, failures, isolation and support boundaries for ordered trees."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.exceptions import NotFittedError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v6 import assess_gpu_shape_microbatch_v6
from assess_gpu_shape_row_partition import assess_gpu_shape_row_partition as assess_gpu_shape_ordered_forest
from ordered_forest_predict import predict_grids_ordered
from row_partition_forest_predict import RowPartitionForestPredictor as OrderedForestPredictor
from test_slo_reuse import fixture


class RowPartitionForestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(42)
        cls.X = rng.normal(size=(1103, 5))
        cls.y = rng.normal(size=(1103, 2))
        cls.forest = ExtraTreesRegressor(n_estimators=25, n_jobs=1, random_state=42).fit(cls.X, cls.y)

    def test_dense_multioutput_all_workers_exact(self):
        expected = self.forest.predict(self.X)
        for workers in (1, 2, 4):
            with OrderedForestPredictor(workers) as predictor:
                np.testing.assert_array_equal(predictor.predict(self.forest, self.X), expected)

    def test_sparse_and_single_output_exact(self):
        f = ExtraTreesRegressor(n_estimators=9, n_jobs=1, random_state=42).fit(self.X, self.y[:, 0])
        sparse = csr_matrix(self.X)
        for workers in (1, 2, 4):
            with OrderedForestPredictor(workers) as predictor:
                np.testing.assert_array_equal(predictor.predict(f, sparse), f.predict(sparse))

    def test_nan_validation_and_predictions_exact(self):
        X = self.X.copy()
        X[::10, 0] = np.nan
        forest = ExtraTreesRegressor(n_estimators=9, n_jobs=1, random_state=42).fit(X, self.y)
        with OrderedForestPredictor(4) as engine:
            np.testing.assert_array_equal(engine.predict(forest, X), forest.predict(X))

    def test_inf_wrong_dimension_and_unfitted_rejected(self):
        X = self.X.copy()
        X[0, 0] = np.inf
        with OrderedForestPredictor() as engine:
            for bad in [X, self.X[:, :-1], self.X[:0]]:
                with self.assertRaises(ValueError):
                    engine.predict(self.forest, bad)
            with self.assertRaises(NotFittedError):
                engine.predict(ExtraTreesRegressor(), self.X)

    def test_out_of_order_completion_keeps_reduction_order(self):
        expected = self.forest.predict(self.X)
        first = self.forest.estimators_[0]
        original = first.predict
        completed = []

        def slow(*args, **kwargs):
            time.sleep(.02)
            completed.append(0)
            return original(*args, **kwargs)

        with patch.object(first, 'predict', side_effect=slow):
            with OrderedForestPredictor(4) as engine:
                np.testing.assert_array_equal(engine.predict(self.forest, self.X), expected)
        self.assertEqual(completed, [0, 0, 0, 0])

    def test_serial_small_batch_never_starts_pool_and_close(self):
        engine = OrderedForestPredictor(4)
        engine.predict(self.forest, self.X[:10])
        self.assertIsNone(engine.pool)
        engine.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            engine.predict(self.forest, self.X)
        for bad in (0, 3, True, 1.0):
            with self.assertRaises(ValueError):
                OrderedForestPredictor(bad)

    def test_one_forest_validation_and_no_repeated_tree_validation(self):
        first = self.forest.estimators_[0]
        with patch.object(self.forest, '_validate_X_predict', wraps=self.forest._validate_X_predict) as validation:
            with patch.object(first, 'predict', wraps=first.predict) as tree:
                with OrderedForestPredictor(4) as engine:
                    engine.predict(self.forest, self.X)
        self.assertEqual(validation.call_count, 1)
        self.assertIs(tree.call_args.kwargs['check_input'], False)

    def test_pool_is_closed_after_prediction_failure(self):
        first = self.forest.estimators_[0]
        engine = OrderedForestPredictor(4)
        with patch.object(first, 'predict', side_effect=MemoryError('injected tree failure')):
            with self.assertRaises(MemoryError):
                with engine:
                    engine.predict(self.forest, self.X)
        self.assertTrue(engine.closed)
        self.assertIsNone(engine.pool)

    def test_concurrent_independent_engines_do_not_mutate_forest(self):
        expected = self.forest.predict(self.X)
        def task(worker):
            with OrderedForestPredictor(worker) as engine:
                return engine.predict(self.forest, self.X)
        with ThreadPoolExecutor(2) as pool:
            for result in pool.map(task, [2, 4]):
                np.testing.assert_array_equal(result, expected)
        self.assertEqual(self.forest.n_jobs, 1)
        self.assertNotIn('predict', self.forest.__dict__)

    def test_unverified_invalid_and_unsupported_parity(self):
        bundle, q, _ = fixture()
        unsupported = copy.deepcopy(q)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unknown'
        bad = copy.deepcopy(q)
        bad['min_requests_per_second'] = float('inf')
        requests = [q, copy.deepcopy(q), unsupported, copy.deepcopy(unsupported), bad, None]
        for active in [bundle, {k: v for k, v in bundle.items() if k != shape.VERIFIED_KEY}]:
            reference = assess_gpu_shape_microbatch_v6(active, requests)
            actual = assess_gpu_shape_ordered_forest(active, requests, 4)
            self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(reference, sort_keys=True))
        actual = assess_gpu_shape_ordered_forest(bundle, requests, 4)
        actual[2]['candidate_assessments'][0]['reasons'].append('mutated')
        self.assertNotIn('mutated', actual[3]['candidate_assessments'][0]['reasons'])

    def test_large_failure_and_bad_count_retry_original_grid(self):
        bundle, q, _ = fixture()
        expected = shape._predict_once(bundle, q['candidates'])
        for mode in ('exception', 'count'):
            engine = OrderedForestPredictor(2)
            def fail(*unused):
                if mode == 'exception':
                    raise MemoryError('combined limit')
                return []
            with patch.object(engine, 'predict_records', side_effect=fail):
                result = predict_grids_ordered(bundle, {'grid': q['candidates']}, 256, engine)
            self.assertEqual(result['grid'], expected)
            engine.close()


if __name__ == '__main__':
    unittest.main()
