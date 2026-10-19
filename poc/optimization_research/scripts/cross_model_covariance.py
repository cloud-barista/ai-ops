"""Exploratory sparse profiling with a cross-model residual covariance prior.

For every outer leave-one-model-out (LOOMO) fold, an absolute performance model
is trained only on the other models. Its anchor residuals on those *training*
models are themselves computed by inner LOOMO. If donor models share workload
conditions, their residual surfaces define an empirical covariance. A smooth
kernel provides a positive definite shrinkage component and a fallback for a
held-out model on a different workload grid. The held-out model contributes
only k explicitly selected GPU1/two-core anchor outcomes before prediction.

This is an exploratory comparison, not a novelty or generalization claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge

from conditional_response import ANCHOR, BASE_FEATURES, CONDITIONAL_FEATURES, fit_model, prepare
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
KEY_COLUMNS = ('workload_task', 'batch_size', 'max_length',
               'request_concurrency', 'repeats')
METHODS = ('cov_transfer', 'kernel_transfer', 'space_cov_transfer',
           'space_kernel_transfer',
           'cov_global_mean', 'space_global_mean', 'space_ridge',
           'zero_shot', 'full_anchor_reference')
RHO = 0.50
NOISE_FRACTION = 0.05


def _absolute() -> ExtraTreesRegressor:
    return ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                               max_features=1.0, random_state=42, n_jobs=2)


def _keyed_anchor(frame: pd.DataFrame) -> pd.DataFrame:
    anchors = _anchor_rows(frame).copy()
    anchors['condition_key'] = list(map(tuple, anchors[list(KEY_COLUMNS)].to_numpy()))
    if anchors.condition_key.duplicated().any():
        raise ValueError('A model has repeated workload conditions')
    return anchors.set_index('condition_key', drop=False).sort_index()


def training_oof_anchor_residuals(train: pd.DataFrame) -> pd.DataFrame:
    """Residuals for donors predicted by models that excluded each donor."""
    pieces = []
    for donor in sorted(train.model_id.unique()):
        fit = train[train.model_id != donor]
        test = train[(train.model_id == donor) &
                     (train.replica_count == ANCHOR[0]) &
                     (train.cpu_core_limit == ANCHOR[1])].copy()
        if fit.empty or test.empty or donor in set(fit.model_id):
            raise ValueError('Invalid inner model split')
        model = _absolute().fit(fit[list(BASE_FEATURES)],
                                fit[['true_log_latency', 'true_log_requests_per_second']])
        prediction = np.asarray(model.predict(test[list(BASE_FEATURES)]))
        test['residual_latency'] = test.true_log_latency.to_numpy() - prediction[:, 0]
        test['residual_rps'] = test.true_log_requests_per_second.to_numpy() - prediction[:, 1]
        pieces.append(test[['model_id', 'workload_task', 'batch_size', 'max_length',
                            'request_concurrency', 'repeats', 'residual_latency',
                            'residual_rps']])
    return pd.concat(pieces, ignore_index=True)


def _coordinates(anchors: pd.DataFrame) -> np.ndarray:
    x = np.column_stack((np.log(anchors.batch_size.to_numpy(dtype=float)),
                         np.log(anchors.max_length.to_numpy(dtype=float)),
                         np.log1p(anchors.request_concurrency.to_numpy(dtype=float))))
    # Fixed physical scales permit held-out grids with a different coordinate
    # range; no fitted transformation needs test labels.
    return x / np.array([1.0, 1.2, 0.8])


def _smooth_covariance(anchors: pd.DataFrame, variance: np.ndarray) -> np.ndarray:
    x = _coordinates(anchors)
    d2 = np.sum((x[:, None, :] - x[None, :, :]) ** 2, axis=2)
    kernel = 0.4 + 0.6 * np.exp(-0.5 * d2)
    return kernel[:, :, None] * variance[None, None, :]


def residual_covariance(test_anchors: pd.DataFrame, donor_oof: pd.DataFrame,
                        rho: float = RHO) -> tuple[np.ndarray, dict]:
    """Construct covariance solely from training-model out-of-fold residuals.

    Empirical covariance requires a complete aligned grid in at least two
    donor models of the same task. Otherwise the smooth kernel is used alone.
    """
    if not 0.0 <= rho <= 1.0:
        raise ValueError('Shrinkage must lie in [0,1]')
    ordered = _keyed_anchor(test_anchors)
    donor = donor_oof.copy()
    donor['condition_key'] = list(map(tuple, donor[list(KEY_COLUMNS)].to_numpy()))
    target_keys = list(ordered.index)
    same_task = donor[donor.workload_task == ordered.workload_task.iloc[0]]
    matrices = []
    donor_names = []
    for model_id, group in same_task.groupby('model_id', sort=True):
        group = group.set_index('condition_key')
        if group.index.is_unique and set(target_keys).issubset(set(group.index)):
            matrices.append(group.loc[target_keys, ['residual_latency',
                                                     'residual_rps']].to_numpy(dtype=float))
            donor_names.append(model_id)
    all_residuals = donor[['residual_latency', 'residual_rps']].to_numpy(dtype=float)
    variance = np.maximum(np.nanmean(all_residuals ** 2, axis=0), 0.02 ** 2)
    smooth = _smooth_covariance(ordered, variance)
    if len(matrices) >= 2:
        surfaces = np.stack(matrices, axis=0)
        empirical = np.einsum('mio,mjo->ijo', surfaces, surfaces) / len(surfaces)
        covariance = (1.0 - rho) * empirical + rho * smooth
        mode = 'aligned_empirical_plus_kernel'
    else:
        covariance = smooth
        mode = 'kernel_fallback_no_aligned_donors'
    if not np.isfinite(covariance).all():
        raise ValueError('Nonfinite prior covariance')
    return covariance, {'mode': mode, 'aligned_donor_models': donor_names,
                        'variance': variance.tolist(),
                        'workload_conditions': len(ordered)}


def choose_covariance_probes(covariance: np.ndarray, budget: int,
                             noise_fraction: float = NOISE_FRACTION) -> np.ndarray:
    """Greedy reduction in integrated posterior variance of both outcomes."""
    cov = np.array(covariance, dtype=float, copy=True)
    if cov.ndim != 3 or cov.shape[0] != cov.shape[1] or cov.shape[2] != 2:
        raise ValueError('Expected n × n × 2 covariance')
    n = cov.shape[0]
    if not 1 <= budget <= n or noise_fraction <= 0:
        raise ValueError('Invalid probe budget or observation noise')
    if not np.allclose(cov, np.transpose(cov, (1, 0, 2))) or not np.isfinite(cov).all():
        raise ValueError('Covariance must be finite and symmetric')
    scale = np.maximum(np.diagonal(cov, axis1=0, axis2=1).mean(axis=1), 1e-8)
    noise = noise_fraction * scale
    chosen: list[int] = []
    available = np.ones(n, dtype=bool)
    for _ in range(budget):
        score = np.zeros(n)
        for t in range(2):
            score += np.sum(cov[:, :, t] ** 2, axis=0) / (
                (np.diag(cov[:, :, t]) + noise[t]) * scale[t])
        score[~available] = -np.inf
        i = int(np.argmax(score))
        chosen.append(i)
        available[i] = False
        for t in range(2):
            col = cov[:, i, t].copy()
            cov[:, :, t] -= np.outer(col, col) / (cov[i, i, t] + noise[t])
    return np.asarray(chosen, dtype=int)


def posterior_residual(covariance: np.ndarray, observed_indices: np.ndarray,
                       observed_residual: np.ndarray,
                       noise_fraction: float = NOISE_FRACTION) -> np.ndarray:
    """Condition a zero-mean log-performance residual field on k anchors."""
    cov = np.asarray(covariance, dtype=float)
    selected = np.asarray(observed_indices, dtype=int)
    values = np.asarray(observed_residual, dtype=float)
    n = cov.shape[0]
    if cov.shape != (n, n, 2) or selected.ndim != 1 or values.shape != (len(selected), 2):
        raise ValueError('Invalid posterior shapes')
    if len(selected) == 0 or len(set(selected)) != len(selected) or (selected < 0).any() or (selected >= n).any():
        raise ValueError('At least one distinct observed index required')
    prediction = np.zeros((n, 2))
    for t in range(2):
        noise = noise_fraction * max(float(np.diag(cov[:, :, t]).mean()), 1e-8)
        c_ss = cov[:, :, t][np.ix_(selected, selected)]
        prediction[:, t] = cov[:, selected, t] @ np.linalg.solve(
            c_ss + noise * np.eye(len(selected)), values[:, t])
    # Observed anchors are known exactly; the noise term regularizes transfer.
    prediction[selected] = values
    return prediction


def _predict_response(static: pd.DataFrame, inferred_anchor: pd.DataFrame,
                      conditional) -> np.ndarray:
    feed = static.copy()
    feed['anchor_log_latency'] = inferred_anchor.loc[feed.workload_group,
                                                     'latency'].to_numpy()
    feed['anchor_log_requests_per_second'] = inferred_anchor.loc[
        feed.workload_group, 'rps'].to_numpy()
    delta = np.asarray(conditional.predict(feed[list(CONDITIONAL_FEATURES)]), dtype=float)
    predicted = np.column_stack((
        feed.anchor_log_latency.to_numpy() + delta[:, 0],
        feed.anchor_log_requests_per_second.to_numpy() + delta[:, 1] +
        feed.prior_log_throughput_gain.to_numpy()))
    return predicted


def predict_sparse(test_raw: pd.DataFrame, cov_groups: list[str],
                   space_groups: list[str], covariance: np.ndarray,
                   absolute, conditional,
                   parameter_bytes: float | None = None) -> pd.DataFrame:
    """Only selected anchor target columns can affect sparse predictions."""
    validate_slates(test_raw)
    anchors = _keyed_anchor(test_raw)
    ordered_groups = anchors.workload_group.tolist()
    index_of_group = {g: i for i, g in enumerate(ordered_groups)}
    if covariance.shape != (len(anchors), len(anchors), 2):
        raise ValueError('Covariance and workload grid disagree')
    for selected in (cov_groups, space_groups):
        if not selected or len(set(selected)) != len(selected) or not set(selected).issubset(index_of_group):
            raise ValueError('Invalid sparse probes')
    # In the external evaluation, the registered size is supplied from the
    # previously known main-campaign model metadata. This avoids reading even
    # static fields from unmeasured external result JSON records.
    known_size = (registered_parameter_bytes(test_raw)
                  if parameter_bytes is None else float(parameter_bytes))
    static = static_features(test_raw, known_size)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(baseline[anchor_mask],
                                   index=static.loc[anchor_mask, 'workload_group'],
                                   columns=['latency', 'rps']).loc[ordered_groups]
    design = group_design(static)

    def observed_residual(probes: list[str]) -> tuple[np.ndarray, np.ndarray]:
        selected = np.asarray([index_of_group[g] for g in probes], dtype=int)
        observed = _anchor_rows(test_raw).loc[probes]
        measured = np.column_stack((np.log(observed.latency_p95_ms.to_numpy(dtype=float)),
                                    np.log((observed.examples_per_second /
                                            observed.batch_size).to_numpy(dtype=float))))
        residual = measured - baseline_anchor.loc[probes].to_numpy()
        return selected, residual

    cov_idx, cov_values = observed_residual(cov_groups)
    space_idx, space_values = observed_residual(space_groups)
    cov_field = posterior_residual(covariance, cov_idx, cov_values)
    smooth = _smooth_covariance(anchors,
                                np.maximum(np.diagonal(covariance, axis1=0,
                                                       axis2=1).mean(axis=1), 1e-8))
    kernel_field = posterior_residual(smooth, cov_idx, cov_values)
    space_cov_field = posterior_residual(covariance, space_idx, space_values)
    space_kernel_field = posterior_residual(smooth, space_idx, space_values)

    def mean_field(selected: np.ndarray, values: np.ndarray) -> np.ndarray:
        x = np.broadcast_to(values.mean(axis=0), (len(anchors), 2)).copy()
        x[selected] = values
        return x

    def ridge_field(selected: np.ndarray, values: np.ndarray) -> np.ndarray:
        ridge = Ridge(alpha=1.0, fit_intercept=False)
        ridge.fit(design.loc[[ordered_groups[i] for i in selected]], values)
        x = ridge.predict(design.loc[ordered_groups])
        x[selected] = values
        return x

    fields = {
        'cov_transfer': (cov_field, cov_idx),
        'kernel_transfer': (kernel_field, cov_idx),
        'space_cov_transfer': (space_cov_field, space_idx),
        'space_kernel_transfer': (space_kernel_field, space_idx),
        'cov_global_mean': (mean_field(cov_idx, cov_values), cov_idx),
        'space_global_mean': (mean_field(space_idx, space_values), space_idx),
        'space_ridge': (ridge_field(space_idx, space_values), space_idx),
    }
    result = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                     'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                     'target_throughput_rps']].copy()
    group_indices = np.asarray([index_of_group[g] for g in static.workload_group])
    for name, (field, selected) in fields.items():
        inferred_anchor = baseline_anchor + field
        pred = _predict_response(static, inferred_anchor, conditional)
        measured_rows = anchor_mask & np.isin(group_indices, selected)
        pred[measured_rows] = inferred_anchor.loc[
            static.loc[measured_rows, 'workload_group']].to_numpy()
        result[f'{name}_latency_log'] = pred[:, 0]
        result[f'{name}_rps_log'] = pred[:, 1]
    result['zero_shot_latency_log'] = baseline[:, 0]
    result['zero_shot_rps_log'] = baseline[:, 1]
    return result


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    summaries = []
    manifest = []
    predictions = []
    fold_modes = {}
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        test_raw = main[main.model_id == held_model]
        if set(train_raw.model_id) & set(test_raw.model_id):
            raise AssertionError('Outer model leakage')
        train = prepare(train_raw)
        absolute = _absolute().fit(train[list(BASE_FEATURES)],
                                   train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        anchors = _keyed_anchor(test_raw)
        covariance, info = residual_covariance(anchors, donor_oof)
        fold_modes[held_model] = info
        design = group_design(static_features(test_raw,
                                               registered_parameter_bytes(test_raw)))
        group_order = anchors.workload_group.tolist()
        all_groups = set(group_order)
        full = None
        for budget in (4, 8, 16):
            selected_idx = choose_covariance_probes(covariance, budget)
            cov_groups = [group_order[int(i)] for i in selected_idx]
            space_groups = select_probes(design, budget, 'medoids', seed=seed)
            pred = predict_sparse(test_raw, cov_groups, space_groups,
                                  covariance, absolute, conditional)
            if full is None:
                full = predict_sparse(test_raw, group_order, group_order,
                                      covariance, absolute, conditional)
            pred['full_anchor_reference_latency_log'] = (
                full.cov_transfer_latency_log.to_numpy())
            pred['full_anchor_reference_rps_log'] = full.cov_transfer_rps_log.to_numpy()
            scores = scenario_metrics(pred, test_raw, methods=METHODS)
            anchor_by_group = _anchor_rows(test_raw)
            for selector, groups in [('covariance', cov_groups),
                                     ('space_filling', space_groups)]:
                rows = anchor_by_group.loc[groups]
                manifest.append({
                    'heldout_model_id': held_model, 'budget': budget,
                    'selector': selector, 'probe_groups_json': json.dumps(groups),
                    'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                    'covariance_mode': info['mode'],
                    'gpu_wall_probe_seconds': float((rows.replica_count * rows.wall_seconds).sum()),
                })
            costs = {s: next(r for r in manifest[::-1] if r['heldout_model_id'] == held_model and
                             r['budget'] == budget and r['selector'] == s)
                     for s in ('covariance', 'space_filling')}
            full_cost = float((anchor_by_group.replica_count * anchor_by_group.wall_seconds).sum())
            for method, vals in scores.items():
                selector = ('none' if method == 'zero_shot' else
                            'full' if method == 'full_anchor_reference' else
                            'space_filling' if method.startswith('space_') else 'covariance')
                cost = (0.0 if selector == 'none' else full_cost if selector == 'full'
                        else costs[selector]['gpu_wall_probe_seconds'])
                summaries.append({'heldout_model_id': held_model,
                                  'budget': budget, 'method': method,
                                  'selector': selector,
                                  'covariance_mode': info['mode'],
                                  'gpu_wall_probe_seconds': cost, **vals})
            pred['heldout_model_id'] = held_model
            pred['budget'] = budget
            pred['covariance_probe_groups_json'] = json.dumps(cov_groups)
            pred['space_probe_groups_json'] = json.dumps(space_groups)
            predictions.append(pred)
        print(f'Finished {held_model}: {len(group_order)} conditions, {info["mode"]}', flush=True)
    metrics = pd.DataFrame(summaries)
    metrics.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifest).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(output / 'predictions.parquet', index=False)
    macro = metrics.groupby(['budget', 'method', 'selector'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        missed_feasible_groups=('missed_feasible_groups', 'sum'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'))
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__, 'seed': seed,
        'outer_split': 'leave one model_id out; inner donor residuals are also leave-one-donor-model-out',
        'heldout_observations': 'only k selected GPU1/two-core anchor measurements',
        'fixed_scored_population': 'all seven nonanchor resource candidates for every held-out workload',
        'prior': {'empirical_weight': 1.0 - RHO, 'smooth_kernel_weight': RHO,
                  'kernel': '0.4 constant + 0.6 RBF on fixed-scaled log batch/length/log1p concurrency',
                  'observation_noise_fraction': NOISE_FRACTION},
        'registration_metadata_assumption': 'model_parameter_bytes is available before profiling',
        'fallback_caveat': 'Qwen has no aligned same-task donor workload grid; only smooth kernel is used',
        'fold_prior_modes': fold_modes,
        'macro_per_model_and_decision_totals': macro.to_dict(orient='records'),
        'slo_note': 'SLOs in this dataset were generated by collection scripts and are not an independent user-request distribution',
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['macro_per_model_and_decision_totals']:
        if row['method'] in ('cov_transfer', 'space_global_mean',
                             'space_ridge', 'zero_shot', 'full_anchor_reference'):
            print(row, flush=True)
