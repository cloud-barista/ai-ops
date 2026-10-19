"""Evaluate frozen SM forecasting models on a preselected unused period.

No .fit calls or model selection are performed. The frozen training module's
feature/quality builder is reused with only its date assignment callback
replaced in a private import. Original files, models and hashes stay intact.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np
import pandas as pd
import polars as pl
import pyarrow.parquet as pq
import sklearn


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def future_assignment(t: int, _bucket: int) -> str | None:
    return 'confirmation' if 116 * 24 <= t <= 122 * 24 + 22 else None


def load_study(freeze: dict):
    path = Path(freeze['training_script'])
    if sha(path) != freeze['training_script_sha256']:
        raise ValueError('Frozen training code changed')
    spec = importlib.util.spec_from_file_location('frozen_sm_hour', path)
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    study.assign_split = future_assignment
    return study


def verify_source(root: Path, manifest: dict) -> dict:
    files = manifest['files']
    expected = {(d, h) for d in range(116, 123) for h in range(24)}
    if len(files) != 168 or {(e['day'], e['hour']) for e in files} != expected:
        raise ValueError('Confirmation partition coverage differs')
    def check(entry):
        path = root / entry['name']
        if path.stat().st_size != entry['uncompressed_bytes'] or sha(path) != entry['sha256']:
            raise ValueError(f"Partition integrity mismatch: {entry['name']}")
        metadata = pq.ParquetFile(path).metadata
        if metadata.num_rows != entry['rows'] or metadata.num_columns != entry['columns']:
            raise ValueError('Parquet metadata mismatch')
        return metadata.num_rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(check, files))
    return {'files_checked': len(files), 'rows': sum(rows), 'errors': 0,
            'bytes': sum(e['uncompressed_bytes'] for e in files)}


def fixed_predictions(frame: pd.DataFrame, bundle: dict) -> dict[str, np.ndarray]:
    current = frame.sm_current.to_numpy()
    features = bundle['features']
    return {
        'persistence': current,
        'past_three_mean': frame.sm_mean3.to_numpy(),
        'train_gpu_median': frame.gpu_spec_public.map(bundle['gpu_median']).fillna(bundle['global_median']).to_numpy(),
        'direct_hgb': np.clip(bundle['models']['direct_hgb'].predict(frame[features]), 0, 100),
        'residual_hgb_alpha_1': np.clip(current + bundle['models']['residual_hgb'].predict(frame[features]), 0, 100),
    }


def cluster_interval(frame, predicted, base, seed=42, repetitions=2000):
    delta = np.abs(frame.target_sm.to_numpy() - base) - np.abs(frame.target_sm.to_numpy() - predicted)
    means = pd.DataFrame({'w': frame.workload_id.to_numpy(), 'delta': delta}).groupby('w').delta.mean().to_numpy()
    rng = np.random.default_rng(seed)
    samples = rng.choice(means, size=(repetitions, len(means)), replace=True).mean(axis=1)
    return {'baseline_minus_model_macro_mae': float(means.mean()),
            'workload_bootstrap_95_ci': np.quantile(samples, [.025, .975]).tolist(),
            'workloads': len(means),
            'interpretation': 'Fixed model, this future week, same cluster; positive favors model. Workload resampling does not capture between-cluster uncertainty.'}


def evaluate_view(frame, predictions, study, freeze):
    if len(frame) == 0:
        return {'rows': 0, 'gate': 'INSUFFICIENT_DATA'}
    counts = study.summarize(frame)
    scores = {name: study.score(frame, p) for name, p in predictions.items()}
    selected = freeze['selected_route']
    baseline = freeze['fixed_best_baseline']
    model, base = scores[selected], scores[baseline]
    interval = cluster_interval(frame, predictions[selected], predictions[baseline],
                                freeze['bootstrap_seed'], freeze['bootstrap_repetitions'])
    days = model['by_day']
    better_days = sum(days[d]['mae'] < base['by_day'][d]['mae'] for d in days)
    support = {
        'minimum_rows': len(frame) >= freeze['minimum_primary_rows'],
        'minimum_workloads': counts['workloads'] >= freeze['minimum_primary_workloads'],
        'minimum_change_events': counts['changed_gt10'] >= freeze['minimum_change_events'],
        'minimum_change_workloads': counts['workloads_with_change'] >= freeze['minimum_change_workloads'],
        'all_seven_days_present': set(days) == {str(d) for d in range(116, 123)},
    }
    checks = {
        'row_mae_lower': model['mae'] < base['mae'],
        'macro_workload_mae_lower': model['workload_macro_mae'] < base['workload_macro_mae'],
        'change_mae_not_worse': model['changed_gt10_mae'] is not None and model['changed_gt10_mae'] <= base['changed_gt10_mae'],
        'p95_error_not_worse': model['p95_absolute_error'] <= base['p95_absolute_error'],
        'daily_row_mae_improved_at_least_5_of_7': better_days >= 5,
        'workload_cluster_ci_lower_positive': interval['workload_bootstrap_95_ci'][0] > 0,
    }
    gate = 'INSUFFICIENT_DATA' if not all(support.values()) else ('PASS' if all(checks.values()) else 'NO_GO')
    return {'counts': counts, 'scores': scores, 'bootstrap': interval,
            'improved_days': better_days, 'support_checks': support,
            'gate_checks': checks, 'gate': gate,
            'direct_hgb_comparison': {'residual_row_mae_lower': model['mae'] < scores['direct_hgb']['mae'],
                                      'residual_macro_mae_lower': model['workload_macro_mae'] < scores['direct_hgb']['workload_macro_mae']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--history-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite confirmation')
    freeze = json.loads(args.freeze.read_text())
    study = load_study(freeze)
    models_root = Path(freeze['model_source'])
    before = {c: sha(models_root / c / 'model_bundle.joblib') for c in freeze['models']}
    if before != freeze['models']:
        raise ValueError('Frozen models changed before evaluation')
    start = time.monotonic()
    args.output.mkdir(parents=True)
    manifest_path = args.data_root / 'MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    verified = verify_source(args.data_root, manifest)
    print(json.dumps({'source_verified': verified}), flush=True)
    files = [str(args.data_root / e['name']) for e in manifest['files']]
    history = [args.history_root / f'asi_opensource_pod_hourly/day=115/hour={h:02d}/part-000.parquet' for h in [22, 23]]
    hist_manifest = json.loads((args.history_root / 'MANIFEST.json').read_text())
    hist_hash = {e['name']: e['sha256'] for e in hist_manifest['files']}
    for p in history:
        if sha(p) != hist_hash[str(p.relative_to(args.history_root))]:
            raise ValueError('History source hash changed')
    raw = pl.scan_parquet(files + [str(p) for p in history], hive_partitioning=True).filter(
        (pl.col('gpu_request') > 0) & (pl.col('job_type_public') == 'online_inference') &
        (pl.col('model_type_public') == 'genai')).select(study.RAW_COLUMNS).collect(engine='streaming')
    frame, audit = study.make_examples(raw)
    del raw
    audit['split_rule'] = 'Future days116-122; target wholly in future period; evaluation subsets use fixed workload hash and original split membership'
    frame.to_parquet(args.output / 'examples.parquet', index=False)
    old = pd.read_parquet(models_root / 'examples.parquet', columns=['pod_id', 'workload_id', 'split', 'strict_single_gpu_server'])
    all_old_ids = set(old.workload_id)
    result = {}
    for cohort in ['one_gpu_pod', 'one_gpu_pod_and_server']:
        source = frame.copy()
        old_cohort = old.copy()
        if cohort == 'one_gpu_pod_and_server':
            source = source[source.strict_single_gpu_server].copy()
            old_cohort = old_cohort[old_cohort.strict_single_gpu_server].copy()
        train_ids = set(old_cohort.loc[old_cohort.split == 'train', 'workload_id'])
        train_pods = set(old_cohort.loc[old_cohort.split == 'train', 'pod_id'])
        bundle = joblib.load(models_root / cohort / 'model_bundle.joblib')
        if bundle['selected'] != freeze['selected_route']:
            raise ValueError('Selected route differs from freeze')
        pred = fixed_predictions(source, bundle)
        primary_mask = source.bucket >= 85
        if set(source.loc[primary_mask, 'workload_id']) & train_ids:
            raise ValueError('Primary confirmation workload overlaps training')
        if set(source.loc[primary_mask, 'pod_id']) & train_pods:
            raise ValueError('Primary confirmation pod overlaps training')
        masks = {
            'untrained_workload_bucket_future': primary_mask,
            'trained_workloads_future': source.workload_id.isin(train_ids),
            'previously_unseen_workloads': ~source.workload_id.isin(all_old_ids),
            'all_eligible_future': np.ones(len(source), dtype=bool),
        }
        views = {}
        for name, mask in masks.items():
            array = np.asarray(mask, dtype=bool)
            subset = source.loc[array].copy()
            views[name] = evaluate_view(subset, {k: v[array] for k, v in pred.items()}, study, freeze)
        out = source[['pod_id', 'workload_id', 'server_id', 'day', 'hour', 't',
                      'bucket', 'target_hour_index', 'sm_current', 'target_sm', 'changed_gt10']].copy()
        out['primary_confirmation'] = primary_mask.to_numpy()
        out['trained_workload'] = source.workload_id.isin(train_ids).to_numpy()
        out['previously_unseen_workload'] = (~source.workload_id.isin(all_old_ids)).to_numpy()
        for name, p in pred.items():
            out['pred_' + name] = p
        folder = args.output / cohort
        folder.mkdir()
        out.to_parquet(folder / 'predictions.parquet', index=False)
        primary = views['untrained_workload_bucket_future']
        unknown = ~source.gpu_spec_public.isin(bundle['gpu_median'])
        unknown_primary = (unknown & primary_mask).to_numpy()
        known_primary = ((~unknown) & primary_mask).to_numpy()
        secondary = {n: evaluate_view(source.loc[mask], {k: v[mask] for k, v in pred.items()}, study, freeze)
                     for n, mask in [('known_gpu_types_primary', known_primary), ('unknown_gpu_types_primary', unknown_primary)]}
        value = {'views': views, 'gpu_type_diagnostics': secondary,
                 'primary_training_workload_overlap': 0, 'primary_training_pod_overlap': 0,
                 'model_sha256': before[cohort]}
        (folder / 'metrics.json').write_text(json.dumps(value, indent=2) + '\n')
        result[cohort] = value
        print(json.dumps({'cohort': cohort, 'primary_gate': primary['gate'],
                          'primary_counts': primary.get('counts'),
                          'primary_scores': primary.get('scores')}), flush=True)
    after = {c: sha(models_root / c / 'model_bundle.joblib') for c in freeze['models']}
    if before != after:
        raise ValueError('Frozen model changed during confirmation')
    final = {'freeze': freeze, 'freeze_sha256': sha(args.freeze),
             'evaluation_script_sha256': sha(Path(__file__)),
             'data_root': str(args.data_root), 'manifest_sha256': sha(manifest_path),
             'source_verification': verified, 'data_audit': audit,
             'models_unchanged': before == after,
             'primary_cohort': 'one_gpu_pod_and_server',
             'primary_view': 'untrained_workload_bucket_future',
             'overall_confirmation_gate': result['one_gpu_pod_and_server']['views']['untrained_workload_bucket_future']['gate'],
             'elapsed_seconds': time.monotonic() - start,
             'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__,
                          'pandas': pd.__version__, 'polars': pl.__version__, 'numpy': np.__version__},
             'scope': 'Unused seven future dates in same cluster, conditional on same-server next observation; no re-fit, no outcome-based selection',
             'cohorts': result}
    (args.output / 'metrics.json').write_text(json.dumps(final, indent=2) + '\n')
    print(json.dumps({'complete': True, 'gate': final['overall_confirmation_gate'],
                      'elapsed_seconds': final['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    main()
