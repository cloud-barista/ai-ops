"""Expanded Alibaba SM training with a four-day, future-period confirmation.

Fit reads days109-125 only. Evaluation loads frozen selected models before
reading days126-129. Train/validation/test quality screens run separately.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import resource
import time

import joblib
import numpy as np
import pandas as pd
import polars as pl
import pyarrow.parquet as pq
import sklearn

ROOT = Path('/home/researcher/ai-mcmp/research')
BASE_SCRIPT = ROOT / 'scripts/train_alibaba_gpu_sm_hour.py'
BASE_SHA = '3f3d1e9f15296bdfb11e4f73c8bcad34edd93d5a4e659eff256eef489a81dabd'
COHORTS = ['one_gpu_pod', 'one_gpu_pod_and_server']
SCOPE = ('Observed online GenAI pods requesting exactly one GPU: source SM '
         'next-hour measurement conditional on same-server observability. '
         'Train109-124, select125, confirm unused126-129; no scale-action/SLO labels.')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + '\n')


def assign_split(t, b):
    if 109 * 24 <= t <= 124 * 24 + 22 and 0 <= b < 70:
        return 'train'
    if 125 * 24 <= t <= 125 * 24 + 22 and 70 <= b < 85:
        return 'validation'
    if 126 * 24 <= t <= 129 * 24 + 22 and 85 <= b < 100:
        return 'test'
    return None


def load_base():
    if sha(BASE_SCRIPT) != BASE_SHA:
        raise ValueError('Pinned base training source changed')
    spec = importlib.util.spec_from_file_location('sm_v3_base', BASE_SCRIPT)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.assign_split, base.SCOPE = assign_split, SCOPE
    return base


def predict_all(frame, bundle):
    current = frame.sm_current.to_numpy()
    residual = bundle['models']['residual_hgb'].predict(frame[bundle['features']])
    preds = {'persistence': current, 'past_three_mean': frame.sm_mean3.to_numpy(),
             'train_gpu_median': frame.gpu_spec_public.map(bundle['gpu_median']).fillna(bundle['global_median']).to_numpy(),
             'direct_hgb': np.clip(bundle['models']['direct_hgb'].predict(frame[bundle['features']]), 0, 100)}
    for alpha in [0., .25, .5, 1.]:
        preds[f'residual_hgb_alpha_{alpha:g}'] = np.clip(current + alpha * residual, 0, 100)
    return preds


def predict_selected(frame, bundle):
    return predict_all(frame, bundle)[bundle['selected']]


def source_entries(new_root, keys):
    roots = [ROOT / 'data/external/alibaba_gpu_2026_days109_115',
             ROOT / 'data/external/alibaba_gpu_2026_days116_122', new_root]
    lookup, lineage = {}, []
    for root in roots:
        manifest = root / 'MANIFEST.json'
        if manifest.exists():
            entries = json.loads(manifest.read_text())['files']
            lineage.append({'root': str(root), 'manifest_sha256': sha(manifest)})
        else:
            # Completed day journals allow fitting while the held-out download continues.
            journals = [root / '.download_journal' / f'day{d}.json' for d, h in keys if d >= 123]
            journals = sorted(set(journals))
            entries = []
            for path in journals:
                entries.extend(json.loads(path.read_text())['entries'])
                lineage.append({'root': str(root), 'journal': str(path.relative_to(root)), 'sha256': sha(path)})
        for e in entries:
            if e['name'].startswith('asi_opensource_pod_hourly/'):
                key = (e['day'], e['hour'])
                if key in keys:
                    if key in lookup:
                        raise ValueError('Duplicate source partition')
                    path = root / e['name']
                    if path.stat().st_size != e['uncompressed_bytes']:
                        raise ValueError('Partition size changed')
                    # New original partitions get full hash verification before model use.
                    if e['day'] >= 123:
                        if sha(path) != e['sha256']:
                            raise ValueError('New partition hash mismatch')
                        metadata = pq.ParquetFile(path).metadata
                        if metadata.num_rows != e['rows'] or metadata.num_columns != e['columns']:
                            raise ValueError('Parquet metadata mismatch')
                    lookup[key] = str(path)
    if set(lookup) != set(keys):
        raise ValueError(f'Missing partition coverage: {set(keys) - set(lookup)}')
    return [lookup[k] for k in sorted(keys)], lineage


def build_role(role, new_root, base):
    periods = {'train': (109, 124), 'validation': (125, 125), 'test': (126, 129)}
    first, last = periods[role]
    keys = {(d, h) for d in range(first, last + 1) for h in range(24)}
    if role != 'train':
        keys.update({(first - 1, 22), (first - 1, 23)})
    paths, lineage = source_entries(new_root, keys)
    raw = pl.scan_parquet(paths, hive_partitioning=True).filter(
        (pl.col('gpu_request') > 0) & (pl.col('job_type_public') == 'online_inference') &
        (pl.col('model_type_public') == 'genai')).select(base.RAW_COLUMNS).collect(engine='streaming')
    frame, audit = base.make_examples(raw)
    frame = frame[frame.split == role].copy().reset_index(drop=True)
    audit['split_rule'] = f'Isolated {role} period {first}-{last}; fixed workload bucket; targets contained in period'
    audit['future_conditioning'] = ('ID consistency screening applies only to this role and its two history hours. '
        'Training membership never depends on future validation/test data. Conditional on a valid next-hour same-server measurement.')
    audit['partitions_read'] = len(paths)
    audit['lineage'] = lineage
    print(json.dumps({'built': role, 'rows': len(frame), 'audit': {k: v for k, v in audit.items() if k != 'lineage'}}), flush=True)
    return frame, audit


def assert_disjoint(parts):
    for a in parts:
        for b in parts:
            if a >= b:
                continue
            for key in ['workload_id', 'pod_id']:
                if set(parts[a][key]) & set(parts[b][key]):
                    raise ValueError(f'{key} overlaps {a}/{b}')


def train_cohort(train, val, output, base):
    if min(len(train), len(val)) < 100:
        raise ValueError('Insufficient training/validation rows')
    output.mkdir()
    weights = 1 / train.groupby('workload_id').workload_id.transform('size')
    weights /= weights.mean()
    models = {}
    for kind in ['direct_hgb', 'residual_hgb']:
        model = base.make_model(42)
        y = train.target_sm if kind == 'direct_hgb' else train.target_sm - train.sm_current
        start = time.monotonic()
        model.fit(train[base.FEATURES], y, model__sample_weight=weights)
        models[kind] = model
        print(json.dumps({'cohort': output.name, 'fit': kind, 'rows': len(train),
                          'seconds': time.monotonic() - start}), flush=True)
    bundle = {'scope': SCOPE, 'models': models, 'features': base.FEATURES,
              'gpu_median': train.groupby('gpu_spec_public').target_sm.median().to_dict(),
              'global_median': float(train.target_sm.median()), 'alphas': base.ALPHAS,
              'prediction_bounds': [0, 100], 'seed': 42}
    preds = predict_all(val, bundle)
    scores = {name: base.score(val, p) for name, p in preds.items()}
    best_baseline = min(base.BASELINES, key=lambda k: scores[k]['selection_score'])
    learned = ['direct_hgb'] + [f'residual_hgb_alpha_{a:g}' for a in base.ALPHAS if a > 0]
    best_learned = min(learned, key=lambda k: scores[k]['selection_score'])
    selected = min(scores, key=lambda k: scores[k]['selection_score'])
    bundle.update({'selected': selected, 'best_learned': best_learned, 'best_baseline': best_baseline})
    joblib.dump(bundle, output / 'model_bundle.joblib')
    saved = val[['pod_id', 'workload_id', 't', 'target_sm']].copy()
    for name, pred in preds.items():
        saved['pred_' + name] = pred
    saved.to_parquet(output / 'validation_predictions.parquet', index=False)
    result = {'selected': selected, 'best_learned': best_learned, 'best_baseline': best_baseline,
              'selection_rule': 'validation only: (row MAE + workload macro MAE)/2',
              'validation_scores': scores, 'train_counts': base.summarize(train), 'validation_counts': base.summarize(val),
              'new123_124_training_rows': int((train.day >= 123).sum()),
              'rows_by_day': {str(d): len(g) for d, g in train.groupby('day')},
              'model_sha256': sha(output / 'model_bundle.joblib')}
    write_json(output / 'fit_metrics.json', result)
    return result


def fit(args, protocol, base):
    if args.output.exists():
        raise ValueError('Refusing to overwrite fit output')
    train, train_audit = build_role('train', args.new_root, base)
    val, val_audit = build_role('validation', args.new_root, base)
    # Validation pod-ID conflicts must not change the fitted training set.
    overlap = val.pod_id.isin(train.pod_id)
    val_audit['prior_train_pod_rows_excluded'] = int(overlap.sum())
    val = val[~overlap].copy().reset_index(drop=True)
    assert_disjoint({'train': train, 'validation': val})
    args.output.mkdir(parents=True)
    train.to_parquet(args.output / 'train_examples.parquet', index=False)
    val.to_parquet(args.output / 'validation_examples.parquet', index=False)
    results = {}
    for cohort in COHORTS:
        a, b = train, val
        if cohort.endswith('and_server'):
            a, b = a[a.strict_single_gpu_server], b[b.strict_single_gpu_server]
        results[cohort] = train_cohort(a, b, args.output / cohort, base)
    write_json(args.output / 'PROTOCOL.json', protocol)
    write_json(args.output / 'fit_metrics.json', {'cohorts': results, 'audits': {'train': train_audit, 'validation': val_audit}})
    freeze = {'selected_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'test_data_read': False, 'training_script_sha256': sha(Path(__file__)),
              'protocol_sha256': sha(args.protocol),
              'models': {c: {'sha256': r['model_sha256'], 'selected': r['selected'],
                             'best_baseline': r['best_baseline']} for c, r in results.items()}}
    write_json(args.output / 'SELECTION_FREEZE.json', freeze)
    print(json.dumps({'fit_complete': True, 'test_data_read': False, 'selected_models': freeze['models']}), flush=True)


def support_checks(frame, protocol, base):
    counts = base.summarize(frame)
    limits = protocol['support']
    return {'minimum_rows': len(frame) >= limits['rows'],
            'minimum_workloads': counts['workloads'] >= limits['workloads'],
            'minimum_change_events': counts['changed_gt10'] >= limits['change_events'],
            'minimum_change_workloads': counts['workloads_with_change'] >= limits['change_workloads'],
            'all_four_days': set(frame.day) == {126, 127, 128, 129}}


def paired_result(frame, pred, baseline, protocol, base):
    model_score, base_score = base.score(frame, pred), base.score(frame, baseline)
    interval = base.bootstrap(frame, pred, baseline, 42)
    interval['interpretation'] = 'Fixed models, same cluster, unused future4-day slice; conditional workload bootstrap only'
    improved_days = sum(model_score['by_day'][d]['mae'] < base_score['by_day'][d]['mae'] for d in model_score['by_day'])
    checks = {'row_mae_lower': model_score['mae'] < base_score['mae'],
              'macro_mae_lower': model_score['workload_macro_mae'] < base_score['workload_macro_mae'],
              'changed_mae_not_worse': model_score['changed_gt10_mae'] is not None and model_score['changed_gt10_mae'] <= base_score['changed_gt10_mae'],
              'p95_not_worse': model_score['p95_absolute_error'] <= base_score['p95_absolute_error'],
              'at_least_three_improved_days': improved_days >= 3,
              'workload_ci_lower_positive': interval['workload_bootstrap_95_ci'][0] > 0}
    support = support_checks(frame, protocol, base)
    status = 'INSUFFICIENT_DATA' if not all(support.values()) else ('PASS' if all(checks.values()) else 'NO_GO')
    return {'model_score': model_score, 'comparator_score': base_score, 'bootstrap': interval,
            'improved_days': improved_days, 'support_checks': support, 'checks': checks, 'gate': status}


def evaluate(args, protocol, base):
    freeze = json.loads((args.output / 'SELECTION_FREEZE.json').read_text())
    if freeze['protocol_sha256'] != sha(args.protocol) or freeze['training_script_sha256'] != sha(Path(__file__)):
        raise ValueError('Code/protocol changed after selection')
    # Freeze validated model files before reading held-out measurement columns.
    bundles, old_bundles = {}, {}
    for c in COHORTS:
        path = args.output / c / 'model_bundle.joblib'
        if sha(path) != freeze['models'][c]['sha256']:
            raise ValueError('v3 model changed after selection')
        bundles[c] = joblib.load(path)
        if bundles[c]['selected'] != freeze['models'][c]['selected']:
            raise ValueError('Selected route changed')
        old_bundles[c] = {}
        for version in ['1', '2']:
            old = protocol['old_models'][version][c]
            if sha(old['path']) != old['sha256']:
                raise ValueError('Preserved old model changed')
            old_bundles[c][version] = joblib.load(old['path'])
    if (args.output / 'metrics.json').exists():
        raise ValueError('Confirmation already exists; refusing another evaluation')
    test, audit = build_role('test', args.new_root, base)
    past_train = pd.read_parquet(args.output / 'train_examples.parquet', columns=['pod_id', 'workload_id'])
    past_val = pd.read_parquet(args.output / 'validation_examples.parquet', columns=['pod_id', 'workload_id'])
    overlap = test.pod_id.isin(set(past_train.pod_id) | set(past_val.pod_id))
    audit['prior_train_validation_pod_rows_excluded'] = int(overlap.sum())
    test = test[~overlap].copy().reset_index(drop=True)
    assert_disjoint({'train': past_train, 'validation': past_val, 'test': test})
    test.to_parquet(args.output / 'test_examples.parquet', index=False)
    results = {}
    for cohort in COHORTS:
        frame = test[test.strict_single_gpu_server].copy() if cohort.endswith('and_server') else test
        if len(frame) == 0:
            raise ValueError('No test examples')
        bundle = bundles[cohort]
        predictions = predict_all(frame, bundle)
        for version, old_bundle in old_bundles[cohort].items():
            predictions[f'v{version}_selected'] = predict_selected(frame, old_bundle)
        saved = frame[['pod_id', 'workload_id', 'server_id', 'day', 'hour', 't', 'target_hour_index',
                       'sm_current', 'target_sm', 'changed_gt10']].copy()
        for name, pred in predictions.items():
            saved['pred_' + name] = pred
        saved.to_parquet(args.output / cohort / 'test_predictions.parquet', index=False)
        scores = {name: base.score(frame, p) for name, p in predictions.items()}
        comparisons = {name: paired_result(frame, predictions[bundle['selected']], predictions[name], protocol, base)
                       for name in [bundle['best_baseline'], 'v1_selected', 'v2_selected']}
        replay = predict_all(frame, joblib.load(args.output / cohort / 'model_bundle.joblib'))
        disk_saved = pd.read_parquet(args.output / cohort / 'test_predictions.parquet')
        if not disk_saved[['pod_id', 'workload_id', 't']].equals(frame[['pod_id', 'workload_id', 't']].reset_index(drop=True)):
            raise ValueError('Stored prediction row order differs')
        differences = {name: float(np.max(np.abs(p - disk_saved['pred_' + name].to_numpy()))) for name, p in replay.items()}
        if any(differences.values()):
            raise ValueError('Saved-model replay differs')
        # Independent reductions of the headline metrics.
        selected = predictions[bundle['selected']]
        err = np.abs(frame.target_sm.to_numpy() - selected)
        macro = pd.DataFrame({'w': frame.workload_id, 'e': err}).groupby('w').e.mean().mean()
        if not np.isclose(err.mean(), scores[bundle['selected']]['mae']) or not np.isclose(macro, scores[bundle['selected']]['workload_macro_mae']):
            raise ValueError('Independent headline metrics differ')
        result = {'selected': bundle['selected'], 'best_baseline': bundle['best_baseline'],
                  'counts': base.summarize(frame), 'scores': scores, 'comparisons': comparisons,
                  'unknown_test_gpu_category_rows': int((~frame.gpu_spec_public.isin(bundle['gpu_median'])).sum()),
                  'model_sha256': sha(args.output / cohort / 'model_bundle.joblib'),
                  'loaded_model_replay_max_abs_difference': differences}
        write_json(args.output / cohort / 'metrics.json', result)
        results[cohort] = result
        print(json.dumps({'cohort': cohort, 'selected': result['selected'], 'test_rows': len(frame),
                          'versus_v2': comparisons['v2_selected'], 'versus_baseline': comparisons[bundle['best_baseline']]}), flush=True)
    for version, files in protocol['old_models'].items():
        for c, old in files.items():
            if sha(old['path']) != old['sha256']:
                raise ValueError('Old model changed during confirmation')
    for c, frozen in freeze['models'].items():
        if sha(args.output / c / 'model_bundle.joblib') != frozen['sha256']:
            raise ValueError('v3 model changed during confirmation')
    write_json(args.output / 'metrics.json', {
        'scope': SCOPE, 'protocol': protocol, 'selection_freeze': freeze,
        'fit': json.loads((args.output / 'fit_metrics.json').read_text()),
        'test_audit': audit, 'cohorts': results, 'model_hashes_unchanged': True,
        'training_test_workload_and_pod_overlap': 0,
        'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__,
                     'numpy': np.__version__, 'pandas': pd.__version__, 'polars': pl.__version__},
        'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        'operational_promotion': 'NONE: GPU measurement forecast only, no real scale-action outcomes'})
    print(json.dumps({'confirmation_complete': True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['fit', 'evaluate', 'all'], required=True)
    parser.add_argument('--new-root', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    if protocol['base_script_sha256'] != BASE_SHA or protocol['train_days'] != [109, 124] or protocol['validation_days'] != [125, 125] or protocol['test_days'] != [126, 129]:
        raise ValueError('Protocol does not match fixed experiment')
    base = load_base()
    if args.phase in ['fit', 'all']:
        fit(args, protocol, base)
    if args.phase in ['evaluate', 'all']:
        evaluate(args, protocol, base)


if __name__ == '__main__':
    main()
