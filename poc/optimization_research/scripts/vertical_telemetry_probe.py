"""Exploratory CPU-affinity response from one measured GPU1/core2 run.

This is a static, same-host capacity prediction. No target core4/8/16
measurement or telemetry enters the predictor. All training excludes the
held-out model IDs (one model, BERT4, or BERT4+DistilBERT).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from conditional_response import CAMPAIGNS, CONDITIONAL_FEATURES, prepare
from family_holdout_transfer import FAMILY, FAMILY_WITH_DISTILBERT
from telemetry_conditioned_scaling import add_anchor_telemetry, TELEMETRY_FEATURES

ROOT = Path(__file__).resolve().parents[1]
TARGET_CORES = (4, 8, 16)
HIDDEN = ('latency_p95_ms', 'examples_per_second', 'node_summary_json',
          'result_json', 'wall_seconds', 'compute_seconds', 'cpu_seconds',
          'max_rss_kib', 'gpu_memory_allocated_bytes',
          'gpu_memory_reserved_bytes',
          'true_log_latency', 'true_log_requests_per_second',
          'latency_residual', 'throughput_residual', 'slo_boundary_margin')
SIGNALS = tuple(TELEMETRY_FEATURES[:4])
QUANTILES = np.linspace(0.2, 0.8, 5)


def fit(train: pd.DataFrame, features: list[str], seed: int) -> ExtraTreesRegressor:
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                                max_features=1.0, random_state=seed, n_jobs=2)
    model.fit(train[features], train[['latency_residual',
                                      'throughput_residual']].to_numpy())
    return model


def predict(model: ExtraTreesRegressor, target: pd.DataFrame,
            features: list[str]) -> np.ndarray:
    delta = np.asarray(model.predict(target[features]), dtype=float)
    return np.column_stack((target.anchor_log_latency.to_numpy() + delta[:, 0],
                            target.anchor_log_requests_per_second.to_numpy() + delta[:, 1]))


def target_rows(prepared: pd.DataFrame, models: set[str]) -> pd.DataFrame:
    target = prepared[prepared.model_id.isin(models) &
                      (prepared.replica_count == 1) &
                      prepared.cpu_core_limit.isin(TARGET_CORES)].copy()
    if target.empty:
        return target
    if target.duplicated(['workload_group', 'cpu_core_limit']).any():
        raise ValueError('Repeated target candidate')
    if target[list(SIGNALS)].isna().any().any():
        raise ValueError('Missing anchor telemetry')
    return target


def splits(main: pd.DataFrame):
    ids = sorted(main.model_id.unique())
    for model_id in ids:
        yield f'model:{model_id}', {model_id}
    for name, family in [('bert4', FAMILY),
                         ('bert4_distilbert', FAMILY_WITH_DISTILBERT)]:
        yield name, set(family)


def decision_summary(rows: pd.DataFrame,
                     levels: dict[str, tuple[np.ndarray, np.ndarray]]) -> pd.DataFrame:
    """Donor-threshold offline CPU-affinity selection; no live action claim."""
    records = []
    for (split, campaign), campaign_rows in rows.groupby(['split', 'campaign']):
        latency_levels, rps_levels = levels[split]
        for workload, slate in campaign_rows.groupby('workload_group'):
            slate = slate.sort_values('cpu_core_limit')
            if tuple(slate.cpu_core_limit) != TARGET_CORES:
                raise ValueError(f'Incomplete vertical candidate slate: {workload}')
            anchor_latency = float(slate.anchor_log_latency.iloc[0])
            anchor_rps = float(slate.anchor_log_requests_per_second.iloc[0])
            actual = ((slate.true_log_latency.to_numpy()[:, None, None]
                       <= latency_levels[None, :, None]) &
                      (slate.true_log_requests_per_second.to_numpy()[:, None, None]
                       >= rps_levels[None, None, :]))
            current_ok = ((anchor_latency <= latency_levels[:, None]) &
                          (anchor_rps >= rps_levels[None, :]))
            possible = actual.any(axis=0)
            for method in ('baseline', 'telemetry'):
                predicted = ((slate[f'{method}_latency_log'].to_numpy()[:, None, None]
                              <= latency_levels[None, :, None]) &
                             (slate[f'{method}_rps_log'].to_numpy()[:, None, None]
                              >= rps_levels[None, None, :]))
                chosen = np.where(predicted.any(axis=0), predicted.argmax(axis=0), -1)
                approved = ~current_ok & (chosen >= 0)
                selected_feasible = np.zeros_like(approved)
                if approved.any():
                    ii, jj = np.where(approved)
                    selected_feasible[ii, jj] = actual[chosen[ii, jj], ii, jj]
                records.append({'split': split, 'campaign': campaign,
                                'model_id': slate.model_id.iloc[0],
                                'workload_group': workload,
                                'method': method, 'queries': 25,
                                'at_risk': int((~current_ok).sum()),
                                'possible': int((~current_ok & possible).sum()),
                                'approved': int(approved.sum()),
                                'successful': int(selected_feasible.sum()),
                                'false_approval': int((approved & ~selected_feasible).sum()),
                                'missed': int((~current_ok & possible & ~approved).sum())})
    return pd.DataFrame(records)


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    data = {campaign: add_anchor_telemetry(prepare(raw[raw.campaign == campaign]))
            for campaign in CAMPAIGNS}
    main = data[CAMPAIGNS[0]]
    features = {'baseline': list(CONDITIONAL_FEATURES),
                'telemetry': list(CONDITIONAL_FEATURES) + list(SIGNALS)}
    parts = []
    levels = {}
    for split, held in splits(main):
        donor = main[(~main.model_id.isin(held)) &
                     (main.replica_count == 1) &
                     (main.cpu_core_limit.isin(TARGET_CORES))]
        if donor.model_id.nunique() < 2 or set(donor.model_id) & held:
            raise ValueError(f'Invalid donor partition: {split}')
        levels[split] = (np.quantile(donor.true_log_latency, QUANTILES),
                         np.quantile(donor.true_log_requests_per_second, QUANTILES))
        models = {name: fit(donor, feats, seed) for name, feats in features.items()}
        for campaign, prepared in data.items():
            target = target_rows(prepared, held)
            if target.empty:
                continue
            hidden = target.copy()
            hidden.loc[:, list(HIDDEN)] = np.nan
            base = target[['campaign', 'model_id', 'workload_group',
                           'execution_id', 'cpu_core_limit',
                           'anchor_log_latency', 'anchor_log_requests_per_second',
                           'true_log_latency', 'true_log_requests_per_second',
                           'latency_slo_ms', 'target_throughput_rps']].copy()
            base.insert(0, 'split', split)
            for name, model in models.items():
                prediction = predict(model, hidden, features[name])
                base[f'{name}_latency_log'] = prediction[:, 0]
                base[f'{name}_rps_log'] = prediction[:, 1]
            parts.append(base)
        print(split, flush=True)
    result = pd.concat(parts, ignore_index=True)
    if result.duplicated(['split', 'campaign', 'execution_id']).any():
        raise ValueError('Duplicate predictions')
    by_model = []
    for (split, campaign, model_id), group in result.groupby(
            ['split', 'campaign', 'model_id']):
        row = {'split': split, 'campaign': campaign, 'model_id': model_id,
               'rows': len(group), 'workloads': group.workload_group.nunique()}
        for method in features:
            row[f'{method}_latency_male'] = float(np.mean(np.abs(
                group[f'{method}_latency_log'] - group.true_log_latency)))
            row[f'{method}_rps_male'] = float(np.mean(np.abs(
                group[f'{method}_rps_log'] - group.true_log_requests_per_second)))
        by_model.append(row)
    metrics = pd.DataFrame(by_model)
    summary = metrics.groupby(['split', 'campaign'], as_index=False).agg(
        models=('model_id', 'nunique'), workloads=('workloads', 'sum'),
        baseline_latency_male=('baseline_latency_male', 'mean'),
        telemetry_latency_male=('telemetry_latency_male', 'mean'),
        baseline_rps_male=('baseline_rps_male', 'mean'),
        telemetry_rps_male=('telemetry_rps_male', 'mean'))
    output.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output / 'predictions.parquet', index=False)
    metrics.to_csv(output / 'per_model.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    decision = decision_summary(result, levels)
    decision.to_csv(output / 'decision_per_workload.csv', index=False)
    decision_summary_frame = decision.groupby(['split', 'campaign', 'method'],
                                               as_index=False).agg(
        workloads=('workload_group', 'nunique'), queries=('queries', 'sum'),
        at_risk=('at_risk', 'sum'), possible=('possible', 'sum'),
        approved=('approved', 'sum'), successful=('successful', 'sum'),
        false_approval=('false_approval', 'sum'), missed=('missed', 'sum'))
    decision_summary_frame.to_csv(output / 'decision_summary.csv', index=False)
    document = {'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'setup': 'GPU1/core2 measured performance and node summary; predict GPU1/core4/8/16 same workload. Train on donor main campaign only.',
                'resource_semantics': 'cpu_core_limit is process affinity, not VM CPU count; all GPUs on one host',
                'signals': list(SIGNALS), 'seed': seed,
                'decision': '25 correlated donor-target quantile pairs per workload; select smallest predicted feasible CPU affinity; hypothetical static capacity only',
                'summary': summary.to_dict(orient='records'),
                'decision_summary': decision_summary_frame.to_dict(orient='records')}
    (output / 'metrics.json').write_text(json.dumps(document, indent=2) + '\n')
    return document


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/vertical_telemetry_probe_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output)['summary'], indent=2))
