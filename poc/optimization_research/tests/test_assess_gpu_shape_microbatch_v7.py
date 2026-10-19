"""Fixed worker routing, contract parity and failure fallback for research v7."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v6 import assess_gpu_shape_microbatch_v6
from assess_gpu_shape_microbatch_v7 import assess_gpu_shape_microbatch_v7
import assess_gpu_shape_microbatch_v7 as v7
from row_partition_forest_predict import RowPartitionForestPredictor
from test_slo_reuse import fixture


class MicrobatchV7Tests(unittest.TestCase):
    def test_fixed_two_workers_and_original_batch_bound(self):
        bundle, request, _ = fixture()
        with patch.object(v7, 'assess_gpu_shape_row_partition', return_value=[]) as route:
            assess_gpu_shape_microbatch_v7(bundle, [request], max_grids=64)
        route.assert_called_once_with(bundle, [request], workers=2, max_grids=64)
        for bad in [0, 257, True, 1.0]:
            with self.assertRaises(ValueError):
                assess_gpu_shape_microbatch_v7(bundle, [request], bad)

    def test_mixed_request_contract_and_independent_reason_lists(self):
        bundle, request, _ = fixture()
        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unknown'
        bad = copy.deepcopy(request)
        bad['min_requests_per_second'] = 0
        reordered = copy.deepcopy(request)
        reordered['candidates'].reverse()
        mixed = [request, copy.deepcopy(request), unsupported, copy.deepcopy(unsupported), bad, reordered, None]
        actual = assess_gpu_shape_microbatch_v7(bundle, mixed)
        expected = assess_gpu_shape_microbatch_v6(bundle, mixed)
        self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(expected, sort_keys=True))
        actual[2]['candidate_assessments'][0]['reasons'].append('mutated')
        self.assertNotIn('mutated', actual[3]['candidate_assessments'][0]['reasons'])

    def test_combined_failure_uses_original_predictor(self):
        bundle, q, _ = fixture()
        requests = []
        for i in range(65):
            request = copy.deepcopy(q)
            for row in request['candidates']:
                row['workload_id'] = f'w{i}'
            requests.append(request)
        reference = assess_gpu_shape_microbatch_v6(bundle, requests)
        with patch.object(RowPartitionForestPredictor, 'predict_records', side_effect=MemoryError('combined_limit')):
            with patch.object(shape, '_predict_once', wraps=shape._predict_once) as retry:
                actual = assess_gpu_shape_microbatch_v7(bundle, requests)
        self.assertEqual(actual, reference)
        self.assertEqual(retry.call_count, 65)


if __name__ == '__main__':
    unittest.main()
