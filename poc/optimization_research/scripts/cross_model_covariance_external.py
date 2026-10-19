"""Test sparse cross-model transfer on previously unseen workload conditions.

Training and probes use only the main exhaustive campaign. Held-out model
outcomes from the novel-matrix and space-filling campaigns are used for final
evaluation only; no external anchor is measured. Donor OOF residual surfaces
are kernel-interpolated from the main grid to external condition coordinates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (_absolute, _coordinates, _keyed_anchor,
                                    _smooth_covariance, training_oof_anchor_residuals,
                                    predict_sparse, RHO)
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
           'space_global_mean', 'zero_shot')


def _base_kernel(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    distance = np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=2)
    return 0.4 + 0.6 * np.exp(-0.5 * distance)


def continuous_prior(target_anchors: pd.DataFrame, donor_oof: pd.DataFrame,
                     ridge: float = 0.1) -> tuple[np.ndarray, dict]:
    """Interpolate each previous model's OOF residual surface without targets."""
    target = _keyed_anchor(target_anchors)
    donor = donor_oof.copy()
    donor['condition_key'] = list(map(tuple, donor[[
        'workload_task', 'batch_size', 'max_length',
        'request_concurrency', 'repeats']].to_numpy()))
    same = donor[donor.workload_task == target.workload_task.iloc[0]]
    scale = np.maximum(np.mean(donor[['residual_latency', 'residual_rps']].to_numpy() ** 2,
                               axis=0), 0.02 ** 2)
    smooth = _smooth_covariance(target, scale)
    if same.empty:
        return smooth, {'mode': 'kernel_fallback_no_same_task_donors',
                        'aligned_donor_models': []}
    groups = {name: group.set_index('condition_key')
              for name, group in same.groupby('model_id', sort=True)}
    # Only the intersection of historical main-grid conditions is used. Every
    # encoder donor has the same 64 conditions; no held-out result is imputed.
    common = set.intersection(*(set(g.index) for g in groups.values()))
    keys = sorted(common)
    if len(groups) < 2 or not keys:
        return smooth, {'mode': 'kernel_fallback_inadequate_shared_grid',
                        'aligned_donor_models': []}
    reference = next(iter(groups.values())).loc[keys]
    x_ref = _coordinates(reference)
    x_target = _coordinates(target)
    k_rr = _base_kernel(x_ref, x_ref)
    k_tr = _base_kernel(x_target, x_ref)
    interpolation = np.linalg.solve(k_rr + ridge * np.eye(len(keys)), k_tr.T).T
    surfaces = []
    for donor_name, donor_rows in groups.items():
        values = donor_rows.loc[keys, ['residual_latency',
                                       'residual_rps']].to_numpy(dtype=float)
        surfaces.append(interpolation @ values)
    surfaces = np.stack(surfaces)
    empirical = np.einsum('mio,mjo->ijo', surfaces, surfaces) / len(surfaces)
    prior = (1.0 - RHO) * empirical + RHO * smooth
    return prior, {'mode': 'interpolated_donor_covariance_plus_kernel',
                   'aligned_donor_models': list(groups),
                   'shared_main_grid_conditions': len(keys),
                   'interpolation_ridge': ridge}


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    usable = raw[raw.eligible & (raw.device == 'cuda')].copy()
    main = usable[usable.campaign == CAMPAIGN]
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)]
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError('External and main workload groups overlap')
    records, manifests, predictions, modes = [], [], [], {}
    for held_model in sorted(main.model_id.unique()):
        train = prepare(main[main.model_id != held_model])
        held_main = main[main.model_id == held_model]
        held_external = external[external.model_id == held_model]
        if held_external.empty:
            continue
        if held_model in set(train.model_id):
            raise ValueError('Held-out model leaked into training')
        absolute = _absolute().fit(train[list(BASE_FEATURES)],
                                   train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        all_test = pd.concat((held_main, held_external), ignore_index=True)
        prior, info = continuous_prior(all_test, donor_oof)
        modes[held_model] = info
        known_parameter_bytes = registered_parameter_bytes(held_main)
        design = group_design(static_features(held_main, known_parameter_bytes))
        probes = select_probes(design, 8, 'medoids', seed=seed)
        predicted = predict_sparse(all_test, probes, probes,
                                   prior, absolute, conditional,
                                   parameter_bytes=known_parameter_bytes)
        predicted = predicted[predicted.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
        if len(predicted) != len(held_external):
            raise ValueError('External prediction count mismatch')
        main_anchors = held_main[(held_main.replica_count == 1) &
                                 (held_main.cpu_core_limit == 2)].set_index('workload_group')
        gpu_wall = float(main_anchors.loc[probes].wall_seconds.sum())
        for campaign in EXTERNAL_CAMPAIGNS:
            subset = predicted[predicted.campaign == campaign].copy()
            truth = held_external[held_external.campaign == campaign]
            if subset.empty:
                continue
            scores = scenario_metrics(subset, truth, methods=METHODS)
            for method, values in scores.items():
                records.append({'heldout_model_id': held_model,
                                'campaign': campaign, 'method': method,
                                'prior_mode': info['mode'],
                                'main_probe_budget': 8,
                                'new_external_probes': 0,
                                'gpu_wall_main_probe_seconds': 0.0 if method == 'zero_shot' else gpu_wall,
                                **values})
            subset['heldout_model_id'] = held_model
            predictions.append(subset)
        manifests.append({'heldout_model_id': held_model,
                          'probe_groups_json': json.dumps(probes),
                          'training_model_ids_json': json.dumps(sorted(train.model_id.unique())),
                          'main_probe_budget': len(probes),
                          'new_external_probes': 0,
                          'gpu_wall_main_probe_seconds': gpu_wall,
                          'prior_mode': info['mode']})
        print(f'Finished external {held_model}: {len(held_external) // 8} groups', flush=True)
    scores = pd.DataFrame(records)
    scores.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(output / 'predictions.parquet', index=False)
    macro = scores.groupby(['campaign', 'prior_mode', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_main_probe_gpu_wall_seconds=('gpu_wall_main_probe_seconds', 'mean'))
    report = {'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'training': 'only other-model main-campaign outcomes',
              'probes': 'only eight held-model main-campaign GPU1/two-core anchors',
              'external_observations_before_prediction': 0,
              'external_conditions_overlap_main': False,
              'fold_prior_modes': modes,
              'per_campaign_prior_method': macro.to_dict(orient='records')}
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_external_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed)
    for row in result['per_campaign_prior_method']:
        print(row, flush=True)
