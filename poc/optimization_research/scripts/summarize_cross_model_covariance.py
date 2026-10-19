"""Recompute stratified and paired summaries from frozen per-model results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
           'space_global_mean', 'cov_transfer', 'zero_shot',
           'full_anchor_reference')


def summarize(frame: pd.DataFrame, seed: int = 42) -> dict:
    data = frame.copy()
    data['stratum'] = data.covariance_mode.map({
        'aligned_empirical_plus_kernel': 'aligned_encoder_8',
        'kernel_fallback_no_aligned_donors': 'unaligned_qwen_1',
    })
    if data.stratum.isna().any():
        raise ValueError('Unknown covariance mode')
    if data.duplicated(['heldout_model_id', 'budget', 'method']).any():
        raise ValueError('Duplicate method/fold/budget')
    if set(data.budget) != {4, 8, 16}:
        raise ValueError('Unexpected profiling budgets')
    for (_, _), group in data.groupby(['heldout_model_id', 'budget']):
        if set(group.method) != set(frame.method.unique()):
            raise ValueError('Method missing from fold')
        for column in ('scored_groups', 'scored_candidates', 'feasible_groups'):
            if group[column].nunique() != 1:
                raise ValueError(f'Unequal {column} within fold')
        if group.scored_candidates.iloc[0] != 7 * group.scored_groups.iloc[0]:
            raise ValueError('Nonanchor scoring population changed')
        for _, row in group.iterrows():
            if row.successful_choices + row.missed_feasible_groups != row.feasible_groups:
                raise ValueError('Success and missed feasibility do not reconcile')
    summaries = []
    for budget in (4, 8, 16):
        subset = data[data.budget == budget]
        for stratum in ('aligned_encoder_8', 'unaligned_qwen_1', 'all_9'):
            selected = subset if stratum == 'all_9' else subset[subset.stratum == stratum]
            for method in METHODS:
                rows = selected[selected.method == method]
                summaries.append({
                    'budget': budget, 'stratum': stratum, 'method': method,
                    'models': int(rows.heldout_model_id.nunique()),
                    'scored_groups': int(rows.scored_groups.sum()),
                    'scored_nonanchor_candidates': int(rows.scored_candidates.sum()),
                    'oracle_feasible_groups': int(rows.feasible_groups.sum()),
                    'latency_male_macro': float(rows.latency_male.mean()),
                    'requests_per_second_male_macro': float(rows.rps_male.mean()),
                    'successful_choices': int(rows.successful_choices.sum()),
                    'slo_violations': int(rows.slo_violations.sum()),
                    'missed_feasible_groups': int(rows.missed_feasible_groups.sum()),
                    'gpu_wall_probe_seconds_mean_model': float(rows.gpu_wall_probe_seconds.mean()),
                    'gpu_wall_probe_seconds_total': float(rows.gpu_wall_probe_seconds.sum()),
                })
    rng = np.random.default_rng(seed)
    paired = []
    for stratum in ('aligned_encoder_8', 'unaligned_qwen_1', 'all_9'):
        selected = data[(data.budget == 8) & (
            data.stratum == stratum if stratum != 'all_9' else True)]
        for baseline in ('space_kernel_transfer', 'space_ridge', 'space_global_mean'):
            left = selected[selected.method == baseline].set_index('heldout_model_id').sort_index()
            right = selected[selected.method == 'space_cov_transfer'].set_index('heldout_model_id').sort_index()
            if list(left.index) != list(right.index):
                raise ValueError('Unpaired held-out model comparison')
            for metric in ('latency_male', 'rps_male', 'successful_choices',
                           'slo_violations', 'gpu_wall_probe_seconds'):
                if metric in ('successful_choices', 'gpu_wall_probe_seconds'):
                    # More successful choices is beneficial; wall is reported
                    # as proposed minus baseline and should equal zero.
                    sign = -1 if metric == 'successful_choices' else 1
                else:
                    sign = 1
                difference = sign * (left[metric].to_numpy(dtype=float) -
                                     right[metric].to_numpy(dtype=float))
                result = {'stratum': stratum, 'budget': 8,
                          'baseline': baseline, 'proposed': 'space_cov_transfer',
                          'metric': metric, 'paired_models': len(difference),
                          'difference_mean_baseline_minus_proposed_except_success': float(difference.mean()),
                          'positive_models': int((difference > 0).sum()),
                          'negative_models': int((difference < 0).sum())}
                if len(difference) >= 2:
                    draws = difference[rng.integers(0, len(difference),
                                                       size=(20000, len(difference)))].mean(axis=1)
                    result['descriptive_model_cluster_bootstrap_95_ci'] = [
                        float(x) for x in np.quantile(draws, [0.025, 0.975])]
                paired.append(result)
    return {'scope': 'exploratory LOOMO; same fixed nonanchor population and same model-wise oracle denominator',
            'interval_caveat': 'Bootstrap intervals are descriptive after exploratory method iteration',
            'stratified': summaries, 'paired_at_k8': paired}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/per_model_metrics.csv')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/stratified_summary.json')
    args = parser.parse_args()
    report = summarize(pd.read_csv(args.input))
    report['input_sha256'] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for row in report['stratified']:
        if row['budget'] == 8 and row['method'] in METHODS[:4]:
            print(row)
