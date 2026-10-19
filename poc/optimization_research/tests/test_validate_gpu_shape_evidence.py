"""Standalone semantic validation of the versioned GPU-shape research JSON."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker


RESEARCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH / 'scripts'))
from assess_gpu_shape import assess_gpu_shape
from emit_gpu_shape_evidence import SCHEMA_PATH, build_evidence
from validate_gpu_shape_evidence import validate_gpu_shape_evidence
from test_assess_gpu_shape import fixture


EVIDENCE_PATH = RESEARCH / 'artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json'
REQUEST_PATH = RESEARCH / 'examples/gpu_shape_request.json'
RESULT_PATH = RESEARCH / 'artifacts/gpu_shape_assessment_v1/example_result.json'
SCRIPT = RESEARCH / 'scripts/validate_gpu_shape_evidence.py'


class ValidateGPUShapeEvidenceTests(unittest.TestCase):
    def evidence(self) -> dict:
        return json.loads(EVIDENCE_PATH.read_text(encoding='utf-8'))

    def test_saved_evidence_and_source_pair_are_valid(self):
        evidence = self.evidence()
        request = json.loads(REQUEST_PATH.read_text(encoding='utf-8'))
        result = json.loads(RESULT_PATH.read_text(encoding='utf-8'))
        self.assertIsNone(validate_gpu_shape_evidence(evidence))
        self.assertIsNone(validate_gpu_shape_evidence(
            evidence, request=request, result=result))
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), '--input', str(EVIDENCE_PATH),
             '--request', str(REQUEST_PATH), '--result', str(RESULT_PATH)],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'gpu_shape_evidence_valid')

    def test_schema_alone_accepts_bad_selection_and_duplicate_grid(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        validator = Draft7Validator(schema, format_checker=FormatChecker())

        selected = self.evidence()
        first = selected['candidates'][0]
        self.assertFalse(first['meets_estimated_benchmark_objective'])
        selected['selected_shape'] = {
            'gpu_replicas': first['gpu_replicas'],
            'cpu_affinity_cores': first['cpu_affinity_cores'],
        }
        self.assertEqual(list(validator.iter_errors(selected)), [])
        with self.assertRaisesRegex(ValueError, 'selected_candidate_mismatch'):
            validate_gpu_shape_evidence(selected)
        with tempfile.TemporaryDirectory() as directory:
            bad_path = Path(directory) / 'bad_evidence.json'
            bad_path.write_text(json.dumps(selected), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), '--input', str(bad_path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertIn('selected_candidate_mismatch', completed.stderr)

        duplicate = self.evidence()
        second = duplicate['candidates'][1]
        for key in ('gpu_replicas', 'cpu_affinity_cores', 'cpu_threads'):
            second[key] = duplicate['candidates'][0][key]
        self.assertEqual(list(validator.iter_errors(duplicate)), [])
        with self.assertRaisesRegex(ValueError, 'incomplete_or_duplicate_gpu_cpu_grid'):
            validate_gpu_shape_evidence(duplicate)

    def test_rejects_inconsistent_candidate_math_and_threads(self):
        for change in ('latency_margin', 'rps_margin', 'qualifies', 'threads'):
            with self.subTest(change=change):
                evidence = self.evidence()
                row = evidence['candidates'][0]
                if change == 'latency_margin':
                    row['batch_latency_margin_ms'] += 1
                elif change == 'rps_margin':
                    row['requests_per_second_margin'] += 1
                elif change == 'qualifies':
                    row['meets_estimated_benchmark_objective'] = True
                else:
                    row['cpu_threads'] = 4
                reason = ('candidate_cpu_threads_must_match_affinity'
                          if change == 'threads' else 'inconsistent_candidate_objective_math')
                with self.assertRaisesRegex(ValueError, reason):
                    validate_gpu_shape_evidence(evidence)

    def test_selected_shape_must_be_the_minimum_qualifying_shape(self):
        evidence = self.evidence()
        passing = [row for row in evidence['candidates']
                   if row['meets_estimated_benchmark_objective']]
        self.assertGreater(len(passing), 1)
        later = max(passing, key=lambda row: (
            row['gpu_replicas'], row['cpu_affinity_cores']))
        evidence['selected_shape'] = {
            'gpu_replicas': later['gpu_replicas'],
            'cpu_affinity_cores': later['cpu_affinity_cores'],
        }
        with self.assertRaisesRegex(ValueError, 'selected_candidate_mismatch'):
            validate_gpu_shape_evidence(evidence)

    def test_full_abstain_reasons_match_candidate_states(self):
        bundle, request, _ = fixture()
        request['min_requests_per_second'] = 1000.0
        no_pass = build_evidence(request, assess_gpu_shape(bundle, request),
                                 generated_at='2026-09-28T00:00:00Z')
        self.assertEqual(no_pass['reasons'], ['no_candidate_meets_estimated_slo'])
        self.assertIsNone(validate_gpu_shape_evidence(no_pass))
        changed = copy.deepcopy(no_pass)
        changed['reasons'] = ['unsupported_candidates:0']
        with self.assertRaisesRegex(ValueError, 'inconsistent_no_candidate_abstain'):
            validate_gpu_shape_evidence(changed)

        bundle, request, _ = fixture()
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        unsupported = build_evidence(request, assess_gpu_shape(bundle, request),
                                     generated_at='2026-09-28T00:00:00Z')
        self.assertTrue(unsupported['reasons'][0].startswith('unsupported_candidates:'))
        self.assertIsNone(validate_gpu_shape_evidence(unsupported))
        unsupported['reasons'] = ['unsupported_candidates:0']
        with self.assertRaisesRegex(ValueError, 'inconsistent_candidate_abstain_reasons'):
            validate_gpu_shape_evidence(unsupported)

        selected_with_abstain = copy.deepcopy(unsupported)
        selected_with_abstain['status'] = 'RESEARCH_ESTIMATE'
        del selected_with_abstain['reasons']
        selected_with_abstain['selected_shape'] = {
            'gpu_replicas': 1, 'cpu_affinity_cores': 4,
        }
        with self.assertRaisesRegex(ValueError, 'selected_with_abstained_candidate'):
            validate_gpu_shape_evidence(selected_with_abstain)

        false_abstain = self.evidence()
        false_abstain['status'] = 'ABSTAIN'
        false_abstain['reasons'] = ['no_candidate_meets_estimated_slo']
        del false_abstain['selected_shape']
        with self.assertRaisesRegex(ValueError, 'abstained_despite_qualifying_candidate'):
            validate_gpu_shape_evidence(false_abstain)

    def test_source_pair_checks_hashes_and_full_object(self):
        evidence = self.evidence()
        request = json.loads(REQUEST_PATH.read_text(encoding='utf-8'))
        result = json.loads(RESULT_PATH.read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError, 'request_and_result_required_together'):
            validate_gpu_shape_evidence(evidence, request=request)

        wrong_request = copy.deepcopy(evidence)
        wrong_request['request_sha256'] = '0' * 64
        self.assertIsNone(validate_gpu_shape_evidence(wrong_request))
        with self.assertRaisesRegex(ValueError, 'request_sha256_mismatch'):
            validate_gpu_shape_evidence(wrong_request, request=request, result=result)

        wrong_id = copy.deepcopy(evidence)
        wrong_id['evidence_id'] = 'sha256:' + '0' * 64
        self.assertIsNone(validate_gpu_shape_evidence(wrong_id))
        with self.assertRaisesRegex(ValueError, 'evidence_id_mismatch'):
            validate_gpu_shape_evidence(wrong_id, request=request, result=result)

        changed_field = copy.deepcopy(evidence)
        changed_field['workload']['application_id'] = 'different-app'
        self.assertIsNone(validate_gpu_shape_evidence(changed_field))
        with self.assertRaisesRegex(ValueError, 'evidence_does_not_match_source_pair'):
            validate_gpu_shape_evidence(changed_field, request=request, result=result)

    def test_standalone_timestamp_uses_emitter_utc_rule(self):
        evidence = self.evidence()
        evidence['generated_at'] = '2026-09-28T02:00:00+02:00'
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        validator = Draft7Validator(schema, format_checker=FormatChecker())
        self.assertEqual(list(validator.iter_errors(evidence)), [])
        with self.assertRaisesRegex(ValueError, 'generated_at_must_be_rfc3339_utc'):
            validate_gpu_shape_evidence(evidence)

    def test_standalone_rejects_producer_trim_empty_fields(self):
        for key in ('application_id', 'workload_id', 'model_id',
                    'model_revision', 'host', 'device_name'):
            with self.subTest(workload_field=key):
                evidence = self.evidence()
                evidence['workload'][key] = '   '
                with self.assertRaisesRegex(ValueError, 'blank_workload_identifier'):
                    validate_gpu_shape_evidence(evidence)

        evidence = self.evidence()
        evidence['benchmark_objective']['source']['reference'] = '   '
        with self.assertRaisesRegex(ValueError, 'invalid_objective_source'):
            validate_gpu_shape_evidence(evidence)

        evidence = self.evidence()
        evidence['status'] = 'ABSTAIN'
        evidence['reasons'] = ['   ']
        del evidence['selected_shape']
        with self.assertRaisesRegex(ValueError, 'invalid_abstain_reasons'):
            validate_gpu_shape_evidence(evidence)

        bundle, request, _ = fixture()
        bundle['support_combinations'] = bundle['support_combinations'][:1]
        evidence = build_evidence(request, assess_gpu_shape(bundle, request),
                                  generated_at='2026-09-28T00:00:00Z')
        evidence['candidates'][0]['reasons'] = ['   ']
        with self.assertRaisesRegex(ValueError, 'invalid_abstained_candidate_reasons'):
            validate_gpu_shape_evidence(evidence)

    def test_partial_abstain_and_nonfinite_json(self):
        request = json.loads(REQUEST_PATH.read_text(encoding='utf-8'))
        stored_result = json.loads(RESULT_PATH.read_text(encoding='utf-8'))
        partial_result = {
            'status': 'ABSTAIN', 'scope': stored_result['scope'],
            'provenance': stored_result['provenance'],
            'reasons': ['incomplete_or_duplicate_gpu_cpu_grid'],
        }
        partial = build_evidence(request, partial_result,
                                 generated_at='2026-09-28T00:00:00Z')
        self.assertIsNone(validate_gpu_shape_evidence(partial))
        self.assertIsNone(validate_gpu_shape_evidence(
            partial, request=request, result=partial_result))

        partial['generated_at'] = float('inf')
        with self.assertRaisesRegex(ValueError, 'non_json_or_nonfinite_input'):
            validate_gpu_shape_evidence(partial)


if __name__ == '__main__':
    unittest.main()
