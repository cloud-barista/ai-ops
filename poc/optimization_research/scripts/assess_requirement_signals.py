"""Independent offline research signals for a declared new or known encoder.

These are measured-profile estimates of a CUDA allocator peak and cached local
model/tokenizer loading time. They are not minimum VRAM, OOM, VM readiness,
deployment, or scaling recommendations. The caller supplies registration
metadata; this API checks its form and time order but cannot certify its truth.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor

from evaluate_load_support import predict_support
from evaluate_vram_conditional import forecast_with_research_estimator
from evaluate_vram_peak import ROOT


SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
VRAM_MODEL = ROOT / 'artifacts/vram_conditional_v1/research_estimator.joblib'
LOAD_MODEL = ROOT / 'artifacts/load_support_v1/support_aware_size_tree_all_main.joblib'
SOURCE_SHA256 = 'b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906'
VRAM_SHA256 = '21387fd6ba6b6185737ed9e10ba0930b9c2696e033ac532287bd84e8f6501e01'
LOAD_SHA256 = '39a8d614c101a89d1ec65dca136a4c24415da3b0d41ff8f4209422fd9ee76131'
SKLEARN_VERSION = '1.6.1'
NUMPY_VERSION = '2.0.2'
PANDAS_VERSION = '2.3.0+4.g1dfc98e16a'
JOBLIB_VERSION = '1.5.1'
PARAMETER_CALCULATION = 'sum(parameter.numel() * parameter.element_size() for parameter in model.parameters())'
ALLOWED_SOURCES = {'application_registry', 'registration_manifest'}
EXPECTED_PROFILE = {
    'host': 'icns',
    'device': 'cuda',
    'device_name': 'NVIDIA RTX PRO 5000 Blackwell',
    'gpu_indices': [0],
    'replica_count': 1,
    'cpu_core_limit': 2,
    'cpu_threads': 2,
}
SIGNALS = {
    'observed_cuda_reserved_peak_gib': {
        'target_definition': 'empirical_q90_of_observed_torch_cuda_max_memory_reserved',
        'unit': 'GiB', 'artifact_sha256': VRAM_SHA256,
    },
    'cached_local_load_seconds': {
        'target_definition': 'cached_local_huggingface_model_and_tokenizer_load_seconds',
        'unit': 'seconds', 'artifact_sha256': LOAD_SHA256,
    },
}
SCOPE = ('Offline research evidence on icns RTX PRO 5000 Blackwell GPU 0, '
         'CUDA replica 1, CPU affinity 2. No minimum VRAM, OOM, VM startup, '
         'deployment, or scaling decision.')
VERIFIED_KEY = '_requirement_signal_verified_artifacts'
REVISION_RE = re.compile(r'^[0-9a-fA-F]{40}$')
REQUEST_FIELDS = {'model_id', 'model_revision', 'workload_task',
                  'batch_size', 'max_length', 'request_concurrency',
                  'decision_at', 'parameter_metadata', 'hardware_profile'}
METADATA_FIELDS = {'model_parameter_count', 'model_parameter_bytes', 'dtype',
                   'calculation', 'source', 'available_at'}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_from_request(profile: dict) -> dict:
    return {key: ('[0]' if key == 'gpu_indices_json' else profile[key])
            for key in ('host', 'device', 'device_name', 'gpu_indices_json',
                        'replica_count', 'cpu_core_limit')}


def _selected_source_panel(source: io.BytesIO) -> pd.DataFrame:
    raw = pd.read_parquet(source)
    selected = raw.loc[
        (raw.campaign == 'khu-hf-local-exhaustive') &
        (raw.host == EXPECTED_PROFILE['host']) &
        (raw.device == EXPECTED_PROFILE['device']) &
        (raw.device_name == EXPECTED_PROFILE['device_name']) &
        (raw.gpu_indices_json == '[0]') &
        (raw.replica_count == 1) &
        (raw.cpu_core_limit == 2) &
        (raw.cpu_threads == 2) &
        (raw.success == 1) &
        (raw.eligible == True)
    ].copy()
    if len(selected) != 575 or selected.model_id.nunique() != 9:
        raise ValueError('unexpected_source_panel')
    if selected.groupby('model_id').model_revision.nunique().max() != 1:
        raise ValueError('inconsistent_training_revision')
    if set(selected.workload_task) != {'inference', 'causal_lm_forward'}:
        raise ValueError('unexpected_training_tasks')
    return selected


def _training_metadata(panel: pd.DataFrame) -> dict[str, dict]:
    result = {}
    for model_id, group in panel.groupby('model_id'):
        readings = {
            (int(json.loads(raw)['model_parameter_count']),
             int(json.loads(raw)['model_parameter_bytes']))
            for raw in group.result_json
        }
        if len(readings) != 1:
            raise ValueError('inconsistent_training_parameter_metadata')
        count, size = next(iter(readings))
        result[str(model_id)] = {
            'revision': str(group.model_revision.iloc[0]),
            'task': str(group.workload_task.iloc[0]),
            'parameter_count': count,
            'parameter_bytes': size,
        }
    return result


def load_verified_bundles(vram_path: Path = VRAM_MODEL,
                          load_path: Path = LOAD_MODEL,
                          source_path: Path = SOURCE) -> dict:
    """Read and verify one byte snapshot of each fixed local artifact."""
    expected = ((source_path, SOURCE_SHA256, 'source'),
                (vram_path, VRAM_SHA256, 'vram_model'),
                (load_path, LOAD_SHA256, 'load_model'))
    verified_bytes: dict[str, bytes] = {}
    for path, digest, name in expected:
        snapshot = path.read_bytes()
        if hashlib.sha256(snapshot).hexdigest() != digest:
            raise ValueError(name + '_sha256_mismatch')
        verified_bytes[name] = snapshot
    versions = ((sklearn.__version__, SKLEARN_VERSION, 'sklearn'),
                (np.__version__, NUMPY_VERSION, 'numpy'),
                (pd.__version__, PANDAS_VERSION, 'pandas'),
                (joblib.__version__, JOBLIB_VERSION, 'joblib'))
    for actual, expected_version, name in versions:
        if actual != expected_version:
            raise ValueError(name + '_version_mismatch')
    # Reading paths again after the hash check would permit a file replacement
    # before parsing or joblib deserialization. Use only verified snapshots.
    panel = _selected_source_panel(io.BytesIO(verified_bytes['source']))
    training_metadata = _training_metadata(panel)
    vram = joblib.load(io.BytesIO(verified_bytes['vram_model']))
    load = joblib.load(io.BytesIO(verified_bytes['load_model']))
    ids = set(panel.model_id.astype(str))
    if vram.get('kind') != 'research_only_observed_cuda_reserved_peak_conditional':
        raise ValueError('unexpected_vram_model_kind')
    if vram.get('bandwidth') != 1.0 or vram.get('bandwidth_name') != 'h1':
        raise ValueError('unexpected_vram_calibration')
    if set(vram.get('training_model_ids', ())) != ids:
        raise ValueError('vram_donor_models_mismatch')
    profile = vram.get('profile', {})
    for key, value in _profile_from_request(EXPECTED_PROFILE).items():
        if profile.get(key) != value:
            raise ValueError('unexpected_vram_hardware_profile')
    counts = panel.groupby('workload_task').model_id.nunique().to_dict()
    if vram.get('task_model_counts') != counts:
        raise ValueError('vram_task_support_mismatch')
    if getattr(vram.get('model'), 'n_features_in_', None) != 7:
        raise ValueError('unexpected_vram_feature_schema')
    scores = vram.get('scores')
    if not isinstance(scores, pd.DataFrame) or not {
            'model_id', 'workload_task', 'parameter_gib', 'ratio'}.issubset(scores.columns):
        raise ValueError('unexpected_vram_scores_schema')
    if load.get('method') != 'support_aware_size_tree':
        raise ValueError('unexpected_load_model_kind')
    if load.get('min_same_task_donors') != 2:
        raise ValueError('unexpected_load_support_threshold')
    if set(load.get('training_model_ids', ())) != ids:
        raise ValueError('load_donor_models_mismatch')
    load_estimator = load.get('estimator')
    if not isinstance(load_estimator, ExtraTreesRegressor) or getattr(
            load_estimator, 'n_features_in_', None) != 1:
        raise ValueError('unexpected_load_feature_schema')
    # Tree predictions with parallel workers can differ in the last bit as
    # partial sums finish in different orders. This changes only inference.
    load_estimator.n_jobs = 1
    for task, count in counts.items():
        if load.get('task_support', {}).get(task, {}).get('model_count') != count:
            raise ValueError('load_task_support_mismatch')
    configurations = set(map(tuple, panel.loc[panel.workload_task == 'inference',
                                              ['batch_size', 'max_length',
                                               'request_concurrency']].to_numpy()))
    if len(configurations) != 64:
        raise ValueError('unexpected_inference_configuration_grid')
    return {
        'vram': vram,
        'load': load,
        'training_metadata': training_metadata,
        'inference_configurations': configurations,
        VERIFIED_KEY: (SOURCE_SHA256, VRAM_SHA256, LOAD_SHA256),
    }


def _valid_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None and timestamp.utcoffset() is not None else None


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _same_json_value(actual: Any, expected: Any) -> bool:
    """Keep JSON booleans and floats from matching integer profile fields."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (actual.keys() == expected.keys()
                and all(_same_json_value(actual[key], value)
                        for key, value in expected.items()))
    if type(expected) is list:
        return (len(actual) == len(expected)
                and all(_same_json_value(a, b) for a, b in zip(actual, expected)))
    return actual == expected


