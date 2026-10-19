"""Explore full workload-by-resource residual transfer with eight anchor probes.

The empirical prior uses only inner leave-one-model-out donor residuals. At
prediction time, the held-out model contributes eight measured GPU1/core2
anchors; all other performance labels are reserved for scoring. This script is
an ablation, not a claim of new Gaussian-process methodology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge

from conditional_response import ANCHOR, BASE_FEATURES, CONDITIONAL_FEATURES, fit_model, prepare
from cross_model_covariance import KEY_COLUMNS, _absolute, _coordinates
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features, validate_slates)

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = tuple((gpu, cpu) for gpu in (1, 2) for cpu in (2, 4, 8, 16))
BUDGET = 8
RHO = 0.5
NOISE_FRACTION = 0.05
JOINT_METHODS = ('joint_empirical', 'joint_kernel', 'direct_global', 'direct_ridge')
REFERENCE_METHODS = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
                     'space_global_mean', 'zero_shot', 'full_anchor_reference')
METHODS = JOINT_METHODS + REFERENCE_METHODS


def ordered_grid(frame: pd.DataFrame, parameter_bytes: float | None = None) -> tuple[pd.DataFrame, list[tuple]]:
    """Put static rows in condition-key then resource order, without outcomes."""
    validate_slates(frame)
    anchors = _anchor_rows(frame).copy()
    anchors['condition_key'] = list(map(tuple, anchors[list(KEY_COLUMNS)].to_numpy()))
    if anchors.condition_key.duplicated().any():
        raise ValueError('Repeated condition key')
    anchors = anchors.sort_values('condition_key')
    keys = anchors.condition_key.tolist()
    groups = anchors.workload_group.tolist()
    size = registered_parameter_bytes(frame) if parameter_bytes is None else parameter_bytes
    static = static_features(frame, size)
    requested = [(group, gpu, cpu) for group in groups for gpu, cpu in RESOURCES]
    static = static.set_index(['workload_group', 'replica_count', 'cpu_core_limit']).loc[requested]
    static = static.reset_index()
    if len(static) != len(requested) or static.execution_id.duplicated().any():
        raise ValueError('Incomplete ordered grid')
    return static, keys


def baseline_predictions(static: pd.DataFrame, absolute, conditional) -> tuple[np.ndarray, np.ndarray]:
    """Absolute and zero-observation conditional baselines on an ordered grid."""
    if len(static) % len(RESOURCES) or not np.all(static.replica_count.iloc[::8] == ANCHOR[0]) or not np.all(static.cpu_core_limit.iloc[::8] == ANCHOR[1]):
        raise ValueError('Expected an ordered eight-resource grid')
    direct = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor = direct[::8]
    feed = static.copy()
    feed['anchor_log_latency'] = np.repeat(anchor[:, 0], 8)
    feed['anchor_log_requests_per_second'] = np.repeat(anchor[:, 1], 8)
    delta = np.asarray(conditional.predict(feed[list(CONDITIONAL_FEATURES)]), dtype=float)
    response = np.column_stack((feed.anchor_log_latency.to_numpy() + delta[:, 0],
                                feed.anchor_log_requests_per_second.to_numpy() +
                                delta[:, 1] + feed.prior_log_throughput_gain.to_numpy()))
    response[::8] = anchor
    return direct, response


def training_oof_surfaces(train_raw: pd.DataFrame, train_prepared: pd.DataFrame,
                          seed: int = 42) -> list[dict]:
    """Donor full-grid residuals from models that excluded that donor entirely."""
    donors = []
    for donor_id in sorted(train_raw.model_id.unique()):
        fit = train_prepared[train_prepared.model_id != donor_id]
        donor_raw = train_raw[train_raw.model_id == donor_id]
        if donor_id in set(fit.model_id) or fit.empty:
            raise AssertionError('Inner model leakage')
        absolute = _absolute().fit(fit[list(BASE_FEATURES)],
                                   fit[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(fit, 'anchor_conditioned', seed)
        static, keys = ordered_grid(donor_raw)
        _, baseline = baseline_predictions(static, absolute, conditional)
        indexed = donor_raw.set_index('execution_id')
        aligned = indexed.loc[static.execution_id]
        truth = np.column_stack((np.log(aligned.latency_p95_ms.to_numpy(dtype=float)),
                                 np.log((aligned.examples_per_second /
                                         aligned.batch_size).to_numpy(dtype=float))))
        residual = (truth - baseline).reshape(len(keys), 8, 2)
        donors.append({'model_id': donor_id, 'task': str(aligned.workload_task.iloc[0]),
                       'keys': keys, 'residual': residual})
    return donors


def joint_prior(held_static: pd.DataFrame, held_keys: list[tuple],
                donors: list[dict], rho: float = RHO) -> tuple[np.ndarray, np.ndarray,
                                                                 np.ndarray, np.ndarray, dict]:
    """Empirical cross-resource/anchor covariance, plus a smooth-kernel control.

    Returns (empirical cross, empirical anchor, kernel cross, kernel anchor).
    The covariance uses second moments, so donor-wide mean residuals can also
    contribute. The held-out performance outcomes are never accepted here.
    """
    if not 0 <= rho <= 1 or len(held_static) != 8 * len(held_keys):
        raise ValueError('Invalid grid or shrinkage')
    n = len(held_keys)
    task = held_static.workload_task.iloc[0]
    aligned = []
    aligned_names = []
    for donor in donors:
        if donor['task'] != task:
            continue
        lookup = {key: i for i, key in enumerate(donor['keys'])}
        if len(lookup) == len(donor['keys']) and all(key in lookup for key in held_keys):
            aligned.append(donor['residual'][[lookup[key] for key in held_keys]])
            aligned_names.append(donor['model_id'])
    all_residuals = np.concatenate([d['residual'] for d in donors], axis=0)
    variance = np.maximum(np.mean(all_residuals ** 2, axis=0), 0.02 ** 2)  # 8 resources × 2 outcomes
    coords = _coordinates(held_static.iloc[::8])
    d2 = np.sum((coords[:, None] - coords[None, :]) ** 2, axis=2)
    workload_kernel = 0.4 + 0.6 * np.exp(-0.5 * d2)
    # Fixed resource similarity. No validation label or held-out outcome tunes it.
    resource_coords = np.array([(np.log(gpu), np.log(cpu / 2)) for gpu, cpu in RESOURCES])
    resource_d2 = ((resource_coords / [0.7, 1.5]) ** 2).sum(axis=1)
    resource_similarity = np.exp(-0.5 * resource_d2)
    smooth_cross = np.empty((n * 8, n, 2))
    for resource in range(8):
        smooth_cross[resource::8] = (workload_kernel[:, :, None] *
            resource_similarity[resource] *
            np.sqrt(variance[resource] * variance[0])[None, None, :])
    smooth_anchor = smooth_cross[::8].copy()
    if len(aligned) >= 2:
        surfaces = np.stack(aligned).reshape(len(aligned), n * 8, 2)
        anchor_surfaces = surfaces[:, ::8, :]
        empirical_cross = np.einsum('mco,mao->cao', surfaces, anchor_surfaces) / len(aligned)
        empirical_anchor = empirical_cross[::8]
        cross = (1 - rho) * empirical_cross + rho * smooth_cross
        anchor = (1 - rho) * empirical_anchor + rho * smooth_anchor
        mode = 'aligned_joint_empirical_plus_kernel'
    else:
        cross, anchor = smooth_cross.copy(), smooth_anchor.copy()
        mode = 'kernel_fallback_no_aligned_donors'
    if not np.isfinite(cross).all() or not np.isfinite(anchor).all():
        raise ValueError('Nonfinite joint prior')
    return cross, anchor, smooth_cross, smooth_anchor, {
        'mode': mode, 'aligned_donor_models': aligned_names,
        'workload_conditions': n,
        'empirical_weight': 1 - rho if aligned else 0.0,
    }


def posterior_joint(cross: np.ndarray, anchor: np.ndarray, indices: np.ndarray,
                    observed_residual: np.ndarray,
                    noise_fraction: float = NOISE_FRACTION) -> np.ndarray:
    """Condition all resource cells on selected anchor residuals."""
    n = anchor.shape[0]
    indices = np.asarray(indices, dtype=int)
    residual = np.asarray(observed_residual, dtype=float)
    if anchor.shape != (n, n, 2) or cross.shape != (n * 8, n, 2) or residual.shape != (len(indices), 2):
        raise ValueError('Invalid joint posterior shapes')
    if len(indices) == 0 or len(set(indices)) != len(indices) or (indices < 0).any() or (indices >= n).any():
        raise ValueError('Invalid measured anchors')
    out = np.empty((n * 8, 2))
    for outcome in range(2):
        noise = noise_fraction * max(float(np.diag(anchor[:, :, outcome]).mean()), 1e-8)
        selected = anchor[:, :, outcome][np.ix_(indices, indices)]
        out[:, outcome] = cross[:, indices, outcome] @ np.linalg.solve(
            selected + noise * np.eye(len(indices)), residual[:, outcome])
    out[8 * indices] = residual
    return out


def predict_joint(held_raw: pd.DataFrame, probe_groups: list[str], donors: list[dict],
                  absolute, conditional, parameter_bytes: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Read only explicitly probed held-out anchor performance fields."""
    static, keys = ordered_grid(held_raw, parameter_bytes)
    groups = static.workload_group.iloc[::8].tolist()
    group_index = {group: i for i, group in enumerate(groups)}
    if len(probe_groups) != BUDGET or len(set(probe_groups)) != BUDGET or not set(probe_groups).issubset(group_index):
        raise ValueError(f'Exactly {BUDGET} distinct anchor probes required')
    indices = np.array([group_index[group] for group in probe_groups])
    direct, response = baseline_predictions(static, absolute, conditional)
    measured = _anchor_rows(held_raw).loc[probe_groups]
    observed = np.column_stack((np.log(measured.latency_p95_ms.to_numpy(dtype=float)),
                                np.log((measured.examples_per_second /
                                        measured.batch_size).to_numpy(dtype=float))))
    anchor_residual = observed - direct[8 * indices]
    prior = joint_prior(static, keys, donors)
    predicted = {
        'joint_empirical': response + posterior_joint(prior[0], prior[1], indices, anchor_residual),
        'joint_kernel': response + posterior_joint(prior[2], prior[3], indices, anchor_residual),
        'direct_global': direct + anchor_residual.mean(axis=0),
    }
    design = group_design(static)
    ridge = Ridge(alpha=1.0, fit_intercept=False).fit(
        design.loc[probe_groups].to_numpy(), anchor_residual)
    predicted['direct_ridge'] = direct + np.repeat(ridge.predict(design.loc[groups].to_numpy()), 8, axis=0)
    for value in predicted.values():
        value[8 * indices] = observed
    out = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                  'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                  'target_throughput_rps']].copy()
    for method, value in predicted.items():
        out[f'{method}_latency_log'] = value[:, 0]
        out[f'{method}_rps_log'] = value[:, 1]
    return out, prior[4]


