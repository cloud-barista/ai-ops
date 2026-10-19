"""Retrospective, label-isolated pilot for gating sparse residual transfer.

The gate sees only donor out-of-fold residuals, workload metadata, and the
eight measured anchors of the held-out model. It never uses non-probe outcomes
or outcomes from the external workload campaigns in method selection.

This is intentionally a conservative benchmark for an *existing* idea:
cross-validation among transfer priors, restricted by donor support. It is not
a claim of a new domain-adaptation method.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from conditional_response import ANCHOR, prepare
from cross_model_covariance import (_keyed_anchor, _smooth_covariance,
                                    posterior_residual, residual_covariance,
                                    training_oof_anchor_residuals, KEY_COLUMNS)
from cross_model_covariance_external import continuous_prior
from remote_sparse_stress_test import first_complete_remote_slate
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS,
                                    _anchor_rows, group_design,
                                    registered_parameter_bytes,
                                    scenario_metrics, static_features)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'data/audit_v1/measurements.parquet'
METHODS = ('always_cov', 'always_kernel', 'direct_ridge',
           'support_only', 'support_loo')
PREFER = ('ridge', 'kernel', 'cov')


def exact_donor_support(target: pd.DataFrame, donor_oof: pd.DataFrame,
                        min_donors: int = 2) -> np.ndarray:
    """Count other models with exactly the same workload coordinate tuple."""
    if min_donors < 1:
        raise ValueError('min_donors must be positive')
    donor = donor_oof[list(KEY_COLUMNS) + ['model_id']].drop_duplicates()
    counts = donor.groupby(list(KEY_COLUMNS)).model_id.nunique().to_dict()
    keys = list(map(tuple, target[list(KEY_COLUMNS)].to_numpy()))
    return np.asarray([counts.get(key, 0) >= min_donors for key in keys], dtype=bool)


def probe_loo_scores(covariance: np.ndarray, design: np.ndarray,
                     selected: np.ndarray, residual: np.ndarray,
                     anchors: pd.DataFrame) -> dict[str, np.ndarray]:
    """Mean absolute leave-one-probe-out residual error, by outcome.

    The held-out probe in each inner fit is one of the already paid-for
    anchors. There is no query or resource-candidate outcome in this step.
    """
    cov = np.asarray(covariance, dtype=float)
    x = np.asarray(design, dtype=float)
    indices = np.asarray(selected, dtype=int)
    y = np.asarray(residual, dtype=float)
    n = len(x)
    if cov.shape != (n, n, 2) or x.ndim != 2 or y.shape != (len(indices), 2):
        raise ValueError('Covariance/design/probe shapes disagree')
    if len(indices) < 3 or len(set(indices)) != len(indices) or (indices < 0).any() or (indices >= n).any():
        raise ValueError('Need at least three distinct valid probes')
    if len(anchors) != n:
        raise ValueError('Kernel coordinates must match the design')
    variance = np.maximum(np.diagonal(cov, axis1=0, axis2=1).mean(axis=1), 1e-8)
    smooth = _smooth_covariance(anchors, variance)
    errors = {method: np.zeros_like(y) for method in PREFER}
    for held in range(len(indices)):
        keep = np.arange(len(indices)) != held
        fit_indices = indices[keep]
        fit_values = y[keep]
        query = indices[held]
        errors['cov'][held] = (posterior_residual(cov, fit_indices, fit_values)[query]
                               - y[held])
        errors['kernel'][held] = (posterior_residual(smooth, fit_indices, fit_values)[query]
                                  - y[held])
        ridge = Ridge(alpha=1.0, fit_intercept=False).fit(x[fit_indices], fit_values)
        errors['ridge'][held] = ridge.predict(x[[query]])[0] - y[held]
    return {name: np.mean(np.abs(value), axis=0) for name, value in errors.items()}


def choose_methods(loo_scores: dict[str, np.ndarray],
                   exact_support: np.ndarray) -> list[list[str]]:
    """Select a method for each query and each outcome without query labels."""
    support = np.asarray(exact_support, dtype=bool)
    if set(loo_scores) != set(PREFER):
        raise ValueError('Expected cov, kernel, and ridge scores')
    if not all(np.asarray(score).shape == (2,) and np.isfinite(score).all()
               for score in loo_scores.values()):
        raise ValueError('Each method requires two finite LOO scores')
    result = []
    for can_cov in support:
        choices = []
        for outcome in range(2):
            candidates = ('ridge', 'kernel', 'cov') if can_cov else ('ridge', 'kernel')
            choices.append(min(candidates, key=lambda method:
                               (float(loo_scores[method][outcome]),
                                PREFER.index(method))))
        result.append(choices)
    return result


def merge_direct_ridge(covariance_predictions: pd.DataFrame,
                       ridge_predictions: pd.DataFrame) -> pd.DataFrame:
    """Join predictions made with exactly the same selected eight probes."""
    columns = ['execution_id', 'ridge_shift_latency_log', 'ridge_shift_rps_log',
               'zero_shot_latency_log', 'zero_shot_rps_log']
    merged = covariance_predictions.merge(ridge_predictions[columns],
                                          on='execution_id', validate='one_to_one',
                                          suffixes=('', '_ridge'))
    if len(merged) != len(covariance_predictions) or len(merged) != len(ridge_predictions):
        raise ValueError('Prediction frames cover different resource cells')
    for outcome in ('latency', 'rps'):
        baseline = f'zero_shot_{outcome}_log'
        if not np.allclose(merged[baseline], merged[f'{baseline}_ridge'], atol=1e-10):
            raise ValueError('Methods used different zero-shot predictors')
    return merged.drop(columns=['zero_shot_latency_log_ridge',
                                'zero_shot_rps_log_ridge'])


def add_gate_columns(predicted: pd.DataFrame, choices: dict[str, list[str]],
                     support: dict[str, bool], remote: bool) -> pd.DataFrame:
    out = predicted.copy()
    for outcome_idx, outcome in enumerate(('latency', 'rps')):
        candidates = {
            'cov': ('kernel_transfer' if remote else 'space_cov_transfer'),
            'kernel': ('kernel_transfer' if remote else 'space_kernel_transfer'),
            'ridge': 'ridge_shift',
        }
        for target, decision in [('always_cov', 'cov'),
                                 ('always_kernel', 'kernel'),
                                 ('direct_ridge', 'ridge')]:
            out[f'{target}_{outcome}_log'] = out[f'{candidates[decision]}_{outcome}_log']
        out[f'support_only_{outcome}_log'] = np.where(
            out.workload_group.map(support).to_numpy(dtype=bool),
            out[f'{candidates["cov"]}_{outcome}_log'],
            out[f'{candidates["ridge"]}_{outcome}_log'])
        selected_methods = out.workload_group.map(
            {key: value[outcome_idx] for key, value in choices.items()})
        if selected_methods.isna().any():
            raise ValueError('A query lacks a gate decision')
        value = np.zeros(len(out))
        for method, column_name in candidates.items():
            mask = selected_methods.eq(method).to_numpy()
            value[mask] = out.loc[mask, f'{column_name}_{outcome}_log'].to_numpy()
        out[f'support_loo_{outcome}_log'] = value
    return out


def _probe_residual(test: pd.DataFrame, predicted: pd.DataFrame,
                    probes: list[str], group_order: list[str]) -> tuple[np.ndarray, np.ndarray]:
    anchor = _anchor_rows(test).loc[probes]
    if any((anchor.replica_count != ANCHOR[0]) | (anchor.cpu_core_limit != ANCHOR[1])):
        raise AssertionError('Only GPU1/core2 probe anchors can be revealed')
    baseline = predicted[(predicted.replica_count == ANCHOR[0]) &
                         (predicted.cpu_core_limit == ANCHOR[1])].set_index('workload_group')
    measured = np.column_stack((np.log(anchor.latency_p95_ms.to_numpy(dtype=float)),
                                np.log((anchor.examples_per_second / anchor.batch_size)
                                       .to_numpy(dtype=float))))
    base = baseline.loc[probes, ['zero_shot_latency_log',
                                 'zero_shot_rps_log']].to_numpy(dtype=float)
    return np.asarray([group_order.index(g) for g in probes], dtype=int), measured - base


def _evaluate_one(test: pd.DataFrame, predicted: pd.DataFrame,
                  prior: np.ndarray, donor_oof: pd.DataFrame,
                  probes: list[str], *, design_raw: pd.DataFrame,
                  probe_predictions: pd.DataFrame,
                  registered_size: float,
                  remote: bool, campaign_name: str,
                  probe_cost: float) -> tuple[list[dict], list[dict]]:
    all_anchors = _keyed_anchor(design_raw)
    group_order = all_anchors.workload_group.tolist()
    if prior.shape != (len(group_order), len(group_order), 2):
        raise ValueError('Prior target grid does not match static design')
    design = group_design(static_features(design_raw, registered_size)).loc[group_order].to_numpy()
    index, residual = _probe_residual(design_raw, probe_predictions, probes, group_order)
    scores = probe_loo_scores(prior, design, index, residual, all_anchors)
    support_bool = exact_donor_support(_keyed_anchor(test), donor_oof)
    target_groups = _keyed_anchor(test).workload_group.tolist()
    support = dict(zip(target_groups, support_bool.tolist()))
    decision = choose_methods(scores, support_bool)
    choices = dict(zip(target_groups, decision))
    scored_frame = add_gate_columns(predicted, choices, support, remote)
    metrics = scenario_metrics(scored_frame, test, methods=METHODS)
    rows = [{'dataset': campaign_name, 'heldout_model_id': test.model_id.iloc[0],
             'method': method, 'probe_budget': len(probes),
             'gpu_wall_probe_seconds': probe_cost, **value}
            for method, value in metrics.items()]
    choices_rows = [{'dataset': campaign_name, 'heldout_model_id': test.model_id.iloc[0],
                     'workload_group': g, 'exact_donor_support': bool(support[g]),
                     'latency_method': choices[g][0], 'rps_method': choices[g][1],
                     'loo_latency_cov': float(scores['cov'][0]),
                     'loo_latency_kernel': float(scores['kernel'][0]),
                     'loo_latency_ridge': float(scores['ridge'][0]),
                     'loo_rps_cov': float(scores['cov'][1]),
                     'loo_rps_kernel': float(scores['kernel'][1]),
                     'loo_rps_ridge': float(scores['ridge'][1])}
                    for g in target_groups]
    return rows, choices_rows


def run(snapshot: Path, output: Path) -> dict:
    """Evaluate fixed K8 space probes on main, external and remote slates."""
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    external = raw[raw.campaign.isin(EXTERNAL_CAMPAIGNS) & raw.eligible &
                   (raw.device == 'cuda')].copy()
    remote, _ = first_complete_remote_slate(raw)
    main_cov = pd.read_parquet(ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    main_cov = main_cov[main_cov.budget == 8]
    external_cov = pd.read_parquet(ROOT / 'artifacts/cross_model_covariance_external_v1/predictions.parquet')
    remote_cov = pd.read_parquet(ROOT / 'artifacts/remote_sparse_stress_v1/predictions.parquet')
    main_ridge = pd.read_parquet(ROOT / 'artifacts/sparse_anchor_transfer_v1/predictions.parquet')
    main_ridge = main_ridge[main_ridge.scenario.str.endswith('|medoids|42|8')]
    external_ridge = pd.read_parquet(ROOT / 'artifacts/sparse_anchor_transfer_v1/external_predictions.parquet')
    external_ridge = external_ridge[external_ridge.scenario.str.endswith('|medoids|42|8')]
    main_pred = merge_direct_ridge(main_cov, main_ridge)
    external_pred = merge_direct_ridge(external_cov, external_ridge)
    manifest = pd.read_csv(ROOT / 'artifacts/cross_model_covariance_v1/split_manifest.csv')
    manifest = manifest[(manifest.budget == 8) & (manifest.selector == 'space_filling')]
    remote_manifest = {item['heldout_model_id']: item
                       for item in json.loads((ROOT / 'artifacts/remote_sparse_stress_v1/metrics.json')
                                              .read_text())['manifest']}
    records, decisions = [], []
    for model in sorted(main.model_id.unique()):
        train = prepare(main[main.model_id != model])
        donor_oof = training_oof_anchor_residuals(train)
        held = main[main.model_id == model]
        probe_row = manifest[manifest.heldout_model_id == model].iloc[0]
        probes = json.loads(probe_row.probe_groups_json)
        probe_cost = float(probe_row.gpu_wall_probe_seconds)
        known_size = registered_parameter_bytes(
            held[held.workload_group.isin(probes)])
        pred = main_pred[main_pred.model_id == model]
        main_prior, _ = residual_covariance(_anchor_rows(held), donor_oof)
        r, d = _evaluate_one(held, pred, main_prior, donor_oof, probes,
                             design_raw=held, probe_predictions=pred,
                             registered_size=known_size,
                             remote=False, campaign_name=CAMPAIGN,
                             probe_cost=probe_cost)
        records.extend(r); decisions.extend(d)
        held_external = external[external.model_id == model]
        if len(held_external):
            combined = pd.concat((held, held_external), ignore_index=True)
            external_prior, _ = continuous_prior(combined, donor_oof)
            for campaign in EXTERNAL_CAMPAIGNS:
                ext = held_external[held_external.campaign == campaign]
                pred = external_pred[(external_pred.model_id == model) &
                                     (external_pred.campaign == campaign)]
                if ext.empty:
                    continue
                r, d = _evaluate_one(ext, pred, external_prior, donor_oof, probes,
                                     design_raw=combined,
                                     probe_predictions=main_pred[main_pred.model_id == model],
                                     registered_size=known_size,
                                     remote=False,
                                     campaign_name=campaign, probe_cost=probe_cost)
                records.extend(r); decisions.extend(d)
        print('Completed local', model, flush=True)
    donor_all = training_oof_anchor_residuals(prepare(main))
    for model in sorted(remote.model_id.unique()):
        held = remote[remote.model_id == model]
        remote_prior, _ = residual_covariance(_anchor_rows(held), donor_all)
        info = remote_manifest[model]
        probes = info['probe_groups']
        pred = remote_cov[remote_cov.model_id == model]
        known_size = registered_parameter_bytes(
            held[held.workload_group.isin(probes)])
        r, d = _evaluate_one(held, pred, remote_prior, donor_all, probes,
                             design_raw=held, probe_predictions=pred,
                             registered_size=known_size,
                             remote=True, campaign_name=held.campaign.iloc[0],
                             probe_cost=float(info['probe_gpu_wall_seconds']))
        records.extend(r); decisions.extend(d)
        print('Completed remote', model, flush=True)
    metrics = pd.DataFrame(records)
    gate = pd.DataFrame(decisions)
    output.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output / 'per_model_metrics.csv', index=False)
    gate.to_csv(output / 'gate_decisions.csv', index=False)
    macro = metrics.groupby(['dataset', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_probe_gpu_wall_seconds=('gpu_wall_probe_seconds', 'mean'))
    digest_paths = [snapshot,
                    ROOT / 'scripts/cross_model_covariance.py',
                    ROOT / 'scripts/cross_model_covariance_external.py',
                    ROOT / 'scripts/remote_sparse_stress_test.py',
                    ROOT / 'scripts/sparse_anchor_transfer.py',
                    ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet',
                    ROOT / 'artifacts/cross_model_covariance_external_v1/predictions.parquet',
                    ROOT / 'artifacts/remote_sparse_stress_v1/predictions.parquet',
                    ROOT / 'artifacts/sparse_anchor_transfer_v1/predictions.parquet',
                    ROOT / 'artifacts/sparse_anchor_transfer_v1/external_predictions.parquet']
    result = {
        'protocol': ('K8 fixed space-filling probes; LOOMO donor residuals; LOO on paid '
                     'anchors only; exact donor coordinate support; no external query labels'),
        'selection_rule': ('For each outcome, choose minimum anchor LOO MAE among '
                           'empirical covariance, smooth kernel and direct ridge. '
                           'Empirical covariance is ineligible without two exact donors.'),
        'support_only_rule': 'empirical covariance with two exact donors, else direct ridge',
        'source_sha256': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):
                          hashlib.sha256(p.read_bytes()).hexdigest() for p in digest_paths},
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'macro_per_dataset': macro.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/support_aware_transfer_gate_v1')
    args = parser.parse_args()
    run(args.snapshot, args.output)
