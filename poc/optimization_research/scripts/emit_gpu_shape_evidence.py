"""Wrap one offline GPU-shape assessment in versioned research evidence.

This module neither recommends a VM nor authorizes an operation. It consumes
the exact request/result pair from assess_gpu_shape and fails closed if the
pair is malformed or inconsistent. It does not attest a mutable model bundle.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from assess_gpu_shape import (MODEL_SHA256, SOURCE_SHA256, TRAIN_SKLEARN_VERSION,
                              SCOPE, _request_errors)


EVIDENCE_TYPE = 'khu.research.gpu_shape_evidence'
SCHEMA_VERSION = '0.1.0'
SCHEMA_PATH = (Path(__file__).resolve().parents[1] / 'contracts'
               / 'gpu_shape_evidence_v0.1.0.schema.json')
WORKLOAD_KEYS = ('application_id', 'workload_id', 'model_id', 'model_revision',
                 'device', 'host', 'device_name', 'batch_size', 'max_length',
                 'request_concurrency', 'repeats')
RESULT_PROVENANCE_KEYS = {'model_sha256', 'source_sha256', 'hashes_verified',
                          'training_sklearn_version', 'runtime_sklearn_version'}
RESULT_BASE_KEYS = {'status', 'scope', 'provenance'}
RESULT_FULL_KEYS = (RESULT_BASE_KEYS | set(WORKLOAD_KEYS)
                    | {'batch_latency_p95_limit_ms',
                       'min_requests_per_second', 'candidate_assessments'})
PREDICTED_KEYS = {'gpu_replicas', 'cpu_affinity_cores', 'status',
                  'predicted_batch_latency_p95_ms',
                  'predicted_requests_per_second', 'batch_latency_margin_ms',
                  'requests_per_second_margin', 'meets_estimated_slo'}
ABSTAINED_KEYS = {'gpu_replicas', 'cpu_affinity_cores', 'status', 'reasons'}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('non_json_or_nonfinite_input') from exc


def _positive_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _finite_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _nonempty_strings(value: Any) -> bool:
    return (isinstance(value, list) and len(value) > 0
            and all(isinstance(item, str) and item.strip() for item in value))


def _close(actual: Any, expected: float) -> bool:
    return (_finite_number(actual)
            and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8))


def _validate_source(source: Any) -> dict | None:
    if source is None:
        return None
    if not isinstance(source, dict) or set(source) != {'kind', 'reference'}:
        raise ValueError('objective_source_requires_kind_and_reference')
    if any(not isinstance(source[key], str) or not source[key].strip()
           for key in ('kind', 'reference')):
        raise ValueError('invalid_objective_source')
    return copy.deepcopy(source)


def _validate_provenance(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != RESULT_PROVENANCE_KEYS:
        raise ValueError('invalid_assessor_provenance_fields')
    if value['model_sha256'] != MODEL_SHA256 or value['source_sha256'] != SOURCE_SHA256:
        raise ValueError('unexpected_assessor_artifact_hash')
    if value['training_sklearn_version'] != TRAIN_SKLEARN_VERSION:
        raise ValueError('unexpected_training_sklearn_version')
    if type(value['hashes_verified']) is not bool:
        raise ValueError('invalid_hash_verification_marker')
    if (not isinstance(value['runtime_sklearn_version'], str)
            or not value['runtime_sklearn_version'].strip()):
        raise ValueError('missing_runtime_sklearn_version')
    return {
        'model_artifact_sha256': value['model_sha256'],
        'training_extract_parquet_sha256': value['source_sha256'],
        # The input is a JSON claim by the assessor. This transformer never
        # verifies the in-memory model or independently authenticates the run.
        'artifact_hash_check': ('assessor_reported_verified_at_load'
                                if value['hashes_verified'] else 'not_verified'),
        'training_sklearn_version': value['training_sklearn_version'],
        'runtime_sklearn_version': value['runtime_sklearn_version'],
    }


def _validate_candidate(row: Any, request_row: dict,
                        latency_limit: float, min_rps: float) -> dict:
    if not isinstance(row, dict) or row.get('status') not in ('PREDICTED', 'ABSTAIN'):
        raise ValueError('invalid_candidate_status')
    shape = (request_row['replica_count'], request_row['cpu_core_limit'])
    if ((row.get('gpu_replicas'), row.get('cpu_affinity_cores')) != shape
            or type(row.get('gpu_replicas')) is not int
            or type(row.get('cpu_affinity_cores')) is not int):
        raise ValueError('candidate_shape_or_order_mismatch')
    output = {'gpu_replicas': row['gpu_replicas'],
              'cpu_affinity_cores': row['cpu_affinity_cores'],
              'cpu_threads': request_row['cpu_threads'],
              'status': row['status']}
    if row['status'] == 'ABSTAIN':
        if set(row) != ABSTAINED_KEYS or not _nonempty_strings(row['reasons']):
            raise ValueError('invalid_abstained_candidate')
        output['reasons'] = list(row['reasons'])
        return output
    if set(row) != PREDICTED_KEYS:
        raise ValueError('invalid_predicted_candidate_fields')
    latency = row['predicted_batch_latency_p95_ms']
    rps = row['predicted_requests_per_second']
    if not _positive_number(latency) or not _positive_number(rps):
        raise ValueError('invalid_predicted_candidate_values')
    latency_margin = latency_limit - latency
    rps_margin = rps - min_rps
    if (not _close(row['batch_latency_margin_ms'], latency_margin)
            or not _close(row['requests_per_second_margin'], rps_margin)
            or type(row['meets_estimated_slo']) is not bool
            or row['meets_estimated_slo'] != (latency_margin >= 0 and rps_margin >= 0)):
        raise ValueError('inconsistent_candidate_objective_math')
    for original, renamed in (
            ('predicted_batch_latency_p95_ms', 'predicted_batch_latency_p95_ms'),
            ('predicted_requests_per_second', 'predicted_requests_per_second'),
            ('batch_latency_margin_ms', 'batch_latency_margin_ms'),
            ('requests_per_second_margin', 'requests_per_second_margin'),
            ('meets_estimated_slo', 'meets_estimated_benchmark_objective')):
        output[renamed] = row[original]
    return output


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    if not isinstance(value, str):
        raise ValueError('generated_at_must_be_rfc3339_utc')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('generated_at_must_be_rfc3339_utc') from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('generated_at_must_be_rfc3339_utc')
    return value


def build_evidence(request: dict, result: dict, *,
                   objective_source: dict | None = None,
                   generated_at: str | None = None) -> dict:
    """Produce research evidence; reject malformed and inconsistent pairs.

    The ID covers the canonical request SHA, raw assessor result and declared
    source, but not generated_at. The assessor's hash flag is only an input
    claim.
    """
    if not isinstance(request, dict) or not isinstance(result, dict):
        raise ValueError('request_and_result_must_be_objects')
    source = _validate_source(objective_source)
    request_sha = hashlib.sha256(_canonical_bytes(request)).hexdigest()
    digest = hashlib.sha256(_canonical_bytes({
        'request_sha256': request_sha,
        'result': result,
        'optional_objective_source': source,
    })).hexdigest()
    if result.get('scope') != SCOPE or result.get('status') not in (
            'RESEARCH_ESTIMATE', 'ABSTAIN'):
        raise ValueError('unsupported_assessor_result')
    provenance = _validate_provenance(result.get('provenance'))
    evidence = {
        'evidence_type': EVIDENCE_TYPE,
        'schema_version': SCHEMA_VERSION,
        'evidence_id': 'sha256:' + digest,
        'request_sha256': request_sha,
        'generated_at': _generated_at(generated_at),
        'status': result['status'],
        'scope': {
            'kind': 'same_host_batch_benchmark_prediction',
            'calibration': 'uncalibrated_point_estimate',
            'deployment_authority': False,
        },
        'provenance': provenance,
    }
    full = 'candidate_assessments' in result
    if not full:
        if set(result) != RESULT_BASE_KEYS | {'reasons'} or result['status'] != 'ABSTAIN':
            raise ValueError('invalid_partial_assessor_result')
        if not _nonempty_strings(result['reasons']):
            raise ValueError('missing_abstain_reasons')
        evidence['reasons'] = list(result['reasons'])
        if source is not None:
            evidence['objective_source'] = source
    else:
        expected = RESULT_FULL_KEYS | ({'selected'} if result['status'] == 'RESEARCH_ESTIMATE'
                                       else {'reasons'})
        if (set(result) != expected or provenance['artifact_hash_check']
                != 'assessor_reported_verified_at_load'):
            raise ValueError('invalid_full_assessor_result')
        if _request_errors(request):
            raise ValueError('full_result_for_invalid_request')
        first = request['candidates'][0]
        if any(result[key] != first[key] for key in WORKLOAD_KEYS):
            raise ValueError('assessor_workload_mismatch')
        if (result['batch_latency_p95_limit_ms'] != request['batch_latency_p95_limit_ms']
                or result['min_requests_per_second'] != request['min_requests_per_second']):
            raise ValueError('assessor_objective_mismatch')
        raw_candidates = result['candidate_assessments']
        if not isinstance(raw_candidates, list) or len(raw_candidates) != 8:
            raise ValueError('expected_eight_assessed_candidates')
        candidates = [
            _validate_candidate(row, request_row,
                                request['batch_latency_p95_limit_ms'],
                                request['min_requests_per_second'])
            for row, request_row in zip(raw_candidates, request['candidates'])
        ]
        evidence['workload'] = {key: result[key] for key in WORKLOAD_KEYS}
        evidence['benchmark_objective'] = {
            'batch_latency_p95_limit_ms': result['batch_latency_p95_limit_ms'],
            'min_requests_per_second': result['min_requests_per_second'],
        }
        if source is not None:
            evidence['benchmark_objective']['source'] = source
        evidence['selection_rule'] = 'min_gpu_replicas_then_cpu_affinity_cores'
        evidence['candidates'] = candidates
        qualifying = [row for row in raw_candidates
                      if row['status'] == 'PREDICTED' and row['meets_estimated_slo']]
        if result['status'] == 'RESEARCH_ESTIMATE':
            if any(row['status'] == 'ABSTAIN' for row in raw_candidates):
                raise ValueError('selected_with_abstained_candidate')
            if len(qualifying) == 0:
                raise ValueError('selected_without_qualifying_candidate')
            chosen = min(qualifying, key=lambda row: (
                row['gpu_replicas'], row['cpu_affinity_cores']))
            if result['selected'] != chosen:
                raise ValueError('selected_candidate_mismatch')
            evidence['selected_shape'] = {
                'gpu_replicas': chosen['gpu_replicas'],
                'cpu_affinity_cores': chosen['cpu_affinity_cores'],
            }
        else:
            if not _nonempty_strings(result['reasons']):
                raise ValueError('missing_abstain_reasons')
            if all(row['status'] == 'PREDICTED' for row in raw_candidates):
                if qualifying or result['reasons'] != ['no_candidate_meets_estimated_slo']:
                    raise ValueError('inconsistent_no_candidate_abstain')
            else:
                unsupported = [index for index, row in enumerate(raw_candidates)
                               if row['status'] == 'ABSTAIN'
                               and row['reasons'] != ['nonfinite_or_nonpositive_prediction']]
                invalid = [index for index, row in enumerate(raw_candidates)
                           if row['status'] == 'ABSTAIN'
                           and row['reasons'] == ['nonfinite_or_nonpositive_prediction']]
                expected_reasons = []
                if unsupported:
                    expected_reasons.append('unsupported_candidates:'
                                            + ','.join(map(str, unsupported)))
                if invalid:
                    expected_reasons.append('invalid_predictions:'
                                            + ','.join(map(str, invalid)))
                if result['reasons'] != expected_reasons:
                    raise ValueError('inconsistent_candidate_abstain_reasons')
            evidence['reasons'] = list(result['reasons'])
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft7Validator.check_schema(schema)
    errors = list(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(evidence))
    if errors:
        raise ValueError('evidence_schema_validation_failed:' + errors[0].message)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--objective-source', type=Path,
                        help='optional JSON object declared by the caller')
    parser.add_argument('--generated-at', type=str,
                        help='UTC RFC3339 timestamp; useful for reproducible offline artifacts')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding='utf-8'))
    result = json.loads(args.result.read_text(encoding='utf-8'))
    source = (json.loads(args.objective_source.read_text(encoding='utf-8'))
              if args.objective_source else None)
    evidence = build_evidence(request, result, objective_source=source,
                              generated_at=args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False,
                                      allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
