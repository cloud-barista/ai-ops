"""Check support-gated composition and frozen reference boundary."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from support_hybrid_transfer import (_verify_physical_artifact,
                                     compose_hybrid, select_rps_by_donor_cv)


def predictions() -> pd.DataFrame:
    return pd.DataFrame({
        'execution_id': ['known', 'new', 'causal'],
        'uses_smooth_latency': [False, True, False],
        'support_smooth_latency_latency_log': [1.0, 1.1, 1.2],
        'space_cov_transfer_rps_log': [2.0, 2.1, 2.2],
        'space_kernel_transfer_rps_log': [3.0, 3.1, 3.2],
        'space_ridge_rps_log': [4.0, 4.1, 4.2],
    })


class SupportHybridTransferTest(unittest.TestCase):
    def test_only_new_same_task_condition_switches_rps(self):
        out = compose_hybrid(predictions(), 'ridge')
        np.testing.assert_allclose(out.support_hybrid_latency_log, [1.0, 1.1, 1.2])
        np.testing.assert_allclose(out.support_hybrid_rps_log, [2.0, 4.1, 2.2])
        self.assertEqual(list(out.selected_new_condition_rps_method),
                         ['cov', 'ridge', 'cov'])

    def test_candidate_uses_only_frozen_predictions(self):
        before = compose_hybrid(predictions(), 'kernel')
        source = predictions()
        source['unmeasured_latency_p95_ms'] = [1e9, 1e9, 1e9]
        source['unmeasured_rps'] = [-1e9, -1e9, -1e9]
        after = compose_hybrid(source, 'kernel')
        np.testing.assert_allclose(before.support_hybrid_latency_log,
                                   after.support_hybrid_latency_log)
        np.testing.assert_allclose(before.support_hybrid_rps_log,
                                   after.support_hybrid_rps_log)
        with self.assertRaises(ValueError):
            compose_hybrid(predictions(), 'external_best')

    def test_rejects_duplicate_and_nonfinite_frozen_predictions(self):
        repeated = pd.concat([predictions(), predictions().iloc[[0]]])
        with self.assertRaises(ValueError):
            compose_hybrid(repeated, 'ridge')
        nan = predictions()
        nan.loc[1, 'space_ridge_rps_log'] = np.nan
        with self.assertRaises(ValueError):
            compose_hybrid(nan, 'ridge')

    def test_mismatched_snapshot_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            snapshot = base / 'snapshot.parquet'
            snapshot.write_bytes(b'snapshot')
            pred = base / 'predictions.parquet'
            pred.write_bytes(b'fake')
            (base / 'metrics.json').write_text(json.dumps({
                'source_snapshot_sha256': 'wrong', 'script_sha256': 'wrong'}))
            with self.assertRaises(ValueError):
                _verify_physical_artifact(snapshot, pred)

    def test_donor_selection_requires_multiple_same_task_models(self):
        tiny = pd.DataFrame({'workload_task': ['inference'] * 4,
                             'model_id': ['a', 'b', 'c', 'd'],
                             'batch_size': [1] * 4,
                             'max_length': [64] * 4,
                             'request_concurrency': [1] * 4,
                             'repeats': [2] * 4})
        with self.assertRaises(ValueError):
            select_rps_by_donor_cv(tiny)


if __name__ == '__main__':
    unittest.main()
