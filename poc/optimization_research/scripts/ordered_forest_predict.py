"""Call-scoped exact ExtraTrees prediction with one validated input matrix.

Pinned to the locally audited sklearn1.6.1 forest/tree validation behavior.
Individual tree outputs are accumulated in estimator order, including when
tree traversal runs concurrently. No fitted parameters or caller object change.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from types import MethodType

import numpy as np
import sklearn
from sklearn.ensemble import ExtraTreesRegressor

import assess_gpu_shape as shape
from predict_performance import predict_records

TREE_WINDOW = 16
PARALLEL_MIN_ROWS = 1024


class OrderedForestPredictor:
    def __init__(self, workers=1):
        if type(workers) is not int or workers not in (1, 2, 4):
            raise ValueError('workers_must_be_1_2_or_4')
        self.workers = workers
        self.pool = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True)
            self.pool = None
        self.closed = True

    def predict(self, forest, X):
        if self.closed:
            raise RuntimeError('predictor_already_closed')
        if type(forest) is not ExtraTreesRegressor or sklearn.__version__ != '1.6.1':
            raise TypeError('ordered_predict_requires_sklearn_1_6_1_ExtraTreesRegressor')
        # This preserves fitted/shape/missing-value/sparse validation exactly.
        X = forest._validate_X_predict(X)
        shape_out = (X.shape[0], forest.n_outputs_) if forest.n_outputs_ > 1 else (X.shape[0],)
        prediction = np.zeros(shape_out, dtype=np.float64)

        def traverse(tree):
            return tree.predict(X, check_input=False)

        trees = forest.estimators_
        if self.workers == 1 or X.shape[0] < PARALLEL_MIN_ROWS:
            for tree in trees:
                prediction += traverse(tree)
        else:
            if self.pool is None:
                self.pool = ThreadPoolExecutor(max_workers=self.workers)
            for start in range(0, len(trees), TREE_WINDOW):
                # map returns in input order. Only16 individual output arrays
                # can be outstanding; never sum subsets in a different order.
                for part in self.pool.map(traverse, trees[start:start + TREE_WINDOW]):
                    prediction += part
        prediction /= len(trees)
        return prediction

    def predict_records(self, bundle, candidates):
        if bundle.get('selected') != 'extra_trees':
            return shape._predict_once(bundle, candidates)
        pipeline = copy.copy(bundle['model'])
        pipeline.steps = [(name, copy.copy(step) if name == 'regressor' else step)
                          for name, step in pipeline.steps]
        regressor = pipeline.named_steps['regressor']
        engine = self

        def ordered_predict(active_forest, X):
            return engine.predict(active_forest, X)

        # Existing predict_records owns support checks, exp and output schema.
        # Configure workers changes only this independent shallow regressor.
        regressor.predict = MethodType(ordered_predict, regressor)
        return predict_records({**bundle, 'model': pipeline}, candidates)


def predict_grids_ordered(bundle, grids, max_grids, engine):
    items = list(grids.items())
    results = {}
    for start in range(0, len(items), max_grids):
        chunk = items[start:start + max_grids]
        candidates = [row for _, grid in chunk for row in grid]
        try:
            raw = engine.predict_records(bundle, candidates)
        except Exception:
            raw = None
        if not isinstance(raw, list) or len(raw) != 8 * len(chunk):
            # Original per-grid predictor is the established failure fallback.
            for key, grid in chunk:
                results[key] = shape._predict_once(bundle, grid)
        else:
            for index, (key, _) in enumerate(chunk):
                results[key] = raw[8 * index:8 * (index + 1)]
    return results
