"""Evaluate main-campaign sparse transfer on repeated, unseen live conditions.

All fitted models use other-model exhaustive rows. A held-out model contributes
only eight selected main-campaign GPU1/CPU2 probes and registered parameter
size. The 47 live rounds supply static conditions before prediction and labels
only afterward. We score the median of each independently repeated live cell;
the 47 rounds are not treated as 47 independent workloads.

The live campaign has only GPU1/CPU2 and, for six workloads, GPU2/CPU2. Its
recorded SLOs are generated benchmark thresholds, not user-facing SLOs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_repeat_variability import VERSION, live_cells
from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_model_covariance import _absolute, predict_sparse, training_oof_anchor_residuals
from cross_model_covariance_external import continuous_prior
from sparse_anchor_transfer import (CAMPAIGN, group_design, registered_parameter_bytes,
                                    select_probes, static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('cov_transfer', 'kernel_transfer', 'space_ridge', 'zero_shot')
OUTCOMES = ('latency_p95_ms', 'examples_per_second', 'wall_seconds',
            'compute_seconds', 'result_json')


def live_truth_and_static(live: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build one truth row per repeated cell and an outcome-free full slate.

    The full slate is needed by the existing predictor's input validation;
    only resource configurations that were actually measured are scored.
    """
    keys = ['workload_group', 'replica_count', 'cpu_core_limit']
    if live.empty or live[keys + ['run_id']].duplicated().any():
        raise ValueError('Missing or duplicate live rounds')
    counts = live.groupby(keys).size()
    if counts.ne(47).any():
        raise ValueError('Expected 47 rounds for every live resource cell')
    truth = live.groupby(keys, as_index=False).agg(
        latency_p95_ms=('latency_p95_ms', 'median'),
        examples_per_second=('examples_per_second', 'median'),
        batch_size=('batch_size', 'first'),
        latency_slo_ms=('latency_slo_ms', 'first'),
        target_throughput_rps=('target_throughput_rps', 'first'),
        model_id=('model_id', 'first'),
    )
    truth['execution_id'] = [
        f'live-v2-median::{g}::gpu{r}::cpu{c}'
        for g, r, c in truth[keys].itertuples(index=False, name=None)]
    if truth.execution_id.duplicated().any():
        raise ValueError('Duplicate median cell ID')
    source = live[(live.replica_count == 1) &
                  (live.cpu_core_limit == 2)].drop_duplicates('workload_group')
    if len(source) != live.workload_group.nunique():
        raise ValueError('Every live workload must have GPU1/CPU2 metadata')
    synthetic = []
    for _, original in source.iterrows():
        for replica in (1, 2):
            for cores in (2, 4, 8, 16):
                row = original.copy()
                row['replica_count'] = replica
                row['cpu_core_limit'] = cores
                row['cpu_threads'] = cores
                row['execution_id'] = (
                    f'live-v2-median::{row.workload_group}::gpu{replica}::cpu{cores}')
                for outcome in OUTCOMES:
                    row[outcome] = np.nan
                synthetic.append(row)
    static = pd.DataFrame(synthetic)
    validate_slates(static)
    if static[list(OUTCOMES)].notna().any().any():
        raise AssertionError('Live performance outcomes entered predictor inputs')
    return truth, static


def mask_heldout(main: pd.DataFrame, selected_groups: list[str]) -> pd.DataFrame:
    """Keep only explicitly purchased main anchor outcomes."""
    out = main.copy()
    available = ((out.replica_count == 1) & (out.cpu_core_limit == 2) &
                 out.workload_group.isin(selected_groups))
    if int(available.sum()) != len(selected_groups):
        raise ValueError('Missing or repeated selected probe')
    out.loc[~available, list(OUTCOMES)] = np.nan
    return out


