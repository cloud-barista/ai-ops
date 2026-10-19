"""Exploratory consensus and safety-margin decisions on frozen K=8 predictions.

All policies use the *same* eight target-model probes. SLO thresholds come
only from the eight other models in each outer fold. Held-out outcomes are
used solely after a policy has selected or declined a candidate. The margin
grid is independent of held-out outcomes. Retrospective comparisons of its
members are exploratory and cannot select a deployable margin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, validate_slates


ROOT = Path(__file__).resolve().parents[1]
MARGINS_LOG = (0.0, 0.05, 0.10, 0.20, 0.30)
METHODS = {
    'covariance': ('space_cov_transfer',),
    'kernel': ('space_kernel_transfer',),
    'ridge': ('space_ridge',),
    'covariance_and_kernel': ('space_cov_transfer', 'space_kernel_transfer'),
    'covariance_and_ridge': ('space_cov_transfer', 'space_ridge'),
    'kernel_and_ridge': ('space_kernel_transfer', 'space_ridge'),
    'all_three': ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge'),
}


def read_heldout(snapshot: Path, predictions: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    pred = pd.read_parquet(predictions)
    pred = pred[pred.budget == 8].copy()
    if len(pred) != len(main) or pred.execution_id.duplicated().any():
        raise ValueError('Expected one frozen K=8 prediction per measurement')
    scored = pred.merge(
        main[['execution_id', 'latency_p95_ms', 'examples_per_second',
              'batch_size']],
        on='execution_id', validate='one_to_one',
    )
    if len(scored) != len(main) or not (scored.model_id == scored.heldout_model_id).all():
        raise ValueError('Held-out predictions and measurements disagree')
    return main, scored


def ordered_slate(heldout: pd.DataFrame) -> pd.DataFrame:
    """Keep cost order identical for every method and synthetic SLO."""
    slate = heldout.sort_values(
        ['workload_group', 'replica_count', 'cpu_core_limit'])
    sizes = slate.groupby('workload_group', sort=True).size()
    if len(sizes) == 0 or not (sizes == 8).all():
        raise ValueError('Every workload must contain eight candidates')
    if slate.duplicated(['workload_group', 'replica_count', 'cpu_core_limit']).any():
        raise ValueError('Repeated candidate configuration')
    return slate


def conservative_prediction(slate: pd.DataFrame,
                            methods: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Consensus is worst predicted latency and worst predicted throughput."""
    latency = np.stack(
        [slate[f'{name}_latency_log'].to_numpy(dtype=float) for name in methods],
        axis=0)
    rps = np.stack(
        [slate[f'{name}_rps_log'].to_numpy(dtype=float) for name in methods],
        axis=0)
    if not np.isfinite(latency).all() or not np.isfinite(rps).all():
        raise ValueError('Nonfinite frozen prediction')
    return np.max(latency, axis=0), np.min(rps, axis=0)


def select(known: tuple[np.ndarray, np.ndarray], groups: int,
           latency_slo_ms: float, target_rps: float,
           margin_log: float) -> np.ndarray:
    """Return candidate index per workload, or -1 for no approval.

    This function cannot access measured outcomes. A log margin m is applied
    symmetrically: predicted latency × exp(m) must meet the SLO and predicted
    RPS / exp(m) must meet the target.
    """
    latency, rps = known
    if margin_log < 0 or latency_slo_ms <= 0 or target_rps <= 0:
        raise ValueError('Margin and targets must be valid')
    if latency.shape != (groups * 8,) or rps.shape != (groups * 8,):
        raise ValueError('Prediction slate has incorrect shape')
    approved = ((latency.reshape(groups, 8) + margin_log <= np.log(latency_slo_ms)) &
                (rps.reshape(groups, 8) - margin_log >= np.log(target_rps)))
    index = np.argmax(approved, axis=1)
    index[~approved.any(axis=1)] = -1
    return index