def _request_errors(request: Any) -> list[str]:
    if not isinstance(request, dict):
        return ['request_must_be_object']
    errors = []
    if set(request) != REQUEST_FIELDS:
        errors.append('request_fields_mismatch')
    if not isinstance(request.get('model_id'), str) or not request['model_id'].strip():
        errors.append('invalid_model_id')
    if not isinstance(request.get('model_revision'), str) or not REVISION_RE.fullmatch(request['model_revision']):
        errors.append('invalid_pinned_model_revision')
    if request.get('workload_task') != 'inference':
        errors.append('unsupported_workload_task')
    for key in ('batch_size', 'max_length', 'request_concurrency'):
        if not _positive_int(request.get(key)):
            errors.append('invalid_' + key)
    profile = request.get('hardware_profile')
    if not isinstance(profile, dict):
        errors.append('missing_hardware_profile')
    elif not _same_json_value(profile, EXPECTED_PROFILE):
        errors.append('unsupported_hardware_profile')
    metadata = request.get('parameter_metadata')
    if not isinstance(metadata, dict):
        errors.append('missing_parameter_metadata')
    else:
        if set(metadata) != METADATA_FIELDS:
            errors.append('parameter_metadata_fields_mismatch')
        count = metadata.get('model_parameter_count')
        byte_count = metadata.get('model_parameter_bytes')
        if not _positive_int(count) or not _positive_int(byte_count) or byte_count > 2**63 - 1:
            errors.append('invalid_parameter_count_or_bytes')
        elif byte_count != count * 4:
            errors.append('parameter_bytes_do_not_match_float32_count')
        if metadata.get('dtype') != 'float32':
            errors.append('unsupported_parameter_dtype')
        if metadata.get('calculation') != PARAMETER_CALCULATION:
            errors.append('unsupported_parameter_calculation')
        if not isinstance(metadata.get('source'), str) or metadata['source'] not in ALLOWED_SOURCES:
            errors.append('unsupported_registration_source')
        available = _valid_timestamp(metadata.get('available_at'))
        decision = _valid_timestamp(request.get('decision_at'))
        if available is None or decision is None or available > decision:
            errors.append('invalid_registration_time_order')
    return errors


