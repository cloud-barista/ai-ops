"""Offline, same-host GPU-replica/CPU-affinity shape estimate for one workload.

This reads the pinned research performance model. It produces neither a VM
shape nor a deployment action; batch benchmark predictions are uncalibrated.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
import sklearn

from predict_performance import (derive_training_condition_support,
                                 predict_records)
from train_performance import FEATURES, ROOT, TARGETS


MODEL_SHA256 = '7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161'
SOURCE_SHA256 = 'b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906'
TRAIN_SKLEARN_VERSION = '1.6.1'
TRAIN_NUMPY_VERSION = '2.0.2'
TRAIN_PANDAS_VERSION = '2.3.0+4.g1dfc98e16a'
VERIFIED_JOBLIB_VERSION = '1.5.1'
VERIFIED_KEY = '_assess_gpu_shape_verified_artifacts'
SHAPES = {(replicas, cores) for replicas in (1, 2) for cores in (2, 4, 8, 16)}
MAX_CACHED_GRIDS = 64
CONSTANT_FIELDS = ('application_id', 'workload_id', 'model_id', 'model_revision',
                   'device', 'host', 'device_name', 'batch_size', 'max_length',
                   'request_concurrency', 'repeats')
CANDIDATE_FIELDS = set(FEATURES) | {'application_id', 'workload_id', 'host', 'device_name'}
REQUEST_FIELDS = {'batch_latency_p95_limit_ms', 'min_requests_per_second',
                  'candidates'}
SCOPE = ('Uncalibrated same-host, known-model batch benchmark estimate. '
         'CPU values are affinity cores; GPU values are benchmark replicas, '
         'not VM resources. No deployment or scaling action is authorized.')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_verified_bundle(model_path: Path, source_path: Path) -> dict:
    """Load exactly the model bytes whose digest was verified."""
    model_bytes = model_path.read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != MODEL_SHA256:
        raise ValueError('model_sha256_mismatch')
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError('source_sha256_mismatch')
    if sklearn.__version__ != TRAIN_SKLEARN_VERSION:
        raise ValueError('sklearn_version_mismatch:trained=' + TRAIN_SKLEARN_VERSION
                         + ',runtime=' + sklearn.__version__)
    for name, actual, expected in (
            ('numpy', np.__version__, TRAIN_NUMPY_VERSION),
            ('pandas', pd.__version__, TRAIN_PANDAS_VERSION),
            ('joblib', joblib.__version__, VERIFIED_JOBLIB_VERSION)):
        if actual != expected:
            raise ValueError(name + '_version_mismatch:expected=' + expected
                             + ',runtime=' + actual)
    # A second path open would allow a replacement between verification and
    # deserialization. The in-memory snapshot also covers in-place rewrites.
    bundle = joblib.load(io.BytesIO(model_bytes))
    if bundle.get('targets') != TARGETS:
        raise ValueError('unexpected_model_targets')
    if bundle.get('features') != FEATURES:
        raise ValueError('unexpected_model_features')
    if bundle.get('selected') is not None:
        # Reuse exactly the source bytes just verified; reopening the path
        # here would allow a different snapshot to define the support guard.
        snapshot = pd.read_parquet(io.BytesIO(source_bytes))
        bundle['training_condition_support'] = derive_training_condition_support(snapshot)
    bundle[VERIFIED_KEY] = (MODEL_SHA256, SOURCE_SHA256)
    return bundle


def _positive_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _request_errors(request: Any) -> list[str]:
    if not isinstance(request, dict):
        return ['request_must_be_object']
    errors = []
    missing = REQUEST_FIELDS - request.keys()
    extra = request.keys() - REQUEST_FIELDS
    if missing:
        errors.append('missing_request_fields:' + ','.join(sorted(missing)))
    if extra:
        errors.append('unsupported_request_fields:'
                      + ','.join(sorted(map(str, extra))))
    if errors:
        return errors
    if not _positive_finite_number(request.get('batch_latency_p95_limit_ms')):
        errors.append('invalid_batch_latency_p95_limit_ms')
    if not _positive_finite_number(request.get('min_requests_per_second')):
        errors.append('invalid_min_requests_per_second')
    candidates = request.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 8:
        errors.append('expected_exactly_eight_candidates')
        return errors
    if any(not isinstance(row, dict) for row in candidates):
        errors.append('candidate_must_be_object')
        return errors
    for index, row in enumerate(candidates):
        missing = CANDIDATE_FIELDS - row.keys()
        extra = row.keys() - CANDIDATE_FIELDS
        if missing or extra:
            errors.append(f'candidate_{index}_fields_mismatch')
        for key in ('application_id', 'workload_id', 'model_id', 'model_revision',
                    'host', 'device_name'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                errors.append(f'candidate_{index}_invalid_{key}')
        if row.get('device') != 'cuda':
            errors.append(f'candidate_{index}_requires_cuda')
        for key in ('batch_size', 'max_length', 'request_concurrency', 'repeats'):
            value = row.get(key)
            if type(value) is not int or not _positive_finite_number(value):
                errors.append(f'candidate_{index}_invalid_{key}')
        replicas, cores, threads = (row.get('replica_count'),
                                    row.get('cpu_core_limit'), row.get('cpu_threads'))
        if not all(type(value) is int for value in (replicas, cores, threads)):
            errors.append(f'candidate_{index}_invalid_resource_counts')
        elif threads != cores:
            errors.append(f'candidate_{index}_cpu_threads_must_match_affinity')
    if errors:
        return errors
    first = candidates[0]
    for index, row in enumerate(candidates[1:], start=1):
        if any(row[key] != first[key] for key in CONSTANT_FIELDS):
            errors.append(f'candidate_{index}_different_workload')
    shapes = [(row['replica_count'], row['cpu_core_limit']) for row in candidates]
    if set(shapes) != SHAPES or len(set(shapes)) != 8:
        errors.append('incomplete_or_duplicate_gpu_cpu_grid')
    return errors


def _predict_once(bundle: dict, candidates: list[dict]) -> list[dict]:
    """Let the predictor set one worker without mutating the caller's model."""
    if bundle['selected'] != 'extra_trees':
        return predict_records(bundle, candidates)
    pipeline = bundle['model']
    independent = copy.copy(pipeline)
    independent.steps = [(name, copy.copy(step) if name == 'regressor' else step)
                         for name, step in pipeline.steps]
    return predict_records({**bundle, 'model': independent}, candidates)


