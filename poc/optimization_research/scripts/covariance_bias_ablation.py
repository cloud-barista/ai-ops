"""Separate donor mean bias from centered cross-model residual covariance.

All four methods use the same eight label-free space-filling anchor probes per
held-out model. Only donor training models contribute to the prior. This is an
exploratory ablation of the frozen cross_model_covariance_v1 experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (
    RHO, _absolute, _keyed_anchor, _predict_response, _smooth_covariance,
    posterior_residual, residual_covariance, training_oof_anchor_residuals,
)
from sparse_anchor_transfer import (
    CAMPAIGN, _anchor_rows, group_design, registered_parameter_bytes,
    scenario_metrics, select_probes, static_features, validate_slates,
)


ROOT = Path(__file__).resolve().parents[1]
METHODS = (
    'raw_second_moment',
    'centered_cov_plus_donor_mean',
    'centered_cov_zero_mean',
    'donor_mean_plus_smooth_kernel',
)


def decomposed_prior(test_anchors: pd.DataFrame,
                     donor_oof: pd.DataFrame) -> tuple[dict[str, np.ndarray], np.ndarray, dict]:
    """Decompose E[rrᵀ] = Cov(r) + E[r]E[r]ᵀ on an aligned grid.

    A new task without two aligned donors uses the same smooth-kernel fallback
    and zero donor mean for all methods.
    """
    ordered = _keyed_anchor(test_anchors)
    raw, info = residual_covariance(test_anchors, donor_oof)
    smooth = _smooth_covariance(ordered, np.asarray(info['variance'], dtype=float))
    mean = np.zeros((len(ordered), 2))
    centered = smooth.copy()
    if info['aligned_donor_models']:
        keys = list(ordered.index)
        donor = donor_oof.copy()
        donor['condition_key'] = list(map(tuple, donor[[
            'workload_task', 'batch_size', 'max_length',
            'request_concurrency', 'repeats']].to_numpy()))
        surfaces = np.stack([
            donor[donor.model_id == model].set_index('condition_key').loc[
                keys, ['residual_latency', 'residual_rps']].to_numpy(dtype=float)
            for model in info['aligned_donor_models']
        ])
        mean = surfaces.mean(axis=0)
        centered_surfaces = surfaces - mean
        centered_empirical = np.einsum(
            'mio,mjo->ijo', centered_surfaces, centered_surfaces
        ) / len(surfaces)
        centered = (1.0 - RHO) * centered_empirical + RHO * smooth
        reconstructed = centered + (1.0 - RHO) * np.einsum(
            'io,jo->ijo', mean, mean)
        np.testing.assert_allclose(raw, reconstructed, atol=1e-8, rtol=1e-8)
    return {'raw': raw, 'centered': centered, 'smooth': smooth}, mean, info


def predict_ablation(test_raw: pd.DataFrame, probe_groups: list[str],
                     donor_oof: pd.DataFrame, absolute, conditional,
                     known_parameter_bytes: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Predict without consulting any unmeasured held-out performance labels."""
    validate_slates(test_raw)
    anchors = _keyed_anchor(test_raw)
    order = anchors.workload_group.tolist()
    index_of = {group: i for i, group in enumerate(order)}
    if not probe_groups or len(probe_groups) != len(set(probe_groups)) or not set(probe_groups).issubset(index_of):
        raise ValueError('Invalid probe group selection')
    priors, donor_mean, info = decomposed_prior(test_raw, donor_oof)
    size = (registered_parameter_bytes(test_raw) if known_parameter_bytes is None
            else float(known_parameter_bytes))
    static = static_features(test_raw, size)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(
        baseline[anchor_mask], index=static.loc[anchor_mask, 'workload_group'],
        columns=['latency', 'rps']
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
    return output, info


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    scores, manifests, predictions, modes = [], [], [], {}
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        test_raw = main[main.model_id == held_model]
        if held_model in set(train_raw.model_id):
            raise AssertionError('Outer model overlap')
        train = prepare(train_raw)
        absolute = _absolute().fit(
            train[list(BASE_FEATURES)],
            train[['true_log_latency', 'true_log_requests_per_second']],
        )
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        size = registered_parameter_bytes(test_raw)
        design = group_design(static_features(test_raw, size))
        probes = select_probes(design, 8, 'medoids', seed=seed)
        predicted, info = predict_ablation(
            test_raw, probes, donor_oof, absolute, conditional,
            known_parameter_bytes=size,
        )
        modes[held_model] = info
        metrics = scenario_metrics(predicted, test_raw, methods=METHODS)
        anchor_rows = _anchor_rows(test_raw)
        gpu_wall = float((anchor_rows.loc[probes].replica_count *
                          anchor_rows.loc[probes].wall_seconds).sum())
        for method, values in metrics.items():
            scores.append({
                'heldout_model_id': held_model, 'method': method,
                'covariance_mode': info['mode'],
                'gpu_wall_probe_seconds': gpu_wall, **values,
            })
        manifests.append({
            'heldout_model_id': held_model,
            'probe_groups_json': json.dumps(probes),
            'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
            'covariance_mode': info['mode'],
            'gpu_wall_probe_seconds': gpu_wall,
        })
        predicted['heldout_model_id'] = held_model
        predictions.append(predicted)
        print(f'Finished {held_model}: {info["mode"]}', flush=True)
    frame = pd.DataFrame(scores)
    frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False
    )
    aggregate = frame.groupby(['covariance_mode', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        oracle_feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_probe_gpu_wall_seconds=('gpu_wall_probe_seconds', 'mean'),
    )
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed': seed, 'budget': 8,
        'probe_selector': 'same label-free k-means medoids for all methods',
        'outer_split': 'leave one model_id out',
        'donor_residual_split': 'inner leave-one-donor-model-out',
        'unprobed_outcomes_accessed_before_prediction': False,
        'prior_shrinkage_weight': RHO,
        'fold_prior_modes': modes,
        'aggregate': aggregate.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/covariance_bias_ablation_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['aggregate']:
        print(row, flush=True)
