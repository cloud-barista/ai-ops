"""Parallelize rows while preserving the serial estimator sum for every row."""
import numpy as np
import sklearn
from concurrent.futures import ThreadPoolExecutor
from sklearn.ensemble import ExtraTreesRegressor

from ordered_forest_predict import OrderedForestPredictor, PARALLEL_MIN_ROWS


def sum_serial_partition(forest, X):
    out_shape = (X.shape[0], forest.n_outputs_) if forest.n_outputs_ > 1 else (X.shape[0],)
    result = np.zeros(out_shape, dtype=np.float64)
    for tree in forest.estimators_:
        result += tree.predict(X, check_input=False)
    result /= len(forest.estimators_)
    return result


class RowPartitionForestPredictor(OrderedForestPredictor):
    def predict(self, forest, X):
        if self.closed:
            raise RuntimeError('predictor_already_closed')
        if type(forest) is not ExtraTreesRegressor or sklearn.__version__ != '1.6.1':
            raise TypeError('row_predict_requires_sklearn_1_6_1_ExtraTreesRegressor')
        X = forest._validate_X_predict(X)
        if self.workers == 1 or X.shape[0] < PARALLEL_MIN_ROWS:
            return sum_serial_partition(forest, X)
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=self.workers)
        # Balanced contiguous blocks and ordered map preserve output row mapping.
        edges = np.linspace(0, X.shape[0], self.workers + 1, dtype=int)
        blocks = [X[edges[i]:edges[i + 1]] for i in range(self.workers)]
        return np.concatenate(list(self.pool.map(lambda part: sum_serial_partition(forest, part), blocks)), axis=0)