def _assess_gpu_shape(bundle: dict, request: dict,
                      predict_candidates: Callable[[dict, list[dict]], list[dict]],
                      *, _prevalidated_errors: list[str] | None = None) -> dict:
    """Validate and score one request using the supplied raw predictor.

    Internal batched callers may pass errors already computed for this exact
    request. Such callers must keep request and bundle read-only for the call.
    """
    verified = bundle.get(VERIFIED_KEY) == (MODEL_SHA256, SOURCE_SHA256)
    provenance = {'model_sha256': MODEL_SHA256,
                  'source_sha256': SOURCE_SHA256,
                  'hashes_verified': verified,
                  'training_sklearn_version': TRAIN_SKLEARN_VERSION,
                  'runtime_sklearn_version': sklearn.__version__}
    errors = (_request_errors(request) if _prevalidated_errors is None
              else _prevalidated_errors)
    if errors:
        return {'status': 'ABSTAIN', 'reasons': errors,
                'scope': SCOPE, 'provenance': provenance}
    if not verified:
        return {'status': 'ABSTAIN', 'reasons': ['unverified_model_bundle'],
                'scope': SCOPE, 'provenance': provenance}
    candidates = request['candidates']
    if bundle.get('targets') != TARGETS or bundle.get('features') != FEATURES:
        return {'status': 'ABSTAIN', 'reasons': ['unexpected_model_schema'],
                'scope': SCOPE, 'provenance': provenance}
    predicted = predict_candidates(bundle, candidates)
    if not isinstance(predicted, list) or len(predicted) != len(candidates):
        return {'status': 'ABSTAIN', 'reasons': ['prediction_count_mismatch'],
                'scope': SCOPE, 'provenance': provenance}
    assessments = []
    unsupported = []
    invalid = []
    for index, (candidate, outcome) in enumerate(zip(candidates, predicted)):
        item = {'gpu_replicas': candidate['replica_count'],
                'cpu_affinity_cores': candidate['cpu_core_limit']}
        if outcome['status'] != 'PREDICTED':
            item.update(status='ABSTAIN', reasons=outcome['reasons'])
            unsupported.append(index)
        else:
            batch_ms = outcome['prediction']['latency_p95_ms']
            examples_per_second = outcome['prediction']['examples_per_second']
            if not (_positive_finite_number(batch_ms)
                    and _positive_finite_number(examples_per_second)):
                item.update(status='ABSTAIN', reasons=['nonfinite_or_nonpositive_prediction'])
                invalid.append(index)
            else:
                rps = examples_per_second / candidate['batch_size']
                latency_margin = request['batch_latency_p95_limit_ms'] - batch_ms
                rps_margin = rps - request['min_requests_per_second']
                item.update(status='PREDICTED',
                            predicted_batch_latency_p95_ms=float(batch_ms),
                            predicted_requests_per_second=float(rps),
                            batch_latency_margin_ms=float(latency_margin),
                            requests_per_second_margin=float(rps_margin),
                            meets_estimated_slo=bool(latency_margin >= 0 and rps_margin >= 0))
        assessments.append(item)
    common = {
        'application_id': candidates[0]['application_id'],
        'workload_id': candidates[0]['workload_id'],
        'model_id': candidates[0]['model_id'],
        'model_revision': candidates[0]['model_revision'],
        'device': candidates[0]['device'],
        'host': candidates[0]['host'],
        'device_name': candidates[0]['device_name'],
        'batch_size': candidates[0]['batch_size'],
        'max_length': candidates[0]['max_length'],
        'request_concurrency': candidates[0]['request_concurrency'],
        'repeats': candidates[0]['repeats'],
        'batch_latency_p95_limit_ms': float(request['batch_latency_p95_limit_ms']),
        'min_requests_per_second': float(request['min_requests_per_second']),
        'candidate_assessments': assessments,
        'scope': SCOPE,
        'provenance': provenance,
    }
    if unsupported or invalid:
        reasons = []
        if unsupported:
            reasons.append('unsupported_candidates:' + ','.join(map(str, unsupported)))
        if invalid:
            reasons.append('invalid_predictions:' + ','.join(map(str, invalid)))
        return {'status': 'ABSTAIN', 'reasons': reasons, **common}
    qualifying = [item for item in assessments if item['meets_estimated_slo']]
    if not qualifying:
        return {'status': 'ABSTAIN',
                'reasons': ['no_candidate_meets_estimated_slo'], **common}
    chosen = min(qualifying, key=lambda item: (item['gpu_replicas'],
                                               item['cpu_affinity_cores']))
    return {'status': 'RESEARCH_ESTIMATE', 'selected': chosen, **common}


