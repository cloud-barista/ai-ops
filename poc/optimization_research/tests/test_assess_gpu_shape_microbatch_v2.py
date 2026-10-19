"""One-pass validation and result isolation for research GPU microbatching."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import assess_gpu_shape_microbatch
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from test_slo_reuse import fixture


class MicrobatchV2Tests(unittest.TestCase):
    def requests(self):
        bundle, first, _ = fixture()
        another = copy.deepcopy(first)
        for row in another['candidates']:
            row['workload_id'] = 'another-workload'
        unsupported = copy.deepcopy(first)
        for row in unsupported['candidates']:
            row['model_revision'] = 'unsupported-revision'
        invalid = copy.deepcopy(first)
        invalid['min_requests_per_second'] = 0
        reordered = copy.deepcopy(another)
        reordered['candidates'].reverse()
        unsupported_constraint = copy.deepcopy(first)
        unsupported_constraint['price_usd_per_hour_max'] = 0.01
        return bundle, [first, another, invalid, unsupported, reordered,
                        None, copy.deepcopy(unsupported), copy.deepcopy(first),
                        unsupported_constraint]

    def test_exact_results_with_one_validation_per_request(self):
        bundle, requests = self.requests()
        expected = assess_gpu_shape_microbatch(bundle, requests, max_grids=2)
        original = shape._request_errors
        with patch.object(shape, '_request_errors', wraps=original) as checked:
            actual = assess_gpu_shape_microbatch_v2(bundle, requests, max_grids=2)
        self.assertEqual(checked.call_count, len(requests))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[8]['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertNotIn('selected', actual[8])
        self.assertIsNot(actual[3]['candidate_assessments'][0]['reasons'],
                         actual[6]['candidate_assessments'][0]['reasons'])
        actual[3]['candidate_assessments'][0]['reasons'].append('changed')
        self.assertNotIn('changed', actual[6]['candidate_assessments'][0]['reasons'])

    def test_combined_failure_retries_per_grid(self):
        bundle, requests = self.requests()
        original = shape._predict_once
        expected = assess_gpu_shape_microbatch(bundle, requests[:2], max_grids=2)

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited):
            actual = assess_gpu_shape_microbatch_v2(bundle, requests[:2], max_grids=2)
        self.assertEqual(actual, expected)

    def test_unverified_bundle_and_invalid_arguments(self):
        bundle, requests = self.requests()
        bundle[shape.VERIFIED_KEY] = ('wrong', 'wrong')
        self.assertEqual(assess_gpu_shape_microbatch_v2(bundle, requests),
                         assess_gpu_shape_microbatch(bundle, requests))
        with self.assertRaisesRegex(TypeError, 'requests_must_be_list'):
            assess_gpu_shape_microbatch_v2(bundle, requests[0])
        for value in (0, 65, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'max_grids_must_be_integer_1_to_64'):
                    assess_gpu_shape_microbatch_v2(bundle, [], value)


if __name__ == '__main__':
    unittest.main()
