"""Exploratory decision- and cost-weighted sparse probe design.

All proposed probe choices use donor-only inner-LOOMO residual covariance,
donor-only SLO quantiles and donor-only probe-time fits. Held-out outcomes are
accessed only at the selected GPU1/core2 anchors and in final scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (_absolute, _keyed_anchor,
                                    choose_covariance_probes, predict_sparse,
                                    residual_covariance,
                                    training_oof_anchor_residuals)
from evaluate_counterfactual_slo import training_threshold_grid
from evaluate_resource_regret import score_threshold
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows,
                                    fit_probe_cost_predictor, group_design,
                                    registered_parameter_bytes,
                                    scenario_metrics, select_probes,
                                    static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
BUDGET = 8
METHODS = (
    'space_cov_transfer', 'cov_transfer', 'random_cov_transfer',
    'decision_cov_transfer', 'cost_cov_transfer', 'decision_cost_cov_transfer',
)


def select_weighted_covariance_probes(
    covariance: np.ndarray, budget: int, importance: np.ndarray,
    predicted_cost: np.ndarray, noise_fraction: float = 0.05,
) -> np.ndarray:
    """Greedy weighted posterior variance reduction divided by expected cost.

    Both outputs are normalized by prior mean variance. The importance score
    weights *target conditions*, while cost belongs to candidate measurements.
    """
    cov = np.asarray(covariance, dtype=float).copy()
    n = cov.shape[0]
    w = np.asarray(importance, dtype=float)
    cost = np.asarray(predicted_cost, dtype=float)
    if (cov.shape != (n, n, 2) or not 1 <= budget <= n or
            w.shape != (n,) or cost.shape != (n,) or noise_fraction <= 0 or
            not np.isfinite(cov).all() or not np.isfinite(w).all() or
            not np.isfinite(cost).all() or (w < 0).any() or
            (cost <= 0).any() or not np.allclose(cov, cov.transpose(1, 0, 2))):
        raise ValueError('Invalid covariance, weights, costs or budget')
    scale = np.maximum(np.diagonal(cov, axis1=0, axis2=1).mean(axis=1), 1e-8)
    noise = noise_fraction * scale
    selected = []
    available = np.ones(n, dtype=bool)
    for _ in range(budget):
        score = np.zeros(n)
        for t in range(2):
            score += (w[:, None] * cov[:, :, t] ** 2).sum(axis=0) / (
                (np.diag(cov[:, :, t]) + noise[t]) * scale[t])
        score /= cost
        score[~available] = -np.inf
        i = int(np.argmax(score))
        selected.append(i)
        available[i] = False
        for t in range(2):
            col = cov[:, i, t].copy()
            cov[:, :, t] -= np.outer(col, col) / (cov[i, i, t] + noise[t])
    return np.asarray(selected, dtype=int)


def donor_threshold_importance(
    static: pd.DataFrame, zero_shot: np.ndarray,
    thresholds: list[dict], group_order: list[str],
    strength: float = 4.0, bandwidth: float = 0.2,
) -> np.ndarray:
    """Mean decision-boundary weight across donor-defined SLO pairs.

    The zero-shot performance predictions and known resource-grid coordinates
    can be computed without any held-out performance measurements.
    """
    if zero_shot.shape != (len(static), 2) or not thresholds:
        raise ValueError('Invalid zero-shot predictions or thresholds')
    frame = static[['workload_group', 'replica_count', 'cpu_core_limit']].copy()
    frame['pred_l'] = zero_shot[:, 0]
    frame['pred_r'] = zero_shot[:, 1]
    frame = frame.sort_values(['workload_group', 'replica_count', 'cpu_core_limit'])
    ordered = frame.workload_group.drop_duplicates().tolist()
    if ordered != sorted(group_order) or frame.groupby('workload_group').size().ne(8).any():
        raise ValueError('Expected eight resource candidates per group')
    latency = frame.pred_l.to_numpy().reshape(len(ordered), 8)
    rps = frame.pred_r.to_numpy().reshape(len(ordered), 8)
    score = np.ones(len(ordered))
    for threshold in thresholds:
        l_slack = np.log(threshold['latency_slo_ms']) - latency
        r_slack = rps - np.log(threshold['target_rps'])
        distance = np.min(np.abs(np.minimum(l_slack, r_slack)), axis=1)
        score += strength * np.exp(-distance / bandwidth) / len(thresholds)
    return pd.Series(score, index=ordered).loc[group_order].to_numpy()


def _selected_groups(indices: np.ndarray, group_order: list[str]) -> list[str]:
    return [group_order[int(i)] for i in indices]


def run(snapshot: Path, reference: Path, output: Path, seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    frozen = pd.read_parquet(reference)
    frozen = frozen[frozen.budget == BUDGET].copy()
    if len(frozen) != len(main) or frozen.execution_id.duplicated().any():
        raise ValueError('Frozen K8 baseline must cover each main cell once')
    output.mkdir(parents=True, exist_ok=True)
    metric_rows = []
    scenario_rows = []
    manifest = []
    prediction_rows = []
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        held_raw = main[main.model_id == held_model]
        train = prepare(train_raw)
        absolute = _absolute().fit(train[list(BASE_FEATURES)],
                                   train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        anchors = _keyed_anchor(held_raw)
        covariance, info = residual_covariance(anchors, donor_oof)
        groups = anchors.workload_group.tolist()
        static = static_features(held_raw, registered_parameter_bytes(held_raw))
        zero_shot = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
        thresholds = training_threshold_grid(train_raw)
        importance = donor_threshold_importance(static, zero_shot, thresholds, groups)
        cost_model = fit_probe_cost_predictor(train, seed)
        anchor_static = static[(static.replica_count == ANCHOR[0]) &
                               (static.cpu_core_limit == ANCHOR[1])].set_index('workload_group').loc[groups]
        estimated_cost = np.exp(np.asarray(cost_model.predict(
            anchor_static[list(BASE_FEATURES)]), dtype=float))
        # A single extreme donor cost estimate must not decide the entire design.
        estimated_cost = np.clip(estimated_cost / np.median(estimated_cost), 0.25, 4.0)
        design = group_design(static)
        selectors = {
            'space_cov_transfer': select_probes(design, BUDGET, 'medoids', seed=seed),
            'cov_transfer': _selected_groups(
                choose_covariance_probes(covariance, BUDGET), groups),
            'random_cov_transfer': select_probes(design, BUDGET, 'random', seed=seed),
            'decision_cov_transfer': _selected_groups(
                select_weighted_covariance_probes(
                    covariance, BUDGET, importance, np.ones(len(groups))), groups),
            'cost_cov_transfer': _selected_groups(
                select_weighted_covariance_probes(
                    covariance, BUDGET, np.ones(len(groups)), estimated_cost), groups),
            'decision_cost_cov_transfer': _selected_groups(
                select_weighted_covariance_probes(
                    covariance, BUDGET, importance, estimated_cost), groups),
        }
        base = frozen[frozen.heldout_model_id == held_model].copy().sort_values('execution_id')
        if set(base.execution_id) != set(held_raw.execution_id):
            raise ValueError('Frozen baseline and held-out fold differ')
        pred = base[['campaign', 'model_id', 'workload_group', 'execution_id',
                     'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                     'target_throughput_rps', 'heldout_model_id']].copy()
        for method in METHODS[:2]:
            pred[f'{method}_latency_log'] = base[f'{method}_latency_log'].to_numpy()
            pred[f'{method}_rps_log'] = base[f'{method}_rps_log'].to_numpy()
        for method in METHODS[2:]:
            chosen = selectors[method]
            newly = predict_sparse(held_raw, chosen, chosen, covariance,
                                   absolute, conditional)
            newly = newly.set_index('execution_id').loc[pred.execution_id]
            pred[f'{method}_latency_log'] = newly.cov_transfer_latency_log.to_numpy()
            pred[f'{method}_rps_log'] = newly.cov_transfer_rps_log.to_numpy()
        scores = scenario_metrics(pred, held_raw, methods=METHODS)
        anchor_truth = _anchor_rows(held_raw)
        for method in METHODS:
            chosen = selectors[method]
            actual_cost = float((anchor_truth.loc[chosen].replica_count *
                                 anchor_truth.loc[chosen].wall_seconds).sum())
            manifest.append({
                'heldout_model_id': held_model, 'method': method,
                'probe_groups_json': json.dumps(chosen), 'probe_count': len(chosen),
                'actual_gpu_wall_seconds': actual_cost,
                'predicted_normalized_cost_sum': float(
                    estimated_cost[[groups.index(x) for x in chosen]].sum()),
                'covariance_mode': info['mode'],
                'donor_models_json': json.dumps(sorted(train_raw.model_id.unique())),
                'importance_min': float(importance.min()),
                'importance_max': float(importance.max()),
            })
            metric_rows.append({'heldout_model_id': held_model, 'method': method,
                                'covariance_mode': info['mode'],
                                'actual_gpu_wall_seconds': actual_cost,
                                **scores[method]})
        scored = pred.merge(held_raw[['execution_id', 'latency_p95_ms',
                                      'examples_per_second', 'batch_size']],
                            on='execution_id', validate='one_to_one')
        for threshold in thresholds:
            method_scores = score_threshold(scored, threshold, methods=METHODS)
            scenario_rows.extend({'heldout_model_id': held_model, **threshold,
                                  **row} for row in method_scores)
        prediction_rows.append(pred)
        print(f'Finished {held_model}: {len(groups)} conditions', flush=True)
    metric_frame = pd.DataFrame(metric_rows)
    scenario_frame = pd.DataFrame(scenario_rows)
    manifest_frame = pd.DataFrame(manifest)
    metric_frame.to_csv(output / 'per_model_metrics.csv', index=False)
    scenario_frame.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    manifest_frame.to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(prediction_rows, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    macro = metric_frame.groupby('method', as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        gpu_wall_seconds=('actual_gpu_wall_seconds', 'mean'))
    synthetic = scenario_frame.groupby('method', as_index=False).agg(
        oracle_feasible=('oracle_feasible', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        false_approvals=('false_approvals', 'sum'),
        oracle_cost_matches=('oracle_cost_matches', 'sum'),
        successful_but_extra_gpu=('successful_but_extra_gpu', 'sum'),
        successful_but_extra_core_same_gpu=(
            'successful_but_extra_core_same_gpu', 'sum'),
        avoidable_gpu2_recommendations=(
            'avoidable_gpu2_recommendations', 'sum'))
    paired = []
    rng = np.random.default_rng(seed)
    for metric, source in (
        ('latency_male', metric_frame), ('rps_male', metric_frame),
        ('actual_gpu_wall_seconds', metric_frame),
        ('successful_choices', scenario_frame),
        ('false_approvals', scenario_frame),
        ('oracle_cost_matches', scenario_frame),
        ('avoidable_gpu2_recommendations', scenario_frame),
    ):
        aggregate = ('mean' if source is metric_frame else 'sum')
        per_model = source.groupby(['heldout_model_id', 'method'])[metric].agg(
            aggregate).unstack('method')
        for baseline in ('space_cov_transfer', 'cov_transfer',
                         'random_cov_transfer'):
            delta = (per_model.decision_cost_cov_transfer -
                     per_model[baseline]).to_numpy(dtype=float)
            draws = delta[rng.integers(0, len(delta),
                                        size=(20000, len(delta)))].mean(axis=1)
            paired.append({
                'metric': metric, 'proposed': 'decision_cost_cov_transfer',
                'baseline': baseline,
                'difference_mean_per_model': float(delta.mean()),
                'descriptive_model_bootstrap_95_interval': [
                    float(x) for x in np.quantile(draws, (0.025, 0.975))],
                'per_model_difference': {
                    str(model): float(value)
                    for model, value in zip(per_model.index, delta)
                },
            })
    summary = {
        'scope': 'exploratory K8 probe design on one GPU host; generated original SLO and donor-quantile synthetic SLO are not user requests',
        'heldout_information': 'static workload/model metadata; 8 selected GPU1/core2 outcomes only',
        'outer_split': 'model_id LOOMO; donor residuals inner model_id LOOMO',
        'probe_count': BUDGET,
        'decision_weight': '1 + 4 * mean over donor-defined 25 SLO pairs of exp(-distance_to_nearest_predicted_joint_boundary / 0.2)',
        'cost_weight': 'donor-trained ExtraTrees predicted anchor wall time, median-normalized and clipped to [0.25, 4]',
        'random_comparison': 'one deterministic random selection per fold, seed 42; illustrative, not a random-design distribution',
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'reference_predictions_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_slo_macro': macro.to_dict(orient='records'),
        'donor_quantile_slo_totals': synthetic.to_dict(orient='records'),
        'paired_decision_cost_minus_baseline': paired,
        'interval_caveat': 'model-level descriptive bootstrap after method exploration; 25 SLO pairs per workload are correlated',
    }
    (output / 'metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--reference', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/probe_design_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    summary = run(args.snapshot, args.reference, args.output, args.seed)
    print(json.dumps(summary['original_slo_macro'], indent=2))
