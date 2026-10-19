"""Offline research evidence contract, including real saved-model output."""
import copy
import hashlib
import json
import sys
from pathlib import Path
import unittest

import sklearn
from jsonschema import Draft7Validator, FormatChecker

RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / 'scripts'))
from assess_gpu_shape import (SCOPE, TRAIN_SKLEARN_VERSION,
                              assess_gpu_shape, load_verified_bundle)
from emit_gpu_shape_evidence import (SCHEMA_PATH, _canonical_bytes,
                                     build_evidence)
from train_performance import ROOT
from test_assess_gpu_shape import fixture


def example_pair():
    request = json.loads((ROOT / 'examples/gpu_shape_request.json').read_text())
    result = json.loads((ROOT / 'artifacts/gpu_shape_assessment_v1/example_result.json').read_text())
    return request, result


class GPUShapeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text())
        Draft7Validator.check_schema(cls.schema)
        cls.validator = Draft7Validator(cls.schema, format_checker=FormatChecker())

    def test_full_evidence_cannot_ignore_extra_request_constraint(self):
        bundle, request, model = fixture()
        valid_result = assess_gpu_shape(bundle, request)
        self.assertEqual(valid_result['status'], 'RESEARCH_ESTIMATE')
        altered = copy.deepcopy(request)
        altered['price_usd_per_hour_max'] = 0.01
        with self.assertRaisesRegex(ValueError, 'full_result_for_invalid_request'):
            build_evidence(altered, valid_result)
        abstained = assess_gpu_shape(bundle, altered)
        self.assertEqual(abstained['reasons'],
                         ['unsupported_request_fields:price_usd_per_hour_max'])
        self.assertEqual(model.calls, 1)
        evidence = build_evidence(altered, abstained)
        self.assertEqual(evidence['status'], 'ABSTAIN')
        self.assertNotIn('selected_shape', evidence)
        self.assertEqual(list(self.validator.iter_errors(evidence)), [])

    def test_real_saved_model_result_is_preserved_and_id_is_stable(self):
        if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
            self.skipTest('saved model requires the training scikit-learn version')
        bundle = load_verified_bundle(
            ROOT / 'artifacts/performance_v1/performance_model.joblib',
            ROOT / 'data/audit_v1/measurements.parquet')
        request, saved_result = example_pair()
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result, saved_result)
        source = {'kind': 'research_demo',
                  'reference': 'research/examples/gpu_shape_request.json'}
        first = build_evidence(request, result, objective_source=source,
                               generated_at='2026-09-28T00:00:00Z')
        second = build_evidence(request, result, objective_source=source,
                                generated_at='2026-09-28T00:00:01Z')
        self.assertEqual(list(self.validator.iter_errors(first)), [])
        self.assertEqual(first['evidence_id'], second['evidence_id'])
        other_source = build_evidence(
            request, result,
            objective_source={'kind': 'research_demo', 'reference': 'another-example'},
            generated_at='2026-09-28T00:00:00Z')
        self.assertNotEqual(first['evidence_id'], other_source['evidence_id'])
        self.assertEqual(first['request_sha256'],
                         hashlib.sha256(_canonical_bytes(request)).hexdigest())
        self.assertEqual(first['selected_shape'],
                         {'gpu_replicas': 2, 'cpu_affinity_cores': 2})
        self.assertEqual(first['benchmark_objective']['source'], source)
        self.assertEqual(len(first['candidates']), 8)
        for raw, wrapped, request_row in zip(result['candidate_assessments'],
                                             first['candidates'], request['candidates']):
            self.assertEqual(wrapped['cpu_threads'], request_row['cpu_threads'])
            for field in ('gpu_replicas', 'cpu_affinity_cores', 'status',
                          'predicted_batch_latency_p95_ms',
                          'predicted_requests_per_second', 'batch_latency_margin_ms',
                          'requests_per_second_margin'):
                self.assertEqual(wrapped[field], raw[field])
            self.assertEqual(wrapped['meets_estimated_benchmark_objective'],
                             raw['meets_estimated_slo'])
        self.assertEqual(first['provenance']['artifact_hash_check'],
                         'assessor_reported_verified_at_load')
        serialized = json.dumps(first, allow_nan=False)
        for forbidden in ('desired_deployment_spec', 'resource_recommendation',
                          'node_count', 'feasible', 'DEPLOY', 'SCALE_OUT'):
            self.assertNotIn(forbidden, serialized)

    def test_partial_abstain_preserves_source_without_fabricating_workload(self):
        request, stored = example_pair()
        request['candidates'][0]['replica_count'] = 1  # invalid duplicate grid
        result = {
            'status': 'ABSTAIN',
            'reasons': ['incomplete_or_duplicate_gpu_cpu_grid'],
            'scope': SCOPE,
            'provenance': stored['provenance'],
        }
        source = {'kind': 'research_demo', 'reference': 'example-invalid-grid'}
        evidence = build_evidence(request, result, objective_source=source,
                                  generated_at='2026-09-28T00:00:00Z')
        self.assertEqual(list(self.validator.iter_errors(evidence)), [])
        self.assertEqual(evidence['status'], 'ABSTAIN')
        self.assertEqual(evidence['objective_source'], source)
        for absent in ('workload', 'benchmark_objective', 'candidates', 'selected_shape'):
            self.assertNotIn(absent, evidence)

    def test_full_abstain_keeps_per_candidate_support_and_reason_indexes(self):
        bundle, request, _ = fixture()
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        result = assess_gpu_shape(bundle, request)
        self.assertEqual(result['status'], 'ABSTAIN')
        evidence = build_evidence(request, result,
                                  generated_at='2026-09-28T00:00:00Z')
        self.assertEqual(list(self.validator.iter_errors(evidence)), [])
        self.assertEqual(evidence['reasons'], result['reasons'])
        self.assertEqual(len(evidence['candidates']), 8)
        self.assertEqual(sum(row['status'] == 'ABSTAIN'
                             for row in evidence['candidates']), 4)
        self.assertNotIn('source', evidence['benchmark_objective'])
        self.assertNotIn('selected_shape', evidence)
        changed = copy.deepcopy(result)
        changed['reasons'] = ['no_candidate_meets_estimated_slo']
        with self.assertRaisesRegex(ValueError, 'inconsistent_candidate_abstain_reasons'):
            build_evidence(request, changed)

    def test_no_qualifying_candidate_and_unverified_partial_result(self):
        bundle, request, _ = fixture()
        request['min_requests_per_second'] = 1000
        result = assess_gpu_shape(bundle, request)
        evidence = build_evidence(request, result,
                                  generated_at='2026-09-28T00:00:00Z')
        self.assertEqual(evidence['reasons'], ['no_candidate_meets_estimated_slo'])
        self.assertEqual(list(self.validator.iter_errors(evidence)), [])
        partial = {
            'status': 'ABSTAIN', 'reasons': ['unverified_model_bundle'],
            'scope': SCOPE,
            'provenance': {**result['provenance'], 'hashes_verified': False},
        }
        evidence = build_evidence(request, partial,
                                  generated_at='2026-09-28T00:00:00Z')
        self.assertEqual(evidence['provenance']['artifact_hash_check'], 'not_verified')
        self.assertEqual(list(self.validator.iter_errors(evidence)), [])
        with self.assertRaisesRegex(ValueError, 'generated_at_must_be_rfc3339_utc'):
            build_evidence(request, partial, generated_at='2026-09-28T02:00:00+02:00')

    def test_fail_closed_on_nonfinite_changed_shape_and_false_selection(self):
        request, result = example_pair()
        changed = copy.deepcopy(result)
        changed['candidate_assessments'][0]['predicted_requests_per_second'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'non_json_or_nonfinite_input'):
            build_evidence(request, changed)
        changed = copy.deepcopy(result)
        changed['candidate_assessments'][0]['cpu_affinity_cores'] = 8
        with self.assertRaisesRegex(ValueError, 'candidate_shape_or_order_mismatch'):
            build_evidence(request, changed)
        changed = copy.deepcopy(result)
        changed['selected']['gpu_replicas'] = 1
        with self.assertRaisesRegex(ValueError, 'selected_candidate_mismatch'):
            build_evidence(request, changed)
        changed = copy.deepcopy(result)
        changed['candidate_assessments'][0]['status'] = 'ABSTAIN'
        changed['candidate_assessments'][0]['reasons'] = ['unsupported_category:model_id']
        for key in ('predicted_batch_latency_p95_ms', 'predicted_requests_per_second',
                    'batch_latency_margin_ms', 'requests_per_second_margin',
                    'meets_estimated_slo'):
            del changed['candidate_assessments'][0][key]
        with self.assertRaisesRegex(ValueError, 'selected_with_abstained_candidate'):
            build_evidence(request, changed)

    def test_schema_rejects_action_vm_fields_and_wrong_abstain_shape(self):
        request, result = example_pair()
        evidence = build_evidence(request, result,
                                  generated_at='2026-09-28T00:00:00Z')
        evidence['node_count'] = 2
        self.assertTrue(list(self.validator.iter_errors(evidence)))
        del evidence['node_count']
        evidence['status'] = 'ABSTAIN'
        evidence['reasons'] = ['no_candidate_meets_estimated_slo']
        self.assertTrue(list(self.validator.iter_errors(evidence)))  # selected still present


if __name__ == '__main__':
    unittest.main()