def assess_gpu_shape(bundle: dict, request: dict) -> dict:
    """Validate one workload and rank its supported benchmark configurations.

    This function has no file I/O or platform side effects. It copies the
    fitted regressor configuration before ``predict_records`` sets one worker,
    and calls that predictor once for all eight candidates.
    """
    return _assess_gpu_shape(bundle, request, _predict_once)


def assess_gpu_shape_many(bundle: dict, requests: list[dict]) -> list[dict]:
    """Assess offline SLO sweeps, reusing raw predictions for identical grids.

    Every request runs the same validation and scoring path as
    ``assess_gpu_shape``. The bounded cache lives for this call only; the
    caller must not mutate the bundle while the call is in progress. This is
    an offline benchmark estimate, not an infrastructure or action decision.
    """
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    raw_by_grid: OrderedDict[str, list[dict]] = OrderedDict()

    def predict_for_request(active_bundle: dict,
                            candidates: list[dict]) -> list[dict]:
        # _assess_gpu_shape reaches this function only after request and
        # verified-bundle checks. Include the full ordered grid and use a
        # collision-free canonical string instead of a shortened digest.
        key = json.dumps(candidates, sort_keys=True, separators=(',', ':'),
                         allow_nan=False)
        if key in raw_by_grid:
            raw_by_grid.move_to_end(key)
        else:
            raw_by_grid[key] = copy.deepcopy(_predict_once(active_bundle,
                                                            candidates))
            if len(raw_by_grid) > MAX_CACHED_GRIDS:
                raw_by_grid.popitem(last=False)
        # Unsupported candidate reasons contain mutable lists. Returning a
        # fresh copy prevents outputs from separate requests sharing them.
        return copy.deepcopy(raw_by_grid[key])

    return [_assess_gpu_shape(bundle, request, predict_for_request)
            for request in requests]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='JSON with batch_latency_p95_limit_ms, '
                             'min_requests_per_second and eight candidates')
    parser.add_argument('--model', type=Path,
                        default=ROOT / 'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    args = parser.parse_args()
    try:
        bundle = load_verified_bundle(args.model, args.source)
        request = json.loads(args.input.read_text())
        result = assess_gpu_shape(bundle, request)
    except (OSError, ValueError) as exc:
        result = {'status': 'ABSTAIN', 'reasons': [str(exc)], 'scope': SCOPE,
                  'provenance': {'model_sha256': MODEL_SHA256,
                                 'source_sha256': SOURCE_SHA256,
                                 'hashes_verified': False,
                                 'training_sklearn_version': TRAIN_SKLEARN_VERSION,
                                 'runtime_sklearn_version': sklearn.__version__}}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
