"""Supplement frozen SwissAI output-token validation without scoring test rows.

This reuses the frozen fitted model and evaluator. The original validation
artifact, model, manifest, protocol and evaluator remain unchanged. The
supplement is exploratory because the validation outcomes were already seen.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import evaluate_swissai_output_tokens as frozen
from swissai_output_tokens_core import summarize_score


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / 'artifacts/swissai_output_tokens_v1/validation.json'
OUTPUT = ROOT / 'artifacts/swissai_output_tokens_v1/validation_slices.json'
EXPECTED_VALIDATION_SHA256 = (
    '3ed9a268eb473b7f44750dc415d65aa9fe9e7a6b8eeb34ff780f758de8fc0857'
)


def verify_frozen_validation() -> dict:
    """Reject changed scores or a changed gate before any supplementary work."""
    if frozen.sha256(VALIDATION) != EXPECTED_VALIDATION_SHA256:
        raise ValueError('frozen_validation_score_changed')
    validation = json.loads(VALIDATION.read_text(encoding='utf-8'))
    if validation['manifest_sha256'] != frozen.sha256(
        frozen.ARTIFACTS / 'manifest.json'
    ):
        raise ValueError('frozen_validation_manifest_mismatch')
    if validation['gate'] != frozen.gate(validation):
        raise ValueError('frozen_validation_gate_mismatch')
    if validation['gate']['passes']:
        raise ValueError('expected_closed_test_gate_changed')
    return validation


def predictions_for(
    data: dict, indices: np.ndarray, snapshots: dict,
    bundle: dict,
) -> dict[str, np.ndarray]:
    predictions = frozen.baseline_predictions(
        data, indices, snapshots,
        bundle['static_global'], bundle['static_model'],
    )
    features = frozen.feature_matrix(
        data, indices, snapshots, bundle['known_codes'],
    )
    predictions['hgb'] = np.maximum(0.0, bundle['model'].predict(features))
    if set(predictions) != set(frozen.METHODS):
        raise ValueError('frozen_methods_changed')
    return predictions


def score_slices(
    data: dict, indices: np.ndarray, predictions: dict[str, np.ndarray],
    known_codes: np.ndarray,
) -> dict:
    """Score only supplied validation indices, split by sampled model support."""
    eligible_validation = frozen.split_indices(
        data, frozen.VALID_BEGIN, frozen.TEST_BEGIN,
    )
    if not np.array_equal(indices, eligible_validation):
        raise ValueError('supplement_requires_full_validation_only')
    known = np.isin(data['codes'][indices], known_codes)
    y = data['y'][indices]
    days = data['day'][indices]
    groups = {
        'all': np.ones(len(indices), dtype=np.bool_),
        'sampled_training_model': known,
        'unseen_training_model': ~known,
    }
    result = {}
    for name, mask in groups.items():
        if not np.any(mask):
            result[name] = {'rows': 0, 'models': 0, 'scores': None}
            continue
        scores = {}
        for method in frozen.METHODS:
            prediction = predictions[method]
            if len(prediction) != len(indices):
                raise ValueError('prediction_length_mismatch:' + method)
            blocks, equal_full = frozen.block_metrics(
                y[mask], prediction[mask], days[mask],
                frozen.VALID_BEGIN, frozen.TEST_BEGIN,
            )
            scores[method] = {
                'overall': summarize_score(y[mask], prediction[mask]),
                'complete_7d_block_equal_pinball_tokens': equal_full,
                'blocks': blocks,
            }
        result[name] = {
            'rows': int(mask.sum()),
            'models': int(np.unique(data['codes'][indices][mask]).size),
            'scores': scores,
        }
    return result


def audit() -> dict:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError('validation_slices_already_exists')
    # load_manifest verifies the frozen model, code, extracted data, provenance,
    # compact source and protocol hashes. load_data repeats the source checks.
    manifest, bundle = frozen.load_manifest()
    validation = verify_frozen_validation()
    data = frozen.load_data()
    indices = frozen.split_indices(data, frozen.VALID_BEGIN, frozen.TEST_BEGIN)
    if len(indices) != validation['rows']:
        raise ValueError('frozen_validation_row_count_mismatch')
    days = np.unique(data['day'][indices])
    normal_history = frozen.history_for_days(data, days)
    normal_predictions = predictions_for(data, indices, normal_history, bundle)
    original = score_slices(data, indices, normal_predictions, bundle['known_codes'])
    if original['all']['scores'] != validation['scores']:
        raise ValueError('frozen_validation_scores_do_not_replay')

    explicit_history = frozen.history_for_days(
        data, days, explicit_finished_only=True,
    )
    explicit_predictions = predictions_for(
        data, indices, explicit_history, bundle,
    )
    sensitivity = score_slices(
        data, indices, explicit_predictions, bundle['known_codes'],
    )

    first_history_start = int(days.min()) * frozen.DAY_US - 7 * frozen.DAY_US
    last_history_end = int(days.max()) * frozen.DAY_US
    history_union = (
        data['eligible'] &
        (data['available'] >= first_history_start) &
        (data['available'] < last_history_end)
    )
    # All scored indices are explicitly the validation slice. The all-row load
    # reads the pinned Parquet physically, as the frozen protocol discloses,
    # but this supplement computes no test label distribution or test score.
    result = {
        'kind': 'swissai_output_tokens_validation_slices_v1',
        'scope': 'exploratory validation-only replay of the frozen model',
        'validation_interval_utc': [frozen.VALID_BEGIN, frozen.TEST_BEGIN],
        'test_scores_or_distribution_computed': False,
        'frozen_validation_sha256': EXPECTED_VALIDATION_SHA256,
        'frozen_manifest_sha256': frozen.sha256(
            frozen.ARTIFACTS / 'manifest.json'
        ),
        'original_frozen_scores_exactly_replayed': True,
        'selected_baseline': validation['gate']['selected_baseline'],
        'frozen_validation_gate_passes': False,
        'normal_history': original,
        'explicit_utc_history_only': sensitivity,
        'history_sensitivity_definition': (
            'Remove naive finished_at records only from prior outcome '
            'windows; keep the validation target rows, static training '
            'quantiles, fitted model and all thresholds unchanged.'
        ),
        'history_union_7d_window_support': {
            'eligible_events': int(history_union.sum()),
            'naive_finished_events_removed': int((
                history_union & data['finished_naive']).sum()),
        },
        'sampled_training_model_definition': (
            'Model category present in the frozen 247802-row training sample.'
        ),
    }
    frozen.write_once(OUTPUT, result)
    return {
        'output': str(OUTPUT),
        'validation_rows': len(indices),
        'unseen_training_model_rows': original['unseen_training_model']['rows'],
        'frozen_validation_gate_passes': False,
    }


def main() -> None:
    print(json.dumps(audit(), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
