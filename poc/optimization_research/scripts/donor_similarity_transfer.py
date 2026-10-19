"""Exploratory donor-similarity weighting of a sparse residual prior.

Every outer held-out model is excluded from absolute/conditional training. Each
historical donor's residual surface is itself computed by an inner leave-one-
donor-model-out fit. Only the eight selected held-out GPU1/two-core anchors
enter donor similarity; all other held-out outcomes are scoring labels only.

Donor weights are fixed softmax functions of squared, donor-normalized anchor
error. The empirical second moment or centered covariance is then shrunk by
the same 50% smooth kernel used by the pooled covariance comparator. This is a
negative/positive hypothesis test, not a claim of algorithmic originality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from covariance_bias_ablation_external import predict_external
from cross_model_covariance import (
    KEY_COLUMNS, RHO, _absolute, _coordinates, _keyed_anchor,
    _smooth_covariance, predict_sparse, residual_covariance,
    training_oof_anchor_residuals,
)
from cross_model_covariance_external import _base_kernel, continuous_prior
from sparse_anchor_transfer import (
    CAMPAIGN, EXTERNAL_CAMPAIGNS, _anchor_rows, group_design,
    registered_parameter_bytes, scenario_metrics, select_probes,
    static_features, validate_slates,
)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('weighted_raw', 'weighted_centered_mean', 'pooled_raw',
           'pooled_centered_zero_mean', 'smooth_kernel', 'space_ridge')
TEMPERATURE = 1.0
INTERPOLATION_RIDGE = 0.1


def donor_surfaces(target_raw: pd.DataFrame, donor_oof: pd.DataFrame,
                   interpolate: bool = False) -> tuple[np.ndarray, list[str]]:
    """Align training-only donor residuals to target static coordinates."""
    target = _keyed_anchor(target_raw)
    task = target.workload_task.iloc[0]
    if target.workload_task.nunique() != 1:
        raise ValueError('One held-out task is required')
    donor = donor_oof[donor_oof.workload_task == task].copy()
    if donor.empty:
        return np.empty((0, len(target), 2)), []
    donor['condition_key'] = list(map(tuple, donor[list(KEY_COLUMNS)].to_numpy()))
    groups = {name: group.set_index('condition_key')
              for name, group in donor.groupby('model_id', sort=True)}
    if len(groups) < 2:
        return np.empty((0, len(target), 2)), []
    names = sorted(groups)
    if not interpolate:
        keys = list(target.index)
        names = [name for name in names
                 if groups[name].index.is_unique and
                 set(keys).issubset(set(groups[name].index))]
        if len(names) < 2:
            return np.empty((0, len(target), 2)), []
        surfaces = np.stack([
            groups[name].loc[keys, ['residual_latency', 'residual_rps']].to_numpy(dtype=float)
            for name in names
        ])
        return surfaces, names
    if any(not group.index.is_unique for group in groups.values()):
        raise ValueError('Duplicate donor condition key')
    common = set.intersection(*(set(group.index) for group in groups.values()))
    if not common:
        return np.empty((0, len(target), 2)), []
    keys = sorted(common)
    reference = groups[names[0]].loc[keys]
    x_reference = _coordinates(reference)
    x_target = _coordinates(target)
    interpolation = np.linalg.solve(
        _base_kernel(x_reference, x_reference) +
        INTERPOLATION_RIDGE * np.eye(len(keys)),
        _base_kernel(x_target, x_reference).T,
    ).T
    surfaces = np.stack([
        interpolation @ groups[name].loc[
            keys, ['residual_latency', 'residual_rps']].to_numpy(dtype=float)
        for name in names
    ])
    return surfaces, names


def donor_weights(surfaces: np.ndarray, selected: np.ndarray,
                  observed_residual: np.ndarray,
                  temperature: float = TEMPERATURE) -> tuple[np.ndarray, np.ndarray]:
    """Fixed Gaussian similarity in donor-scaled selected-anchor space."""
    if surfaces.ndim != 3 or surfaces.shape[2] != 2:
        raise ValueError('Expected donor × conditions × 2 residual surfaces')
    selected = np.asarray(selected, dtype=int)
    observed = np.asarray(observed_residual, dtype=float)
    if (len(surfaces) < 2 or selected.ndim != 1 or not len(selected) or
            len(set(selected)) != len(selected) or
            np.any(selected < 0) or np.any(selected >= surfaces.shape[1]) or
            observed.shape != (len(selected), 2) or temperature <= 0):
        raise ValueError('Invalid donor similarity input')
    at_probe = surfaces[:, selected, :]
    variance = np.maximum(np.mean(at_probe ** 2, axis=(0, 1)), 0.05 ** 2)
    normalized_distance = np.mean(
        (at_probe - observed[None, :, :]) ** 2 / variance[None, None, :],
        axis=(1, 2),
    )
    logits = -0.5 * normalized_distance / temperature
    unnormalized = np.exp(logits - logits.max())
    weights = unnormalized / unnormalized.sum()
    if not np.isfinite(weights).all():
        raise ValueError('Nonfinite donor weights')
    return weights, normalized_distance


def weighted_prior(target_raw: pd.DataFrame, donor_oof: pd.DataFrame,
                   selected: np.ndarray, observed_residual: np.ndarray,
                   interpolate: bool = False) -> tuple[dict[str, np.ndarray], np.ndarray, dict]:
    """Form weighted second moment and centered covariance with a fixed kernel shrinkage."""
    target = _keyed_anchor(target_raw)
    donor = donor_oof[['residual_latency', 'residual_rps']].to_numpy(dtype=float)
    variance = np.maximum(np.mean(donor ** 2, axis=0), 0.02 ** 2)
    smooth = _smooth_covariance(target, variance)
    surfaces, names = donor_surfaces(target_raw, donor_oof, interpolate)
    mean = np.zeros((len(target), 2))
    raw = smooth.copy()
    centered = smooth.copy()
    pooled_centered = smooth.copy()
    weights = np.array([])
    distances = np.array([])
    mode = 'smooth_kernel_fallback'
    if len(names) >= 2:
        pooled_mean = surfaces.mean(axis=0)
        pooled_centered_surfaces = surfaces - pooled_mean[None, :, :]
        pooled_centered = (1 - RHO) * np.einsum(
            'mio,mjo->ijo', pooled_centered_surfaces,
            pooled_centered_surfaces) / len(surfaces) + RHO * smooth
        weights, distances = donor_weights(surfaces, selected,
                                           observed_residual)
        raw_empirical = np.einsum('m,mio,mjo->ijo', weights, surfaces, surfaces)
        mean = np.einsum('m,mio->io', weights, surfaces)
        centered_surfaces = surfaces - mean[None, :, :]
        centered_empirical = np.einsum('m,mio,mjo->ijo',
                                       weights, centered_surfaces,
                                       centered_surfaces)
        raw = (1 - RHO) * raw_empirical + RHO * smooth
        centered = (1 - RHO) * centered_empirical + RHO * smooth
        np.testing.assert_allclose(raw, centered + (1 - RHO) *
                                   np.einsum('io,jo->ijo', mean, mean))
        mode = 'weighted_aligned_donor_surfaces' if not interpolate else 'weighted_interpolated_donor_surfaces'
    for matrix in (raw, centered, pooled_centered):
        if not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.transpose(1, 0, 2)):
            raise ValueError('Invalid weighted covariance')
    return {'raw': raw, 'centered': centered, 'smooth': smooth,
            'pooled_centered': pooled_centered}, mean, {
        'mode': mode, 'donor_model_ids': names,
        'donor_weights': weights.tolist(),
        'normalized_anchor_distance': distances.tolist(),
        'effective_donors': float(1 / np.sum(weights ** 2)) if len(weights) else 0.0,
    }


def selected_anchor_residual(test_raw: pd.DataFrame, probe_groups: list[str],
                             absolute, known_size: float) -> tuple[np.ndarray, np.ndarray]:
    """Read held-out outcome columns solely from explicitly selected anchors."""
    anchors = _keyed_anchor(test_raw)
    order = anchors.workload_group.tolist()
    indices = {group: i for i, group in enumerate(order)}
    if not probe_groups or len(probe_groups) != len(set(probe_groups)) or not set(probe_groups).issubset(indices):
        raise ValueError('Invalid selected probes')
    selected = np.asarray([indices[group] for group in probe_groups], dtype=int)
    static = static_features(test_raw, known_size)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(
        baseline[anchor_mask], index=static.loc[anchor_mask, 'workload_group'],
        columns=['latency', 'rps'],
    ).loc[probe_groups]
    observed = _anchor_rows(test_raw).loc[probe_groups]
    logged = np.column_stack((
        np.log(observed.latency_p95_ms.to_numpy(dtype=float)),
        np.log((observed.examples_per_second / observed.batch_size).to_numpy(dtype=float)),
    ))
    return selected, logged - baseline_anchor.to_numpy()


def _evaluate_fold(test_raw: pd.DataFrame, probes: list[str], donor_oof: pd.DataFrame,
                   absolute, conditional, known_size: float,
                   interpolate: bool) -> tuple[pd.DataFrame, dict]:
    selected, observed = selected_anchor_residual(test_raw, probes, absolute, known_size)
    weighted, mean, info = weighted_prior(test_raw, donor_oof,
                                           selected, observed, interpolate)
    if interpolate:
        pooled, pooled_info = continuous_prior(test_raw, donor_oof)
    else:
        pooled, pooled_info = residual_covariance(test_raw, donor_oof)
    base = predict_sparse(test_raw, probes, probes, pooled,
                          absolute, conditional, known_size)
    alternative = predict_external(test_raw, probes, weighted, mean,
                                   absolute, conditional, known_size)
    centered_baseline = predict_external(
        test_raw, probes,
        {'raw': weighted['pooled_centered'],
         'centered': weighted['pooled_centered'],
         'smooth': weighted['smooth']},
        np.zeros_like(mean), absolute, conditional, known_size,
    )
    out = base.copy()
    for source, target in (
            ('raw_second_moment', 'weighted_raw'),
            ('centered_cov_plus_donor_mean', 'weighted_centered_mean')):
        out[f'{target}_latency_log'] = alternative[f'{source}_latency_log'].to_numpy()
        out[f'{target}_rps_log'] = alternative[f'{source}_rps_log'].to_numpy()
    for source, target in (('space_cov_transfer', 'pooled_raw'),
                           ('space_kernel_transfer', 'smooth_kernel')):
        out[f'{target}_latency_log'] = out[f'{source}_latency_log']
        out[f'{target}_rps_log'] = out[f'{source}_rps_log']
    out['pooled_centered_zero_mean_latency_log'] = (
        centered_baseline['raw_second_moment_latency_log'].to_numpy())
    out['pooled_centered_zero_mean_rps_log'] = (
        centered_baseline['raw_second_moment_rps_log'].to_numpy())
    info['pooled_prior_mode'] = pooled_info['mode']
    return out, info


def _aggregate(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    return frame.groupby(['campaign', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        scored_groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_probe_gpu_wall_seconds=('gpu_wall_probe_seconds', 'mean'),
    ).to_dict(orient='records')


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    source = pd.read_parquet(snapshot)
    usable = source[source.eligible & source.device.eq('cuda')]
    main = usable[usable.campaign == CAMPAIGN]
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)]
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError('Main/external workload groups overlap')
    records, manifests, predictions, modes = [], [], [], {}
    for held_model in sorted(main.model_id.unique()):
        training_raw = main[main.model_id != held_model]
        held_main = main[main.model_id == held_model]
        held_external = external[external.model_id == held_model]
        if held_model in set(training_raw.model_id):
            raise AssertionError('Outer held-out model leakage')
        training = prepare(training_raw)
        absolute = _absolute().fit(training[list(BASE_FEATURES)],
                                   training[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(training, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(training)
        if held_model in set(donor_oof.model_id):
            raise AssertionError('Held-out model in donor residuals')
        known_size = registered_parameter_bytes(held_main)
        design = group_design(static_features(held_main, known_size))
        probes = select_probes(design, 8, 'medoids', seed=seed)
        anchor_rows = _anchor_rows(held_main)
        gpu_wall = float((anchor_rows.loc[probes].replica_count *
                          anchor_rows.loc[probes].wall_seconds).sum())
        main_pred, main_info = _evaluate_fold(
            held_main, probes, donor_oof, absolute, conditional,
            known_size, interpolate=False,
        )
        modes[held_model] = {'main': main_info}
        for method, values in scenario_metrics(main_pred, held_main, METHODS).items():
            records.append({'heldout_model_id': held_model, 'campaign': CAMPAIGN,
                            'method': method, 'gpu_wall_probe_seconds': gpu_wall,
                            **values})
        main_pred['heldout_model_id'] = held_model
        predictions.append(main_pred)
        if not held_external.empty:
            all_test = pd.concat((held_main, held_external), ignore_index=True)
            external_pred, external_info = _evaluate_fold(
                all_test, probes, donor_oof, absolute, conditional,
                known_size, interpolate=True,
            )
            modes[held_model]['external'] = external_info
            external_pred = external_pred[
                external_pred.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
            if len(external_pred) != len(held_external):
                raise AssertionError('External prediction count changed')
            for campaign in EXTERNAL_CAMPAIGNS:
                subset = external_pred[external_pred.campaign == campaign]
                truth = held_external[held_external.campaign == campaign]
                if subset.empty:
                    continue
                for method, values in scenario_metrics(subset, truth, METHODS).items():
                    records.append({'heldout_model_id': held_model,
                                    'campaign': campaign, 'method': method,
                                    'gpu_wall_probe_seconds': gpu_wall, **values})
            external_pred['heldout_model_id'] = held_model
            predictions.append(external_pred)
        manifests.append({'heldout_model_id': held_model,
                          'training_model_ids_json': json.dumps(sorted(training_raw.model_id.unique())),
                          'main_probe_groups_json': json.dumps(probes),
                          'main_probe_budget': len(probes),
                          'external_probe_budget': 0,
                          'gpu_wall_probe_seconds': gpu_wall,
                          'main_donor_weights_json': json.dumps(main_info['donor_weights']),
                          'main_donor_ids_json': json.dumps(main_info['donor_model_ids'])})
        print(f'Finished {held_model}: {main_info["mode"]}', flush=True)
    scores = pd.DataFrame(records)
    scores.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed': seed, 'main_probe_budget': 8, 'external_probe_budget': 0,
        'outer_split': 'leave one model_id out',
        'donor_residual_split': 'inner leave-one-donor-model-out',
        'similarity': 'softmax(-0.5 * mean normalized squared selected-anchor residual distance); fixed temperature 1',
        'shrinkage': {'donor_empirical': 1 - RHO, 'smooth_kernel': RHO},
        'external_interpolation_ridge': INTERPOLATION_RIDGE,
        'unprobed_outcomes_accessed_before_prediction': False,
        'fold_prior_modes': modes,
        'aggregate': _aggregate(scores),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/donor_similarity_transfer_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['aggregate']:
        print(row, flush=True)
