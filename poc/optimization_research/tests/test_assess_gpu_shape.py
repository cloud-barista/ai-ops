"""Fail-closed contract for the offline eight-candidate shape estimate."""
import json
import hashlib
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import joblib
import pandas as pd
import sklearn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assess_gpu_shape as shape
from assess_gpu_shape import (MODEL_SHA256, SOURCE_SHA256, TRAIN_SKLEARN_VERSION,
                              VERIFIED_KEY, _assess_gpu_shape,
                              assess_gpu_shape, load_verified_bundle)
from predict_performance import predict_records
from train_performance import FEATURES, PRIMARY, ROOT, TARGETS


class CountingModel:
    def __init__(self):
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        replicas = frame['replica_count'].to_numpy(dtype=float)
        cores = frame['cpu_core_limit'].to_numpy(dtype=float)
        batches = frame['batch_size'].to_numpy(dtype=float)
        rps = 2 * cores + 5 * replicas
        return np.log(np.column_stack((np.full(len(frame), 4.0), batches * rps)))


def fixture():
    candidates = []
    for replicas in (2, 1):
        for cores in (16, 8, 4, 2):
            candidates.append({
                'application_id': 'app-a', 'workload_id': 'workload-a',
                'model_id': 'a', 'model_revision': 'rev', 'device': 'cuda',
                'host': 'h', 'device_name': 'gpu', 'batch_size': 2,
                'max_length': 32, 'request_concurrency': 1,
                'cpu_threads': cores, 'cpu_core_limit': cores,
                'replica_count': replicas, 'repeats': 8,
            })
    model = CountingModel()
    bundle = {
        VERIFIED_KEY: (MODEL_SHA256, SOURCE_SHA256),
        'selected': 'test_regressor', 'model': model,
        'features': FEATURES, 'targets': TARGETS,
        'categorical_support': {
            'model_id': ['a'], 'model_revision': ['rev'], 'device': ['cuda'],
            'host': ['h'], 'device_name': ['gpu'],
        },
        'numeric_support': {
            'batch_size': [2, 2], 'max_length': [32, 32],
            'request_concurrency': [1, 1], 'cpu_threads': [2, 16],
            'cpu_core_limit': [2, 16], 'replica_count': [1, 2],
            'repeats': [8, 8],
        },
        'support_combination_columns': [
            'model_id', 'model_revision', 'host', 'device', 'device_name',
            'replica_count',
        ],
        'support_combinations': [('a', 'rev', 'h', 'cuda', 'gpu', 1),
                                 ('a', 'rev', 'h', 'cuda', 'gpu', 2)],
    }
    request = {'batch_latency_p95_limit_ms': 5.0,
               'min_requests_per_second': 12.0, 'candidates': candidates}
    return bundle, request, model


