"""Exploratory, training-fold-defined latency and throughput SLO stress test.

This does not create user SLO labels. The threshold grid for each held-out
model is determined only by the other models' measured distributions. Frozen
K=8 predictions are then scored against the held-out measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sparse_anchor_transfer import CAMPAIGN, validate_slates


ROOT = Path(__file__).resolve().parents[1]
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
METHODS = (
    'space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
    'space_global_mean', 'zero_shot', 'full_anchor_reference',
)
PROBE_COUNTS = {
    'space_cov_transfer': 8, 'space_kernel_transfer': 8,
    'space_ridge': 8, 'space_global_mean': 8,
    'zero_shot': 0, 'full_anchor_reference': 'all 63-64 workloads',
}


def training_threshold_grid(train: pd.DataFrame) -> list[dict]:
    """Generate threshold pairs without consulting the held-out model."""
    if train.empty:
        raise ValueError('No training rows')
    latency = train.latency_p95_ms.to_numpy(dtype=float)
    rps = (train.examples_per_second / train.batch_size).to_numpy(dtype=float)
    if (latency <= 0).any() or (rps <= 0).any():
        raise ValueError('Threshold source must have positive outcomes')
    lq = np.quantile(latency, QUANTILES)
    rq = np.quantile(rps, QUANTILES)
    return [
        {'latency_quantile': float(ql), 'rps_quantile': float(qr),
         'latency_slo_ms': float(lq[i]), 'target_rps': float(rq[j])}
        for i, ql in enumerate(QUANTILES)
        for j, qr in enumerate(QUANTILES)
    ]


def score_query(held: pd.DataFrame, threshold: dict,
                methods: tuple[str, ...] = METHODS) -> tuple[dict, list[dict]]:
    """Choose lowest GPU/core predicted feasible cell; use truth only to score."""
    slate = held.sort_values(['workload_group', 'replica_count', 'cpu_core_limit'])
    log_l = np.log(threshold['latency_slo_ms'])
    log_r = np.log(threshold['target_rps'])
    true_l = np.log(slate.latency_p95_ms.to_numpy(dtype=float))
    true_r = np.log((slate.examples_per_second /
                     slate.batch_size).to_numpy(dtype=float))
    actual = (true_l <= log_l) & (true_r >= log_r)
    diagnostics = slate[['workload_group']].copy()
    diagnostics['feasible'] = actual
    diagnostics['latency_only_failure'] = (true_l > log_l) & (true_r >= log_r)
    diagnostics['rps_only_failure'] = (true_l <= log_l) & (true_r < log_r)
    by_group = diagnostics.groupby('workload_group').agg(
        feasible_count=('feasible', 'sum'),
        latency_only_failure=('latency_only_failure', 'any'),
        rps_only_failure=('rps_only_failure', 'any'),
    )
    oracle_feasible = int((by_group.feasible_count > 0).sum())
    info = {
        'oracle_feasible': oracle_feasible,
        'nontrivial_feasibility': int(
            ((by_group.feasible_count > 0) &
             (by_group.feasible_count < 8)).sum()),
        'both_constraints_active': int(
            (by_group.latency_only_failure &
             by_group.rps_only_failure).sum()),
    }
    scored = []
    for name in methods:
        predicted = ((slate[f'{name}_latency_log'].to_numpy(dtype=float) <= log_l) &
                     (slate[f'{name}_rps_log'].to_numpy(dtype=float) >= log_r))
        candidates = slate.loc[predicted, ['workload_group']].copy()
        candidates['actual_feasible'] = actual[predicted]
        chosen = candidates.drop_duplicates('workload_group')
        success = int(chosen.actual_feasible.sum())
        false_approval = int(len(chosen) - success)
        scored.append({
            'method': name, 'oracle_feasible': oracle_feasible,
            'selected': int(len(chosen)), 'successful_choices': success,
            'false_approvals': false_approval,
            'missed_feasible': int(oracle_feasible - success),
        })
    return info, scored


def pareto_summary(main: pd.DataFrame) -> dict:
    """Describe performance-only Pareto fronts within each eight-cell slate."""
    size_count: dict[int, int] = {}
    sole_config_count: dict[str, int] = {}
    for _, group in main.groupby('workload_group'):
        latency = group.latency_p95_ms.to_numpy(dtype=float)
        rps = (group.examples_per_second / group.batch_size).to_numpy(dtype=float)
        if len(group) != 8:
            raise ValueError('Expected eight candidate cells per workload')
        non_dominated = []
        for i in range(8):
            dominated = any(
                latency[j] <= latency[i] and rps[j] >= rps[i] and
                (latency[j] < latency[i] or rps[j] > rps[i])
                for j in range(8) if j != i
            )
            if not dominated:
                non_dominated.append(i)
        size_count[len(non_dominated)] = size_count.get(len(non_dominated), 0) + 1
        if len(non_dominated) == 1:
            winner = group.iloc[non_dominated[0]]
            key = f"gpu{int(winner.replica_count)}_core{int(winner.cpu_core_limit)}"
            sole_config_count[key] = sole_config_count.get(key, 0) + 1
    return {
        'description': 'latency lower and requests/sec higher; resource cost is excluded',
        'front_size_counts': {str(k): v for k, v in sorted(size_count.items())},
        'single_front_config_counts': dict(sorted(sole_config_count.items())),
    }


def run(snapshot: Path, predictions: Path, output: Path) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    pred = pd.read_parquet(predictions)
    pred = pred[pred.budget == 8].copy()
    if pred.duplicated('execution_id').any() or len(pred) != len(main):
        raise ValueError('Expected one K8 prediction per held-out main cell')
    scored = pred.merge(
        main[['execution_id', 'latency_p95_ms', 'examples_per_second',
              'batch_size']],
        on='execution_id', validate='one_to_one',
    )
    if len(scored) != len(main) or not (scored.model_id == scored.heldout_model_id).all():
        raise ValueError('Prediction and held-out truth mismatch')
    rows = []
    diagnostics = []
    for model_id in sorted(main.model_id.unique()):
        train = main[main.model_id != model_id]
        held = scored[scored.model_id == model_id]
        if len(held) not in (63 * 8, 64 * 8):
            raise ValueError('Unexpected held-out slate size')
        for threshold in training_threshold_grid(train):
            info, results = score_query(held, threshold)
            key = {'heldout_model_id': model_id, **threshold}
            diagnostics.append({**key, **info})
            rows.extend({**key, **result} for result in results)
    result = pd.DataFrame(rows)
    diagnostic = pd.DataFrame(diagnostics)
    output.mkdir(parents=True, exist_ok=True)
    result.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    diagnostic.to_csv(output / 'scenario_diagnostics.csv', index=False)
    aggregate = result.groupby('method', as_index=False).agg(
        model_count=('heldout_model_id', 'nunique'),
        model_threshold_pairs=('oracle_feasible', 'size'),
        oracle_feasible=('oracle_feasible', 'sum'),
        selected=('selected', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        false_approvals=('false_approvals', 'sum'),
        missed_feasible=('missed_feasible', 'sum'),
    )
    if not (aggregate.oracle_feasible == aggregate.oracle_feasible.iloc[0]).all():
        raise AssertionError('Unequal oracle denominator')
    paired = []
    left = result[result.method == 'space_cov_transfer'].groupby(
        'heldout_model_id')[['successful_choices', 'false_approvals']].sum()
    rng = np.random.default_rng(42)
    for baseline in ('space_kernel_transfer', 'space_ridge', 'space_global_mean'):
        right = result[result.method == baseline].groupby(
            'heldout_model_id')[['successful_choices', 'false_approvals']].sum()
        if list(left.index) != list(right.index):
            raise AssertionError('Unequal model folds')
        for column in left.columns:
            differences = (left[column] - right[column]).to_numpy(dtype=float)
            draws = differences[rng.integers(0, len(differences),
                                             size=(20000, len(differences)))].mean(axis=1)
            paired.append({
                'baseline': baseline, 'metric': column,
                'difference_proposed_minus_baseline_total': int(differences.sum()),
                'difference_mean_per_model': float(differences.mean()),
                'descriptive_model_bootstrap_95_interval': [
                    float(x) for x in np.quantile(draws, (0.025, 0.975))
                ],
                'per_model_difference': {
                    str(model): int(value)
                    for model, value in zip(left.index, differences)
                },
            })
    report = {
        'scope': 'exploratory, synthetic batch-SLO stress test; not user SLOs',
        'threshold_source': 'each outer fold\'s other eight models only',
        'quantiles': list(QUANTILES),
        'query_count': int(diagnostic.shape[0]),
        'workload_query_count': int(
            sum(main[main.model_id == m].workload_group.nunique() *
                len(QUANTILES) ** 2 for m in main.model_id.unique())),
        'oracle_feasible_workload_queries': int(diagnostic.oracle_feasible.sum()),
        'nontrivial_workload_queries': int(diagnostic.nontrivial_feasibility.sum()),
        'both_constraints_active_workload_queries': int(
            diagnostic.both_constraints_active.sum()),
        'probe_count_by_method': PROBE_COUNTS,
        'performance_pareto': pareto_summary(main),
        'selection': 'fewest GPUs then fewest CPU cores among predicted feasible',
        'interval_caveat': 'descriptive after method exploration; 25 correlated queries per workload',
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'aggregate': aggregate.to_dict(orient='records'),
        'paired_covariance_minus_baseline': paired,
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/counterfactual_slo_v1')
    args = parser.parse_args()
    summary = run(args.snapshot, args.predictions, args.output)
    print(json.dumps(summary['aggregate'], indent=2))