def _signal_result(name: str, status: str, relation: str,
                   estimate: float | None = None,
                   reasons: list[str] | None = None) -> dict:
    descriptor = SIGNALS[name]
    return {
        'status': status,
        'estimate': estimate,
        'unit': descriptor['unit'],
        'target_definition': descriptor['target_definition'],
        'training_relation': relation,
        'artifact_sha256': descriptor['artifact_sha256'],
        'reasons': list(reasons) if reasons else [],
    }


def _result(vram: dict, load: dict, hashes_verified_at_load: bool) -> dict:
    return {
        'scope': SCOPE,
        'metadata_provenance': 'caller_declared',
        'artifact_hashes_verified_at_load': hashes_verified_at_load,
        'source_sha256': SOURCE_SHA256,
        'training_sklearn_version': SKLEARN_VERSION,
        'runtime_sklearn_version': sklearn.__version__,
        'runtime_numpy_version': np.__version__,
        'runtime_pandas_version': pd.__version__,
        'runtime_joblib_version': joblib.__version__,
        'hardware_profile': copy.deepcopy(EXPECTED_PROFILE),
        'signals': {
            'observed_cuda_reserved_peak_gib': vram,
            'cached_local_load_seconds': load,
        },
    }


def _abstain_both(reasons: list[str], hashes_verified_at_load: bool = False) -> dict:
    return _result(
        _signal_result('observed_cuda_reserved_peak_gib', 'ABSTAIN', 'UNKNOWN', reasons=reasons),
        _signal_result('cached_local_load_seconds', 'ABSTAIN', 'UNKNOWN', reasons=reasons),
        hashes_verified_at_load,
    )


