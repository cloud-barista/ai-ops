"""Exploratory upper-quantile forecasts of *observed* CUDA reserved peaks.

Each held model is excluded from point-model training and donor-only residual
calibration. The 0.9 donor quantile is a heuristic for transfer across models:
it does not provide a conformal coverage guarantee for an unseen model, and
observed reserved peaks are not minimum VRAM or OOM feasibility labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn import __version__ as sklearn_version
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from evaluate_vram_peak import (BERT_FAMILY, CAMPAIGNS, DISTILBERT, ROOT,
                                SNAPSHOT, selected_measurements,
                                static_features)


OUTPUT = ROOT / 'artifacts/vram_upper_v1'
QUANTILE = 0.9
METHODS = ('ridge_point_floor', 'donor_overhead_q90',
           'ridge_additive_oof_q90', 'overhead_scaled_oof_q90',
           'ridge_ratio_oof_q90')
FEATURE_NAMES = ('log_parameter_gib', 'log_batch_size', 'log_max_length',
                 'log1p_request_concurrency', 'is_causal_lm',
                 'log_batch_x_log_length',
                 'log_parameter_x_log_batch_x_log_length')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def weighted_model_quantile(values: np.ndarray, model_ids: np.ndarray,
                            quantile: float = QUANTILE) -> float:
    """Pool donor-cell scores so each donor model has total weight 1/M."""
    values = np.asarray(values, dtype=float)
    model_ids = np.asarray(model_ids, dtype=str)
    if len(values) == 0 or len(values) != len(model_ids):
        raise ValueError('Scores and model IDs must have equal nonzero length')
    if not np.isfinite(values).all() or not (0 < quantile < 1):
        raise ValueError('Scores must be finite; quantile must lie in (0, 1)')
    unique, counts = np.unique(model_ids, return_counts=True)
    frequency = dict(zip(unique, counts))
    weights = np.array([1.0 / (len(unique) * frequency[model])
                        for model in model_ids])
    ordering = np.argsort(values, kind='stable')
    index = np.searchsorted(np.cumsum(weights[ordering]), quantile,
                            side='left')
    return float(values[ordering[min(index, len(values) - 1)]])


def point_predict(train: pd.DataFrame, target: pd.DataFrame,
                  method: str) -> np.ndarray:
    """Static-feature ridge; target outcomes are never read by this function."""
    if train.empty or target.empty:
        raise ValueError('Point fit requires nonempty donor and target frames')
    if set(train.model_id).intersection(set(target.model_id)):
        raise ValueError('Target model included in point-model training')
    if method not in ('direct', 'overhead'):
        raise ValueError('Unknown point model')
    target_y = train.reserved_peak_gib.to_numpy(dtype=float)
    donor_weight = train.parameter_gib.to_numpy(dtype=float)
    if not np.isfinite(target_y).all() or not (target_y > donor_weight).all():
        raise ValueError('Training reserved peak must exceed known parameter size')
    if method == 'overhead':
        response = np.log(target_y - donor_weight)
    else:
        response = np.log(target_y)
    model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    model.fit(static_features(train), response)
    predicted = np.exp(model.predict(static_features(target)))
    target_weight = target.parameter_gib.to_numpy(dtype=float)
    if method == 'overhead':
        return target_weight + predicted
    return np.maximum(target_weight, predicted)


def donor_calibration(donor: pd.DataFrame) -> dict[str, float]:
    """Cross-fit every donor model, without using a target-model outcome."""
    models = sorted(donor.model_id.unique())
    if len(models) < 3:
        raise ValueError('At least three donor models are needed for cross-fit calibration')
    rows = []
    for model_id in models:
        train = donor.loc[donor.model_id != model_id]
        target = donor.loc[donor.model_id == model_id]
        direct = point_predict(train, target, 'direct')
        overhead = point_predict(train, target, 'overhead')
        rows.extend(zip([model_id] * len(target),
                        target.reserved_peak_gib.to_numpy(dtype=float),
                        target.parameter_gib.to_numpy(dtype=float),
                        direct, overhead))
    crossfit = pd.DataFrame(rows, columns=['model_id', 'peak', 'parameter',
                                           'direct', 'overhead'])
    ids = crossfit.model_id.to_numpy()
    direct_addition = weighted_model_quantile(
        (crossfit.peak - crossfit.direct).to_numpy(), ids)
    direct_ratio = weighted_model_quantile(
        (crossfit.peak / crossfit.direct).to_numpy(), ids)
    overhead_ratio = weighted_model_quantile(
        ((crossfit.peak - crossfit.parameter) /
         (crossfit.overhead - crossfit.parameter)).to_numpy(), ids)
    constant_overhead = weighted_model_quantile(
        (donor.reserved_peak_gib - donor.parameter_gib).to_numpy(),
        donor.model_id.to_numpy())
    return {
        'donor_models': len(models), 'donor_cells': len(donor),
        'direct_addition_gib': max(0.0, direct_addition),
        'direct_ratio': max(1.0, direct_ratio),
        'overhead_ratio': max(1.0, overhead_ratio),
        'constant_overhead_gib': constant_overhead,
    }


def forecast_fold(donor: pd.DataFrame, target: pd.DataFrame,
                  held_ids: set[str],
                  calibration: dict[str, float] | None = None) -> dict[str, np.ndarray]:
    """Return five comparable peaks in GiB using only main-campaign donors."""
    if set(donor.model_id).intersection(held_ids):
        raise ValueError('Held model included in donor frame')
    if not set(target.model_id).issubset(held_ids):
        raise ValueError('Target frame contains a donor model')
    if donor.empty or target.empty:
        raise ValueError('Nonempty donors and targets are required')
    if calibration is None:
        calibration = donor_calibration(donor)
    direct = point_predict(donor, target, 'direct')
    overhead = point_predict(donor, target, 'overhead')
    parameter = target.parameter_gib.to_numpy(dtype=float)
    predictions = {
        'ridge_point_floor': direct,
        'donor_overhead_q90': parameter + calibration['constant_overhead_gib'],
        'ridge_additive_oof_q90': direct + calibration['direct_addition_gib'],
        'overhead_scaled_oof_q90': parameter +
        calibration['overhead_ratio'] * (overhead - parameter),
        'ridge_ratio_oof_q90': calibration['direct_ratio'] * direct,
    }
    for name, values in predictions.items():
        if len(values) != len(target) or not np.isfinite(values).all():
            raise ValueError(f'Invalid forecast from {name}')
        if (values < parameter).any():
            raise ValueError(f'Forecast below known parameter size from {name}')
    return predictions


def donor_task_support(donor: pd.DataFrame,
                       target: pd.DataFrame) -> np.ndarray:
    """Require two independent donor models for the requested task type."""
    counts = donor.groupby('workload_task').model_id.nunique().to_dict()
    return target.workload_task.map(lambda task: counts.get(task, 0) >= 2).to_numpy(dtype=bool)


def fit_research_estimator(donor: pd.DataFrame) -> dict:
    """Fit the selected ratio-calibrated Ridge on all main-campaign models."""
    profile_columns = ('host', 'device', 'device_name', 'gpu_indices_json',
                       'replica_count', 'cpu_core_limit')
    if any(donor[column].nunique() != 1 for column in profile_columns):
        raise ValueError('Research estimator requires one fixed hardware profile')
    hardware_profile = {column: donor[column].iloc[0]
                        for column in profile_columns}
    calibration = donor_calibration(donor)
    response = np.log(donor.reserved_peak_gib.to_numpy(dtype=float))
    pipeline = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    pipeline.fit(static_features(donor), response)
    return {
        'kind': 'research_only_observed_cuda_reserved_peak',
        'feature_names': FEATURE_NAMES,
        'profile': 'local GPU1, one replica, CPU affinity 2',
        'hardware_profile': hardware_profile,
        'registered_parameter_bytes_required': True,
        'target': 'torch.cuda.max_memory_reserved() GiB',
        'model': pipeline,
        'direct_ratio': calibration['direct_ratio'],
        'calibration': calibration,
        'donor_models': sorted(donor.model_id.unique()),
        'task_model_counts': donor.groupby('workload_task').model_id.nunique().to_dict(),
    }


def forecast_with_research_estimator(bundle: dict,
                                     target: pd.DataFrame) -> np.ndarray:
    """Use only static registration/request fields; this is not a VRAM guarantee."""
    if target.empty:
        raise ValueError('Target frame must be nonempty')
    if tuple(bundle['feature_names']) != FEATURE_NAMES:
        raise ValueError('Unexpected feature schema')
    for column, expected in bundle['hardware_profile'].items():
        if column not in target or not target[column].eq(expected).all():
            raise ValueError(f'Unsupported hardware profile: {column}')
    if any(bundle['task_model_counts'].get(task, 0) < 2
           for task in target.workload_task):
        raise ValueError('Unsupported task: fewer than two training models')
    parameter = target.parameter_gib.to_numpy(dtype=float)
    point = np.maximum(parameter,
                       np.exp(bundle['model'].predict(static_features(target))))
    return float(bundle['direct_ratio']) * point


def evaluate(campaigns: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = pd.concat([campaigns[name] for name in CAMPAIGNS],
                         ignore_index=True)
    if (selected.host.nunique() != 1 or selected.device_name.nunique() != 1 or
            set(selected.gpu_indices_json) != {'[0]'}):
        raise ValueError('Expected one local host and physical GPU index zero')
    main = campaigns[CAMPAIGNS[0]]
    models = sorted(main.model_id.unique())
    if set(BERT_FAMILY + (DISTILBERT,)) - set(models):
        raise ValueError('Expected family-holdout model missing')
    folds = [(f'loomo:{model}', {model}) for model in models]
    folds += [('bert4', set(BERT_FAMILY)),
              ('bert4_plus_distilbert', set(BERT_FAMILY + (DISTILBERT,)))]
    forecasts = []
    calibrations = []
    for fold_name, held_ids in folds:
        donor = main.loc[~main.model_id.isin(held_ids)]
        calibration = donor_calibration(donor)
        calibrations.append({'fold': fold_name,
                             'held_models': '|'.join(sorted(held_ids)),
                             'donor_model_ids': '|'.join(sorted(donor.model_id.unique())),
                             **calibration})
        for campaign_name, frame in campaigns.items():
            target = frame.loc[frame.model_id.isin(held_ids)]
            if target.empty:
                continue
            by_method = forecast_fold(donor, target, held_ids, calibration)
            supported = donor_task_support(donor, target)
            for method, forecast in by_method.items():
                forecasts.extend({
                    'fold': fold_name, 'campaign': campaign_name,
                    'model_id': model_id, 'workload_group': workload,
                    'method': method, 'parameter_gib': float(parameter),
                    'observed_peak_gib': float(observed),
                    'forecast_peak_gib': float(prediction),
                    'supported_task': bool(is_supported),
                } for model_id, workload, parameter, observed, prediction,
                      is_supported in zip(
                    target.model_id, target.workload_group, target.parameter_gib,
                    target.reserved_peak_gib, forecast, supported))
    return pd.DataFrame(forecasts), pd.DataFrame(calibrations)


def summarize(forecasts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = forecasts.copy()
    difference = data.observed_peak_gib - data.forecast_peak_gib
    data['covered'] = (difference <= 0).astype(float)
    data['excess_mib'] = np.maximum(-difference, 0) * 1024
    data['shortfall_mib'] = np.maximum(difference, 0) * 1024
    data['pinball90_mib'] = np.where(difference >= 0, 0.9 * difference,
                                     -0.1 * difference) * 1024
    data['forecast_mib'] = data.forecast_peak_gib * 1024
    per_model = data.groupby(['fold', 'campaign', 'method', 'model_id'],
                             as_index=False).agg(
        observations=('covered', 'size'), coverage=('covered', 'mean'),
        mean_excess_mib=('excess_mib', 'mean'),
        mean_shortfall_mib=('shortfall_mib', 'mean'),
        max_shortfall_mib=('shortfall_mib', 'max'),
        pinball90_mib=('pinball90_mib', 'mean'),
        mean_forecast_mib=('forecast_mib', 'mean'),
    )
    aggregate = per_model.groupby(['fold', 'campaign', 'method'],
                                  as_index=False).agg(
        models=('model_id', 'size'), observations=('observations', 'sum'),
        coverage_macro=('coverage', 'mean'),
        mean_excess_mib_macro=('mean_excess_mib', 'mean'),
        mean_shortfall_mib_macro=('mean_shortfall_mib', 'mean'),
        worst_shortfall_mib=('max_shortfall_mib', 'max'),
        pinball90_mib_macro=('pinball90_mib', 'mean'),
        mean_forecast_mib_macro=('mean_forecast_mib', 'mean'),
    )
    return per_model, aggregate


def cross_fold_summary(per_model: pd.DataFrame,
                       supported_only: bool = False,
                       forecasts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pool model-held-out folds with one vote per held model."""
    rows = per_model.loc[per_model.fold.str.startswith('loomo:')].copy()
    if supported_only:
        if forecasts is None:
            raise ValueError('Forecast rows required for the support-filtered summary')
        support = forecasts.loc[forecasts.fold.str.startswith('loomo:')].groupby(
            ['fold', 'campaign', 'model_id'], as_index=False).supported_task.all()
        rows = rows.merge(support, on=['fold', 'campaign', 'model_id'],
                          validate='many_to_one')
        rows = rows.loc[rows.supported_task]
    if rows.empty:
        raise ValueError('No supported held-out models')
    return rows.groupby(['campaign', 'method'], as_index=False).agg(
        models=('model_id', 'size'), observations=('observations', 'sum'),
        coverage_macro=('coverage', 'mean'),
        mean_excess_mib_macro=('mean_excess_mib', 'mean'),
        mean_shortfall_mib_macro=('mean_shortfall_mib', 'mean'),
        worst_shortfall_mib=('max_shortfall_mib', 'max'),
        pinball90_mib_macro=('pinball90_mib', 'mean'),
        mean_forecast_mib_macro=('mean_forecast_mib', 'mean'),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    campaigns = selected_measurements(pd.read_parquet(args.snapshot))
    forecasts, calibration = evaluate(campaigns)
    per_model, aggregate = summarize(forecasts)
    loomo_summary = cross_fold_summary(per_model)
    supported_summary = cross_fold_summary(per_model, True, forecasts)
    args.output.mkdir(parents=True, exist_ok=True)
    forecasts.to_csv(args.output / 'forecasts.csv', index=False)
    calibration.to_csv(args.output / 'calibration.csv', index=False)
    per_model.to_csv(args.output / 'per_model_metrics.csv', index=False)
    aggregate.to_csv(args.output / 'aggregate_metrics.csv', index=False)
    loomo_summary.to_csv(args.output / 'loomo_summary.csv', index=False)
    supported_summary.to_csv(args.output / 'supported_loomo_summary.csv', index=False)
    estimator_path = args.output / 'research_estimator.joblib'
    estimator = fit_research_estimator(campaigns[CAMPAIGNS[0]])
    joblib.dump(estimator, estimator_path)
    manifest = {
        'scope': 'Exploratory observed PyTorch CUDA reserved peak on one local GPU; not minimum VRAM or OOM safety',
        'nominal_quantile': QUANTILE,
        'model_selection': 'Exploratory comparison after inspecting pilot data; no confirmation claim',
        'donor_data': CAMPAIGNS[0],
        'evaluation_data': list(CAMPAIGNS),
        'selected_rows': {name: len(frame) for name, frame in campaigns.items()},
        'host': campaigns[CAMPAIGNS[0]].host.iloc[0],
        'device_name': campaigns[CAMPAIGNS[0]].device_name.iloc[0],
        'gpu_indices_json': '[0]',
        'calibration': 'Leave-one-donor-model-out cross-fit, equal weight per donor model, empirical 0.9 quantile',
        'support_gate': 'at least two independent donor models with the same workload_task',
        'folds': int(calibration.shape[0]),
        'source_sha256': sha256(args.snapshot),
        'script_sha256': sha256(Path(__file__)),
        'research_estimator_sha256': sha256(estimator_path),
        'research_estimator_feature_names': FEATURE_NAMES,
        'research_estimator_direct_ratio': estimator['direct_ratio'],
        'numpy_version': np.__version__, 'pandas_version': pd.__version__,
        'sklearn_version': sklearn_version,
    }
    (args.output / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