def score_selection(index: np.ndarray, truth: np.ndarray) -> dict:
    """Truth enters here only, after the selection has been fixed."""
    if truth.shape != (len(index), 8):
        raise ValueError('Expected one eight-candidate truth slate per workload')
    active = index >= 0
    good = truth[np.flatnonzero(active), index[active]]
    success = int(good.sum())
    oracle_feasible = int(truth.any(axis=1).sum())
    return {
        'oracle_feasible': oracle_feasible,
        'selected': int(active.sum()),
        'successful_choices': success,
        'false_approvals': int(active.sum() - success),
        'missed_feasible': int(oracle_feasible - success),
        'false_approval_rate_among_selected': (
            float((active.sum() - success) / active.sum())
            if active.sum() else None),
    }


def evaluate_scenario(slate: pd.DataFrame, threshold: dict) -> list[dict]:
    slate = ordered_slate(slate)
    groups = slate.workload_group.nunique()
    latency_slo = float(threshold['latency_slo_ms'])
    target_rps = float(threshold['target_rps'])
    true_latency = slate.latency_p95_ms.to_numpy(dtype=float).reshape(groups, 8)
    true_rps = (slate.examples_per_second / slate.batch_size).to_numpy(
        dtype=float).reshape(groups, 8)
    truth = (true_latency <= latency_slo) & (true_rps >= target_rps)
    result = []
    for policy, components in METHODS.items():
        known = conservative_prediction(slate, components)
        for margin_log in MARGINS_LOG:
            index = select(known, groups, latency_slo, target_rps, margin_log)
            result.append({
                'policy': policy, 'components': '+'.join(components),
                'margin_log': margin_log,
                **score_selection(index, truth),
            })
    return result


def dominators(aggregate: pd.DataFrame) -> list[dict]:
    """Find single-model margins that beat consensus on both error and yield.

    The comparison is descriptive: selecting a margin from this result using
    held-out labels would be an invalid training procedure.
    """
    singles = aggregate[aggregate.policy.isin(('covariance', 'kernel', 'ridge'))]
    consensus = aggregate[~aggregate.policy.isin(('covariance', 'kernel', 'ridge'))]
    rows = []
    for _, target in consensus.iterrows():
        eligible = singles[
            (singles.successful_choices >= target.successful_choices) &
            (singles.false_approvals <= target.false_approvals)]
        strict = eligible[
            (eligible.successful_choices > target.successful_choices) |
            (eligible.false_approvals < target.false_approvals)]
        rows.append({
            'policy': target.policy,
            'margin_log': float(target.margin_log),
            'dominated_by_single_fixed_margin': bool(len(strict)),
            'dominating_single_policies': [
                {'policy': row.policy, 'margin_log': float(row.margin_log),
                 'successful_choices': int(row.successful_choices),
                 'false_approvals': int(row.false_approvals)}
                for row in strict.itertuples()
            ],
        })
    return rows


def paired_model_comparison(result: pd.DataFrame,
                            left: tuple[str, float],
                            right: tuple[str, float]) -> dict:
    """Describe a paired contrast, resampling independent model folds only."""
    left_rows = result[(result.policy == left[0]) &
                       (result.margin_log == left[1])].groupby(
                           'heldout_model_id')[
                               ['selected', 'successful_choices',
                                'false_approvals']].sum().sort_index()
    right_rows = result[(result.policy == right[0]) &
                        (result.margin_log == right[1])].groupby(
                            'heldout_model_id')[
                                ['selected', 'successful_choices',
                                 'false_approvals']].sum().sort_index()
    if len(left_rows) != 9 or not left_rows.index.equals(right_rows.index):
        raise ValueError('Expected the same nine held-out model folds')
    rng = np.random.default_rng(42)
    comparison = {'left': {'policy': left[0], 'margin_log': left[1]},
                  'right': {'policy': right[0], 'margin_log': right[1]},
                  'interval_caveat': 'descriptive after method exploration; model folds are the resampling units'}
    for column in left_rows.columns:
        differences = (left_rows[column] - right_rows[column]).to_numpy(dtype=float)
        draws = differences[rng.integers(0, len(differences),
                                         size=(20000, len(differences)))].mean(axis=1)
        comparison[column] = {
            'difference_total': int(differences.sum()),
            'difference_mean_per_model': float(differences.mean()),
            'descriptive_model_bootstrap_95_interval_for_mean': [
                float(v) for v in np.quantile(draws, (0.025, 0.975))],
            'per_model_difference': dict(zip(left_rows.index.tolist(),
                                             differences.astype(int).tolist())),
        }
    return comparison


