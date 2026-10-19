"""The 256-grid path preserves exact decisions and per-grid retry."""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v5 import assess_gpu_shape_microbatch_v5
from assess_gpu_shape_microbatch_v6 import assess_gpu_shape_microbatch_v6
from test_slo_reuse import fixture


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class MicrobatchV6Tests(unittest.TestCase):
    @staticmethod
    def requests(count=65):
        bundle, request, _ = fixture()
        requests = []
        for index in range(count):
            changed = copy.deepcopy(request)
            for row in changed['candidates']:
                row['workload_id'] = f'workload-{index}'
            requests.append(changed)
        return bundle, requests

    def test_default_crosses_old_bound_with_exact_output(self):
        bundle, requests = self.requests()
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predictor:
            expected = assess_gpu_shape_microbatch_v5(bundle, requests)
        self.assertEqual(predictor.call_count, 2)
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predictor:
            actual = assess_gpu_shape_microbatch_v6(bundle, requests)
        self.assertEqual(predictor.call_count, 1)
        self.assertEqual(canonical(actual), canonical(expected))

    def test_large_prediction_failure_retries_original_grids(self):
        bundle, requests = self.requests()
        expected = assess_gpu_shape_microbatch_v5(bundle, requests, max_grids=1)
        original = shape._predict_once

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited) as predictor:
            actual = assess_gpu_shape_microbatch_v6(bundle, requests)
        self.assertEqual(predictor.call_count, 66)
        self.assertEqual(canonical(actual), canonical(expected))

    def test_copied_grid_reuse_and_invalid_request_parity(self):
        bundle, requests = self.requests(1)
        copied = [copy.deepcopy(requests[0]) for _ in range(65)]
        copied[6]['batch_latency_p95_limit_ms'] = -1
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predictor:
            actual = assess_gpu_shape_microbatch_v6(bundle, copied)
        self.assertEqual(predictor.call_count, 1)
        self.assertEqual(canonical(actual),
                         canonical(assess_gpu_shape_microbatch_v5(bundle, copied)))

    def test_new_batch_limit_does_not_change_old_limit(self):
        bundle, requests = self.requests(1)
        for value in (0, True, 257, 1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'max_grids_must_be_integer_1_to_256'):
                    assess_gpu_shape_microbatch_v6(bundle, requests, value)
        with self.assertRaisesRegex(ValueError, 'max_grids_must_be_integer_1_to_64'):
            assess_gpu_shape_microbatch_v5(bundle, requests, 65)

    def test_mixed_invalid_unsupported_and_reordered_requests(self):
        bundle, requests = self.requests(2)
        bad_slo = copy.deepcopy(requests[0])
        bad_slo['min_requests_per_second'] = 0
        bad_candidate = copy.deepcopy(requests[0])
        bad_candidate['candidates'][0]['replica_count'] = True
        reordered = copy.deepcopy(requests[1])
        reordered['candidates'].reverse()
        unsupported = copy.deepcopy(requests[0])
        for row in unsupported['candidates']:
            row['model_revision'] = 'unsupported'
        mixed = [requests[0], bad_slo, bad_candidate, reordered,
                 unsupported, copy.deepcopy(unsupported), None]
        old = assess_gpu_shape_microbatch_v5(bundle, mixed)
        new = assess_gpu_shape_microbatch_v6(bundle, mixed)
        self.assertEqual(canonical(new), canonical(old))
        new[4]['candidate_assessments'][0]['reasons'].append('mutated')
        self.assertNotIn('mutated', new[5]['candidate_assessments'][0]['reasons'])


if __name__ == '__main__':
    unittest.main()
