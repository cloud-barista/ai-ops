"""The larger default batch keeps exact outputs and retry semantics."""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v4 import assess_gpu_shape_microbatch_v4
from assess_gpu_shape_microbatch_v5 import assess_gpu_shape_microbatch_v5
from test_slo_reuse import fixture


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class MicrobatchV5Tests(unittest.TestCase):
    @staticmethod
    def requests():
        bundle, request, model = fixture()
        requests = []
        for index in range(9):
            changed = copy.deepcopy(request)
            for row in changed['candidates']:
                row['workload_id'] = f'workload-{index}'
            requests.append(changed)
        return bundle, requests, model

    def test_default_reduces_calls_with_exact_output(self):
        bundle, requests, _ = self.requests()
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predictor:
            expected = assess_gpu_shape_microbatch_v4(bundle, requests)
        self.assertEqual(predictor.call_count, 2)
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predictor:
            actual = assess_gpu_shape_microbatch_v5(bundle, requests)
        self.assertEqual(predictor.call_count, 1)
        self.assertEqual(canonical(actual), canonical(expected))

    def test_large_call_failure_retries_each_original_grid(self):
        bundle, requests, _ = self.requests()
        expected = assess_gpu_shape_microbatch_v4(bundle, requests, 1)
        original = shape._predict_once

        def limited(active_bundle, candidates):
            if len(candidates) > 8:
                raise MemoryError('combined_predictor_limit')
            return original(active_bundle, candidates)

        with patch.object(shape, '_predict_once', side_effect=limited) as predictor:
            actual = assess_gpu_shape_microbatch_v5(bundle, requests)
        self.assertEqual(predictor.call_count, 10)
        self.assertEqual(canonical(actual), canonical(expected))


if __name__ == '__main__':
    unittest.main()