def run(snapshot: Path, predictions: Path, output: Path) -> dict:
    main, scored = read_heldout(snapshot, predictions)
    rows = []
    for heldout_model in sorted(main.model_id.unique()):
        # These threshold values are fixed by the other models only.
        thresholds = training_threshold_grid(main[main.model_id != heldout_model])
        held = scored[scored.model_id == heldout_model]
        for threshold in thresholds:
            prefix = {'heldout_model_id': heldout_model, **threshold}
            rows.extend({**prefix, **r} for r in evaluate_scenario(held, threshold))
    per_scenario = pd.DataFrame(rows)
    aggregate = per_scenario.groupby(
        ['policy', 'components', 'margin_log'], as_index=False).agg(
            model_slo_scenarios=('heldout_model_id', 'size'),
            oracle_feasible=('oracle_feasible', 'sum'),
            selected=('selected', 'sum'),
            successful_choices=('successful_choices', 'sum'),
            false_approvals=('false_approvals', 'sum'),
            missed_feasible=('missed_feasible', 'sum'),
        )
    aggregate['false_approval_rate_among_selected'] = (
        aggregate.false_approvals / aggregate.selected)
    aggregate['success_fraction_of_oracle'] = (
        aggregate.successful_choices / aggregate.oracle_feasible)
    if not (aggregate.oracle_feasible == aggregate.oracle_feasible.iloc[0]).all():
        raise AssertionError('Oracle feasible denominator differs across policies')
    comparisons = dominators(aggregate)
    output.mkdir(parents=True, exist_ok=True)
    per_scenario.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    aggregate.to_csv(output / 'aggregate.csv', index=False)
    report = {
        'scope': 'exploratory; 25 correlated synthetic threshold pairs per workload; no real user SLO',
        'threshold_source': 'other eight models in each outer fold',
        'prediction_source': 'frozen K=8 space-probe outer-fold predictions',
        'same_probe_count_for_all_policies': 8,
        'selection': 'fewest GPUs then fewest CPU cores among predicted feasible',
        'margin_interpretation': 'log margin m: latency prediction times exp(m), RPS prediction divided by exp(m)',
        'margins_log': list(MARGINS_LOG),
        'policies': {k: list(v) for k, v in METHODS.items()},
        'slo_scenarios': int(per_scenario[['heldout_model_id',
                                          'latency_quantile', 'rps_quantile']]
                             .drop_duplicates().shape[0]),
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'retrospective_dominance_caveat':
            'Dominance uses held-out results for description only; it cannot select a deployable margin.',
        'aggregate': aggregate.to_dict(orient='records'),
        'single_margin_dominance_of_consensus': comparisons,
        'matched_aggregate_approval_count_comparison': paired_model_comparison(
            per_scenario, ('all_three', 0.0), ('covariance', 0.05)),
        'same_zero_margin_comparison': paired_model_comparison(
            per_scenario, ('all_three', 0.0), ('covariance', 0.0)),
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
                        default=ROOT / 'artifacts/decision_consensus_v1')
    args = parser.parse_args()
    summary = run(args.snapshot, args.predictions, args.output)
    print(json.dumps([
        r for r in summary['aggregate'] if r['margin_log'] == 0.0
    ], indent=2))