class AssessGpuShapeTests(unittest.TestCase):
    def test_missing_or_unsupported_top_level_fields_abstain_before_scoring(self):
        for change, expected in (
                ('price', ['unsupported_request_fields:price_usd_per_hour_max']),
                ('node', ['unsupported_request_fields:node_count']),
                ('missing', ['missing_request_fields:min_requests_per_second']),
                ('both', ['missing_request_fields:min_requests_per_second',
                          'unsupported_request_fields:price_usd_per_hour_max'])):
            with self.subTest(change=change):
                bundle, request, model = fixture()
                if change in ('price', 'both'):
                    request['price_usd_per_hour_max'] = 0.01
                if change == 'node':
                    request['node_count'] = 1
                if change in ('missing', 'both'):
                    del request['min_requests_per_second']
                result = assess_gpu_shape(bundle, request)
                self.assertEqual(result['status'], 'ABSTAIN')
                self.assertEqual(result['reasons'], expected)
                self.assertNotIn('selected', result)
                self.assertNotIn('candidate_assessments', result)
                self.assertEqual(model.calls, 0)

    def test_predictor_count_mismatch_abstains_without_selection(self):
        bundle, request, _ = fixture()
        for length in (7, 9):
            with self.subTest(length=length):
                result = _assess_gpu_shape(
                    bundle, request,
                    lambda _bundle, _candidates: [{} for _ in range(length)])
                self.assertEqual(result['status'], 'ABSTAIN')
                self.assertEqual(result['reasons'], ['prediction_count_mismatch'])
                self.assertNotIn('selected', result)

    def test_one_batch_call_converts_examples_to_requests_and_orders_shapes(self):
        bundle, request, model = fixture()
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'RESEARCH_ESTIMATE')
        self.assertEqual(model.calls, 1)
        self.assertEqual((result['selected']['gpu_replicas'],
                          result['selected']['cpu_affinity_cores']), (1, 4))
        self.assertAlmostEqual(result['selected']['predicted_requests_per_second'], 13)
        self.assertAlmostEqual(result['selected']['requests_per_second_margin'], 1)
        self.assertEqual(result['candidate_assessments'][0]['gpu_replicas'], 2)
        self.assertTrue(result['provenance']['hashes_verified'])
        self.assertEqual(result['host'], 'h')
        self.assertEqual(result['device_name'], 'gpu')
        self.assertEqual(result['batch_size'], 2)
        self.assertEqual(result['max_length'], 32)
        self.assertEqual(result['request_concurrency'], 1)
        self.assertEqual(result['repeats'], 8)
        self.assertEqual(result['provenance']['runtime_sklearn_version'],
                         sklearn.__version__)
        self.assertNotIn('DEPLOY', json.dumps(result))
        self.assertNotIn('node_count', json.dumps(result))

    def test_unsupported_candidate_abstains_without_partial_selection(self):
        bundle, request, model = fixture()
        request['candidates'][3]['model_id'] = 'other'
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertNotIn('selected', result)
        self.assertEqual(model.calls, 0)  # mixed model identity is invalid before scoring
        self.assertIn('different_workload', result['reasons'][0])

        bundle, request, model = fixture()
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertNotIn('selected', result)
        self.assertEqual(model.calls, 1)  # supported half scored in one call
        self.assertEqual([r['status'] for r in result['candidate_assessments']].count('ABSTAIN'), 4)

    def test_no_predicted_candidate_abstains(self):
        bundle, request, model = fixture()
        request['min_requests_per_second'] = 1000
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertEqual(result['reasons'], ['no_candidate_meets_estimated_slo'])
        self.assertEqual(model.calls, 1)
        self.assertFalse(any(row['meets_estimated_slo']
                             for row in result['candidate_assessments']))

    def test_nonfinite_model_output_abstains(self):
        class BrokenModel:
            calls = 0

            def predict(self, frame):
                self.calls += 1
                return np.column_stack((np.full(len(frame), np.nan),
                                        np.full(len(frame), np.log(20.0))))

        bundle, request, _ = fixture()
        broken = BrokenModel()
        bundle['model'] = broken
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertNotIn('selected', result)
        self.assertEqual(broken.calls, 1)
        self.assertEqual(len(result['candidate_assessments']), 8)
        self.assertTrue(all(row['status'] == 'ABSTAIN'
                            for row in result['candidate_assessments']))

    def test_invalid_grid_workload_slo_and_outcome_input_fail_before_scoring(self):
        for change in ('duplicate', 'different_app', 'different_batch',
                       'threads', 'nonpositive_slo', 'overflow_slo',
                       'huge_feature', 'outcome_leak'):
            with self.subTest(change=change):
                bundle, request, model = fixture()
                if change == 'duplicate':
                    request['candidates'][0]['replica_count'] = 1
                elif change == 'different_app':
                    request['candidates'][0]['application_id'] = 'app-b'
                elif change == 'different_batch':
                    request['candidates'][0]['batch_size'] = 3
                elif change == 'threads':
                    request['candidates'][0]['cpu_threads'] = 2
                elif change == 'nonpositive_slo':
                    request['batch_latency_p95_limit_ms'] = float('nan')
                elif change == 'overflow_slo':
                    request['min_requests_per_second'] = 10 ** 1000
                elif change == 'huge_feature':
                    request['candidates'][0]['max_length'] = 10 ** 1000
                else:
                    request['candidates'][0]['latency_p95_ms'] = 1.0
                result = assess_gpu_shape(bundle, request)
                self.assertEqual(result['status'], 'ABSTAIN')
                self.assertEqual(model.calls, 0)

    def test_unverified_bundle_abstains(self):
        bundle, request, model = fixture()
        del bundle[VERIFIED_KEY]
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertEqual(result['reasons'], ['unverified_model_bundle'])
        self.assertFalse(result['provenance']['hashes_verified'])
        self.assertEqual(model.calls, 0)

    def test_hash_check_rejects_wrong_artifact_before_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'fake.joblib'
            source = Path(temporary) / 'fake.parquet'
            model.write_bytes(b'not a joblib model')
            source.write_bytes(b'not the original source')
            with self.assertRaisesRegex(ValueError, 'model_sha256_mismatch'):
                load_verified_bundle(model, source)
            with self.assertRaisesRegex(ValueError, 'source_sha256_mismatch'):
                load_verified_bundle(
                    ROOT / 'artifacts/performance_v1/performance_model.joblib', source)

    def test_loads_the_verified_bytes_if_model_path_changes_during_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'model.joblib'
            source = Path(temporary) / 'source.parquet'
            joblib.dump({'features': FEATURES, 'targets': TARGETS}, model)
            source.write_bytes(b'source snapshot')
            model_digest = hashlib.sha256(model.read_bytes()).hexdigest()
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            actual_load = joblib.load

            def replace_then_load(stream):
                model.write_bytes(b'replaced after digest verification')
                return actual_load(stream)

            with patch.object(shape, 'MODEL_SHA256', model_digest), \
                 patch.object(shape, 'SOURCE_SHA256', source_digest), \
                 patch.object(shape.sklearn, '__version__', shape.TRAIN_SKLEARN_VERSION), \
                 patch.object(shape.np, '__version__', shape.TRAIN_NUMPY_VERSION), \
                 patch.object(shape.pd, '__version__', shape.TRAIN_PANDAS_VERSION), \
                 patch.object(shape.joblib, '__version__', shape.VERIFIED_JOBLIB_VERSION), \
                 patch.object(shape.joblib, 'load', side_effect=replace_then_load):
                bundle = load_verified_bundle(model, source)

            self.assertEqual(bundle['features'], FEATURES)
            self.assertEqual(bundle[VERIFIED_KEY], (model_digest, source_digest))
            self.assertNotEqual(hashlib.sha256(model.read_bytes()).hexdigest(),
                                model_digest)

    def test_numpy_pandas_and_joblib_versions_fail_before_unpickling(self):
        model = ROOT / 'artifacts/performance_v1/performance_model.joblib'
        source = ROOT / 'data/audit_v1/measurements.parquet'
        with patch.object(shape.sklearn, '__version__', shape.TRAIN_SKLEARN_VERSION), \
             patch.object(shape.np, '__version__', shape.TRAIN_NUMPY_VERSION), \
             patch.object(shape.pd, '__version__', shape.TRAIN_PANDAS_VERSION), \
             patch.object(shape.joblib, '__version__', shape.VERIFIED_JOBLIB_VERSION):
            for name, module in (('numpy', shape.np), ('pandas', shape.pd),
                                 ('joblib', shape.joblib)):
                with self.subTest(library=name):
                    with patch.object(module, '__version__', '0.0'), \
                         patch.object(shape.joblib, 'load',
                                      side_effect=AssertionError('must not deserialize')):
                        with self.assertRaisesRegex(ValueError, name + '_version_mismatch'):
                            load_verified_bundle(model, source)

    def test_real_saved_model_and_example(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            with self.assertRaisesRegex(ValueError, 'sklearn_version_mismatch'):
                load_verified_bundle(
                    ROOT / 'artifacts/performance_v1/performance_model.joblib',
                    ROOT / 'data/audit_v1/measurements.parquet')
            return
        bundle = load_verified_bundle(
            ROOT / 'artifacts/performance_v1/performance_model.joblib',
            ROOT / 'data/audit_v1/measurements.parquet')
        self.assertEqual(bundle['model'].named_steps['regressor'].n_jobs, 2)
        request = json.loads((ROOT / 'examples/gpu_shape_request.json').read_text())
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(bundle['model'].named_steps['regressor'].n_jobs, 2)
        self.assertEqual(result['status'], 'RESEARCH_ESTIMATE')
        self.assertEqual((result['selected']['gpu_replicas'],
                          result['selected']['cpu_affinity_cores']), (2, 2))
        self.assertEqual(len(result['candidate_assessments']), 8)
        self.assertTrue(result['provenance']['hashes_verified'])

    def test_real_qwen_unmeasured_batch_and_concurrency_abstain(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires its training sklearn version')
        source = ROOT / 'data/audit_v1/measurements.parquet'
        bundle = load_verified_bundle(
            ROOT / 'artifacts/performance_v1/performance_model.joblib', source)
        primary = pd.read_parquet(source)
        qwen = primary.loc[
            primary.eligible & primary.campaign.eq('khu-hf-local-exhaustive') &
            primary.device.eq('cuda') &
            primary.model_id.eq('Qwen/Qwen2.5-0.5B-Instruct')
        ].iloc[0]
        request = json.loads((ROOT / 'examples/gpu_shape_request.json').read_text())
        for candidate in request['candidates']:
            candidate.update(model_id=str(qwen.model_id),
                             model_revision=str(qwen.model_revision),
                             batch_size=1, max_length=64,
                             request_concurrency=1, repeats=6)
        observed = assess_gpu_shape(bundle, request)
        self.assertTrue(all(item['status'] == 'PREDICTED'
                            for item in observed['candidate_assessments']))
        for name, value, reason in (
                ('batch_size', 16, 'outside_model_condition_range:batch_size'),
                ('request_concurrency', 6,
                 'outside_model_condition_range:request_concurrency'),
                ('max_length', 32, 'outside_model_condition_range:max_length'),
                ('repeats', 8, 'unsupported_model_condition_value:repeats')):
            with self.subTest(feature=name):
                changed = json.loads(json.dumps(request))
                for candidate in changed['candidates']:
                    candidate[name] = value
                result = assess_gpu_shape(bundle, changed)
                self.assertEqual(result['status'], 'ABSTAIN')
                self.assertNotIn('selected', result)
                self.assertTrue(all(item['status'] == 'ABSTAIN'
                                    and reason in item['reasons']
                                    for item in result['candidate_assessments']))

    def test_real_cpu_conditions_do_not_borrow_cuda_training_range(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires its training sklearn version')
        source = ROOT / 'data/audit_v1/measurements.parquet'
        bundle = load_verified_bundle(
            ROOT / 'artifacts/performance_v1/performance_model.joblib', source)
        snapshot = pd.read_parquet(source)
        cpu = snapshot.loc[
            snapshot.eligible & snapshot.campaign.eq(PRIMARY) &
            snapshot.device.eq('cpu')
        ]
        records = cpu[FEATURES + ['host', 'device_name']].to_dict('records')
        predictions = predict_records(bundle, records)
        rejected = [row for row in predictions if row['status'] == 'ABSTAIN']
        self.assertEqual(len(rejected), 28)
        self.assertTrue(all(any(reason.startswith('outside_model_condition_range:')
                                for reason in row['reasons']) for row in rejected))


if __name__ == '__main__':
    unittest.main()