def assess_requirement_signals(bundles: dict, request: dict) -> dict:
    """Return separate research estimates; bundles must remain read-only.

    VERIFIED_KEY records disk hash verification at load time. It cannot
    authenticate subsequent in-memory mutation by the Python caller.
    """
    verified = bundles.get(VERIFIED_KEY) == (SOURCE_SHA256, VRAM_SHA256, LOAD_SHA256)
    errors = _request_errors(request)
    if errors:
        return _abstain_both(errors, verified)
    if not verified:
        return _abstain_both(['unverified_model_bundles'])
    model_id = request['model_id']
    model_revision = request['model_revision'].lower()
    training = bundles['training_metadata']
    metadata = request['parameter_metadata']
    if model_id in training:
        trained = training[model_id]
        if model_revision != trained['revision'].lower():
            return _abstain_both(['known_model_revision_mismatch'], True)
        if (request['workload_task'] != trained['task'] or
                metadata['model_parameter_count'] != trained['parameter_count'] or
                metadata['model_parameter_bytes'] != trained['parameter_bytes']):
            return _abstain_both(['known_model_registration_metadata_mismatch'], True)
    elif model_revision in {entry['revision'].lower() for entry in training.values()}:
        # A renamed ID cannot make an exact training revision an unseen model.
        return _abstain_both(['training_revision_claimed_under_new_model_id'], True)
    relation = ('IN_SAMPLE_EXPLORATORY' if model_id in training
                else 'DECLARED_UNSEEN_MODEL')
    parameter_gib = metadata['model_parameter_bytes'] / (1024.0 ** 3)
    support = bundles['load']['task_support'].get(request['workload_task'], {})
    common = {
        'model_id': model_id,
        'workload_task': request['workload_task'],
        'parameter_gib': parameter_gib,
        'batch_size': request['batch_size'],
        'max_length': request['max_length'],
        'request_concurrency': request['request_concurrency'],
        'host': EXPECTED_PROFILE['host'],
        'device': EXPECTED_PROFILE['device'],
        'device_name': EXPECTED_PROFILE['device_name'],
        'gpu_indices_json': '[0]',
        'replica_count': 1,
        'cpu_core_limit': 2,
    }
    frame = pd.DataFrame([common])
    shared_reasons = []
    if support.get('model_count', 0) < 2:
        shared_reasons.append('fewer_than_two_same_task_donor_models')
    if not math.isfinite(parameter_gib) or parameter_gib <= 0:
        shared_reasons.append('invalid_parameter_gib')
    elif not (support.get('min_parameter_gib', math.inf) <= parameter_gib <=
              support.get('max_parameter_gib', -math.inf)):
        shared_reasons.append('parameter_size_outside_same_task_training_range')
    if shared_reasons:
        return _abstain_both(shared_reasons, True)
    if model_id in training:
        vram = _signal_result('observed_cuda_reserved_peak_gib', 'ABSTAIN',
                              'IN_SAMPLE_EXPLORATORY',
                              reasons=['conditional_vram_requires_new_model_id'])
    elif ((request['batch_size'], request['max_length'],
           request['request_concurrency']) not in bundles['inference_configurations']):
        vram = _signal_result('observed_cuda_reserved_peak_gib', 'ABSTAIN', relation,
                              reasons=['unobserved_request_configuration'])
    else:
        try:
            estimate = float(forecast_with_research_estimator(bundles['vram'], frame)[0])
            if not math.isfinite(estimate) or estimate <= 0:
                raise ValueError('invalid_forecast')
            vram = _signal_result('observed_cuda_reserved_peak_gib',
                                  'RESEARCH_ESTIMATE', relation, estimate)
        except (ValueError, TypeError, OverflowError):
            vram = _signal_result('observed_cuda_reserved_peak_gib', 'ABSTAIN', relation,
                                  reasons=['vram_prediction_failed'])
    try:
        predicted = predict_support(bundles['load'], frame).iloc[0]
        estimate = float(predicted.predicted_load_seconds)
        if not bool(predicted.answered) or not math.isfinite(estimate) or estimate <= 0:
            raise ValueError('invalid_forecast')
        load = _signal_result('cached_local_load_seconds',
                              'RESEARCH_ESTIMATE', relation, estimate)
    except (ValueError, TypeError, OverflowError):
        load = _signal_result('cached_local_load_seconds', 'ABSTAIN', relation,
                              reasons=['load_prediction_failed'])
    return _result(vram, load, True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--vram-model', type=Path, default=VRAM_MODEL)
    parser.add_argument('--load-model', type=Path, default=LOAD_MODEL)
    args = parser.parse_args()
    try:
        request = json.loads(args.input.read_text())
        bundles = load_verified_bundles(args.vram_model, args.load_model,
                                         args.source)
        result = assess_requirement_signals(bundles, request)
    except (OSError, ValueError, TypeError) as exc:
        result = _abstain_both([str(exc)])
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
