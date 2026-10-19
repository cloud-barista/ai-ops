"""Versioned offline benchmark evidence with physical GPU and split provenance.

The saved model still estimates same-host batch benchmark performance only.
This wrapper never produces VM requirements or deployment actions. It leaves
the v0.1 assessor, emitter, schema, examples, and pinned model unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
import sklearn
from jsonschema import Draft7Validator, FormatChecker

from assess_gpu_shape import (MODEL_SHA256, SOURCE_SHA256, SCOPE, SHAPES,
                              TRAIN_SKLEARN_VERSION, _request_errors,
                              assess_gpu_shape,
                              load_verified_bundle)
from emit_gpu_shape_evidence import (_canonical_bytes, _generated_at,
                                     WORKLOAD_KEYS, build_evidence)
from generate_gpu_shape_evidence import _write_json_atomic
from train_performance import ROOT
from validate_gpu_shape_evidence import validate_gpu_shape_evidence


SCHEMA_PATH = ROOT / 'contracts/gpu_shape_evidence_v0.2.0.schema.json'
MODEL_PATH = ROOT / 'artifacts/performance_v1/performance_model.joblib'
SOURCE_PATH = ROOT / 'data/audit_v1/measurements.parquet'
SPLIT_PATH = ROOT / 'artifacts/performance_v1/split_manifest.csv'
SPLIT_SHA256 = '817ccd79e180e0373db085ac254698b26c258b060aea633bcc73c621f86648f8'
EXPECTED_INDICES = {1: [0], 2: [0, 1]}
KEY_FIELDS = ('model_id', 'model_revision', 'host', 'device', 'device_name',
              'batch_size', 'max_length', 'request_concurrency', 'repeats')
EXTERNAL_PARTITIONS = {
    'khu-hf-local-novel-matrix': 'novel_matrix',
    'khu-hf-local-space-filling': 'space_filling',
}
PARTITIONS = {'train', 'validation', 'test', *EXTERNAL_PARTITIONS.values()}
SUPPORT_BASIS = 'exact_workload_input_tuple_in_verified_tracked_campaigns'
REQUEST_FIELDS = {'batch_latency_p95_limit_ms', 'min_requests_per_second',
                  'candidates'}


def _same_int_array(value: Any, expected: list[int]) -> bool:
    return (type(value) is list and len(value) == len(expected)
            and all(type(actual) is int and actual == wanted
                    for actual, wanted in zip(value, expected)))


def _split_request(request: Any) -> tuple[dict | None, list[str]]:
    """Validate physical identity before loading or scoring any model."""
    if not isinstance(request, dict):
        return None, ['request_must_be_object']
    missing = REQUEST_FIELDS - request.keys()
    extra = request.keys() - REQUEST_FIELDS
    if missing or extra:
        reasons = []
        if missing:
            reasons.append('missing_request_fields:' + ','.join(sorted(missing)))
        if extra:
            reasons.append('unsupported_request_fields:' + ','.join(sorted(extra)))
        return None, reasons
    candidates = request.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 8:
        return None, ['expected_exactly_eight_candidates']
    errors = []
    old = copy.deepcopy(request)
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f'candidate_{index}_must_be_object')
            continue
        count = candidate.get('replica_count')
        expected = EXPECTED_INDICES.get(count) if type(count) is int else None
        if expected is None or not _same_int_array(candidate.get('gpu_indices'), expected):
            errors.append(f'candidate_{index}_unsupported_or_missing_gpu_indices')
        old['candidates'][index].pop('gpu_indices', None)
    return (old if not errors else None), errors


def _verified_support_index(source_path: Path, split_path: Path) -> dict[tuple, str]:
    """Derive exact input membership from pinned bytes, never workload_id."""
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError('source_sha256_mismatch')
    split_bytes = split_path.read_bytes()
    if hashlib.sha256(split_bytes).hexdigest() != SPLIT_SHA256:
        raise ValueError('split_manifest_sha256_mismatch')
    frame = pd.read_parquet(io.BytesIO(source_bytes))
    manifest = pd.read_csv(io.BytesIO(split_bytes), dtype=str)
    identity = ['source_db', 'execution_id']
    if set(manifest.split) != {'train', 'validation', 'test'}:
        raise ValueError('unexpected_split_values')
    if manifest.duplicated(identity).any():
        raise ValueError('duplicate_split_execution')
    primary = frame[(frame.campaign == 'khu-hf-local-exhaustive') & frame.eligible]
    if len(primary) != 4856 or len(manifest) != len(primary):
        raise ValueError('incomplete_primary_split_manifest')
    primary = primary.merge(manifest[identity + ['split']], on=identity,
                            how='left', validate='one_to_one')
    if primary.split.isna().any() or primary.groupby('workload_group').split.nunique().max() != 1:
        raise ValueError('inconsistent_primary_split_manifest')
    if primary.split.value_counts().to_dict() != {
            'train': 3348, 'validation': 748, 'test': 760}:
        raise ValueError('unexpected_primary_split_counts')
    sources = [(name, part) for name, part in primary.groupby('split')]
    for campaign, label in EXTERNAL_PARTITIONS.items():
        sources.append((label, frame[(frame.campaign == campaign) & frame.eligible]))
    index: dict[tuple, str] = {}
    for partition, part in sources:
        if partition not in PARTITIONS:
            raise ValueError('unexpected_support_partition')
        cuda = part[part.device == 'cuda']
        for _, group in cuda.groupby(list(KEY_FIELDS)):
            shapes = set(zip(group.replica_count, group.cpu_core_limit))
            if (len(group) != 8 or shapes != SHAPES
                    or not group.cpu_threads.eq(group.cpu_core_limit).all()):
                raise ValueError('incomplete_tracked_gpu_shape_grid')
        for row in cuda.itertuples(index=False):
            expected = EXPECTED_INDICES.get(row.replica_count)
            try:
                observed = json.loads(row.gpu_indices_json)
            except (TypeError, ValueError) as exc:
                raise ValueError('invalid_snapshot_gpu_indices') from exc
            if expected is None or not _same_int_array(observed, expected):
                raise ValueError('unsupported_snapshot_gpu_indices')
            key = tuple(getattr(row, field) for field in KEY_FIELDS)
            previous = index.setdefault(key, partition)
            if previous != partition:
                raise ValueError('ambiguous_joint_input_partition')
    if len(index) != 637:
        raise ValueError('unexpected_tracked_workload_count')
    if pd.Series(list(index.values())).value_counts().to_dict() != {
            'train': 396, 'validation': 89, 'test': 90,
            'novel_matrix': 18, 'space_filling': 44}:
        raise ValueError('unexpected_partition_workload_counts')
    return index


def _support_relation(workload: dict, index: dict[tuple, str]) -> dict:
    features = {field: workload[field] for field in KEY_FIELDS}
    return {
        'partition': index.get(tuple(features.values()), 'unknown'),
        'feature_tuple_sha256': hashlib.sha256(_canonical_bytes(features)).hexdigest(),
        'basis': SUPPORT_BASIS,
        'independent_performance_proof': False,
    }


def _finish_v0_2(base: dict, request: Any, *,
                 index: dict[tuple, str] | None, support_verified: bool) -> dict:
    evidence = copy.deepcopy(base)
    evidence['schema_version'] = '0.2.0'
    evidence['request_sha256'] = hashlib.sha256(_canonical_bytes(request)).hexdigest()
    evidence['provenance']['split_manifest_sha256'] = SPLIT_SHA256
    evidence['provenance']['support_index_check'] = (
        'verified_snapshot_and_manifest' if support_verified else 'not_checked')
    if 'candidates' in evidence:
        if index is None:
            raise ValueError('missing_verified_support_index')
        evidence['support_relation'] = _support_relation(evidence['workload'], index)
        for row, original in zip(evidence['candidates'], request['candidates']):
            row['gpu_indices'] = copy.deepcopy(original['gpu_indices'])
        if 'selected_shape' in evidence:
            count = evidence['selected_shape']['gpu_replicas']
            evidence['selected_shape']['gpu_indices'] = copy.deepcopy(EXPECTED_INDICES[count])
    evidence['evidence_id'] = _evidence_id(evidence)
    _validate_schema(evidence)
    return evidence


def _evidence_id(evidence: dict) -> str:
    payload = {key: value for key, value in evidence.items()
               if key not in {'evidence_id', 'generated_at'}}
    return 'sha256:' + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_schema(evidence: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    Draft7Validator.check_schema(schema)
    error = next(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(evidence), None)
    if error is not None:
        raise ValueError('evidence_schema_validation_failed:' + error.message)


def generate_gpu_shape_evidence_v0_2(
        request: dict, *, objective_source: dict | None = None,
        generated_at: str | None = None,
        model_path: Path = MODEL_PATH, source_path: Path = SOURCE_PATH,
        split_path: Path = SPLIT_PATH) -> dict:
    """Emit pinned offline evidence; invalid physical indices fail before score."""
    _canonical_bytes(request)
    old_request, physical_errors = _split_request(request)
    if physical_errors:
        # This partial ABSTAIN does not load the model or claim artifact checks.
        partial = {'status': 'ABSTAIN', 'reasons': physical_errors,
                   'scope': SCOPE,
                   'provenance': {
                       'model_sha256': MODEL_SHA256,
                       'source_sha256': SOURCE_SHA256,
                       'hashes_verified': False,
                       'training_sklearn_version': TRAIN_SKLEARN_VERSION,
                       'runtime_sklearn_version': sklearn.__version__,
                   }}
        base = build_evidence(request, partial,
                              objective_source=objective_source,
                              generated_at=generated_at)
        return _finish_v0_2(base, request, index=None, support_verified=False)
    index = _verified_support_index(source_path, split_path)
    bundle = load_verified_bundle(model_path, source_path)
    result = assess_gpu_shape(bundle, old_request)
    base = build_evidence(old_request, result,
                          objective_source=objective_source,
                          generated_at=generated_at)
    return _finish_v0_2(base, request, index=index, support_verified=True)


def validate_gpu_shape_evidence_v0_2(
        evidence: Any, *, request: dict | None = None,
        source_path: Path = SOURCE_PATH, split_path: Path = SPLIT_PATH) -> None:
    """Check structure, math, physical identity, and pinned support relation.

    This checks content consistency, not authenticity of model execution.
    Supply the original request to verify its hash and declared GPU indices.
    """
    _canonical_bytes(evidence)
    _validate_schema(evidence)
    _generated_at(evidence['generated_at'])
    if evidence['evidence_id'] != _evidence_id(evidence):
        raise ValueError('evidence_id_mismatch')
    if (evidence['provenance']['model_artifact_sha256'] != MODEL_SHA256
            or evidence['provenance']['training_extract_parquet_sha256'] != SOURCE_SHA256
            or evidence['provenance']['split_manifest_sha256'] != SPLIT_SHA256):
        raise ValueError('unexpected_artifact_hash')
    if request is not None:
        if evidence['request_sha256'] != hashlib.sha256(_canonical_bytes(request)).hexdigest():
            raise ValueError('request_sha256_mismatch')
        stripped, errors = _split_request(request)
        if 'candidates' in evidence and errors:
            raise ValueError('full_evidence_for_invalid_request')
        if (errors and 'candidates' not in evidence
                and evidence['reasons'] != errors):
            raise ValueError('prevalidation_abstain_reasons_mismatch')
    else:
        stripped = None
    base = copy.deepcopy(evidence)
    base['schema_version'] = '0.1.0'
    base.pop('support_relation', None)
    base['provenance'].pop('split_manifest_sha256')
    base['provenance'].pop('support_index_check')
    if 'candidates' in base:
        if evidence['provenance']['support_index_check'] != 'verified_snapshot_and_manifest':
            raise ValueError('unverified_full_support_index')
        if evidence['provenance']['artifact_hash_check'] != 'assessor_reported_verified_at_load':
            raise ValueError('unverified_full_model')
        for row in base['candidates']:
            row.pop('gpu_indices')
        if 'selected_shape' in base:
            base['selected_shape'].pop('gpu_indices')
    validate_gpu_shape_evidence(base)
    if 'candidates' in evidence:
        for field in ('batch_size', 'max_length', 'request_concurrency', 'repeats'):
            if type(evidence['workload'][field]) is not int:
                raise ValueError('noninteger_workload_feature')
        index = _verified_support_index(source_path, split_path)
        expected_relation = _support_relation(evidence['workload'], index)
        if evidence['support_relation'] != expected_relation:
            raise ValueError('support_relation_mismatch')
        for row in evidence['candidates']:
            if any(type(row[field]) is not int for field in (
                    'gpu_replicas', 'cpu_affinity_cores', 'cpu_threads')):
                raise ValueError('noninteger_candidate_shape')
            expected = EXPECTED_INDICES.get(row['gpu_replicas'])
            if expected is None or not _same_int_array(row['gpu_indices'], expected):
                raise ValueError('candidate_gpu_indices_mismatch')
        if 'selected_shape' in evidence:
            selected = evidence['selected_shape']
            if any(type(selected[field]) is not int for field in (
                    'gpu_replicas', 'cpu_affinity_cores')):
                raise ValueError('noninteger_selected_shape')
            if not _same_int_array(selected['gpu_indices'],
                                   EXPECTED_INDICES[selected['gpu_replicas']]):
                raise ValueError('selected_gpu_indices_mismatch')
        if request is not None:
            if stripped is None or len(request['candidates']) != len(evidence['candidates']):
                raise ValueError('request_candidates_mismatch')
            if _request_errors(stripped):
                raise ValueError('invalid_original_request')
            for original, row in zip(request['candidates'], evidence['candidates']):
                if (original['replica_count'] != row['gpu_replicas']
                        or original['cpu_core_limit'] != row['cpu_affinity_cores']
                        or original['gpu_indices'] != row['gpu_indices']):
                    raise ValueError('request_candidate_shape_mismatch')
            for field in WORKLOAD_KEYS:
                if request['candidates'][0][field] != evidence['workload'][field]:
                    raise ValueError('request_workload_mismatch')
            objective = evidence['benchmark_objective']
            for field in ('batch_latency_p95_limit_ms', 'min_requests_per_second'):
                if request[field] != objective[field]:
                    raise ValueError('request_objective_mismatch')
    elif evidence['provenance']['support_index_check'] == 'not_checked':
        if evidence['provenance']['artifact_hash_check'] != 'not_verified':
            raise ValueError('inconsistent_prevalidation_provenance')


def validate_gpu_shape_evidence_v0_2_strict(
        evidence: Any, *, request: dict,
        model_path: Path = MODEL_PATH, source_path: Path = SOURCE_PATH,
        split_path: Path = SPLIT_PATH) -> None:
    """Replay a request against pinned artifacts and compare the full evidence.

    The regular validator remains a structural consistency check. Strict
    verification needs the original request and reruns the saved model. Even
    an early ABSTAIN must pass the artifact checks before it is accepted.
    """
    if request is None:
        raise ValueError('strict_replay_requires_request')
    validate_gpu_shape_evidence_v0_2(
        evidence, request=request, source_path=source_path,
        split_path=split_path)
    if 'candidates' not in evidence:
        # Early prevalidation ABSTAIN normally avoids artifact I/O. A strict
        # receiver must still verify the model and tracked source bytes.
        _verified_support_index(source_path, split_path)
        load_verified_bundle(model_path, source_path)
    objective_source = (evidence['benchmark_objective'].get('source')
                        if 'benchmark_objective' in evidence
                        else evidence.get('objective_source'))
    replay = generate_gpu_shape_evidence_v0_2(
        request, objective_source=objective_source,
        generated_at=evidence['generated_at'], model_path=model_path,
        source_path=source_path, split_path=split_path)
    if _canonical_bytes(replay) != _canonical_bytes(evidence):
        raise ValueError('pinned_model_replay_mismatch')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--objective-source', type=Path)
    parser.add_argument('--generated-at', type=str)
    parser.add_argument('--model', type=Path, default=MODEL_PATH)
    parser.add_argument('--source', type=Path, default=SOURCE_PATH)
    parser.add_argument('--split-manifest', type=Path, default=SPLIT_PATH)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError('output_path_already_exists')
        request = json.loads(args.request.read_text(encoding='utf-8'))
        objective_source = (json.loads(args.objective_source.read_text(encoding='utf-8'))
                            if args.objective_source else None)
        evidence = generate_gpu_shape_evidence_v0_2(
            request, objective_source=objective_source,
            generated_at=args.generated_at, model_path=args.model,
            source_path=args.source, split_path=args.split_manifest)
        validate_gpu_shape_evidence_v0_2(
            evidence, request=request, source_path=args.source,
            split_path=args.split_manifest)
        _write_json_atomic(args.output, evidence)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'gpu_shape_evidence_v0_2_failed: {exc}\n')


if __name__ == '__main__':
    main()
