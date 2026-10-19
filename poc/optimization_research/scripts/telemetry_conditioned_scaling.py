"""Explore whether current-replica telemetry helps predict a second replica.

The held-out application's GPU1/CPU2 outcome and node summary are available
before predicting GPU2/CPU2. All GPU2 labels and node summaries are used only
for scoring. Training excludes the entire held-out model ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from conditional_response import (ANCHOR, CAMPAIGNS, CONDITIONAL_FEATURES,
                                  fit_model, predict, prepare)


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_KEYS = ('gpu_utilization_percent_mean', 'gpu_utilization_percent_max',
                'gpu_memory_used_mib_max', 'cpu_percent_mean',
                'running_sample_count')
TELEMETRY_FEATURES = tuple('anchor_log1p_' + key for key in SUMMARY_KEYS)
VARIANTS = {
    'telemetry': TELEMETRY_FEATURES,
    'gpu_telemetry': TELEMETRY_FEATURES[:3],
    'no_count': TELEMETRY_FEATURES[:4],
    'count_only': TELEMETRY_FEATURES[4:],
}


def add_anchor_telemetry(prepared: pd.DataFrame) -> pd.DataFrame:
    """Join only the observed GPU1/CPU2 summary to each candidate slate."""
    anchor = prepared[(prepared.replica_count == ANCHOR[0]) &
                      (prepared.cpu_core_limit == ANCHOR[1])]
    if anchor.workload_group.duplicated().any():
        raise ValueError('Repeated anchor')
    values = []
    for raw in anchor.node_summary_json:
        summary = json.loads(raw)
        row = [float(summary[key]) for key in SUMMARY_KEYS]
        if not np.isfinite(row).all() or np.min(row) < 0:
            raise ValueError('Invalid anchor telemetry')
        values.append(np.log1p(row))
    telemetry = pd.DataFrame(values, columns=TELEMETRY_FEATURES,
                             index=anchor.workload_group.to_numpy())
    if telemetry.index.has_duplicates:
        raise ValueError('Repeated anchor telemetry')
    out = prepared.join(telemetry, on='workload_group', validate='many_to_one')
    if out[list(TELEMETRY_FEATURES)].isna().any().any():
        raise ValueError('Missing anchor telemetry')
    return out


def fit_telemetry_model(train: pd.DataFrame, seed: int = 42,
                        telemetry_features: tuple[str, ...] = TELEMETRY_FEATURES):
    features = list(CONDITIONAL_FEATURES) + list(telemetry_features)
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                                max_features=1.0, random_state=seed, n_jobs=2)
    model.fit(train[features], train[['latency_residual', 'throughput_residual']])
    return model


def predict_telemetry(model, fold: pd.DataFrame,
                      telemetry_features: tuple[str, ...] = TELEMETRY_FEATURES) -> np.ndarray:
    delta = np.asarray(model.predict(fold[list(CONDITIONAL_FEATURES) +
                                          list(telemetry_features)]), dtype=float)
    return np.column_stack((fold.anchor_log_latency.to_numpy() + delta[:, 0],
                            fold.anchor_log_requests_per_second.to_numpy() +
                            fold.prior_log_throughput_gain.to_numpy() + delta[:, 1]))


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    data = {name: add_anchor_telemetry(prepare(raw[raw.campaign == name]))
            for name in CAMPAIGNS}
    main = data[CAMPAIGNS[0]]
    predictions = []
    for held_model in sorted(main.model_id.unique()):
        train = main[main.model_id != held_model]
        if held_model in set(train.model_id):
            raise AssertionError('Outer model leakage')
        baseline = fit_model(train, 'anchor_conditioned', seed)
        augmented = {name: fit_telemetry_model(train, seed, features)
                     for name, features in VARIANTS.items()}
        for campaign, frame in data.items():
            fold = frame[(frame.model_id == held_model) &
                         (frame.replica_count == 2) &
                         (frame.cpu_core_limit == 2)]
            if fold.empty:
                continue
            base_pred = predict(baseline, fold, 'anchor_conditioned')
            variant_preds = {name: predict_telemetry(model, fold, VARIANTS[name])
                             for name, model in augmented.items()}
            result = fold[['campaign', 'model_id', 'workload_group', 'execution_id',
                           'anchor_log_latency', 'anchor_log_requests_per_second',
                           'true_log_latency', 'true_log_requests_per_second',
                           'latency_slo_ms', 'target_throughput_rps']].copy()
            for index, name in enumerate(('latency', 'rps')):
                result[f'baseline_{name}_log'] = base_pred[:, index]
                for variant, variant_pred in variant_preds.items():
                    result[f'{variant}_{name}_log'] = variant_pred[:, index]
            predictions.append(result)
    pred = pd.concat(predictions, ignore_index=True)
    pred.to_parquet(output / 'predictions.parquet', index=False)
    summaries = []
    for (campaign, model_id), group in pred.groupby(['campaign', 'model_id']):
        item = {'campaign': campaign, 'model_id': model_id,
                'workload_groups': len(group)}
        for method in ('baseline', *VARIANTS):
            for target in ('latency', 'rps'):
                item[f'{method}_{target}_male'] = float(np.mean(np.abs(
                    group[f'{method}_{target}_log'] - group[f'true_log_{"requests_per_second" if target == "rps" else "latency"}'])))
        summaries.append(item)
    per_model = pd.DataFrame(summaries).sort_values(['campaign', 'model_id'])
    per_model.to_csv(output / 'per_model_metrics.csv', index=False)
    metrics = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'split': 'outer leave-one-model-out; donor training only on local-exhaustive',
        'heldout_observation': 'GPU1/CPU2 outcome and node summary for the same workload',
        'target': 'GPU2/CPU2 latency and requests per second',
        'model': 'ExtraTrees 100 trees, min leaf 2, seed 42 for all methods',
        'telemetry_variants': {name: list(features)
                               for name, features in VARIANTS.items()},
        'by_campaign': {},
    }
    for campaign, group in per_model.groupby('campaign'):
        metrics['by_campaign'][campaign] = {
            'models': len(group), 'workload_groups': int(group.workload_groups.sum()),
            **{name: float(group[name].mean()) for name in group.columns
               if name.endswith('_male')},
        }
    (output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    return metrics


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/telemetry_conditioned_scaling_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output), indent=2))
