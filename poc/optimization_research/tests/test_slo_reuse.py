"""Exact reuse of raw predictions while the original assessor owns decisions."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assess_gpu_shape import (MODEL_SHA256, SOURCE_SHA256, VERIFIED_KEY,
                              assess_gpu_shape)
from evaluate_slo_reuse import CachedPredictor, MULTIPLIERS, make_requests
import evaluate_slo_reuse as reuse_module
import assess_gpu_shape as shape_module
from train_performance import FEATURES, TARGETS


class CountingModel:
    def __init__(self):
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        cores = frame.cpu_core_limit.to_numpy(dtype=float)
        replicas = frame.replica_count.to_numpy(dtype=float)
        batch = frame.batch_size.to_numpy(dtype=float)
        return np.log(np.column_stack((np.full(len(frame), 4.0),
                                       batch * (2 * cores + 5 * replicas))))


def fixture():
    candidates = []
    for replicas in (1, 2):
        for cores in (2, 4, 8, 16):
            candidates.append({
                'application_id': 'app', 'workload_id': 'w',
                'model_id': 'm', 'model_revision': 'rev',
                'device': 'cuda', 'host': 'h', 'device_name': 'gpu',
                'batch_size': 2, 'max_length': 32,
                'request_concurrency': 1, 'cpu_threads': cores,
                'cpu_core_limit': cores, 'replica_count': replicas,
                'repeats': 8,
            })
    model = CountingModel()
    bundle = {
        VERIFIED_KEY: (MODEL_SHA256, SOURCE_SHA256),
        'selected': 'test_regressor', 'model': model,
        'features': FEATURES, 'targets': TARGETS,
        'categorical_support': {'model_id': ['m'],
                                'model_revision': ['rev', 'rev2'],
                                'device': ['cuda'], 'host': ['h', 'h2'],
                                'device_name': ['gpu']},
        'numeric_support': {
            'batch_size': [2, 4], 'max_length': [32, 64],
            'request_concurrency': [1, 2], 'cpu_threads': [2, 16],
            'cpu_core_limit': [2, 16], 'replica_count': [1, 2],
            'repeats': [7, 8],
        },
        'support_combination_columns': [
            'model_id', 'model_revision', 'host', 'device', 'device_name',
            'replica_count',
        ],
        'support_combinations': [
            ('m', revision, host, 'cuda', 'gpu', replicas)
            for revision in ('rev', 'rev2') for host in ('h', 'h2')
            for replicas in (1, 2)
        ],
    }
    request = {'batch_latency_p95_limit_ms': 5.0,
               'min_requests_per_second': 12.0,
               'candidates': candidates}
    return bundle, request, model


class SloReuseTests(unittest.TestCase):
    def test_shallow_copied_extra_trees_path_still_hits_24_times(self):
        class FakePipeline:
            def __init__(self):
                self.steps = [('regressor', object())]

        bundle, request, _ = fixture()
        bundle['selected'] = 'extra_trees'
        bundle['model'] = FakePipeline()
        calls = []

        def fake_predict(_copied_bundle, records):
            calls.append(1)
            return [{'status': 'PREDICTED',
                     'prediction': {'latency_p95_ms': 4.0,
                                    'examples_per_second': float(
                                        row['batch_size']
                                        * (2 * row['cpu_core_limit']
                                           + 5 * row['replica_count']))}}
                    for row in records]

        requests = []
        for lm in MULTIPLIERS:
            for rm in MULTIPLIERS:
                changed = copy.deepcopy(request)
                changed['batch_latency_p95_limit_ms'] *= lm
                changed['min_requests_per_second'] *= rm
                requests.append(changed)
        with patch.object(shape_module, 'predict_records', fake_predict):
            fresh = [shape_module.assess_gpu_shape(bundle, q) for q in requests]
        self.assertEqual(len(calls), 25)
        with patch.object(reuse_module, 'ORIGINAL_PREDICT', fake_predict):
            reuse = CachedPredictor()
            cached = [reuse.assess(bundle, q) for q in requests]
        self.assertEqual(fresh, cached)
        self.assertEqual((reuse.hits, reuse.misses), (24, 1))
        self.assertEqual(len(calls), 26)

    def test_all_slo_outputs_match_with_one_model_call(self):
        bundle, request, model = fixture()
        requests = []
        for lm in MULTIPLIERS:
            for rm in MULTIPLIERS:
                changed = copy.deepcopy(request)
                changed['batch_latency_p95_limit_ms'] *= lm
                changed['min_requests_per_second'] *= rm
                requests.append(changed)
        fresh = [assess_gpu_shape(bundle, q) for q in requests]
        self.assertEqual(model.calls, 25)
        reuse = CachedPredictor()
        cached = [reuse.assess(bundle, q) for q in requests]
        self.assertEqual(fresh, cached)
        self.assertEqual((reuse.hits, reuse.misses), (24, 1))
        self.assertEqual(model.calls, 26)
        self.assertIn('RESEARCH_ESTIMATE', [r['status'] for r in cached])
        self.assertIn('ABSTAIN', [r['status'] for r in cached])

    def test_key_tracks_order_candidates_support_and_model(self):
        bundle, request, model = fixture()
        reuse = CachedPredictor()
        reuse.assess(bundle, request)
        self.assertEqual(reuse.misses, 1)
        for field, value in [('model_revision', 'rev2'), ('host', 'h2'),
                             ('repeats', 7), ('batch_size', 4),
                             ('max_length', 64), ('request_concurrency', 2)]:
            changed = copy.deepcopy(request)
            for row in changed['candidates']:
                row[field] = value
            self.assertEqual(reuse.assess(bundle, changed),
                             assess_gpu_shape(bundle, changed))
            self.assertEqual(reuse.misses, 2)
            reuse = CachedPredictor()
            reuse.assess(bundle, request)
        changed = copy.deepcopy(request)
        changed['candidates'].reverse()
        self.assertEqual(reuse.assess(bundle, changed),
                         assess_gpu_shape(bundle, changed))
        self.assertEqual(reuse.misses, 2)
        bundle['support_combinations'] = bundle['support_combinations'][:-1]
        self.assertEqual(reuse.assess(bundle, request),
                         assess_gpu_shape(bundle, request))
        self.assertEqual(reuse.misses, 3)
        bundle['model'] = CountingModel()
        self.assertEqual(reuse.assess(bundle, request),
                         assess_gpu_shape(bundle, request))
        self.assertEqual(reuse.misses, 4)
        self.assertGreater(model.calls, 0)

    def test_invalid_slo_or_unverified_bundle_never_uses_cached_predictions(self):
        bundle, request, _ = fixture()
        reuse = CachedPredictor()
        reuse.assess(bundle, request)
        invalid = copy.deepcopy(request)
        invalid['batch_latency_p95_limit_ms'] = -1
        self.assertEqual(reuse.assess(bundle, invalid),
                         assess_gpu_shape(bundle, invalid))
        self.assertEqual((reuse.hits, reuse.misses), (0, 1))
        bundle[VERIFIED_KEY] = ('other-model', SOURCE_SHA256)
        result = reuse.assess(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertEqual(result['reasons'], ['unverified_model_bundle'])
        self.assertEqual((reuse.hits, reuse.misses), (0, 1))

    def test_unsupported_candidate_reasons_are_identical(self):
        bundle, request, _ = fixture()
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        fresh = assess_gpu_shape(bundle, request)
        reuse = CachedPredictor()
        first = reuse.assess(bundle, request)
        changed_slo = copy.deepcopy(request)
        changed_slo['min_requests_per_second'] = 1000.0
        second = reuse.assess(bundle, changed_slo)
        self.assertEqual(fresh, first)
        self.assertEqual(second, assess_gpu_shape(bundle, changed_slo))
        self.assertEqual((reuse.hits, reuse.misses), (1, 1))
        self.assertEqual(second['status'], 'ABSTAIN')
        self.assertIn('unsupported_candidates:', second['reasons'][0])


if __name__ == '__main__':
    unittest.main()
