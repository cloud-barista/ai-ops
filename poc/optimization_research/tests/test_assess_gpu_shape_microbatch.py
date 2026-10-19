"""Exact offline coalescing across distinct GPU benchmark workloads."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import assess_gpu_shape_microbatch
from test_slo_reuse import fixture


class MicrobatchTests(unittest.TestCase):
    def test_distinct_grids_duplicate_slos_and_boundaries_match(self):
        bundle, request, model = fixture()
        second = copy.deepcopy(request)
        for row in second['candidates']:
            row['application_id'] = 'app-b'
            row['workload_id'] = 'workload-b'
        reordered = copy.deepcopy(second)
        reordered['candidates'].reverse()
        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unknown'
        invalid_slo = copy.deepcopy(request)
        invalid_slo['batch_latency_p95_limit_ms'] = 0
        incomplete = copy.deepcopy(request)
        incomplete['candidates'].pop()
        unsupported_constraint = copy.deepcopy(request)
        unsupported_constraint['price_usd_per_hour_max'] = 0.01
        requests = [request, second, invalid_slo, unsupported,
                    reordered, None, copy.deepcopy(request), incomplete,
                    copy.deepcopy(unsupported), unsupported_constraint]
        expected = shape.assess_gpu_shape_many(bundle, requests)
        model.calls = 0
        actual = assess_gpu_shape_microbatch(bundle, requests, max_grids=8)
        self.assertEqual(json.dumps(actual, sort_keys=True),
                         json.dumps(expected, sort_keys=True))
        self.assertEqual(model.calls, 1)  # all supported grids in one predict
        self.assertEqual(actual[0], actual[6])
        self.assertEqual(actual[2]['reasons'], ['invalid_batch_latency_p95_limit_ms'])
        self.assertEqual(actual[3]['status'], 'ABSTAIN')
        self.assertEqual(actual[4]['candidate_assessments'][0]['gpu_replicas'],
                         second['candidates'][-1]['replica_count'])
        self.assertEqual(actual[5]['reasons'], ['request_must_be_object'])
        self.assertEqual(actual[7]['reasons'], ['expected_exactly_eight_candidates'])
        self.assertEqual(actual[9]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertNotIn('selected', actual[9])
        self.assertIsNot(actual[3]['candidate_assessments'][0]['reasons'],
                         actual[8]['candidate_assessments'][0]['reasons'])
        actual[3]['candidate_assessments'][0]['reasons'].append('changed')
        self.assertNotIn('changed', actual[8]['candidate_assessments'][0]['reasons'])

    def test_smaller_chunks_keep_exact_output(self):
        bundle, request, model = fixture()
        requests = []
        for number in range(5):
            changed = copy.deepcopy(request)
            for row in changed['candidates']:
                row['workload_id'] = f'workload-{number}'
            requests.extend((changed, copy.deepcopy(changed)))
        expected = shape.assess_gpu_shape_many(bundle, requests)
        model.calls = 0
        actual = assess_gpu_shape_microbatch(bundle, requests, max_grids=2)
        self.assertEqual(actual, expected)
        self.assertEqual(model.calls, 3)

    def test_combined_predictor_failure_retries_original_grid_boundary(self):
        bundle, request, _ = fixture()
        second = copy.deepcopy(request)
        for row in second['candidates']:
            row['workload_id'] = 'another-workload'
        requests = [request, second]
        expected = shape.assess_gpu_shape_many(bundle, requests)
        original = shape._predict_once

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited):
            actual = assess_gpu_shape_microbatch(bundle, requests, max_grids=2)
        self.assertEqual(actual, expected)

        def failing_single(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            if candidates[0]['workload_id'] == 'another-workload':
                raise ValueError('original_grid_failure')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=failing_single):
            with self.assertRaisesRegex(ValueError, 'original_grid_failure'):
                assess_gpu_shape_microbatch(bundle, requests, max_grids=2)

    def test_unverified_schema_and_argument_rejection(self):
        bundle, request, model = fixture()
        bundle[shape.VERIFIED_KEY] = ('wrong', 'wrong')
        self.assertEqual(assess_gpu_shape_microbatch(bundle, [request, request]),
                         shape.assess_gpu_shape_many(bundle, [request, request]))
        self.assertEqual(model.calls, 0)
        bundle[shape.VERIFIED_KEY] = (shape.MODEL_SHA256, shape.SOURCE_SHA256)
        bundle['targets'] = ['wrong']
        self.assertEqual(assess_gpu_shape_microbatch(bundle, [request]),
                         shape.assess_gpu_shape_many(bundle, [request]))
        self.assertEqual(model.calls, 0)
        with self.assertRaisesRegex(TypeError, 'requests_must_be_list'):
            assess_gpu_shape_microbatch(bundle, request)
        for size in (0, -1, 65, True, 2.5):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, 'max_grids_must_be_integer_1_to_64'):
                    assess_gpu_shape_microbatch(bundle, [], size)


if __name__ == '__main__':
    unittest.main()
