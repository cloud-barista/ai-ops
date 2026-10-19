"""Negative control for the aligned donor residual surface in the K8 study.

Each donor's paired latency/RPS residual vectors are randomly reassigned among
that donor's workload conditions. This preserves its empirical marginal errors
and destroys their alignment to workload conditions. The outer held-out model,
inner donor OOF fit, eight label-free probes, response model, and scored slate
are identical to the original covariance experiment. This is a mechanistic
check on a frozen dataset, not an independent generalization test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

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
PERMUTATION_SEEDS = tuple(range(1000, 1064))
METHODS = ('aligned_empirical', 'smooth_kernel')


def permute_donor_conditions(donor_oof: pd.DataFrame, seed: int,
                             held_model: str) -> pd.DataFrame:
    """Shuffle paired residuals within each donor and task, never across them.

    A stable hash isolates the random stream for each donor/task pair. The
    permutation never uses a held-out performance outcome.
    """
    result = donor_oof.copy()
    columns = ['residual_latency', 'residual_rps']
    for (donor, task), rows in donor_oof.groupby(
            ['model_id', 'workload_task'], sort=True):
        indices = rows.index.to_numpy()
        token = f'{seed}|{held_model}|{donor}|{task}'.encode()
        stream_seed = int.from_bytes(hashlib.sha256(token).digest()[:8], 'big')
        permutation = np.random.default_rng(stream_seed).permutation(len(indices))
        result.loc[indices, columns] = rows[columns].to_numpy()[permutation]
    return result


def prepare_prediction(test_raw: pd.DataFrame, probe_groups: list[str],
                       absolute) -> dict:
    """Only selected anchor outcomes enter the reusable held-out fold state."""
    validate_slates(test_raw)
    anchors = _keyed_anchor(test_raw)
    order = anchors.workload_group.tolist()
    index_of = {group: i for i, group in enumerate(order)}
    if len(probe_groups) != 8 or len(set(probe_groups)) != 8 or not set(probe_groups).issubset(index_of):
        raise ValueError('Exactly eight distinct known workload probes are required')
    size = registered_parameter_bytes(test_raw)
    static = static_features(test_raw, size)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(
        baseline[anchor_mask], index=static.loc[anchor_mask, 'workload_group'],
        columns=['latency', 'rps'],
    ).loc[order]
    measured = _anchor_rows(test_raw).loc[probe_groups]
    observed = np.column_stack((
        np.log(measured.latency_p95_ms.to_numpy(dtype=float)),
        np.log((measured.examples_per_second /
                measured.batch_size).to_numpy(dtype=float)),
    ))
    selected = np.asarray([index_of[group] for group in probe_groups], dtype=int)
    residual = observed - baseline_anchor.loc[probe_groups].to_numpy()
    selected_mask = anchor_mask & static.workload_group.isin(probe_groups).to_numpy()
    return {
        'anchors': anchors, 'order': order, 'static': static,
        'baseline_anchor': baseline_anchor, 'selected': selected,
        'residual': residual, 'selected_mask': selected_mask,
    }


def predict_prior(state: dict, covariance: np.ndarray,
                  conditional, method: str) -> pd.DataFrame:
    field = posterior_residual(covariance, state['selected'], state['residual'])
    inferred = state['baseline_anchor'] + field
    static = state['static']
    prediction = _predict_response(static, inferred, conditional)
    selected_mask = state['selected_mask']
    prediction[selected_mask] = inferred.loc[
        static.loc[selected_mask, 'workload_group']].to_numpy()
    output = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                     'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                     'target_throughput_rps']].copy()
    output[f'{method}_latency_log'] = prediction[:, 0]
    output[f'{method}_rps_log'] = prediction[:, 1]
    return output


def run(snapshot: Path, output: Path, seed: int = 42,
        permutation_seeds: tuple[int, ...] = PERMUTATION_SEEDS) -> dict:
    if not permutation_seeds or len(set(permutation_seeds)) != len(permutation_seeds):
        raise ValueError('At least one unique permutation seed is required')
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    scores, manifests, predictions = [], [], []
    skipped = []
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        test_raw = main[main.model_id == held_model]
        train = prepare(train_raw)
        absolute = _absolute().fit(
            train[list(BASE_FEATURES)],
            train[['true_log_latency', 'true_log_requests_per_second']],
        )
        conditional = fit_model(train, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(train)
        anchor = _keyed_anchor(test_raw)
        original_covariance, info = residual_covariance(anchor, donor_oof)
        if info['mode'] != 'aligned_empirical_plus_kernel':
            skipped.append({'heldout_model_id': held_model,
                            'reason': 'No aligned same-task donor grid'})
            print(f'Skipped {held_model}: no aligned donor grid', flush=True)
            continue
        design = group_design(static_features(
            test_raw, registered_parameter_bytes(test_raw)))
        probes = select_probes(design, 8, 'medoids', seed=seed)
        state = prepare_prediction(test_raw, probes, absolute)
        gpu_wall = float((_anchor_rows(test_raw).loc[probes].replica_count *
                          _anchor_rows(test_raw).loc[probes].wall_seconds).sum())
        # The smooth-only comparison in the original script uses the mean
        # diagonal of the mixed prior. This mean is permutation-invariant.
        variance = np.maximum(np.diagonal(original_covariance,
                                           axis1=0, axis2=1).mean(axis=1), 1e-8)
        smooth = _smooth_covariance(anchor, variance)
        reference = {
            'aligned_empirical': original_covariance,
            'smooth_kernel': smooth,
        }
        manifest = {
            'heldout_model_id': held_model,
            'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
            'aligned_donor_models_json': json.dumps(info['aligned_donor_models']),
            'probe_groups_json': json.dumps(probes),
            'gpu_wall_probe_seconds': gpu_wall,
        }
        manifests.append(manifest)
        for method, covariance in reference.items():
            prediction = predict_prior(state, covariance, conditional, method)
            metric = scenario_metrics(prediction, test_raw,
                                      methods=(method,))[method]
            scores.append({'heldout_model_id': held_model, 'method': method,
                           'permutation_seed': -1,
                           'gpu_wall_probe_seconds': gpu_wall, **metric})
            prediction['heldout_model_id'] = held_model
            prediction['method'] = method
            prediction['permutation_seed'] = -1
            predictions.append(prediction)
        for permutation_seed in permutation_seeds:
            shuffled = permute_donor_conditions(donor_oof,
                                                permutation_seed, held_model)
            shuffled_covariance, shuffled_info = residual_covariance(anchor, shuffled)
            if shuffled_info['aligned_donor_models'] != info['aligned_donor_models']:
                raise AssertionError('Permutation changed donor support')
            shuffled_variance = np.maximum(np.diagonal(shuffled_covariance,
                                                        axis1=0, axis2=1).mean(axis=1), 1e-8)
            np.testing.assert_allclose(shuffled_variance, variance, rtol=1e-11,
                                       atol=1e-11)
            method = 'permuted_empirical'
            prediction = predict_prior(state, shuffled_covariance,
                                       conditional, method)
            metric = scenario_metrics(prediction, test_raw,
                                      methods=(method,))[method]
            scores.append({'heldout_model_id': held_model, 'method': method,
                           'permutation_seed': permutation_seed,
                           'gpu_wall_probe_seconds': gpu_wall, **metric})
            prediction['heldout_model_id'] = held_model
            prediction['method'] = method
            prediction['permutation_seed'] = permutation_seed
            predictions.append(prediction)
        print(f'Finished {held_model}: {len(permutation_seeds)} permutations',
              flush=True)
    frame = pd.DataFrame(scores)
    if frame.heldout_model_id.nunique() != 8:
        raise AssertionError('Expected eight aligned encoder folds')
    frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    reference = frame[frame.permutation_seed == -1].groupby('method').agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'),
    ).reset_index()
    permutation = frame[frame.method == 'permuted_empirical'].groupby(
        'permutation_seed').agg(
            models=('heldout_model_id', 'nunique'),
            latency_male=('latency_male', 'mean'),
            rps_male=('rps_male', 'mean'),
            successful_choices=('successful_choices', 'sum'),
            slo_violations=('slo_violations', 'sum'),
        ).reset_index()
    if not permutation.models.eq(8).all():
        raise AssertionError('Incomplete control seed')
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__,
        'outer_seed': seed, 'permutation_seeds': list(permutation_seeds),
        'probe_count_per_model': 8,
        'outer_split': 'leave one model out',
        'donor_residual_split': 'inner leave one donor model out',
        'negative_control': 'within-donor and within-task paired residual vectors independently shuffled across workload conditions',
        'heldout_observations': 'only selected eight GPU1/two-core anchor outcomes',
        'scored_population': 'all seven nonanchor candidates per workload',
        'empirical_covariance_weight': 1.0 - RHO,
        'reference': reference.to_dict(orient='records'),
        'permutation_macro_by_seed': permutation.to_dict(orient='records'),
        'skipped_unaligned_models': skipped,
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/covariance_permutation_control_v1')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--permutation-count', type=int, default=len(PERMUTATION_SEEDS))
    arguments = parser.parse_args()
    if not 1 <= arguments.permutation_count <= len(PERMUTATION_SEEDS):
        parser.error(f'--permutation-count must be in [1, {len(PERMUTATION_SEEDS)}]')
    outcome = run(arguments.snapshot, arguments.output, arguments.seed,
                  PERMUTATION_SEEDS[:arguments.permutation_count])
    for row in outcome['reference']:
        print(row, flush=True)
