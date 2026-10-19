"""Can a small CPU preprofile replace GPU probes for GPU resource prediction?

This is a retrospective, exploratory leave-model-out test. The frozen main
campaign has eight CPU2 workload observations for each of eight encoder
models, and the same eight conditions have GPU1/CPU2 observations. All held
model GPU outcomes are hidden from the CPU method. The CPU-to-GPU residual
map is fitted on *inner* leave-model-out donor residuals.
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

from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (
    KEY_COLUMNS, _absolute, _keyed_anchor, _predict_response, _smooth_covariance,
    posterior_residual, residual_covariance, training_oof_anchor_residuals,
)
from sparse_anchor_transfer import (
    CAMPAIGN, _anchor_rows, group_design,
    scenario_metrics, select_probes, static_features, validate_slates,
)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('zero_shot', 'cpu8_transfer', 'gpu4_covariance',
           'gpu4_cpu8_fusion', 'gpu8_kernel', 'gpu8_covariance')
CPU_CORE_LIMIT = 2
PSEUDO_OBSERVATION_NOISE = 0.5


def paired_cpu2(gpu: pd.DataFrame, cpu: pd.DataFrame) -> pd.DataFrame:
    """Match exact model/workload groups and check CPU precedes GPU profiling."""
    cpu2 = cpu[(cpu.device == 'cpu') & (cpu.cpu_core_limit == CPU_CORE_LIMIT) &
               (cpu.replica_count == 1) & cpu.eligible].copy()
    gpu1 = _anchor_rows(gpu).reset_index(drop=True)
    if cpu2.duplicated('workload_group').any():
        raise ValueError('Repeated CPU2 workload group')
    fields = ['workload_group', 'model_id', 'model_revision', 'workload_task',
              'batch_size', 'max_length', 'request_concurrency', 'repeats',
              'dataset', 'input_encoding']
    matched = cpu2.merge(gpu1[fields + ['started_at']].rename(
        columns={'started_at': 'gpu_started_at'}),
        on=fields, validate='one_to_one')
    if matched.empty or not (matched.finished_at < matched.gpu_started_at).all():
        raise ValueError('Each CPU measurement must finish before its paired GPU anchor starts')
    if (matched[['latency_p95_ms', 'examples_per_second',
                 'wall_seconds', 'batch_size']] <= 0).any().any():
        raise ValueError('Invalid CPU measurements')
    return matched


def cpu_frame(gpu: pd.DataFrame, paired_cpu: pd.DataFrame) -> pd.DataFrame:
    """CPU targets with GPU-independent static registration/workload fields."""
    by_model = []
    for model_id, group in gpu.groupby('model_id'):
        measured = paired_cpu[paired_cpu.model_id == model_id]
        if measured.empty:
            continue
        cpu_sizes = {json.loads(value)['model_parameter_bytes']
                     for value in measured.result_json}
        if len(cpu_sizes) != 1 or next(iter(cpu_sizes)) <= 0:
            raise ValueError('CPU records need one positive registered model size')
        anchored = _anchor_rows(group).reset_index(drop=True)
        metadata = static_features(anchored, float(next(iter(cpu_sizes))))
        cols = list(dict.fromkeys(['workload_group', *BASE_FEATURES, *KEY_COLUMNS]))
        frame = metadata[cols].merge(measured[
            ['workload_group', 'latency_p95_ms', 'examples_per_second',
             'wall_seconds']],
            on='workload_group', validate='one_to_one')
        frame['model_id'] = model_id
        frame['cpu_log_latency'] = np.log(frame.latency_p95_ms)
        frame['cpu_log_rps'] = np.log(frame.examples_per_second / frame.batch_size)
        by_model.append(frame)
    if not by_model:
        raise ValueError('No paired CPU/GPU conditions')
    return pd.concat(by_model, ignore_index=True)


def fit_cpu_absolute(train_cpu: pd.DataFrame) -> ExtraTreesRegressor:
    if train_cpu.model_id.nunique() < 2:
        raise ValueError('CPU fit needs multiple donor models')
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                                max_features=1.0, random_state=42, n_jobs=2)
    model.fit(train_cpu[list(BASE_FEATURES)],
              train_cpu[['cpu_log_latency', 'cpu_log_rps']])
    return model


def donor_residual_pairs(train_cpu: pd.DataFrame,
                         gpu_oof: pd.DataFrame) -> pd.DataFrame:
    """No donor's own CPU record is used when predicting its CPU baseline."""
    pieces = []
    for donor in sorted(train_cpu.model_id.unique()):
        fit = train_cpu[train_cpu.model_id != donor]
        test = train_cpu[train_cpu.model_id == donor].copy()
        predicted = fit_cpu_absolute(fit).predict(test[list(BASE_FEATURES)])
        test['cpu_latency_residual'] = test.cpu_log_latency.to_numpy() - predicted[:, 0]
        test['cpu_rps_residual'] = test.cpu_log_rps.to_numpy() - predicted[:, 1]
        pieces.append(test[['model_id', 'workload_group', *KEY_COLUMNS,
                            'cpu_latency_residual', 'cpu_rps_residual']])
    pairs = pd.concat(pieces, ignore_index=True)
    # GPU residuals are produced by an inner leave-one-GPU-model-out fit.
    # Their key includes task/input settings; workload_group is not returned.
    known = gpu_oof.copy()
    pairs = pairs.merge(known[['model_id', *KEY_COLUMNS,
                               'residual_latency', 'residual_rps']],
                        on=['model_id', *KEY_COLUMNS],
                        validate='one_to_one')
    if len(pairs) != len(train_cpu):
        raise ValueError('Lost CPU/GPU donor residual pairs')
    return pairs


