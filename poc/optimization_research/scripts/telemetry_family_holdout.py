"""Stress-test GPU1 telemetry transfer after jointly excluding BERT relatives.

Only donor models from the exhaustive campaign fit the response predictors.
Each target workload supplies its observed GPU1/CPU2 performance and telemetry;
target GPU2 outcomes and telemetry are hidden from prediction and used solely
for scoring. Synthetic decision thresholds come only from donor GPU2 labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import CAMPAIGNS, fit_model, predict, prepare
from family_holdout_transfer import FAMILY, FAMILY_WITH_DISTILBERT, family_partition
from telemetry_conditioned_scaling import (TELEMETRY_FEATURES, VARIANTS,
                                           add_anchor_telemetry,
                                           fit_telemetry_model, predict_telemetry)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('baseline', 'telemetry', 'no_count')
HIDDEN_GPU2_COLUMNS = ('true_log_latency', 'true_log_requests_per_second',
                       'latency_p95_ms', 'examples_per_second', 'node_summary_json',
                       'latency_residual', 'throughput_residual',
                       'slo_boundary_margin')
QUANTILES = np.linspace(0.2, 0.8, 5)


def hide_gpu2_outcomes(fold: pd.DataFrame) -> pd.DataFrame:
    """Make accidental target GPU2 label or telemetry access fail loudly."""
    if fold.empty or not ((fold.replica_count == 2) &
                          (fold.cpu_core_limit == 2)).all():
        raise ValueError('Expected only target GPU2/CPU2 candidate rows')
    out = fold.copy()
    out.loc[:, list(HIDDEN_GPU2_COLUMNS)] = np.nan
    if out[list(HIDDEN_GPU2_COLUMNS)].notna().any().any():
        raise AssertionError('Target GPU2 outcome remained visible')
    return out


def decision_rows(group: pd.DataFrame, latency_levels: np.ndarray,
                  rps_levels: np.ndarray) -> list[dict]:
    """Evaluate 25 correlated hypothetical limits per measured workload."""
    results = []
    current_l = group.anchor_log_latency.to_numpy()
    current_r = group.anchor_log_requests_per_second.to_numpy()
    true_l = group.true_log_latency.to_numpy()
    true_r = group.true_log_requests_per_second.to_numpy()
    for latency_limit in latency_levels:
        for minimum_rps in rps_levels:
            current_ok = (current_l <= latency_limit) & (current_r >= minimum_rps)
            gpu2_ok = (true_l <= latency_limit) & (true_r >= minimum_rps)
            at_risk = ~current_ok
            for method in METHODS:
                approved = (at_risk &
                            (group[f'{method}_latency_log'].to_numpy() <= latency_limit) &
                            (group[f'{method}_rps_log'].to_numpy() >= minimum_rps))
                results.append({
                    'method': method,
                    'queries': int(len(group)),
                    'at_risk': int(at_risk.sum()),
                    'beneficial_scale': int((at_risk & gpu2_ok).sum()),
                    'scale_approved': int(approved.sum()),
                    'successful_scale': int((approved & gpu2_ok).sum()),
                    'false_scale_approval': int((approved & ~gpu2_ok).sum()),
                    'missed_beneficial_scale': int((at_risk & gpu2_ok & ~approved).sum()),
                })
    return results


def run(snapshot: Path, output: Path, seed: int = 42,
        family: tuple[str, ...] = FAMILY) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGNS[0]) & raw.eligible &
               (raw.device == 'cuda')].copy()
    donors, targets = family_partition(main, family)
    train = add_anchor_telemetry(prepare(donors))
    if set(train.model_id) & set(family):
        raise AssertionError('Joint family exclusion failed')
    baseline = fit_model(train, 'anchor_conditioned', seed)
    augmented = {
        'telemetry': fit_telemetry_model(train, seed, VARIANTS['telemetry']),
        'no_count': fit_telemetry_model(train, seed, VARIANTS['no_count']),
    }
    train_gpu2 = train[(train.replica_count == 2) &
                       (train.cpu_core_limit == 2)]
    latency_levels = np.quantile(train_gpu2.true_log_latency, QUANTILES)
    rps_levels = np.quantile(train_gpu2.true_log_requests_per_second, QUANTILES)
    prediction_parts = []
    for campaign in CAMPAIGNS:
        target = targets if campaign == CAMPAIGNS[0] else raw[
            (raw.campaign == campaign) & raw.eligible &
            (raw.device == 'cuda') & raw.model_id.isin(family)].copy()
        prepared = add_anchor_telemetry(prepare(target))
        fold = prepared[(prepared.replica_count == 2) &
                        (prepared.cpu_core_limit == 2)].copy()
        hidden = hide_gpu2_outcomes(fold)
        base_pred = predict(baseline, hidden, 'anchor_conditioned')
        variant_preds = {
            name: predict_telemetry(model, hidden, VARIANTS[name])
            for name, model in augmented.items()
        }
        result = fold[['campaign', 'model_id', 'workload_group', 'execution_id',
                       'anchor_log_latency', 'anchor_log_requests_per_second',
                       'true_log_latency', 'true_log_requests_per_second',
                       'latency_slo_ms', 'target_throughput_rps']].copy()
        for feature in TELEMETRY_FEATURES:
            result[feature] = hidden[feature].to_numpy()
        for method, array in [('baseline', base_pred), *variant_preds.items()]:
            result[f'{method}_latency_log'] = array[:, 0]
            result[f'{method}_rps_log'] = array[:, 1]
        prediction_parts.append(result)
        print(f'Finished {campaign}: {len(result)} GPU2 target workloads', flush=True)
    pred = pd.concat(prediction_parts, ignore_index=True)
    if pred.duplicated(['campaign', 'model_id', 'workload_group']).any():
        raise ValueError('Repeated target workload prediction')
    rows, decisions = [], []
    for (campaign, model_id), group in pred.groupby(['campaign', 'model_id']):
        for method in METHODS:
            rows.append({
                'campaign': campaign,
                'model_id': model_id,
                'method': method,
                'workloads': int(len(group)),
                'latency_male': float(np.abs(group[f'{method}_latency_log'] -
                                             group.true_log_latency).mean()),
                'rps_male': float(np.abs(group[f'{method}_rps_log'] -
                                         group.true_log_requests_per_second).mean()),
            })
        for record in decision_rows(group, latency_levels, rps_levels):
            decisions.append({'campaign': campaign, 'model_id': model_id,
                              **record})
    per_model = pd.DataFrame(rows)
    decision_frame = pd.DataFrame(decisions)
    decision_per_model = decision_frame.groupby(
        ['campaign', 'model_id', 'method'], as_index=False).sum(numeric_only=True)
    summary = per_model.groupby(['campaign', 'method'], as_index=False).agg(
        models=('model_id', 'nunique'), workloads=('workloads', 'sum'),
        latency_male=('latency_male', 'mean'), rps_male=('rps_male', 'mean'))
    decision_summary = decision_per_model.groupby(
        ['campaign', 'method'], as_index=False).sum(numeric_only=True)
    summary = summary.merge(decision_summary, on=['campaign', 'method'],
                            validate='one_to_one')
    output.mkdir(parents=True, exist_ok=True)
    pred.to_parquet(output / 'predictions.parquet', index=False)
    per_model.to_csv(output / 'per_model_metrics.csv', index=False)
    decision_per_model.to_csv(output / 'decision_per_model.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    result = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'heldout_family': list(family),
        'donor_models': sorted(train.model_id.unique()),
        'training': 'only donor models in local-exhaustive, all heldout family excluded jointly',
        'target_observations': 'same-workload GPU1/CPU2 performance and node summary only',
        'target': 'GPU2/CPU2 batch p95 and requests per second, same host',
        'decision': '25 correlated donor GPU2 quantile target pairs per workload; static capacity only',
        'donor_target_levels': {
            'latency_log': latency_levels.tolist(),
            'rps_log': rps_levels.tolist(),
        },
        'summary': summary.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--include-distilbert', action='store_true')
    args = parser.parse_args()
    family = FAMILY_WITH_DISTILBERT if args.include_distilbert else FAMILY
    output = args.output or ROOT / 'artifacts' / (
        'telemetry_family_plus_distilbert_v1' if args.include_distilbert else
        'telemetry_family_holdout_v1')
    result = run(args.snapshot, output, args.seed, family)
    for row in result['summary']:
        print(row, flush=True)
