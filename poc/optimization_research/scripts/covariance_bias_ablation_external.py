"""External-condition ablation of donor mean versus centered residual covariance.

Only eight held-model main-campaign anchors are observed. External outcomes
are used by scenario_metrics after all predictions have been generated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from covariance_bias_ablation import METHODS
from cross_model_covariance import (
    RHO, _absolute, _keyed_anchor, _predict_response, _smooth_covariance,
    posterior_residual, training_oof_anchor_residuals,
)
from cross_model_covariance_external import _base_kernel, continuous_prior
from sparse_anchor_transfer import (
    CAMPAIGN, EXTERNAL_CAMPAIGNS, _anchor_rows, group_design,
    registered_parameter_bytes, scenario_metrics, select_probes,
    static_features, validate_slates,
)


ROOT = Path(__file__).resolve().parents[1]


def decomposed_continuous_prior(test_raw: pd.DataFrame,
                                donor_oof: pd.DataFrame,
                                ridge: float = 0.1) -> tuple[dict[str, np.ndarray], np.ndarray, dict]:
    """Interpolate donor surfaces, then decompose their second moment."""
    target = _keyed_anchor(test_raw)
    raw, info = continuous_prior(test_raw, donor_oof, ridge=ridge)
    scale = np.maximum(np.mean(
        donor_oof[['residual_latency', 'residual_rps']].to_numpy(dtype=float) ** 2,
        axis=0,
    ), 0.02 ** 2)
    smooth = _smooth_covariance(target, scale)
    mean = np.zeros((len(target), 2))
    centered = smooth.copy()
    if info['aligned_donor_models']:
        donor = donor_oof.copy()
        donor['condition_key'] = list(map(tuple, donor[[
            'workload_task', 'batch_size', 'max_length',
            'request_concurrency', 'repeats']].to_numpy()))
        same = donor[donor.workload_task == target.workload_task.iloc[0]]
        groups = {name: group.set_index('condition_key')
                  for name, group in same.groupby('model_id', sort=True)}
        keys = sorted(set.intersection(*(set(group.index) for group in groups.values())))
        reference = next(iter(groups.values())).loc[keys]
        x_ref = np.column_stack((
            np.log(reference.batch_size.to_numpy(dtype=float)),
            np.log(reference.max_length.to_numpy(dtype=float)),
            np.log1p(reference.request_concurrency.to_numpy(dtype=float)),
        )) / np.array([1.0, 1.2, 0.8])
        x_target = np.column_stack((
            np.log(target.batch_size.to_numpy(dtype=float)),
            np.log(target.max_length.to_numpy(dtype=float)),
            np.log1p(target.request_concurrency.to_numpy(dtype=float)),
        )) / np.array([1.0, 1.2, 0.8])
        k_rr = _base_kernel(x_ref, x_ref)
        k_tr = _base_kernel(x_target, x_ref)
        interpolation = np.linalg.solve(
            k_rr + ridge * np.eye(len(keys)), k_tr.T
        ).T
        surfaces = np.stack([
            interpolation @ groups[name].loc[
                keys, ['residual_latency', 'residual_rps']].to_numpy(dtype=float)
            for name in info['aligned_donor_models']
        ])
        mean = surfaces.mean(axis=0)
        centered_surfaces = surfaces - mean
        centered_empirical = np.einsum(
            'mio,mjo->ijo', centered_surfaces, centered_surfaces
        ) / len(surfaces)
        centered = (1.0 - RHO) * centered_empirical + RHO * smooth
        reconstructed = centered + (1.0 - RHO) * np.einsum(
            'io,jo->ijo', mean, mean
        )
        np.testing.assert_allclose(raw, reconstructed, atol=1e-8, rtol=1e-8)
    return {'raw': raw, 'centered': centered, 'smooth': smooth}, mean, info


def predict_external(test_raw: pd.DataFrame, probe_groups: list[str],
                     priors: dict[str, np.ndarray], donor_mean: np.ndarray,
                     absolute, conditional, known_parameter_bytes: float) -> pd.DataFrame:
    """Use known main anchors to predict every candidate at new conditions."""
    validate_slates(test_raw)
    anchors = _keyed_anchor(test_raw)
    order = anchors.workload_group.tolist()
    index_of = {group: i for i, group in enumerate(order)}
    if not probe_groups or len(probe_groups) != len(set(probe_groups)) or not set(probe_groups).issubset(index_of):
        raise ValueError('Invalid main-campaign probe selection')
    static = static_features(test_raw, known_parameter_bytes)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(
        baseline[anchor_mask], index=static.loc[anchor_mask, 'workload_group'],
        columns=['latency', 'rps'],
    ).loc[order]
    selected = np.asarray([index_of[g] for g in probe_groups], dtype=int)
    measured = _anchor_rows(test_raw).loc[probe_groups]
    observed = np.column_stack((
        np.log(measured.latency_p95_ms.to_numpy(dtype=float)),
        np.log((measured.examples_per_second / measured.batch_size).to_numpy(dtype=float)),
    ))
    observed_residual = observed - baseline_anchor.loc[probe_groups].to_numpy()
    specifications = {
        'raw_second_moment': (priors['raw'], np.zeros_like(donor_mean)),
        'centered_cov_plus_donor_mean': (priors['centered'], donor_mean),
        'centered_cov_zero_mean': (priors['centered'], np.zeros_like(donor_mean)),
        'donor_mean_plus_smooth_kernel': (priors['smooth'], donor_mean),
    }
    output = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                     'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                     'target_throughput_rps']].copy()
    measured_rows = anchor_mask & static.workload_group.isin(probe_groups).to_numpy()
    for name, (covariance, prior_mean) in specifications.items():
        field = prior_mean + posterior_residual(
            covariance, selected, observed_residual - prior_mean[selected]
        )
        inferred = baseline_anchor + field
        prediction = _predict_response(static, inferred, conditional)
        prediction[measured_rows] = inferred.loc[
            static.loc[measured_rows, 'workload_group']].to_numpy()
        output[f'{name}_latency_log'] = prediction[:, 0]
        output[f'{name}_rps_log'] = prediction[:, 1]
    return output


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    usable = raw[raw.eligible & raw.device.eq('cuda')].copy()
    main = usable[usable.campaign == CAMPAIGN]
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)]
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError('Main and external workload groups overlap')
    scores, manifests, predictions, modes = [], [], [], {}
    for held_model in sorted(main.model_id.unique()):
        train = prepare(main[main.model_id != held_model])
        held_main = main[main.model_id == held_model]
        held_external = external[external.model_id == held_model]
        if held_model in set(train.model_id) or held_external.empty:
            if held_external.empty:
                continue
            raise AssertionError('Outer model overlap')
        absolute = _absolute().fit(
            train[list(BASE_FEATURES)],
            train[['true_log_latency', 'true_log_requests_per_second']],
        )
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        all_test = pd.concat((held_main, held_external), ignore_index=True)
        priors, donor_mean, info = decomposed_continuous_prior(all_test, donor_oof)
        modes[held_model] = info
        known_size = registered_parameter_bytes(held_main)
        design = group_design(static_features(held_main, known_size))
        probes = select_probes(design, 8, 'medoids', seed=seed)
        predicted = predict_external(
            all_test, probes, priors, donor_mean, absolute, conditional,
            known_parameter_bytes=known_size,
        )
        predicted = predicted[predicted.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
        if len(predicted) != len(held_external):
            raise AssertionError('External prediction count changed')
        main_anchors = _anchor_rows(held_main)
        gpu_wall = float((main_anchors.loc[probes].replica_count *
                          main_anchors.loc[probes].wall_seconds).sum())
        for campaign in EXTERNAL_CAMPAIGNS:
            subset = predicted[predicted.campaign == campaign].copy()
            truth = held_external[held_external.campaign == campaign]
            if subset.empty:
                continue
            for method, values in scenario_metrics(
                    subset, truth, methods=METHODS).items():
                scores.append({
                    'heldout_model_id': held_model, 'campaign': campaign,
                    'method': method, 'prior_mode': info['mode'],
                    'main_probe_budget': 8, 'new_external_probes': 0,
                    'gpu_wall_main_probe_seconds': gpu_wall, **values,
                })
        manifests.append({
            'heldout_model_id': held_model,
            'probe_groups_json': json.dumps(probes),
            'training_model_ids_json': json.dumps(sorted(train.model_id.unique())),
            'main_probe_budget': 8, 'new_external_probes': 0,
            'gpu_wall_main_probe_seconds': gpu_wall,
            'prior_mode': info['mode'],
        })
        predicted['heldout_model_id'] = held_model
        predictions.append(predicted)
        print(f'Finished external {held_model}', flush=True)
    frame = pd.DataFrame(scores)
    frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False
    )
    aggregate = frame.groupby(['campaign', 'prior_mode', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        scored_groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        oracle_feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_main_probe_gpu_wall_seconds=('gpu_wall_main_probe_seconds', 'mean'),
    )
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed': seed, 'main_probe_budget': 8, 'new_external_probes': 0,
        'outer_split': 'leave one model_id out; train only other-model main-campaign outcomes',
        'prior': 'inner donor residuals interpolated from main grid to external static coordinates',
        'fold_prior_modes': modes, 'aggregate': aggregate.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/covariance_bias_ablation_external_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['aggregate']:
        print(row, flush=True)