def pseudo_posterior(covariance: np.ndarray, observed_indices: np.ndarray,
                     pseudo_residual: np.ndarray,
                     noise_fraction: float = PSEUDO_OBSERVATION_NOISE) -> np.ndarray:
    """Smooth uncertain CPU-derived *estimates*, without treating them as truth."""
    cov = np.asarray(covariance, dtype=float)
    indices = np.asarray(observed_indices, dtype=int)
    pseudo = np.asarray(pseudo_residual, dtype=float)
    n = cov.shape[0]
    if cov.shape != (n, n, 2) or pseudo.shape != (len(indices), 2) or len(indices) == 0:
        raise ValueError('Invalid CPU pseudo-observations')
    if len(set(indices)) != len(indices) or min(indices) < 0 or max(indices) >= n:
        raise ValueError('Invalid CPU pseudo-observation indices')
    result = np.empty((n, 2), dtype=float)
    for target in range(2):
        k = cov[:, :, target]
        noise = noise_fraction * max(float(np.diag(k).mean()), 1e-8)
        result[:, target] = k[:, indices] @ np.linalg.solve(
            k[np.ix_(indices, indices)] + noise * np.eye(len(indices)),
            pseudo[:, target])
    return result


def predict_fold(test_gpu: pd.DataFrame, test_cpu: pd.DataFrame,
                 absolute, conditional, cpu_absolute,
                 cpu_to_gpu: Ridge, gpu_oof: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    validate_slates(test_gpu)
    anchors = _keyed_anchor(test_gpu)
    groups = anchors.workload_group.tolist()
    cpu_sizes = test_cpu.log_parameter_bytes.unique()
    if len(cpu_sizes) != 1:
        raise ValueError('Inconsistent target model size in CPU preprofile')
    static = static_features(test_gpu, float(np.exp(cpu_sizes[0])))
    zero = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == 1) &
                   (static.cpu_core_limit == CPU_CORE_LIMIT)).to_numpy()
    baseline_anchor = pd.DataFrame(zero[anchor_mask],
                                   index=static.loc[anchor_mask, 'workload_group'],
                                   columns=['latency', 'rps']).loc[groups]
    matched = test_cpu.set_index('workload_group').loc[
        sorted(test_cpu.workload_group)]
    observed_groups = matched.index.tolist()
    if not set(observed_groups).issubset(groups):
        raise ValueError('CPU probe outside held-out GPU grid')
    indices = np.asarray([groups.index(group) for group in observed_groups], dtype=int)
    observed_cpu = matched[['cpu_log_latency', 'cpu_log_rps']].to_numpy()
    pred_cpu = cpu_absolute.predict(matched[list(BASE_FEATURES)])
    cpu_residual = observed_cpu - pred_cpu
    pseudo_gpu_residual = cpu_to_gpu.predict(cpu_residual)
    cov, info = residual_covariance(anchors, gpu_oof)
    smooth = _smooth_covariance(anchors, np.asarray(info['variance']))
    cpu_field = pseudo_posterior(smooth, indices, pseudo_gpu_residual)
    gpu_measured = _anchor_rows(test_gpu).loc[observed_groups]
    gpu_obs = np.column_stack([
        np.log(gpu_measured.latency_p95_ms.to_numpy()),
        np.log((gpu_measured.examples_per_second /
                gpu_measured.batch_size).to_numpy())])
    gpu_residual = gpu_obs - baseline_anchor.loc[observed_groups].to_numpy()
    kernel_field = posterior_residual(smooth, indices, gpu_residual)
    covariance_field = posterior_residual(cov, indices, gpu_residual)
    # The four GPU locations are selected from static workload coordinates.
    # The CPU observations and every held-out GPU outcome are unavailable to
    # this selector. Both four-GPU methods use these exact same measurements.
    design = group_design(static)
    gpu4_groups = select_probes(design.loc[observed_groups], 4, 'medoids')
    gpu4_local_indices = np.asarray([observed_groups.index(g)
                                     for g in gpu4_groups], dtype=int)
    gpu4_grid_indices = indices[gpu4_local_indices]
    gpu4_residual = gpu_residual[gpu4_local_indices]
    gpu4_field = posterior_residual(cov, gpu4_grid_indices, gpu4_residual)
    fusion_field = cpu_field + posterior_residual(
        cov, gpu4_grid_indices,
        gpu4_residual - cpu_field[gpu4_grid_indices])
    fields = {'cpu8_transfer': cpu_field,
              'gpu4_covariance': gpu4_field,
              'gpu4_cpu8_fusion': fusion_field,
              'gpu8_kernel': kernel_field,
              'gpu8_covariance': covariance_field}
    result = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                     'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                     'target_throughput_rps']].copy()
    result['zero_shot_latency_log'] = zero[:, 0]
    result['zero_shot_rps_log'] = zero[:, 1]
    for method, field in fields.items():
        inferred = baseline_anchor + field
        prediction = _predict_response(static, inferred, conditional)
        if method.startswith('gpu'):
            measured_groups = gpu4_groups if method.startswith('gpu4') else observed_groups
            known = anchor_mask & static.workload_group.isin(measured_groups).to_numpy()
            prediction[known] = gpu_obs[[observed_groups.index(g) for g in
                                         static.loc[known, 'workload_group']]]
        result[f'{method}_latency_log'] = prediction[:, 0]
        result[f'{method}_rps_log'] = prediction[:, 1]
    details = {'paired_conditions': len(observed_groups),
               'gpu_covariance_mode': info['mode'],
               'cpu_wall_seconds': float(matched.wall_seconds.sum()),
               'gpu_wall_seconds': float(gpu_measured.wall_seconds.sum()),
               'gpu4_wall_seconds': float(gpu_measured.loc[gpu4_groups].wall_seconds.sum()),
               'gpu4_groups_json': json.dumps(gpu4_groups),
               'cpu_finished_before_gpu_pairs': len(observed_groups)}
    return result, details


