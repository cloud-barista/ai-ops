"""Copied-grid reuse preserves v2's public result and error boundaries."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
import assess_gpu_shape_microbatch_v4 as v4
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from test_slo_reuse import fixture


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class MicrobatchV4Tests(unittest.TestCase):
    def test_copied_equal_grid_validates_and_serializes_only_once(self):
        bundle, request, model = fixture()
        requests = []
        for index in range(25):
            changed = copy.deepcopy(request)
            changed['batch_latency_p95_limit_ms'] = 4.0 + index / 10
            changed['min_requests_per_second'] = 10.0 + index
            requests.append(changed)
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 8)
        model.calls = 0
        with patch.object(shape, '_request_errors', wraps=shape._request_errors) as checked:
            with patch.object(v4, '_grid_key', wraps=v4._grid_key) as keyed:
                actual = v4.assess_gpu_shape_microbatch_v4(bundle, requests, 8)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual((checked.call_count, keyed.call_count, model.calls),
                         (1, 1, 1))

    def test_bool_float_and_custom_value_never_match_valid_grid(self):
        class EqualInt:
            def __eq__(self, other):
                return other == 1

        bundle, request, _ = fixture()
        bool_grid = copy.deepcopy(request)
        bool_grid['candidates'][0]['replica_count'] = True
        float_grid = copy.deepcopy(request)
        float_grid['candidates'][0]['replica_count'] = 1.0
        custom_grid = copy.deepcopy(request)
        custom_grid['candidates'][0]['replica_count'] = EqualInt()
        requests = [request, bool_grid, float_grid, custom_grid]
        expected = assess_gpu_shape_microbatch_v2(bundle, requests)
        actual = v4.assess_gpu_shape_microbatch_v4(bundle, requests)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(actual[1]['reasons'],
                         ['candidate_0_invalid_resource_counts'])
        self.assertEqual(actual[2]['reasons'],
                         ['candidate_0_invalid_resource_counts'])
        self.assertEqual(actual[3]['reasons'],
                         ['candidate_0_invalid_resource_counts'])

    def test_error_order_value_changes_reordered_and_unsupported(self):
        bundle, request, _ = fixture()
        bad_slo = copy.deepcopy(request)
        bad_slo['min_requests_per_second'] = 0
        invalid = copy.deepcopy(request)
        invalid['min_requests_per_second'] = 0
        invalid['candidates'][0]['cpu_threads'] = 3
        wrong_grid = copy.deepcopy(request)
        wrong_grid['candidates'][-1]['replica_count'] = 1
        reordered = copy.deepcopy(request)
        reordered['candidates'].reverse()
        unsupported = copy.deepcopy(request)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unsupported'
        request_with_extra = {**request, 'price_usd_per_hour_max': 0.01}
        requests = [request, bad_slo, invalid, wrong_grid, reordered,
                    unsupported, copy.deepcopy(unsupported), request_with_extra,
                    None, {**request, 'batch_latency_p95_limit_ms': float('inf')}]
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 2)
        actual = v4.assess_gpu_shape_microbatch_v4(bundle, requests, 2)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(actual[2]['reasons'],
                         ['invalid_min_requests_per_second',
                          'candidate_0_cpu_threads_must_match_affinity'])
        self.assertEqual(actual[7]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertIsNot(actual[5]['candidate_assessments'][0]['reasons'],
                         actual[6]['candidate_assessments'][0]['reasons'])
        actual[5]['candidate_assessments'][0]['reasons'].append('changed')
        self.assertNotIn('changed', actual[6]['candidate_assessments'][0]['reasons'])

    def test_unverified_schema_and_combined_prediction_retry(self):
        bundle, request, model = fixture()
        request_copy = copy.deepcopy(request)
        bundle[shape.VERIFIED_KEY] = ('wrong', 'wrong')
        self.assertEqual(v4.assess_gpu_shape_microbatch_v4(
            bundle, [request, request_copy]), assess_gpu_shape_microbatch_v2(
            bundle, [request, request_copy]))
        self.assertEqual(model.calls, 0)
        bundle[shape.VERIFIED_KEY] = (shape.MODEL_SHA256, shape.SOURCE_SHA256)
        bundle['targets'] = ['wrong']
        self.assertEqual(v4.assess_gpu_shape_microbatch_v4(bundle, [request]),
                         assess_gpu_shape_microbatch_v2(bundle, [request]))
        bundle['targets'] = shape.TARGETS
        second = copy.deepcopy(request)
        for row in second['candidates']:
            row['workload_id'] = 'another-workload'
        requests = [request, second, request_copy]
        expected = assess_gpu_shape_microbatch_v2(bundle, requests, 2)
        original = shape._predict_once

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited):
            actual = v4.assess_gpu_shape_microbatch_v4(bundle, requests, 2)
        self.assertEqual(canonical(actual), canonical(expected))

    def test_argument_boundaries_and_call_scoped_cache(self):
        bundle, request, _ = fixture()
        self.assertEqual(v4.assess_gpu_shape_microbatch_v4(bundle, []), [])
        with self.assertRaisesRegex(TypeError, 'requests_must_be_list'):
            v4.assess_gpu_shape_microbatch_v4(bundle, request)
        for value in (0, 65, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,
                                            'max_grids_must_be_integer_1_to_64'):
                    v4.assess_gpu_shape_microbatch_v4(bundle, [], value)
        with patch.object(shape, '_request_errors', wraps=shape._request_errors) as checked:
            v4.assess_gpu_shape_microbatch_v4(bundle, [request], 8)
            v4.assess_gpu_shape_microbatch_v4(bundle, [request], 8)
        self.assertEqual(checked.call_count, 2)


if __name__ == '__main__':
    unittest.main()
