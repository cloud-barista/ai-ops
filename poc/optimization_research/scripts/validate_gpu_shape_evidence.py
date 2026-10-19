"""Validate standalone offline GPU-shape evidence and, optionally, its source pair.

The JSON Schema checks fields and types. This verifier also checks the eight
benchmark shapes, objective arithmetic, and selection rule. A standalone
evidence object cannot authenticate its own request/result-derived hashes;
supplying both original JSON objects enables a full-object regeneration check.
Neither mode verifies model execution or authorizes deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from assess_gpu_shape import SHAPES
from emit_gpu_shape_evidence import (SCHEMA_PATH, _canonical_bytes, _close,
                                     _generated_at, _nonempty_strings,
                                     _validate_source,
                                     build_evidence)


WORKLOAD_STRING_KEYS = ('application_id', 'workload_id', 'model_id',
                        'model_revision', 'host', 'device_name')


def validate_gpu_shape_evidence(evidence: Any, *,
                                request: dict | None = None,
                                result: dict | None = None) -> None:
    """Raise ValueError for malformed or semantically inconsistent evidence.

    With a source pair, regenerate the evidence using the original emitter and
    compare the full object, including request_sha256 and evidence_id. Without
    that pair, those two strings are only syntax-checked by the schema.
    """
    _canonical_bytes(evidence)  # Reject non-JSON objects and NaN/Infinity.
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    error = next(validator.iter_errors(evidence), None)
    if error is not None:
        raise ValueError('evidence_schema_validation_failed:' + error.message)
    _generated_at(evidence['generated_at'])  # The emitter requires UTC, not any offset.
    if 'workload' in evidence and any(
            not evidence['workload'][key].strip() for key in WORKLOAD_STRING_KEYS):
        raise ValueError('blank_workload_identifier')
    if 'reasons' in evidence and not _nonempty_strings(evidence['reasons']):
        raise ValueError('invalid_abstain_reasons')
    source = (evidence['benchmark_objective'].get('source')
              if 'benchmark_objective' in evidence
              else evidence.get('objective_source'))
    _validate_source(source)

    if 'candidates' in evidence:
        candidates = evidence['candidates']
        shapes = [(row['gpu_replicas'], row['cpu_affinity_cores'])
                  for row in candidates]
        if set(shapes) != SHAPES or len(set(shapes)) != len(SHAPES):
            raise ValueError('incomplete_or_duplicate_gpu_cpu_grid')
        if any(row['cpu_threads'] != row['cpu_affinity_cores']
               for row in candidates):
            raise ValueError('candidate_cpu_threads_must_match_affinity')

        objective = evidence['benchmark_objective']
        latency_limit = objective['batch_latency_p95_limit_ms']
        min_rps = objective['min_requests_per_second']
        qualifying = []
        unsupported = []
        invalid = []
        for index, row in enumerate(candidates):
            if row['status'] == 'ABSTAIN':
                if not _nonempty_strings(row['reasons']):
                    raise ValueError('invalid_abstained_candidate_reasons')
                if row['reasons'] == ['nonfinite_or_nonpositive_prediction']:
                    invalid.append(index)
                else:
                    unsupported.append(index)
                continue
            latency_margin = latency_limit - row['predicted_batch_latency_p95_ms']
            rps_margin = row['predicted_requests_per_second'] - min_rps
            meets = latency_margin >= 0 and rps_margin >= 0
            if (not _close(row['batch_latency_margin_ms'], latency_margin)
                    or not _close(row['requests_per_second_margin'], rps_margin)
                    or row['meets_estimated_benchmark_objective'] != meets):
                raise ValueError('inconsistent_candidate_objective_math')
            if meets:
                qualifying.append(row)

        if unsupported or invalid:
            if evidence['status'] != 'ABSTAIN':
                raise ValueError('selected_with_abstained_candidate')
            reasons = []
            if unsupported:
                reasons.append('unsupported_candidates:'
                               + ','.join(map(str, unsupported)))
            if invalid:
                reasons.append('invalid_predictions:'
                               + ','.join(map(str, invalid)))
            if evidence['reasons'] != reasons:
                raise ValueError('inconsistent_candidate_abstain_reasons')
        elif qualifying:
            if evidence['status'] != 'RESEARCH_ESTIMATE':
                raise ValueError('abstained_despite_qualifying_candidate')
            chosen = min(qualifying, key=lambda row: (
                row['gpu_replicas'], row['cpu_affinity_cores']))
            selected = {'gpu_replicas': chosen['gpu_replicas'],
                        'cpu_affinity_cores': chosen['cpu_affinity_cores']}
            if evidence['selected_shape'] != selected:
                raise ValueError('selected_candidate_mismatch')
        elif (evidence['status'] != 'ABSTAIN'
              or evidence['reasons'] != ['no_candidate_meets_estimated_slo']):
            raise ValueError('inconsistent_no_candidate_abstain')

    if (request is None) != (result is None):
        raise ValueError('request_and_result_required_together')
    if request is not None:
        request_sha = hashlib.sha256(_canonical_bytes(request)).hexdigest()
        if evidence['request_sha256'] != request_sha:
            raise ValueError('request_sha256_mismatch')
        expected = build_evidence(
            request, result, objective_source=source,
            generated_at=evidence['generated_at'])
        if evidence['evidence_id'] != expected['evidence_id']:
            raise ValueError('evidence_id_mismatch')
        if evidence != expected:
            raise ValueError('evidence_does_not_match_source_pair')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='GPU-shape research evidence JSON')
    parser.add_argument('--request', type=Path,
                        help='original assessor request JSON; requires --result')
    parser.add_argument('--result', type=Path,
                        help='original assessor result JSON; requires --request')
    args = parser.parse_args()
    try:
        if (args.request is None) != (args.result is None):
            raise ValueError('request_and_result_required_together')
        evidence = json.loads(args.input.read_text(encoding='utf-8'))
        request = (json.loads(args.request.read_text(encoding='utf-8'))
                   if args.request else None)
        result = (json.loads(args.result.read_text(encoding='utf-8'))
                  if args.result else None)
        validate_gpu_shape_evidence(evidence, request=request, result=result)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'gpu_shape_evidence_invalid: {exc}\n')
    print('gpu_shape_evidence_valid')


if __name__ == '__main__':
    main()