def run(snapshot: Path, output: Path, reference: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible & (raw.device == 'cuda')].copy()
    validate_slates(main)
    reference_predictions = pd.read_parquet(reference / 'predictions.parquet')
    reference_predictions = reference_predictions[reference_predictions.budget == BUDGET]
    reference_manifest = pd.read_csv(reference / 'split_manifest.csv')
    reference_manifest = reference_manifest[(reference_manifest.budget == BUDGET) &
                                            (reference_manifest.selector == 'space_filling')]
    if reference_predictions.execution_id.duplicated().any() or len(reference_manifest) != main.model_id.nunique():
        raise ValueError('Reference population or probes are incomplete')
    rows, predictions, manifests, modes = [], [], [], {}
    for held_id in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_id]
        held_raw = main[main.model_id == held_id]
        prepared = prepare(train_raw)
        absolute = _absolute().fit(prepared[list(BASE_FEATURES)],
                                   prepared[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(prepared, 'anchor_conditioned', seed)
        donor_surfaces = training_oof_surfaces(train_raw, prepared, seed)
        design = group_design(static_features(held_raw, registered_parameter_bytes(held_raw)))
        probe_groups = select_probes(design, BUDGET, 'medoids', seed=seed)
        ref_row = reference_manifest[reference_manifest.heldout_model_id == held_id]
        if len(ref_row) != 1 or probe_groups != json.loads(ref_row.iloc[0].probe_groups_json):
            raise AssertionError('Probe selection differs from reference')
        pred, info = predict_joint(held_raw, probe_groups, donor_surfaces,
                                   absolute, conditional)
        ref = reference_predictions[reference_predictions.heldout_model_id == held_id]
        ref_columns = ['execution_id'] + [f'{method}_{outcome}_log'
            for method in REFERENCE_METHODS for outcome in ('latency', 'rps')]
        pred = pred.merge(ref[ref_columns], on='execution_id', validate='one_to_one')
        if len(pred) != len(held_raw) or set(pred.execution_id) != set(held_raw.execution_id):
            raise AssertionError('Scored population changed')
        anchor_rows = _anchor_rows(held_raw).loc[probe_groups]
        cost = float((anchor_rows.replica_count * anchor_rows.wall_seconds).sum())
        if not np.isclose(cost, ref_row.iloc[0].gpu_wall_probe_seconds):
            raise AssertionError('Probe GPU-wall cost differs from reference')
        results = scenario_metrics(pred, held_raw, methods=METHODS)
        for method, vals in results.items():
            rows.append({'heldout_model_id': held_id, 'method': method,
                         'budget': BUDGET, 'prior_mode': info['mode'],
                         'gpu_wall_probe_seconds': 0.0 if method == 'zero_shot' else
                         float((_anchor_rows(held_raw).replica_count *
                                _anchor_rows(held_raw).wall_seconds).sum())
                         if method == 'full_anchor_reference' else cost,
                         **vals})
        pred['heldout_model_id'] = held_id
        predictions.append(pred)
        manifests.append({'heldout_model_id': held_id, 'budget': BUDGET,
                          'probe_groups_json': json.dumps(probe_groups),
                          'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                          'prior_mode': info['mode'], 'aligned_donor_models_json': json.dumps(info['aligned_donor_models']),
                          'gpu_wall_probe_seconds': cost})
        modes[held_id] = info
        print(f'Finished {held_id}: {info["mode"]}', flush=True)
    scores = pd.DataFrame(rows)
    scores.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(output / 'predictions.parquet', index=False)
    macro = scores.groupby('method', as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        feasible_groups=('feasible_groups', 'sum'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean')).to_dict('records')
    metadata = {'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                'reference_predictions_sha256': hashlib.sha256((reference / 'predictions.parquet').read_bytes()).hexdigest(),
                'reference_manifest_sha256': hashlib.sha256((reference / 'split_manifest.csv').read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'sklearn_version': sklearn.__version__, 'seed': seed,
                'outer_split': 'leave one model_id out',
                'inner_split': 'leave one donor model_id out for each full resource residual surface',
                'heldout_observations': f'only {BUDGET} selected GPU1/two-core anchors',
                'fixed_scored_population': 'all seven nonanchor resource candidates for every workload',
                'joint_prior': {'empirical_weight': 1 - RHO, 'smooth_weight': RHO,
                                'observation_noise_fraction': NOISE_FRACTION},
                'registration_metadata_assumption': 'model_parameter_bytes is available before profiling',
                'fold_modes': modes, 'macro_per_model_and_decision_totals': macro,
                'slo_note': 'The dataset SLOs were generated by the collection scripts'}
    (output / 'metrics.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/joint_resource_residual_v1')
    parser.add_argument('--reference', type=Path, default=ROOT / 'artifacts/cross_model_covariance_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    run(args.snapshot, args.output, args.reference, args.seed)