def run(snapshot: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    gpu = raw[(raw.campaign == CAMPAIGN) & raw.eligible & raw.device.eq('cuda')].copy()
    cpu = raw[(raw.campaign == CAMPAIGN) & raw.eligible & raw.device.eq('cpu')].copy()
    validate_slates(gpu)
    pairs = paired_cpu2(gpu, cpu)
    cpu_features = cpu_frame(gpu, pairs)
    records, metrics, predictions = [], [], []
    for held in sorted(cpu_features.model_id.unique()):
        train_gpu = gpu[gpu.model_id != held]
        test_gpu = gpu[gpu.model_id == held]
        train_cpu = cpu_features[cpu_features.model_id != held]
        test_cpu = cpu_features[cpu_features.model_id == held]
        if held in set(train_gpu.model_id) or held in set(train_cpu.model_id):
            raise AssertionError('Outer model leakage')
        prepared = prepare(train_gpu)
        absolute = _absolute().fit(prepared[list(BASE_FEATURES)],
                                   prepared[['true_log_latency',
                                             'true_log_requests_per_second']])
        conditional = fit_model(prepared, 'anchor_conditioned', 42)
        cpu_absolute = fit_cpu_absolute(train_cpu)
        gpu_oof = training_oof_anchor_residuals(prepared)
        donor_cpu_gpu = donor_residual_pairs(train_cpu, gpu_oof)
        transfer = Ridge(alpha=1.0, fit_intercept=True).fit(
            donor_cpu_gpu[['cpu_latency_residual',
                           'cpu_rps_residual']].to_numpy(),
            donor_cpu_gpu[['residual_latency', 'residual_rps']].to_numpy())
        predicted, details = predict_fold(test_gpu, test_cpu, absolute,
                                          conditional, cpu_absolute,
                                          transfer, gpu_oof)
        scored = scenario_metrics(predicted, test_gpu, methods=METHODS)
        for method, score in scored.items():
            metrics.append({'model_id': held, 'method': method, **score})
        details['heldout_model_id'] = held
        details['training_model_ids_json'] = json.dumps(sorted(train_cpu.model_id.unique()))
        details['transfer_intercept'] = transfer.intercept_.tolist()
        details['transfer_coefficients'] = transfer.coef_.tolist()
        records.append(details)
        predictions.append(predicted)
        print(f'Finished {held}: CPU {details["cpu_wall_seconds"]:.1f}s, '
              f'GPU {details["gpu_wall_seconds"]:.1f}s', flush=True)
    detail = pd.DataFrame(records)
    detail.to_csv(output / 'split_manifest.csv', index=False)
    score = pd.DataFrame(metrics)
    score.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    macro = score.groupby('method', as_index=False).agg(
        models=('model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        missed_feasible_groups=('missed_feasible_groups', 'sum'))
    result = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__,
        'campaign': CAMPAIGN,
        'scope': 'Eight encoder models with CPU2/GPU1 exact workload pairs; Qwen has no CPU main-campaign records',
        'outer_split': 'leave one model_id out',
        'inner_split': 'donor CPU and GPU residuals each leave their donor model out',
        'heldout_observations_cpu_method': 'only eight CPU2 outcomes and their registration metadata; no heldout GPU outcomes or result JSON; eight CPU probes were not all completed before the first GPU in the actual campaign, so this is an offline reordered policy',
        'gpu_baseline_observations': 'GPU1/two-core outcomes for the exact eight CPU-measured workload conditions',
        'scored_population': 'all 64 workload conditions times seven non-anchor GPU resources per model',
        'cpu_pseudo_observation_noise_fraction': PSEUDO_OBSERVATION_NOISE,
        'mean_cpu_wall_seconds_per_model': float(detail.cpu_wall_seconds.mean()),
        'mean_gpu_wall_seconds_per_model': float(detail.gpu_wall_seconds.mean()),
        'mean_gpu4_wall_seconds_per_model': float(detail.gpu4_wall_seconds.mean()),
        'macro': macro.to_dict(orient='records'),
        'limitation': 'Batch latency/throughput and collection-script SLOs, not user-request measurements or VM price',
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cpu_preprofile_transfer_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output)['macro'], indent=2))
