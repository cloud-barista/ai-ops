"""Score-frozen chronological p90 experiment on SwissAI output tokens.

The raw-output extraction is a local research artifact. ``prepare`` fits only
training labels, ``validate`` fixes the comparator and gate, and ``test`` is
unavailable unless that frozen gate passes. Neither scores VM sizing or scale.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import polars as pl
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from swissai_output_tokens_core import (
    DAY_US, DaySnapshot, build_day_snapshots, pinball_loss,
    recent_prediction, static_quantiles, summarize_score,
)


ROOT = Path(__file__).resolve().parents[1]
COMPACT = ROOT / 'data/external/swissai_serving_trace/requests_sorted.parquet'
EXTRACTED = ROOT / 'data/external/swissai_serving_trace/output_tokens_sorted.parquet'
EXTRACTION_META = ROOT / 'data/external/swissai_serving_trace/output_tokens_extraction_v1.json'
PROTOCOL = ROOT / 'reports/swissai_output_tokens_protocol.md'
CORE = ROOT / 'scripts/swissai_output_tokens_core.py'
ARTIFACTS = ROOT / 'artifacts/swissai_output_tokens_v1'
EXTRACTION_QUALITY = ARTIFACTS / 'extraction_quality.json'
COMPACT_SHA = 'd5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b'
PROTOCOL_SHA = '8de1b6c67aa392ed038b41be97c25845943606bd7c94561f743fc92307ace512'
RAW_SHA = '79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7'
EXTRACTION_META_SHA = '2fffb5010404b630c72ba9034a4d4c7f08d2895642483d79a9a32d265d47758b'
EXPECTED_ROWS = 16_329_237
FROZEN_SPLIT_COUNTS = {
    'train': (7_965_677, 6_220_129),
    'validation': (300_467, 212_296),
    'test': (736_928, 498_008),
}
HOUR_US = 3_600_000_000
TRAIN_BEGIN = '2025-05-16'
VALID_BEGIN = '2025-08-16'
TEST_BEGIN = '2025-09-16'
TEST_END = '2025-10-24'
PARAMETERS = ('temperature', 'top_p', 'frequency_penalty',
              'presence_penalty', 'max_tokens')
METHODS = ('static_global', 'static_model', 'recent_global',
           'recent_model', 'hgb')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def day_number(value: str) -> int:
    stamp = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1_000_000) // DAY_US


def alias(name: str) -> str:
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]


def write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'refusing_to_replace:{path}')
    path.write_text(json.dumps(value, sort_keys=True, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def verify_pins() -> None:
    if sha256(PROTOCOL) != PROTOCOL_SHA:
        raise ValueError('protocol_changed_after_freeze')
    if sha256(COMPACT) != COMPACT_SHA:
        raise ValueError('sorted_swissai_trace_changed')
    if not EXTRACTED.exists():
        raise FileNotFoundError('run_frozen_output_extraction_first')
    if sha256(EXTRACTION_META) != EXTRACTION_META_SHA:
        raise ValueError('extraction_provenance_changed')
    metadata = json.loads(EXTRACTION_META.read_text(encoding='utf-8'))
    if (metadata['source']['sha256'] != RAW_SHA or
            metadata['compact']['sha256'] != COMPACT_SHA or
            metadata['protocol_frozen_sha256'] != PROTOCOL_SHA or
            metadata['output']['sha256'] != sha256(EXTRACTED) or
            metadata['quality_audit']['sha256'] != sha256(EXTRACTION_QUALITY)):
        raise ValueError('extraction_provenance_does_not_match_files')


def load_data() -> dict:
    """Load the pinned local artifact; no held-out score is computed here."""
    verify_pins()
    compact = pl.read_parquet(COMPACT, columns=[
        'created_utc_us', 'finished_utc_us', 'finished_naive',
        'model', 'status', 'source_row'])
    outputs = pl.read_parquet(EXTRACTED, columns=[
        'source_row', 'reported_output_tokens', *PARAMETERS])
    if compact.height != EXPECTED_ROWS or outputs.height != EXPECTED_ROWS:
        raise ValueError('swissai_row_count_changed')
    if not np.array_equal(compact['source_row'].to_numpy(),
                          outputs['source_row'].to_numpy()):
        raise ValueError('source_row_join_mismatch')
    created = compact['created_utc_us'].to_numpy()
    if np.any(created[1:] < created[:-1]):
        raise ValueError('compact_creation_time_not_sorted')
    categorical = compact['model'].cast(pl.Categorical)
    codes = categorical.to_physical().to_numpy().astype(np.int32)
    categories = categorical.cat.get_categories().to_list()
    if len(categories) > 255:
        raise ValueError('model_count_exceeds_categorical_bins')
    valid_model = compact['model'].is_not_null().to_numpy()
    valid_model &= (~compact['model'].is_in(['\\N', '<missing>', '']).to_numpy())
    status_default = (compact['status'] == 'DEFAULT').to_numpy()
    finished = compact['finished_utc_us'].to_numpy().astype(np.float64)
    y_float = outputs['reported_output_tokens'].to_numpy().astype(np.float64)
    y_valid = np.isfinite(y_float) & (y_float >= 0)
    finish_valid = np.isfinite(finished) & (finished > created)
    eligible = valid_model & status_default & y_valid & finish_valid
    availability = np.full(len(created), np.iinfo(np.int64).max,
                           dtype=np.int64)
    availability[eligible] = finished[eligible].astype(np.int64) + HOUR_US
    params = np.column_stack([outputs[key].to_numpy().astype(np.float64)
                              for key in PARAMETERS])
    return {
        'created': created.astype(np.int64),
        'day': (created // DAY_US).astype(np.int32),
        'available': availability,
        'y': y_float,
        'eligible': eligible,
        'codes': codes,
        'model_aliases': [alias(item) for item in categories],
        'params': params,
        'finished_naive': compact['finished_naive'].to_numpy(),
        'output_observed': y_valid,
        'default_status': status_default,
        'model_valid': valid_model,
        'positive_finished': finish_valid,
    }


def split_indices(data: dict, start: str, end: str) -> np.ndarray:
    lower = day_number(start)
    upper = day_number(end)
    return np.flatnonzero(data['eligible'] &
                          (data['day'] >= lower) & (data['day'] < upper))


def quality_counts(data: dict, start: str, end: str) -> dict:
    in_split = ((data['day'] >= day_number(start)) &
                (data['day'] < day_number(end)))
    accepted = in_split & data['eligible']
    return {
        'arrivals': int(in_split.sum()),
        'reported_output_observed': int((in_split & data['output_observed']).sum()),
        'reported_output_unavailable': int((in_split & ~data['output_observed']).sum()),
        'default_and_output_observed': int((in_split & data['default_status'] &
                                            data['output_observed']).sum()),
        'eligible_after_finish_and_model_checks': int(accepted.sum()),
        'eligible_model_count': int(np.unique(data['codes'][accepted]).size),
        'eligible_days': int(np.unique(data['day'][accepted]).size),
        'invalid_model': int((in_split & ~data['model_valid']).sum()),
        'nonpositive_or_missing_finish': int((in_split & ~data['positive_finished']).sum()),
    }


def history_for_days(data: dict, requested_days: np.ndarray,
                     *, explicit_finished_only: bool = False) -> dict[int, DaySnapshot]:
    valid = data['eligible']
    if explicit_finished_only:
        valid = valid & ~data['finished_naive']
    return build_day_snapshots(
        requested_days, data['available'][valid], data['codes'][valid],
        data['y'][valid].astype(np.int64), len(data['model_aliases']))


def sample_training(indices: np.ndarray, day: np.ndarray) -> np.ndarray:
    random = np.random.default_rng(42)
    selected = []
    for current in np.unique(day[indices]):
        eligible = indices[day[indices] == current]
        if len(eligible) > 5_000:
            eligible = np.sort(random.choice(eligible, 5_000, replace=False))
        selected.append(eligible)
    return np.concatenate(selected) if selected else np.array([], dtype=np.int64)


def feature_matrix(data: dict, indices: np.ndarray,
                   snapshots: dict[int, DaySnapshot],
                   known_codes: np.ndarray) -> np.ndarray:
    """Current submit-time fields plus earlier day-start outcome summaries."""
    n = len(indices)
    x = np.full((n, 16), np.nan, dtype=np.float64)
    codes = data['codes'][indices]
    known = np.searchsorted(known_codes, codes)
    exists = (known < len(known_codes))
    exists[exists] &= known_codes[known[exists]] == codes[exists]
    x[exists, 0] = known[exists]
    created = data['created'][indices]
    days = data['day'][indices]
    x[:, 1] = (created // HOUR_US) % 24
    x[:, 2] = (days + 3) % 7  # 1970-01-01 was Thursday (Monday=0).
    x[:, 3:8] = data['params'][indices]
    for day in np.unique(days):
        positions = np.flatnonzero(days == day)
        if int(day) not in snapshots:
            raise ValueError('missing_causal_day_snapshot')
        snapshot = snapshots[int(day)]
        row_codes = codes[positions]
        for offset, summary in ((8, snapshot.one_day),
                                (12, snapshot.seven_day)):
            x[positions, offset] = np.log1p(summary.model_count[row_codes])
            x[positions, offset + 1] = summary.model_p50[row_codes]
            x[positions, offset + 2] = summary.model_p90[row_codes]
            x[positions, offset + 3] = summary.p90
    return x


def baseline_predictions(data: dict, indices: np.ndarray,
                         snapshots: dict[int, DaySnapshot],
                         static_global: float,
                         static_model: np.ndarray) -> dict[str, np.ndarray]:
    codes = data['codes'][indices]
    days = data['day'][indices]
    predictions = {
        'static_global': np.full(len(indices), static_global, dtype=np.float64),
        'static_model': static_model[codes].astype(np.float64),
        'recent_global': np.empty(len(indices), dtype=np.float64),
        'recent_model': np.empty(len(indices), dtype=np.float64),
    }
    for day in np.unique(days):
        positions = np.flatnonzero(days == day)
        seven = snapshots[int(day)].seven_day
        predictions['recent_global'][positions] = (
            seven.p90 if seven.count else static_global)
        predictions['recent_model'][positions] = recent_prediction(
            snapshots[int(day)], codes[positions], static_global)
    return predictions


def block_metrics(y: np.ndarray, prediction: np.ndarray, days: np.ndarray,
                  start: str, end: str) -> tuple[list[dict], float | None]:
    first = day_number(start)
    full_count = (day_number(end) - first) // 7
    details = []
    full_losses = []
    for block in range((day_number(end) - first + 6) // 7):
        low = first + 7 * block
        high = min(low + 7, day_number(end))
        mask = (days >= low) & (days < high)
        if not np.any(mask):
            details.append({'block': block, 'days': int(high - low), 'count': 0})
            continue
        score = summarize_score(y[mask], prediction[mask])
        details.append({'block': block, 'days': int(high - low), **score})
        if block < full_count:
            full_losses.append(score['mean_pinball_tokens'])
    return details, (float(np.mean(full_losses))
                     if len(full_losses) == full_count else None)


def evaluate_methods(data: dict, indices: np.ndarray,
                     snapshots: dict[int, DaySnapshot], known_codes: np.ndarray,
                     static_global: float, static_model: np.ndarray,
                     model: HistGradientBoostingRegressor,
                     start: str, end: str) -> dict:
    if not len(indices):
        raise ValueError('empty_evaluation_population')
    y = data['y'][indices]
    predictions = baseline_predictions(data, indices, snapshots,
                                       static_global, static_model)
    x = feature_matrix(data, indices, snapshots, known_codes)
    predictions['hgb'] = np.maximum(0.0, model.predict(x))
    days = data['day'][indices]
    scores = {}
    for method, pred in predictions.items():
        blocks, equal_full = block_metrics(y, pred, days, start, end)
        scores[method] = {
            'overall': summarize_score(y, pred),
            'complete_7d_block_equal_pinball_tokens': equal_full,
            'blocks': blocks,
        }
    by_model = {}
    for code in np.unique(data['codes'][indices]):
        mask = data['codes'][indices] == code
        if mask.sum() < 1_000:
            continue
        by_model[data['model_aliases'][code]] = {
            method: summarize_score(y[mask], pred[mask])
            for method, pred in predictions.items()}
    return {'rows': len(indices), 'scores': scores, 'by_model_at_least_1000': by_model}


def gate(validation: dict) -> dict:
    scores = validation['scores']
    baselines = ('static_global', 'static_model', 'recent_global',
                 'recent_model')
    if any(scores[name]['complete_7d_block_equal_pinball_tokens'] is None
           for name in METHODS):
        return {'passes': False, 'reason': 'missing_complete_validation_block'}
    selected = min(baselines,
                   key=lambda name: scores[name]['complete_7d_block_equal_pinball_tokens'])
    model_score = scores['hgb']['complete_7d_block_equal_pinball_tokens']
    reference = scores[selected]['complete_7d_block_equal_pinball_tokens']
    by_model = validation['by_model_at_least_1000']
    model_wins = sum(item['hgb']['mean_pinball_tokens'] <=
                     item[selected]['mean_pinball_tokens']
                     for item in by_model.values())
    conditions = {
        'at_least_5pct_lower_equal_block_loss': model_score <= 0.95 * reference,
        'overall_coverage_at_least_85pct': scores['hgb']['overall']['coverage'] >= 0.85,
        'supported_models_coverage_at_least_85pct': all(
            item['hgb']['coverage'] >= 0.85 for item in by_model.values()),
        'supported_model_majority_loss_not_worse': (
            len(by_model) > 0 and model_wins > len(by_model) / 2),
    }
    return {'passes': all(conditions.values()), 'selected_baseline': selected,
            'conditions': conditions, 'supported_models': len(by_model),
            'supported_model_wins': model_wins}


def frozen_test_gate(result: dict, selected_baseline: str) -> dict:
    """Apply the validation-frozen comparator and thresholds on the test."""
    scores = result['scores']
    reference = scores[selected_baseline]['complete_7d_block_equal_pinball_tokens']
    model_score = scores['hgb']['complete_7d_block_equal_pinball_tokens']
    by_model = result['by_model_at_least_1000']
    model_wins = sum(item['hgb']['mean_pinball_tokens'] <=
                     item[selected_baseline]['mean_pinball_tokens']
                     for item in by_model.values())
    model_blocks = scores['hgb']['blocks']
    baseline_blocks = scores[selected_baseline]['blocks']
    full_pairs = [(new, old) for new, old in zip(model_blocks, baseline_blocks)
                  if new['days'] == 7 and new['count'] and old['count']]
    block_wins = sum(new['mean_pinball_tokens'] < old['mean_pinball_tokens']
                     for new, old in full_pairs)
    conditions = {
        'at_least_5pct_lower_equal_block_loss': (
            reference is not None and model_score is not None and
            model_score <= 0.95 * reference),
        'overall_coverage_at_least_85pct': scores['hgb']['overall']['coverage'] >= 0.85,
        'supported_models_coverage_at_least_85pct': all(
            item['hgb']['coverage'] >= 0.85 for item in by_model.values()),
        'supported_model_majority_loss_not_worse': (
            len(by_model) > 0 and model_wins > len(by_model) / 2),
        'complete_7d_block_majority_win': (
            len(full_pairs) > 0 and block_wins > len(full_pairs) / 2),
    }
    return {'passes': all(conditions.values()), 'selected_baseline': selected_baseline,
            'conditions': conditions, 'complete_blocks': len(full_pairs),
            'complete_block_wins': block_wins, 'supported_models': len(by_model),
            'supported_model_wins': model_wins}


def load_manifest() -> tuple[dict, dict]:
    manifest_path = ARTIFACTS / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    pins = manifest['sha256']
    if pins['protocol'] != sha256(PROTOCOL) or pins['compact'] != sha256(COMPACT):
        raise ValueError('frozen_source_or_protocol_changed')
    if pins['extracted'] != sha256(EXTRACTED):
        raise ValueError('frozen_extracted_labels_changed')
    if (pins['extraction_meta'] != sha256(EXTRACTION_META) or
            pins['extraction_quality'] != sha256(EXTRACTION_QUALITY)):
        raise ValueError('frozen_extraction_audit_changed')
    if pins['evaluator'] != sha256(Path(__file__)) or pins['core'] != sha256(CORE):
        raise ValueError('frozen_evaluation_code_changed')
    model_path = ARTIFACTS / 'model.joblib'
    if pins['model'] != sha256(model_path):
        raise ValueError('frozen_fitted_model_changed')
    return manifest, joblib.load(model_path)


def prepare() -> dict:
    if (ARTIFACTS / 'model.joblib').exists() or (ARTIFACTS / 'manifest.json').exists():
        raise FileExistsError('frozen_training_artifact_already_exists')
    data = load_data()
    quality = {name: quality_counts(data, start, end) for name, start, end in (
        ('train', TRAIN_BEGIN, VALID_BEGIN),
        ('validation', VALID_BEGIN, TEST_BEGIN),
        ('test', TEST_BEGIN, TEST_END))}
    for name, (arrivals, observed) in FROZEN_SPLIT_COUNTS.items():
        if (quality[name]['arrivals'],
                quality[name]['reported_output_observed']) != (arrivals, observed):
            raise ValueError('frozen_split_output_observation_counts_changed:' + name)
    if any(quality[name]['eligible_after_finish_and_model_checks'] < 10_000 or
           quality[name]['eligible_model_count'] < 3 or
           quality[name]['eligible_days'] < 7
           for name in ('validation', 'test')):
        raise ValueError('predeclared_label_support_gate_failed')
    train_all = split_indices(data, TRAIN_BEGIN, VALID_BEGIN)
    train_all = train_all[data['available'][train_all] <
                          day_number(VALID_BEGIN) * DAY_US]
    if not len(train_all):
        raise ValueError('no_train_labels_available_by_validation_start')
    static_global, static_model = static_quantiles(
        data['codes'][train_all], data['y'][train_all].astype(np.int64),
        len(data['model_aliases']))
    sample = sample_training(train_all, data['day'])
    known_codes = np.unique(data['codes'][sample])
    train_days = np.unique(data['day'][sample])
    history = history_for_days(data, train_days)
    x_train = feature_matrix(data, sample, history, known_codes)
    model = HistGradientBoostingRegressor(
        loss='quantile', quantile=0.9, max_iter=120, max_leaf_nodes=15,
        max_depth=4, min_samples_leaf=200, learning_rate=0.05,
        l2_regularization=10, early_stopping=False, random_state=42,
        categorical_features=[0])
    model.fit(x_train, data['y'][sample])
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    bundle = {'model': model, 'known_codes': known_codes,
              'static_global': static_global, 'static_model': static_model,
              'model_aliases': data['model_aliases']}
    joblib.dump(bundle, ARTIFACTS / 'model.joblib')
    manifest = {
        'kind': 'swissai_output_tokens_v1_score_frozen',
        'target': 'logged DEFAULT positive-duration observed output tokens',
        'quality_counts': quality,
        'training_rows_available': int(len(train_all)),
        'training_rows_sampled': int(len(sample)),
        'training_model_count': int(len(known_codes)),
        'training_sklearn_version': sklearn.__version__,
        'source_raw_sha256': RAW_SHA,
        'splits_utc': [TRAIN_BEGIN, VALID_BEGIN, TEST_BEGIN, TEST_END],
        'sha256': {
            'compact': sha256(COMPACT), 'extracted': sha256(EXTRACTED),
            'extraction_meta': sha256(EXTRACTION_META),
            'extraction_quality': sha256(EXTRACTION_QUALITY),
            'protocol': sha256(PROTOCOL), 'evaluator': sha256(Path(__file__)),
            'core': sha256(CORE),
            'model': sha256(ARTIFACTS / 'model.joblib'),
        },
    }
    write_once(ARTIFACTS / 'manifest.json', manifest)
    return {'training_rows_sampled': len(sample), 'quality_counts': quality,
            'manifest_sha256': sha256(ARTIFACTS / 'manifest.json')}


def validate() -> dict:
    manifest, bundle = load_manifest()
    if (ARTIFACTS / 'validation.json').exists():
        raise FileExistsError('validation_already_scored')
    data = load_data()
    indices = split_indices(data, VALID_BEGIN, TEST_BEGIN)
    history = history_for_days(data, np.unique(data['day'][indices]))
    result = evaluate_methods(data, indices, history, bundle['known_codes'],
                              bundle['static_global'], bundle['static_model'],
                              bundle['model'], VALID_BEGIN, TEST_BEGIN)
    result['gate'] = gate(result)
    result['manifest_sha256'] = sha256(ARTIFACTS / 'manifest.json')
    write_once(ARTIFACTS / 'validation.json', result)
    return {'validation_rows': result['rows'], 'gate': result['gate']}


def test() -> dict:
    manifest, bundle = load_manifest()
    validation_path = ARTIFACTS / 'validation.json'
    validation = json.loads(validation_path.read_text(encoding='utf-8'))
    if validation['manifest_sha256'] != sha256(ARTIFACTS / 'manifest.json'):
        raise ValueError('validation_manifest_changed')
    if not validation['gate']['passes']:
        raise ValueError('frozen_validation_gate_failed_test_remains_closed')
    if (ARTIFACTS / 'test.json').exists():
        raise FileExistsError('test_already_scored')
    data = load_data()
    indices = split_indices(data, TEST_BEGIN, TEST_END)
    history = history_for_days(data, np.unique(data['day'][indices]))
    result = evaluate_methods(data, indices, history, bundle['known_codes'],
                              bundle['static_global'], bundle['static_model'],
                              bundle['model'], TEST_BEGIN, TEST_END)
    result['selected_baseline'] = validation['gate']['selected_baseline']
    result['gate'] = frozen_test_gate(result, result['selected_baseline'])
    result['manifest_sha256'] = sha256(ARTIFACTS / 'manifest.json')
    result['validation_sha256'] = sha256(validation_path)
    write_once(ARTIFACTS / 'test.json', result)
    return {'test_rows': result['rows'],
            'selected_baseline': result['selected_baseline']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'validate', 'test'))
    args = parser.parse_args()
    result = {'prepare': prepare, 'validate': validate, 'test': test}[args.phase]()
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
