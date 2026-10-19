"""Cost-aware audit of frozen sparse predictions under training-fold SLOs.

This is an exploratory resource-order audit, not a VM-price or user-SLO study.
Resource order is lexicographic: fewer GPUs, then fewer CPU cores. Thresholds
come only from the eight training models in each leave-one-model-out fold.
Held-out measurements enter the final scoring oracle, never threshold choice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, CONFIGS, validate_slates


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
           'space_global_mean')
EXPECTED_CONFIGS = tuple(sorted(CONFIGS))


def score_threshold(slate: pd.DataFrame, threshold: dict,
                    methods: tuple[str, ...] = METHODS) -> list[dict]:
    """Evaluate one model's complete 8-config workload slates at one SLO.

    Every count uses workload-SLO queries as units. For a successful selection,
    compare cost against the least-resource *truly* feasible configuration.
    GPU and CPU excess are separate because no exchange rate is known.
    """
    ordered = slate.sort_values(['workload_group', 'replica_count',
                                 'cpu_core_limit']).reset_index(drop=True)
    if ordered.empty or len(ordered) % 8:
        raise ValueError('Expected complete eight-cell slates')
    n = len(ordered) // 8
    group = ordered.workload_group.to_numpy().reshape(n, 8)
    if not np.all(group == group[:, :1]):
        raise ValueError('Rows are not grouped by workload_group')
    configs = ordered[['replica_count', 'cpu_core_limit']].to_numpy().reshape(n, 8, 2)
    if not np.array_equal(configs, np.broadcast_to(
            np.asarray(EXPECTED_CONFIGS), configs.shape)):
        raise ValueError('Each workload requires the same ordered 8-config slate')
    latency = ordered.latency_p95_ms.to_numpy(dtype=float).reshape(n, 8)
    rps = ((ordered.examples_per_second / ordered.batch_size)
           .to_numpy(dtype=float).reshape(n, 8))
    if np.any(latency <= 0) or np.any(rps <= 0):
        raise ValueError('Outcomes must be positive')
    log_l = float(np.log(threshold['latency_slo_ms']))
    log_r = float(np.log(threshold['target_rps']))
    truth = (np.log(latency) <= log_l) & (np.log(rps) >= log_r)
    oracle_exists = truth.any(axis=1)
    oracle_index = np.where(oracle_exists, truth.argmax(axis=1), -1)
    oracle_gpu = np.where(oracle_exists,
                          np.asarray(EXPECTED_CONFIGS)[np.maximum(oracle_index, 0), 0],
                          -1)
    oracle_core = np.where(oracle_exists,
                           np.asarray(EXPECTED_CONFIGS)[np.maximum(oracle_index, 0), 1],
                           -1)
    oracle_gpu1 = oracle_gpu == 1
    nontrivial = oracle_exists & (truth.sum(axis=1) < 8)
    output = []
    for method in methods:
        predicted = (
            ordered[f'{method}_latency_log'].to_numpy(dtype=float).reshape(n, 8) <= log_l
        ) & (
            ordered[f'{method}_rps_log'].to_numpy(dtype=float).reshape(n, 8) >= log_r
        )
        selected = predicted.any(axis=1)
        selected_index = np.where(selected, predicted.argmax(axis=1), -1)
        selected_true_feasible = np.take_along_axis(
            truth, np.maximum(selected_index, 0)[:, None], axis=1
        )[:, 0]
        success = selected & selected_true_feasible
        false_approval = selected & ~selected_true_feasible
        cost_match = success & (selected_index == oracle_index)
        selected_gpu = np.asarray(EXPECTED_CONFIGS)[np.maximum(selected_index, 0), 0]
        selected_core = np.asarray(EXPECTED_CONFIGS)[np.maximum(selected_index, 0), 1]
        avoidable_gpu2 = oracle_gpu1 & selected & (selected_gpu == 2)
        extra_gpu = success & (selected_gpu > oracle_gpu)
        extra_core = success & (selected_gpu == oracle_gpu) & (
            selected_core > oracle_core)
        if np.any(success & ~oracle_exists):
            raise AssertionError('A true success requires an oracle feasible option')
        if np.any(success & (selected_index < oracle_index)):
            raise AssertionError('A success cannot cost less than the oracle')
        if not np.all((cost_match | extra_gpu | extra_core)[success]):
            raise AssertionError('Every success needs one exclusive resource-cost class')
        output.append({
            'method': method,
            'workload_queries': n,
            'oracle_feasible': int(oracle_exists.sum()),
            'nontrivial_oracle_feasible': int(nontrivial.sum()),
            'oracle_gpu1_feasible': int(oracle_gpu1.sum()),
            'selected': int(selected.sum()),
            'successful_choices': int(success.sum()),
            'false_approvals': int(false_approval.sum()),
            'missed_feasible': int((oracle_exists & ~success).sum()),
            'oracle_cost_matches': int(cost_match.sum()),
            'successful_but_extra_gpu': int(extra_gpu.sum()),
            'successful_but_extra_core_same_gpu': int(extra_core.sum()),
            'extra_gpu_units_on_success': int(
                (selected_gpu[success] - oracle_gpu[success]).sum()),
            'extra_cpu_cores_same_gpu_on_success': int(
                (selected_core[extra_core] - oracle_core[extra_core]).sum()),
            'avoidable_gpu2_recommendations': int(avoidable_gpu2.sum()),
            'avoidable_gpu2_successes': int((avoidable_gpu2 & success).sum()),
            'nontrivial_successes': int((nontrivial & success).sum()),
            'nontrivial_oracle_cost_matches': int((nontrivial & cost_match).sum()),
        })
    return output


def paired_model_bootstrap(scenarios: pd.DataFrame) -> list[dict]:
    """Descriptive outer-model bootstrap; synthetic queries are correlated."""
    columns = ('successful_choices', 'false_approvals', 'oracle_cost_matches',
               'avoidable_gpu2_recommendations')
    rng = np.random.default_rng(42)
    rows = []
    for population, subset in scenarios.groupby('population'):
        cov = subset[subset.method == 'space_cov_transfer'].groupby(
            'heldout_model_id')[list(columns)].sum().sort_index()
        for baseline in ('space_kernel_transfer', 'space_ridge'):
            other = subset[subset.method == baseline].groupby(
                'heldout_model_id')[list(columns)].sum().sort_index()
            if not cov.index.equals(other.index):
                raise ValueError('Comparison methods have different held-out folds')
            for column in columns:
                difference = (cov[column] - other[column]).to_numpy(dtype=int)
                draws = difference[rng.integers(
                    0, len(difference), size=(20000, len(difference)))].mean(axis=1)
                rows.append({
                    'population': population, 'baseline': baseline,
                    'metric': column,
                    'difference_total': int(difference.sum()),
                    'difference_mean_per_model': float(difference.mean()),
                    'descriptive_model_bootstrap_95_interval': [
                        float(x) for x in np.quantile(draws, [0.025, 0.975])],
                    'per_model_difference': {
                        str(model): int(value)
                        for model, value in zip(cov.index, difference)
                    },
                })
    return rows


def run(snapshot: Path, predictions: Path, output: Path) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    pred = pd.read_parquet(predictions)
    pred = pred[pred.budget == 8].copy()
    if pred.execution_id.duplicated().any() or len(pred) != len(main):
        raise ValueError('Expected one K8 prediction per held-out main cell')
    keys = ['execution_id', 'model_id', 'workload_group', 'replica_count',
            'cpu_core_limit']
    columns = keys + ['latency_p95_ms', 'examples_per_second', 'batch_size']
    scored = pred.merge(main[columns], on=keys, validate='one_to_one')
    if len(scored) != len(main) or not (
            scored.model_id == scored.heldout_model_id).all():
        raise ValueError('Prediction and held-out truth mismatch')
    rows = []
    for model_id in sorted(main.model_id.unique()):
        train = main[main.model_id != model_id]
        held = scored[scored.model_id == model_id]
        probe_json = held.space_probe_groups_json.unique()
        if len(probe_json) != 1:
            raise ValueError('Expected one common K8 probe set per held-out model')
        probe_groups = json.loads(probe_json[0])
        if len(probe_groups) != 8 or len(set(probe_groups)) != 8 or not (
                set(probe_groups) <= set(held.workload_group)):
            raise ValueError('Invalid K8 probe groups')
        unprobed = held[~held.workload_group.isin(probe_groups)]
        for threshold in training_threshold_grid(train):
            for population, sample in (('all_workloads', held),
                                       ('unprobed_workloads', unprobed)):
                rows.extend({
                    'population': population,
                    'heldout_model_id': model_id, **threshold, **metric,
                } for metric in score_threshold(sample, threshold))
    scenarios = pd.DataFrame(rows)
    additive = [column for column in scenarios.columns
                if column not in {'population', 'method', 'heldout_model_id',
                                  'latency_quantile', 'rps_quantile',
                                  'latency_slo_ms', 'target_rps'}]
    aggregate = scenarios.groupby(['population', 'method'],
                                  as_index=False)[additive].sum()
    for _, group in aggregate.groupby('population'):
        for column in ('oracle_feasible', 'oracle_gpu1_feasible',
                       'workload_queries'):
            if group[column].nunique() != 1:
                raise AssertionError(f'Unequal {column} denominator')
    output.mkdir(parents=True, exist_ok=True)
    scenarios.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    summary = {
        'scope': 'exploratory synthetic batch-SLO resource-order audit; not VM prices or user SLOs',
        'threshold_source': 'other eight models only for each outer held-out model',
        'resource_order': 'lexicographic GPU count then CPU cores; no monetary conversion',
        'selection': 'first predicted feasible cell in resource order; abstain if none',
        'oracle': 'first truly feasible cell in resource order; truth used for scoring only',
        'denominators': {
            'success_and_cost_match': 'oracle_feasible workload-SLO queries',
            'false_approvals': 'all workload-SLO queries; selected subset also reported',
            'unnecessary_gpu2': 'oracle_gpu1_feasible workload-SLO queries',
            'extra_resource_sums': 'successful selections only; report success counts jointly',
        },
        'probe_count_per_model_for_each_compared_method': 8,
        'populations': {
            'all_workloads': 'all held-out workload groups',
            'unprobed_workloads': 'excludes the eight groups whose GPU1/core2 anchor was observed',
        },
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'aggregate': aggregate.to_dict(orient='records'),
        'paired_covariance_minus_baseline': paired_model_bootstrap(scenarios),
    }
    (output / 'metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/resource_regret_v1')
    args = parser.parse_args()
    metrics = run(args.snapshot, args.predictions, args.output)
    print(json.dumps(metrics['aggregate'], indent=2))
