"""Retrain with retained Alibaba GPU days109-122; previously seen-period study.

Reuse the hash-pinned v1 feature builder and HGB training procedure. The v1
models are read for a paired comparison only and are never fitted or replaced.
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
import sklearn

ROOT = Path('/home/researcher/ai-mcmp/research')
BASE_SCRIPT = ROOT / 'scripts/train_alibaba_gpu_sm_hour.py'
BASE_SHA = '3f3d1e9f15296bdfb11e4f73c8bcad34edd93d5a4e659eff256eef489a81dabd'
V1_SHAS = {
    'one_gpu_pod': '2245eee0c2db77488344ff269aad0fcafca55b041112cc1c89a3a0c738f19e41',
    'one_gpu_pod_and_server': '15472dcd11a5578a61bd3ea9fbdf1dfa91d4ce50272d043a7cda9cf179d70389',
}
SPLIT_RULE = ('workload SHA256 bucket: train<70 days109-120; '
              'validation70-84 day121; test85-99 day122; target within split period')
SCOPE = ('Next-hour source GPU SM measurement, conditional on valid same-server '
         'observability; combined previously inspected days109-122. Workload '
         'and time-disjoint exploratory evaluation, not independent confirmation.')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assign_split(t, b):
    if 109 * 24 <= t <= 120 * 24 + 22 and b < 70:
        return 'train'
    if 121 * 24 <= t <= 121 * 24 + 22 and 70 <= b < 85:
        return 'validation'
    if 122 * 24 <= t <= 122 * 24 + 22 and 85 <= b < 100:
        return 'test'
    return None


def load_base():
    if sha(BASE_SCRIPT) != BASE_SHA:
        raise ValueError('Pinned v1 training source changed')
    spec = importlib.util.spec_from_file_location('sm_hour_v2_base', BASE_SCRIPT)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.assign_split = assign_split
    base.SCOPE = SCOPE
    return base


def predict_bundle(frame, bundle, name=None):
    name = bundle['selected'] if name is None else name
    if name == 'persistence':
        return frame.sm_current.to_numpy()
    if name == 'past_three_mean':
        return frame.sm_mean3.to_numpy()
    if name == 'train_gpu_median':
        return frame.gpu_spec_public.map(bundle['gpu_median']).fillna(bundle['global_median']).to_numpy()
    if name == 'direct_hgb':
        pred = bundle['models']['direct_hgb'].predict(frame[bundle['features']])
    elif name.startswith('residual_hgb_alpha_'):
        alpha = float(name.removeprefix('residual_hgb_alpha_'))
        pred = frame.sm_current.to_numpy() + alpha * bundle['models']['residual_hgb'].predict(frame[bundle['features']])
    else:
        raise ValueError(f'Unknown prediction path: {name}')
    return np.clip(pred, 0, 100)


def split_counts_by_day(frame):
    return {split: {str(day): int(len(part)) for day, part in group.groupby('day')}
            for split, group in frame.groupby('split')}


def compare_and_replay(frame, name, output, base):
    test = frame[frame.split == 'test'].copy()
    v1_path = ROOT / f'artifacts/alibaba_gpu_sm_hour_v1/{name}/model_bundle.joblib'
    before = sha(v1_path)
    if before != V1_SHAS[name]:
        raise ValueError('Preserved v1 model hash changed')
    v1 = joblib.load(v1_path)
    v2 = joblib.load(output / name / 'model_bundle.joblib')
    predictions = {'persistence': test.sm_current.to_numpy(),
                   'v1_selected': predict_bundle(test, v1),
                   'v2_selected': predict_bundle(test, v2)}
    # Replay *all* saved v2 paths after loading the actual model file.
    saved = pd.read_parquet(output / name / 'test_predictions.parquet')
    if not saved[['pod_id', 'workload_id', 't']].equals(test[['pod_id', 'workload_id', 't']].reset_index(drop=True)):
        raise ValueError('Saved prediction row order changed')
    replay = {}
    for col in saved.columns:
        if col.startswith('pred_'):
            path = col[len('pred_'):]
            pred = predict_bundle(test, v2, path)
            difference = float(np.max(np.abs(saved[col].to_numpy() - pred)))
            if difference != 0:
                raise ValueError(f'Saved prediction replay mismatch: {path}')
            replay[path] = difference
    scores = {k: base.score(test, p) for k, p in predictions.items()}
    # Independent reduction of headline metrics, not a second fit.
    for key, pred in predictions.items():
        err = np.abs(test.target_sm.to_numpy() - pred)
        macro = pd.Series(err).groupby(test.workload_id.to_numpy()).mean().mean()
        if not np.isclose(err.mean(), scores[key]['mae']) or not np.isclose(macro, scores[key]['workload_macro_mae']):
            raise ValueError('Independent metric reduction failed')
    old_examples = pd.read_parquet(ROOT / 'artifacts/alibaba_gpu_sm_hour_v1/examples.parquet', columns=['workload_id'])
    novel = ~test.workload_id.isin(old_examples.workload_id)
    views = {'all_test': np.ones(len(test), dtype=bool),
             'absent_from_v1_processed_examples': novel.to_numpy()}
    view_results = {}
    for key, mask in views.items():
        if mask.any():
            part = test.loc[mask]
            view_results[key] = {'support': base.summarize(part),
                                 'scores': {k: base.score(part, p[mask]) for k, p in predictions.items()}}
    paired = base.bootstrap(test, predictions['v2_selected'], predictions['v1_selected'], 42)
    paired['interpretation'] = 'v1 minus v2 equal-workload MAE on seen-period day122; conditional fixed-model interval only'
    compare = test[['pod_id', 'workload_id', 't', 'target_sm']].copy()
    for key, pred in predictions.items():
        compare['pred_' + key] = pred
    compare.to_parquet(output / name / 'v1_v2_test_predictions.parquet', index=False)
    if sha(v1_path) != before:
        raise ValueError('v1 model changed during comparison')
    return {'v1_selected': v1['selected'], 'v2_selected': v2['selected'],
            'scores': scores, 'views': view_results, 'v1_minus_v2_bootstrap': paired,
            'v1_model_sha256_before_and_after': before,
            'v2_all_paths_replay_max_absolute_difference': replay,
            'comparison_status': 'EXPLORATORY_SEEN_PERIOD; no test-driven retuning'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    if protocol['split_rule'] != SPLIT_RULE or protocol['base_script_sha256'] != BASE_SHA:
        raise ValueError('Protocol/source mismatch')
    if args.output.exists():
        raise ValueError('Output exists; refusing to overwrite')
    start = time.monotonic()
    base = load_base()
    sources, scans = [], []
    for first, last in [(109, 115), (116, 122)]:
        root = ROOT / f'data/external/alibaba_gpu_2026_days{first}_{last}'
        manifest_path = root / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        entries = [e for e in manifest['files'] if e['name'].startswith('asi_opensource_pod_hourly/')]
        expected = {(d, h) for d in range(first, last + 1) for h in range(24)}
        if len(entries) != 168 or {(e['day'], e['hour']) for e in entries} != expected:
            raise ValueError('Incomplete or unexpected source partition coverage')
        for e in entries:
            if (root / e['name']).stat().st_size != e['uncompressed_bytes']:
                raise ValueError('Raw size differs from previously hash-audited manifest')
        sources.append({'root': str(root), 'manifest_sha256': sha(manifest_path),
                        'pod_partitions': len(entries), 'pod_rows': sum(e['rows'] for e in entries),
                        'integrity': 'Full hashes verified in prior audits; this run checks manifest coverage and file sizes.'})
        scans.append(pl.scan_parquet(str(root / 'asi_opensource_pod_hourly/day=*/hour=*/part-000.parquet'), hive_partitioning=True))
    raw = pl.concat(scans).filter((pl.col('gpu_request') > 0) &
          (pl.col('job_type_public') == 'online_inference') &
          (pl.col('model_type_public') == 'genai')).select(base.RAW_COLUMNS).collect(engine='streaming')
    print(json.dumps({'source_filtered_rows': len(raw), 'read_seconds': time.monotonic() - start}), flush=True)
    examples, audit = base.make_examples(raw)
    del raw
    audit['split_rule'] = SPLIT_RULE
    audit['future_conditioning'] = ('ID consistency checked over the full combined14-day source. '
        'Targets require valid next-hour same-server observation; missing targets excluded, never zero-filled. '
        'Previously inspected periods; conditional selection prevents a claim of blind evaluation.')
    args.output.mkdir(parents=True)
    examples.to_parquet(args.output / 'examples.parquet', index=False)
    examples[['pod_id', 'workload_id', 'day', 't', 'target_hour_index', 'bucket', 'split',
              'strict_single_gpu_server']].to_parquet(args.output / 'split_manifest.parquet', index=False)
    (args.output / 'data_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    (args.output / 'PROTOCOL.json').write_text(json.dumps(protocol, indent=2) + '\n')
    cohorts = {}
    for name, frame in [('one_gpu_pod', examples),
                        ('one_gpu_pod_and_server', examples[examples.strict_single_gpu_server].copy())]:
        results = base.run_cohort(frame, args.output / name, 42)
        checks = results['exploratory_gate_checks']
        checks['test_day_row_mae_improved'] = checks.pop('both_test_days_row_mae_improved')
        results['bootstrap']['interpretation'] = 'Conditional fixed-model interval on previously inspected day122; positive favors best learned model'
        results['rows_by_day_and_split'] = split_counts_by_day(frame)
        train = frame[frame.split == 'train']
        results['new_period_training_rows'] = int((train.day >= 116).sum())
        results['new_period_training_workloads'] = int(train.loc[train.day >= 116, 'workload_id'].nunique())
        results['paired_v1_v2'] = compare_and_replay(frame, name, args.output, base)
        (args.output / name / 'metrics.json').write_text(json.dumps(results, indent=2) + '\n')
        cohorts[name] = results
    result = {'scope': SCOPE, 'split_rule': SPLIT_RULE, 'sources': sources,
              'base_script_sha256': BASE_SHA, 'wrapper_sha256': sha(Path(__file__)),
              'protocol_sha256': sha(args.protocol), 'features': base.FEATURES,
              'seed': 42, 'audit': audit, 'cohorts': cohorts,
              'operational_promotion': 'NONE; new unused-period confirmation required',
              'versions': {'python': platform.python_version(), 'sklearn': sklearn.__version__,
                           'numpy': np.__version__, 'pandas': pd.__version__, 'polars': pl.__version__},
              'elapsed_seconds': time.monotonic() - start,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    (args.output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'completed': True, 'seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    main()