def pair_decisions(predicted: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """At fixed CPU2, choose the fewest GPUs satisfying recorded thresholds."""
    if (predicted.execution_id.duplicated().any() or
            set(predicted.execution_id) != set(truth.execution_id)):
        raise ValueError('Prediction and truth resource cells disagree')
    paired = predicted.groupby('workload_group').filter(
        lambda rows: set(rows.replica_count) == {1, 2} and len(rows) == 2)
    if paired.empty:
        return pd.DataFrame()
    paired = paired.copy()
    paired['true_feasible'] = (
        (paired.latency_p95_ms <= paired.latency_slo_ms) &
        (paired.examples_per_second / paired.batch_size >=
         paired.target_throughput_rps))
    records = []
    for group_id, rows in paired.groupby('workload_group'):
        rows = rows.sort_values('replica_count')
        actual = rows.loc[rows.true_feasible].head(1)
        for method in METHODS:
            predicted_feasible = (
                (rows[f'{method}_latency_log'] <= np.log(rows.latency_slo_ms)) &
                (rows[f'{method}_rps_log'] >= np.log(rows.target_throughput_rps)))
            selected = rows.loc[predicted_feasible].head(1)
            records.append({
                'model_id': rows.model_id.iloc[0],
                'workload_group': group_id,
                'method': method,
                'oracle_gpu': int(actual.replica_count.iloc[0]) if len(actual) else None,
                'selected_gpu': int(selected.replica_count.iloc[0]) if len(selected) else None,
                'chosen_truly_feasible': bool(selected.true_feasible.iloc[0]) if len(selected) else False,
                'false_approval': bool(not selected.true_feasible.iloc[0]) if len(selected) else False,
                'missed_feasible': bool(len(actual) and not len(selected)),
            })
    return pd.DataFrame(records)


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    live, audited_cells = live_cells(raw)
    if live.collector_version.nunique() != 1 or live.collector_version.iloc[0] != VERSION:
        raise ValueError('Collector version mismatch')
    truth, live_static = live_truth_and_static(live)
    if len(audited_cells) != len(truth) or len(truth) != 16:
        raise ValueError('Repeated-cell audit and evaluation cells disagree')
    if set(main.workload_group) & set(live_static.workload_group):
        raise ValueError('Live and main workload groups overlap')
    scores = []
    manifests = []
    predicted_parts = []
    prior_modes = {}
    for held_model in sorted(live.model_id.unique()):
        train = prepare(main[main.model_id != held_model])
        held_main = main[main.model_id == held_model].copy()
        held_live = live_static[live_static.model_id == held_model].copy()
        held_truth = truth[truth.model_id == held_model].copy()
        if held_main.empty or held_live.empty or held_model in set(train.model_id):
            raise ValueError('Invalid leave-one-model-out fold')
        known_size = registered_parameter_bytes(held_main)
        design = group_design(static_features(held_main, known_size))
        selected = select_probes(design, 8, 'medoids', seed=seed)
        if set(selected) & set(held_live.workload_group):
            raise ValueError('External condition was selected as a probe')
        anchor = held_main[(held_main.replica_count == 1) &
                           (held_main.cpu_core_limit == 2)].set_index('workload_group')
        gpu_wall = float(anchor.loc[selected].wall_seconds.sum())
        masked = mask_heldout(held_main, selected)
        test_static = pd.concat([masked, held_live], ignore_index=True)
        absolute = _absolute().fit(
            train[list(BASE_FEATURES)],
            train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        covariance, info = continuous_prior(test_static, donor_oof)
        prior_modes[held_model] = info
        predicted = predict_sparse(test_static, selected, selected, covariance,
                                   absolute, conditional, parameter_bytes=known_size)
        predicted = predicted[predicted.campaign == 'khu-hf-live'].copy()
        predicted = predicted.merge(
            held_truth[['execution_id', 'batch_size', 'latency_p95_ms',
                        'examples_per_second']], on='execution_id', how='inner',
            validate='one_to_one')
        if len(predicted) != len(held_truth):
            raise ValueError('Missing live predicted resource cell')
        for method in METHODS:
            l_error = np.abs(predicted[f'{method}_latency_log'] -
                             np.log(predicted.latency_p95_ms))
            r_error = np.abs(predicted[f'{method}_rps_log'] -
                             np.log(predicted.examples_per_second /
                                    predicted.batch_size))
            for gpu, part in predicted.groupby('replica_count'):
                scores.append({
                    'heldout_model_id': held_model,
                    'method': method,
                    'replica_count': int(gpu),
                    'resource_cells': int(len(part)),
                    'latency_male': float(l_error.loc[part.index].mean()),
                    'rps_male': float(r_error.loc[part.index].mean()),
                    'main_probe_budget': 0 if method == 'zero_shot' else 8,
                    'new_live_probes': 0,
                    'main_probe_gpu_wall_seconds': 0.0 if method == 'zero_shot' else gpu_wall,
                })
        predicted['heldout_model_id'] = held_model
        predicted_parts.append(predicted)
        manifests.append({
            'heldout_model_id': held_model,
            'train_model_ids_json': json.dumps(sorted(train.model_id.unique())),
            'selected_main_groups_json': json.dumps(selected),
            'live_group_ids_json': json.dumps(sorted(held_live.workload_group.unique())),
            'main_probe_gpu_wall_seconds': gpu_wall,
            'prior_mode': info['mode'],
        })
        print(f'Finished {held_model}: {len(held_truth)} repeated cells', flush=True)
    predictions = pd.concat(predicted_parts, ignore_index=True)
    scored = pd.DataFrame(scores)
    decisions = pair_decisions(predictions, truth)
    round_feasibility = live.assign(
        true_feasible=((live.latency_p95_ms <= live.latency_slo_ms) &
                       (live.examples_per_second / live.batch_size >=
                        live.target_throughput_rps))).groupby(
        ['model_id', 'workload_group', 'replica_count', 'cpu_core_limit'],
        as_index=False).agg(rounds=('true_feasible', 'size'),
                            feasible_rounds=('true_feasible', 'sum'),
                            feasible_fraction=('true_feasible', 'mean'))
    output.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(output / 'predictions.parquet', index=False)
    scored.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    decisions.to_csv(output / 'paired_gpu_decisions.csv', index=False)
    round_feasibility.to_csv(output / 'round_feasibility.csv', index=False)
    rows = []
    for method in METHODS:
        part = predictions
        l_error = np.abs(part[f'{method}_latency_log'] -
                         np.log(part.latency_p95_ms))
        r_error = np.abs(part[f'{method}_rps_log'] -
                         np.log(part.examples_per_second / part.batch_size))
        decision = decisions[decisions.method == method]
        rows.append({
            'method': method,
            'resource_cells': int(len(part)),
            'model_count': int(part.model_id.nunique()),
            'workload_count': int(part.workload_group.nunique()),
            'micro_latency_male': float(l_error.mean()),
            'micro_rps_male': float(r_error.mean()),
            'gpu1_latency_male': float(l_error[part.replica_count == 1].mean()),
            'gpu1_rps_male': float(r_error[part.replica_count == 1].mean()),
            'gpu2_latency_male': float(l_error[part.replica_count == 2].mean()),
            'gpu2_rps_male': float(r_error[part.replica_count == 2].mean()),
            'paired_workloads': int(len(decision)),
            'feasible_pairs': int(decision.oracle_gpu.notna().sum()),
            'chosen_pairs': int(decision.selected_gpu.notna().sum()),
            'successful_choices': int(decision.chosen_truly_feasible.sum()),
            'false_approvals': int(decision.false_approval.sum()),
            'missed_feasible': int(decision.missed_feasible.sum()),
            'exact_min_gpu_choices': int(((decision.selected_gpu == decision.oracle_gpu) &
                                          decision.oracle_gpu.notna()).sum()),
        })
    summary = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'collector_version': VERSION,
        'training': 'other-model exhaustive campaign only',
        'heldout_observations': '8 main-campaign GPU1/CPU2 anchors; no live outcomes',
        'repeat_rounds_per_cell': 47,
        'scoring_unit': 'median per distinct live resource cell',
        'decision_scope': 'GPU1 versus GPU2 at CPU2 for six measured pairs; recorded generated SLO',
        'prior_modes': prior_modes,
        'method_summary': rows,
    }
    (output / 'metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/live_repeat_transfer_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['method_summary']:
        print(row, flush=True)
