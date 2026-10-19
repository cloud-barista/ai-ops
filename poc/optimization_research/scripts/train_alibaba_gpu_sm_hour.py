"""Exploratory, workload-disjoint next-hour SM measurement forecasting.

This predicts a measurement conditional on the pod remaining observable on the
same server. It does not predict a scale action, deployment failure or SLO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import time

import joblib
import numpy as np
import pandas as pd
import polars as pl
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = Path('/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days109_115')
CATEGORICAL = ['gpu_spec_public', 'priority_class']
NUMERIC = ['sm_current', 'sm_lag1', 'sm_lag2', 'sm_mean3', 'sm_std3',
           'sm_delta1', 'sm_delta2', 'hour_sin', 'hour_cos',
           'server_gpu_count', 'cpu_request_cores']
FEATURES = CATEGORICAL + NUMERIC
RAW_COLUMNS = ['pod_id', 'workload_id', 'server_id', 'gpu_request',
               'server_gpu_count', 'gpu_spec_public', 'priority_class',
               'cpu_request_cores', 'avg_gpu_sm_util', 'day', 'hour']
BASELINES = ['persistence', 'past_three_mean', 'train_gpu_median']
ALPHAS = [0.0, 0.25, 0.5, 1.0]
SCOPE = ('Observed online GenAI pods requesting exactly one GPU; next-hour '
         'source SM measurement conditional on same-server observability; '
         'exploratory previously audited seven-day slice, not blind validation')


def bucket(workload: str) -> int:
    return int(hashlib.sha256(workload.encode()).hexdigest()[:8], 16) % 100


def assign_split(t: int, b: int) -> str | None:
    # Every target hour is contained in its split's date interval. Validation
    # and test may use their own workload's completed history before that date.
    if 109 * 24 <= t <= 112 * 24 + 22 and b < 70:
        return 'train'
    if 113 * 24 <= t <= 113 * 24 + 22 and 70 <= b < 85:
        return 'validation'
    if 114 * 24 <= t <= 115 * 24 + 22 and b >= 85:
        return 'test'
    return None


def make_examples(raw: pl.DataFrame) -> tuple[pd.DataFrame, dict]:
    missing = set(RAW_COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f'Missing columns: {sorted(missing)}')
    if raw.select(pl.any_horizontal(pl.col('pod_id').is_null(),
                                    pl.col('server_id').is_null(),
                                    pl.col('day').is_null(),
                                    pl.col('hour').is_null()).any()).item():
        raise ValueError('Missing pod/server/time key')
    if not raw['hour'].is_between(0, 23).all():
        raise ValueError('Invalid hour')
    p = raw.with_columns((pl.col('day') * 24 + pl.col('hour')).alias('t'))
    keys = ['pod_id', 't']
    duplicates = p.group_by(keys).len().filter(pl.col('len') > 1).select(keys)
    duplicate_rows = len(p.join(duplicates, on=keys, how='inner'))
    p = p.join(duplicates, on=keys, how='anti')
    stable = p.group_by('pod_id').agg(
        pl.col('workload_id').n_unique().alias('n'),
        pl.col('workload_id').null_count().alias('nulls'))
    valid_ids = stable.filter((pl.col('n') == 1) & (pl.col('nulls') == 0)).select('pod_id')
    p = p.join(valid_ids, on='pod_id', how='inner')
    valid = ((pl.col('gpu_request') == 1) &
             pl.col('avg_gpu_sm_util').is_finite() &
             pl.col('avg_gpu_sm_util').is_between(0, 100))
    f = p.filter(valid).rename({'avg_gpu_sm_util': 'sm_current'})
    # Explicit shifted keys avoid accidental many-to-many or cross-day joins.
    nxt = f.select('pod_id', (pl.col('t') - 1).alias('t'),
                   pl.col('server_id').alias('next_server'),
                   pl.col('sm_current').alias('target_sm'),
                   pl.col('server_gpu_count').alias('next_gpu_count'))
    pairs = f.join(nxt, on=keys, how='inner', validate='1:1').filter(
        pl.col('server_id') == pl.col('next_server')).drop('next_server')
    for lag in [1, 2]:
        past = f.select('pod_id', (pl.col('t') + lag).alias('t'),
                        pl.col('server_id').alias('past_server'),
                        pl.col('sm_current').alias(f'sm_lag{lag}'),
                        pl.col('server_gpu_count').alias(f'lag{lag}_gpu_count'))
        pairs = pairs.join(past, on=keys, how='inner', validate='1:1').filter(
            pl.col('server_id') == pl.col('past_server')).drop('past_server')
    strict = pl.all_horizontal(*[pl.col(c) == 1 for c in [
        'server_gpu_count', 'next_gpu_count', 'lag1_gpu_count', 'lag2_gpu_count']])
    frame = pairs.with_columns(strict.alias('strict_single_gpu_server')).to_pandas()
    ids = {w: bucket(w) for w in frame.workload_id.unique()}
    frame['bucket'] = frame.workload_id.map(ids)
    frame['split'] = [assign_split(int(t), int(b)) for t, b in zip(frame.t, frame.bucket)]
    frame = frame[frame.split.notna()].copy()
    sm = frame[['sm_current', 'sm_lag1', 'sm_lag2']].to_numpy()
    frame['sm_mean3'] = sm.mean(axis=1)
    frame['sm_std3'] = sm.std(axis=1)
    frame['sm_delta1'] = frame.sm_current - frame.sm_lag1
    frame['sm_delta2'] = frame.sm_current - frame.sm_lag2
    frame['hour_sin'] = np.sin(2 * math.pi * frame.hour / 24)
    frame['hour_cos'] = np.cos(2 * math.pi * frame.hour / 24)
    frame['target_hour_index'] = frame.t + 1
    frame['measurement_available_hour_index'] = frame.t + 1
    frame['target_available_hour_index'] = frame.t + 2
    frame['changed_gt10'] = (frame.target_sm - frame.sm_current).abs() > 10
    frame[NUMERIC] = frame[NUMERIC].replace([np.inf, -np.inf], np.nan)
    frame[CATEGORICAL] = frame[CATEGORICAL].fillna('unknown')
    for a, b in [('train', 'validation'), ('train', 'test'), ('validation', 'test')]:
        for key in ['workload_id', 'pod_id']:
            if set(frame.loc[frame.split == a, key]) & set(frame.loc[frame.split == b, key]):
                raise ValueError(f'{key} overlap between {a} and {b}')
    return frame.sort_values(['split', 't', 'pod_id']).reset_index(drop=True), {
        'source_online_genai_gpu_rows': len(raw),
        'duplicate_pod_hour_keys_excluded': len(duplicates),
        'duplicate_rows_excluded': duplicate_rows,
        'unstable_or_unknown_workload_pods_excluded': len(stable) - len(valid_ids),
        'valid_single_requested_gpu_rows_before_history': len(f),
        'same_server_three_history_and_next_pairs_before_split': len(pairs),
        'split_rule': 'SHA256 workload first8hex modulo100: train<70 days109-112; validation70-84 day113; test85-99 days114-115; target stays inside period',
        'future_conditioning': 'Quality screening uses selected-week ID consistency; outcome is conditional on valid same-server next observation. Missing next values are excluded, never zero-filled.',
    }


def summarize(frame: pd.DataFrame) -> dict:
    events = frame.groupby('workload_id').changed_gt10.sum()
    n = int(events.sum())
    return {'rows': len(frame), 'pods': int(frame.pod_id.nunique()),
            'workloads': int(frame.workload_id.nunique()),
            'changed_gt10': n, 'workloads_with_change': int((events > 0).sum()),
            'largest_workload_change_share': float(events.max() / n) if n else None,
            'next_sm_ge80': int((frame.target_sm >= 80).sum())}


def score(frame: pd.DataFrame, pred: np.ndarray) -> dict:
    errors = np.abs(frame.target_sm.to_numpy() - pred)
    if not np.isfinite(pred).all():
        raise ValueError('Nonfinite prediction')
    grouped = pd.DataFrame({'w': frame.workload_id.to_numpy(), 'error': errors}).groupby('w').error.mean()
    changed = frame.changed_gt10.to_numpy()
    result = {'mae': float(errors.mean()), 'workload_macro_mae': float(grouped.mean()),
              'p95_absolute_error': float(np.quantile(errors, .95)),
              'changed_gt10_mae': float(errors[changed].mean()) if changed.any() else None,
              'unchanged_or_small_change_mae': float(errors[~changed].mean()) if (~changed).any() else None,
              'selection_score': float((errors.mean() + grouped.mean()) / 2)}
    result['by_day'] = {str(day): {
        'rows': int((frame.day == day).sum()),
        'mae': float(errors[frame.day.to_numpy() == day].mean())}
        for day in sorted(frame.day.unique())}
    return result


def make_model(seed: int) -> Pipeline:
    transform = ColumnTransformer([
        ('categorical', OneHotEncoder(handle_unknown='ignore', sparse_output=False), CATEGORICAL),
        ('numeric', 'passthrough', NUMERIC)])
    return Pipeline([('features', transform), ('model', HistGradientBoostingRegressor(
        loss='absolute_error', max_iter=150, learning_rate=.05,
        max_leaf_nodes=15, min_samples_leaf=50, l2_regularization=1,
        early_stopping=False, random_state=seed))])


def bootstrap(frame: pd.DataFrame, pred: np.ndarray, base: np.ndarray, seed: int) -> dict:
    improvement = np.abs(frame.target_sm.to_numpy() - base) - np.abs(frame.target_sm.to_numpy() - pred)
    means = pd.DataFrame({'w': frame.workload_id.to_numpy(), 'd': improvement}).groupby('w').d.mean().to_numpy()
    rng = np.random.default_rng(seed)
    sample = rng.choice(means, size=(2000, len(means)), replace=True).mean(axis=1)
    return {'baseline_minus_model_macro_mae': float(means.mean()),
            'workload_bootstrap_95_ci': np.quantile(sample, [.025, .975]).tolist(),
            'workloads': len(means),
            'interpretation': 'conditional on fixed trained model and audited seven-day slice; positive favors model'}


def run_cohort(frame: pd.DataFrame, output: Path, seed: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    splits = {k: frame[frame.split == k].copy() for k in ['train', 'validation', 'test']}
    if min(len(v) for v in splits.values()) < 100:
        raise ValueError('Too few examples for an exploratory fit')
    train, val, test = [splits[k] for k in ['train', 'validation', 'test']]
    weights = 1 / train.groupby('workload_id').workload_id.transform('size')
    weights /= weights.mean()
    lookup = train.groupby('gpu_spec_public').target_sm.median().to_dict()
    fallback = float(train.target_sm.median())
    models = {}
    for kind in ['direct_hgb', 'residual_hgb']:
        model = make_model(seed)
        y = train.target_sm if kind == 'direct_hgb' else train.target_sm - train.sm_current
        started = time.monotonic()
        model.fit(train[FEATURES], y, model__sample_weight=weights)
        models[kind] = model
        print(json.dumps({'fit': kind, 'rows': len(train), 'seconds': time.monotonic() - started}), flush=True)
    def predict_all(part: pd.DataFrame) -> dict[str, np.ndarray]:
        current = part.sm_current.to_numpy()
        residual = models['residual_hgb'].predict(part[FEATURES])
        predictions = {
            'persistence': current,
            'past_three_mean': part.sm_mean3.to_numpy(),
            'train_gpu_median': part.gpu_spec_public.map(lookup).fillna(fallback).to_numpy(),
            'direct_hgb': np.clip(models['direct_hgb'].predict(part[FEATURES]), 0, 100)}
        for alpha in ALPHAS:
            predictions[f'residual_hgb_alpha_{alpha:g}'] = np.clip(current + alpha * residual, 0, 100)
        return predictions
    validation_predictions = predict_all(val)
    validation = {k: score(val, p) for k, p in validation_predictions.items()}
    # Fixed order resolves ties in favor of persistence. Never select on test.
    best_baseline = min(BASELINES, key=lambda k: validation[k]['selection_score'])
    learned = ['direct_hgb'] + [f'residual_hgb_alpha_{a:g}' for a in ALPHAS if a > 0]
    best_learned = min(learned, key=lambda k: validation[k]['selection_score'])
    selected = min(validation, key=lambda k: validation[k]['selection_score'])
    (output / 'validation_selection.json').write_text(json.dumps({
        'selected': selected, 'best_baseline': best_baseline, 'best_learned': best_learned,
        'criterion': '(row MAE + equal-workload MAE)/2; validation only',
        'validation': validation}, indent=2) + '\n')
    test_predictions = predict_all(test)
    test_scores = {k: score(test, p) for k, p in test_predictions.items()}
    comparison = bootstrap(test, test_predictions[best_learned], test_predictions[best_baseline], seed)
    ml, base = test_scores[best_learned], test_scores[best_baseline]
    daily_ok = all(ml['by_day'][d]['mae'] < base['by_day'][d]['mae'] for d in base['by_day'])
    checks = {'validation_selected_learned_model': selected in learned,
              'test_row_mae_improved': ml['mae'] < base['mae'],
              'test_workload_macro_mae_improved': ml['workload_macro_mae'] < base['workload_macro_mae'],
              'test_changed_mae_not_worse': ml['changed_gt10_mae'] is not None and ml['changed_gt10_mae'] <= base['changed_gt10_mae'],
              'both_test_days_row_mae_improved': daily_ok,
              'workload_bootstrap_lower_bound_positive': comparison['workload_bootstrap_95_ci'][0] > 0}
    bundle = {'scope': SCOPE, 'selected': selected, 'best_learned': best_learned,
              'models': models, 'features': FEATURES, 'gpu_median': lookup,
              'global_median': fallback, 'alphas': ALPHAS,
              'prediction_bounds': [0, 100], 'seed': seed}
    joblib.dump(bundle, output / 'model_bundle.joblib')
    out = test[['pod_id', 'workload_id', 'server_id', 'day', 'hour', 't',
                'target_hour_index', 'sm_current', 'target_sm', 'changed_gt10']].copy()
    for name, prediction in test_predictions.items():
        out['pred_' + name] = prediction
    out.to_parquet(output / 'test_predictions.parquet', index=False)
    result = {'split_counts': {k: summarize(v) for k, v in splits.items()},
              'selected': selected, 'best_baseline': best_baseline, 'best_learned': best_learned,
              'validation': validation, 'test': test_scores, 'bootstrap': comparison,
              'exploratory_gate_checks': checks,
              'exploratory_improvement_gate': 'PASS' if all(checks.values()) else 'NO_GO',
              'operational_promotion': 'NONE; new unused-period confirmation and event data required',
              'unknown_test_gpu_categories': int((~test.gpu_spec_public.isin(lookup)).sum())}
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'cohort': output.name, 'selected': selected, 'gate': result['exploratory_improvement_gate'],
                      'best_learned_test': ml, 'best_baseline_test': base}), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if not args.protocol.is_file():
        raise ValueError('Freeze protocol before fitting')
    if args.output.exists():
        raise ValueError('Output already exists; use a new run directory')
    start = time.monotonic()
    args.output.mkdir(parents=True)
    manifest_path = args.data_root / 'MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    expected = {(d, h) for d in range(109, 116) for h in range(24)}
    pod_files = [e for e in manifest['files'] if e['name'].startswith('asi_opensource_pod_hourly/')]
    if len(pod_files) != 168 or {(e['day'], e['hour']) for e in pod_files} != expected:
        raise ValueError('Unexpected pod partition coverage')
    for entry in pod_files:
        if (args.data_root / entry['name']).stat().st_size != entry['uncompressed_bytes']:
            raise ValueError('Raw partition size differs from audited manifest')
    scan = pl.scan_parquet(str(args.data_root / 'asi_opensource_pod_hourly/day=*/hour=*/part-000.parquet'), hive_partitioning=True)
    raw = scan.filter((pl.col('gpu_request') > 0) &
                      (pl.col('job_type_public') == 'online_inference') &
                      (pl.col('model_type_public') == 'genai')).select(RAW_COLUMNS).collect(engine='streaming')
    print(json.dumps({'source_rows': len(raw), 'read_seconds': time.monotonic() - start}), flush=True)
    examples, audit = make_examples(raw)
    del raw
    examples.to_parquet(args.output / 'examples.parquet', index=False)
    examples[['pod_id', 'workload_id', 't', 'target_hour_index', 'bucket', 'split',
              'strict_single_gpu_server']].to_parquet(args.output / 'split_manifest.parquet', index=False)
    (args.output / 'data_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    results = {'one_gpu_pod': run_cohort(examples, args.output / 'one_gpu_pod', args.seed),
               'one_gpu_pod_and_server': run_cohort(examples[examples.strict_single_gpu_server].copy(),
                                                  args.output / 'one_gpu_pod_and_server', args.seed)}
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    result = {'scope': SCOPE, 'raw_data_root': str(args.data_root.resolve()),
              'manifest_sha256': sha(manifest_path), 'script_sha256': sha(Path(__file__)),
              'protocol_sha256': sha(args.protocol), 'examples_sha256': sha(args.output / 'examples.parquet'),
              'seed': args.seed, 'features': FEATURES, 'audit': audit,
              'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__,
                           'numpy': np.__version__, 'pandas': pd.__version__, 'polars': pl.__version__},
              'elapsed_seconds': time.monotonic() - start,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              'cohorts': results}
    (args.output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'completed': True, 'elapsed_seconds': result['elapsed_seconds'],
                      'peak_rss_mib': result['peak_rss_mib']}), flush=True)


if __name__ == '__main__':
    main()
