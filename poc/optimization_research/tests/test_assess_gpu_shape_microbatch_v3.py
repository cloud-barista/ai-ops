"""Shared-grid validation reuse must preserve the original assessor boundary."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
import assess_gpu_shape_microbatch_v3 as v3
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from test_slo_reuse import fixture


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class MicrobatchV3Tests(unittest.TestCase):
    def test_shared_candidate_object_validates_and_serializes_once(self):
        bundle, request, model = fixture()
        requests = []
        for index in range(25):
            requests.append({**request,
                             'batch_latency_p95_limit_ms': 4.0 + index / 10,
                             'min_requests_per_second': 10.0 + index})
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 8)
        model.calls = 0
        with patch.object(shape, '_request_errors', wraps=shape._request_errors) as checked:
            with patch.object(v3, '_grid_key', wraps=v3._grid_key) as keyed:
                actual = v3.assess_gpu_shape_microbatch_v3(bundle, requests, 8)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(keyed.call_count, 1)
        self.assertEqual(model.calls, 1)

    def test_invalid_slo_still_combines_candidate_errors_in_original_order(self):
        bundle, request, _ = fixture()
        invalid_grid = copy.deepcopy(request)
        invalid_grid['min_requests_per_second'] = 0
        invalid_grid['candidates'][0]['cpu_threads'] = 3
        both = [invalid_grid, {**invalid_grid}]
        expected = assess_gpu_shape_microbatch_v2(bundle, both, 8)
        actual = v3.assess_gpu_shape_microbatch_v3(bundle, both, 8)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(actual[0]['reasons'],
                         ['invalid_min_requests_per_second',
                          'candidate_0_cpu_threads_must_match_affinity'])
        self.assertIsNot(actual[0]['reasons'], actual[1]['reasons'])
        actual[0]['reasons'].append('changed')
        self.assertNotIn('changed', actual[1]['reasons'])

    def test_early_header_error_and_cache_do_not_cross_calls(self):
        bundle, request, _ = fixture()
        invalid_grid = copy.deepcopy(request)
        invalid_grid['candidates'][0]['cpu_threads'] = 3
        unsupported_field = {**invalid_grid, 'price_usd_per_hour_max': 0.01}
        missing_field = {**invalid_grid}
        del missing_field['min_requests_per_second']
        expected = assess_gpu_shape_microbatch_v2(
            bundle, [unsupported_field, missing_field, invalid_grid], 8)
        actual = v3.assess_gpu_shape_microbatch_v3(
            bundle, [unsupported_field, missing_field, invalid_grid], 8)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(actual[0]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertEqual(actual[1]['reasons'],
                         ['missing_request_fields:min_requests_per_second'])

        with patch.object(shape, '_request_errors', wraps=shape._request_errors) as checked:
            v3.assess_gpu_shape_microbatch_v3(bundle, [request], 8)
            v3.assess_gpu_shape_microbatch_v3(bundle, [request], 8)
        self.assertEqual(checked.call_count, 2)

    def test_mixed_boundaries_and_separate_equal_objects_match_v2(self):
        bundle, request, _ = fixture()
        bad_slo_shared = {**request, 'min_requests_per_second': 0}
        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_revision'] = 'not-supported'
        incomplete = copy.deepcopy(request)
        incomplete['candidates'].pop()
        reordered = copy.deepcopy(request)
        reordered['candidates'].reverse()
        duplicate_shape = copy.deepcopy(request)
        duplicate_shape['candidates'][-1]['replica_count'] = 1
        unsupported_field = {**request, 'price_usd_per_hour_max': 0.01}
        requests = [request, bad_slo_shared, copy.deepcopy(request), unsupported,
                    incomplete, reordered, duplicate_shape, unsupported_field,
                    None, {**request, 'batch_latency_p95_limit_ms': float('inf')},
                    {**request, 'min_requests_per_second': 25.0},
                    copy.deepcopy(unsupported)]
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 2)
        actual = v3.assess_gpu_shape_microbatch_v3(bundle, requests, 2)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(actual[7]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertNotIn('selected', actual[7])
        self.assertIsNot(actual[3]['candidate_assessments'][0]['reasons'],
                         actual[11]['candidate_assessments'][0]['reasons'])
        actual[3]['candidate_assessments'][0]['reasons'].append('changed')
        self.assertNotIn('changed', actual[11]['candidate_assessments'][0]['reasons'])

    def test_combined_predict_failure_retries_per_grid(self):
        bundle, first, _ = fixture()
        second = copy.deepcopy(first)
        for row in second['candidates']:
            row['workload_id'] = 'another-workload'
        requests = [first, second, {**first, 'min_requests_per_second': 18.0}]
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 2)
        original = shape._predict_once

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited):
            actual = v3.assess_gpu_shape_microbatch_v3(bundle, requests, 2)
        self.assertEqual(canonical(actual), canonical(expected))

        def failing_single(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            if candidates[0]['workload_id'] == 'another-workload':
                raise ValueError('original_grid_failure')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=failing_single):
            with self.assertRaisesRegex(ValueError, 'original_grid_failure'):
                v3.assess_gpu_shape_microbatch_v3(bundle, requests, 2)

    def test_unverified_schema_empty_and_argument_boundaries(self):
        bundle, request, model = fixture()
        self.assertEqual(v3.assess_gpu_shape_microbatch_v3(bundle, []), [])
        bundle[shape.VERIFIED_KEY] = ('wrong', 'wrong')
        expected = assess_gpu_shape_microbatch_v2(bundle, [request, request])
        self.assertEqual(v3.assess_gpu_shape_microbatch_v3(bundle, [request, request]),
                         expected)
        self.assertEqual(model.calls, 0)
        bundle[shape.VERIFIED_KEY] = (shape.MODEL_SHA256, shape.SOURCE_SHA256)
        bundle['targets'] = ['wrong']
        self.assertEqual(v3.assess_gpu_shape_microbatch_v3(bundle, [request]),
                         assess_gpu_shape_microbatch_v2(bundle, [request]))
        self.assertEqual(model.calls, 0)
        with self.assertRaisesRegex(TypeError, 'requests_must_be_list'):
            v3.assess_gpu_shape_microbatch_v3(bundle, request)
        for value in (0, 65, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'max_grids_must_be_integer_1_to_64'):
                    v3.assess_gpu_shape_microbatch_v3(bundle, [], value)


if __name__ == '__main__':
    unittest.main()
