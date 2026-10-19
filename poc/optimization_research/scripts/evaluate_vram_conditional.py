"""Exploratory, task/size-conditional upper forecast of observed CUDA peaks.

Outer evaluation holds out whole models. The bandwidth for each outer fold is
selected by a second, donor-only leave-one-model-out evaluation. All point
models and ratio scores exclude the outer target model. These are empirical
upper quantiles of observed allocator peaks, not minimum VRAM or OOM bounds.
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
                                SNAPSHOT, selected_measurements, static_features)
from evaluate_vram_upper import point_predict


OUTPUT = ROOT / 'artifacts/vram_conditional_v1'
QUANTILE = 0.9
BANDWIDTHS = (0.5, 1.0, 2.0, None)  # None is task-only, no size kernel.
METHODS = ('ridge_point_floor', 'global_ratio_q90', 'task_ratio_q90',
           'conditional_h1_q90', 'conditional_h2_q90',
           'donor_selected_q90')
PROFILE_COLUMNS = ('host', 'device', 'device_name', 'gpu_indices_json',
                   'replica_count', 'cpu_core_limit')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bandwidth_name(bandwidth: float | None) -> str:
    return 'task_only' if bandwidth is None else f'h{bandwidth:g}'


def model_balanced_quantile(scores: pd.DataFrame, bandwidth: float | None,
                            parameter_gib: float | None = None) -> float:
    """Weighted empirical q90; every donor model has one vote before kernel."""
    if scores.empty or not (0 < QUANTILE < 1):
        raise ValueError('Nonempty calibration scores are required')
    if bandwidth is not None and (bandwidth <= 0 or parameter_gib is None or
                                  not np.isfinite(parameter_gib) or parameter_gib <= 0):
        raise ValueError('Positive target parameter size and bandwidth required')
    counts = scores.groupby('model_id').size().to_dict()
    ids = scores.model_id.astype(str).to_numpy()
    values = scores.ratio.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Ratios must be finite and positive')
    weight = np.array([1.0 / counts[model_id] for model_id in ids])
    if bandwidth is not None:
        distance = np.log(scores.parameter_gib.to_numpy(dtype=float) /
                          parameter_gib)
        weight *= np.exp(-0.5 * (distance / bandwidth) ** 2)
    if not np.isfinite(weight).all() or weight.sum() <= 0:
        raise ValueError('Invalid conditional weights')
    ordering = np.argsort(values, kind='stable')
    cumulative = np.cumsum(weight[ordering]) / weight.sum()
    index = min(np.searchsorted(cumulative, QUANTILE, side='left'),
                len(values) - 1)
    return max(1.0, float(values[ordering[index]]))


def crossfit_scores(donor: pd.DataFrame) -> pd.DataFrame:
    """Fit each donor model's point predictions without that donor model."""
    models = sorted(donor.model_id.unique())
    if len(models) < 3:
        raise ValueError('At least three donor models needed for cross-fit')
    rows = []
    for held_id in models:
        train = donor.loc[donor.model_id != held_id]
        target = donor.loc[donor.model_id == held_id]
        point = point_predict(train, target, 'direct')
        ratio = target.reserved_peak_gib.to_numpy(dtype=float) / point
        rows.append(pd.DataFrame({
            'model_id': target.model_id.to_numpy(),
            'workload_task': target.workload_task.to_numpy(),
            'parameter_gib': target.parameter_gib.to_numpy(dtype=float),
            'ratio': ratio,
        }))
    return pd.concat(rows, ignore_index=True)


def factor(scores: pd.DataFrame, task: str, parameter_gib: float,
           bandwidth: float | None, global_only: bool = False) -> float:
    if global_only:
        return model_balanced_quantile(scores, None)
    same_task = scores.loc[scores.workload_task == task]
    if same_task.model_id.nunique() < 2:
        raise ValueError('Unsupported task: fewer than two donor models')
    return model_balanced_quantile(same_task, bandwidth, parameter_gib)


def forecasts_from_scores(donor: pd.DataFrame, target: pd.DataFrame,
                          scores: pd.DataFrame,
                          selected_bandwidth: float | None) -> dict[str, np.ndarray]:
    """Predict target cells with factors learned wholly from donor models."""
    if set(donor.model_id).intersection(set(target.model_id)):
        raise ValueError('Target model must be excluded from donor training')
    if set(scores.model_id) != set(donor.model_id):
        raise ValueError('Calibration scores do not match donor models')
    point = point_predict(donor, target, 'direct')
    task_counts = donor.groupby('workload_task').model_id.nunique().to_dict()
    if any(task_counts.get(task, 0) < 2 for task in target.workload_task):
        raise ValueError('Unsupported target task')
    result = {name: point.copy() for name in METHODS}
    for model_id in target.model_id.unique():
        rows = target.model_id.eq(model_id).to_numpy()
        tasks = target.loc[rows, 'workload_task'].unique()
        sizes = target.loc[rows, 'parameter_gib'].unique()
        if len(tasks) != 1 or len(sizes) != 1:
            raise ValueError('Target model registration metadata is inconsistent')
        task, parameter = tasks[0], float(sizes[0])
        assigned = {
            'global_ratio_q90': factor(scores, task, parameter, None,
                                       global_only=True),
            'task_ratio_q90': factor(scores, task, parameter, None),
            'conditional_h1_q90': factor(scores, task, parameter, 1.0),
            'conditional_h2_q90': factor(scores, task, parameter, 2.0),
            'donor_selected_q90': factor(scores, task, parameter,
                                         selected_bandwidth),
        }
        for name, multiplier in assigned.items():
            result[name][rows] = point[rows] * multiplier
    if any(not np.isfinite(values).all() or (values <= 0).any()
           for values in result.values()):
        raise ValueError('Nonfinite or nonpositive forecast')
    return result


def pinball90_mib(y: np.ndarray, forecast: np.ndarray) -> float:
    difference = np.asarray(y, dtype=float) - np.asarray(forecast, dtype=float)
    return float(np.where(difference >= 0, 0.9 * difference,
                          -0.1 * difference).mean() * 1024.0)


def select_bandwidth(donor: pd.DataFrame) -> tuple[float | None, pd.DataFrame]:
    """Inner model-held-out selection on main-campaign donor data only."""
    rows = []
    for inner_held in sorted(donor.model_id.unique()):
        train = donor.loc[donor.model_id != inner_held]
        target = donor.loc[donor.model_id == inner_held]
        task = str(target.workload_task.iloc[0])
        if train.loc[train.workload_task == task].model_id.nunique() < 2:
            continue
        if len(train.model_id.unique()) < 3:
            continue
        scores = crossfit_scores(train)
        point = point_predict(train, target, 'direct')
        y = target.reserved_peak_gib.to_numpy(dtype=float)
        parameter = float(target.parameter_gib.iloc[0])
        for bandwidth in BANDWIDTHS:
            multiplier = factor(scores, task, parameter, bandwidth)
            rows.append({
                'inner_held_model': inner_held,
                'bandwidth': bandwidth_name(bandwidth),
                'pinball90_mib': pinball90_mib(y, point * multiplier),
                'coverage': float(np.mean(point * multiplier >= y)),
            })
    if not rows:
        raise ValueError('Insufficient supported donor models for selection')
    details = pd.DataFrame(rows)
    average = details.groupby('bandwidth').pinball90_mib.mean().to_dict()
    selected = min(BANDWIDTHS,
                   key=lambda bw: (average[bandwidth_name(bw)],
                                   BANDWIDTHS.index(bw)))
    return selected, details


def evaluate(campaigns: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    main = campaigns[CAMPAIGNS[0]]
    models = sorted(main.model_id.unique())
    if set(BERT_FAMILY + (DISTILBERT,)) - set(models):
        raise ValueError('Expected family holdout model missing')
    folds = [(f'loomo:{model_id}', {model_id}) for model_id in models]
    folds += [('bert4', set(BERT_FAMILY)),
              ('bert4_plus_distilbert', set(BERT_FAMILY + (DISTILBERT,)))]
    forecast_rows, selection_rows, abstention_rows = [], [], []
    for fold, held_ids in folds:
        donor = main.loc[~main.model_id.isin(held_ids)]
        chosen, inner = select_bandwidth(donor)
        inner['fold'] = fold
        inner['selected_bandwidth'] = bandwidth_name(chosen)
        selection_rows.append(inner)
        scores = crossfit_scores(donor)
        task_counts = donor.groupby('workload_task').model_id.nunique().to_dict()
        for campaign_name, frame in campaigns.items():
            held = frame.loc[frame.model_id.isin(held_ids)]
            for model_id, target in held.groupby('model_id', sort=True):
                task = str(target.workload_task.iloc[0])
                if task_counts.get(task, 0) < 2:
                    abstention_rows.append({
                        'fold': fold, 'campaign': campaign_name,
                        'model_id': model_id, 'workload_task': task,
                        'reason': 'fewer than two same-task donor models',
                    })
                    continue
                by_method = forecasts_from_scores(donor, target, scores, chosen)
                for method, prediction in by_method.items():
                    forecast_rows.extend({
                        'fold': fold, 'campaign': campaign_name,
                        'model_id': model_id,
                        'workload_group': workload,
                        'method': method,
                        'parameter_gib': float(parameter),
                        'observed_peak_gib': float(observed),
                        'forecast_peak_gib': float(predicted),
                        'selected_bandwidth': bandwidth_name(chosen),
                    } for workload, parameter, observed, predicted in zip(
                        target.workload_group, target.parameter_gib,
                        target.reserved_peak_gib, prediction))
    return (pd.DataFrame(forecast_rows), pd.concat(selection_rows, ignore_index=True),
            pd.DataFrame(abstention_rows))


def summarize(forecasts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = forecasts.copy()
    difference = frame.observed_peak_gib - frame.forecast_peak_gib
    frame['covered'] = (difference <= 0).astype(float)
    frame['excess_mib'] = np.maximum(-difference, 0) * 1024.0
    frame['shortfall_mib'] = np.maximum(difference, 0) * 1024.0
    frame['pinball90_mib'] = np.where(difference >= 0, 0.9 * difference,
                                     -0.1 * difference) * 1024.0
    per_model = frame.groupby(['fold', 'campaign', 'method', 'model_id'],
                              as_index=False).agg(
        observations=('covered', 'size'), coverage=('covered', 'mean'),
        mean_excess_mib=('excess_mib', 'mean'),
        mean_shortfall_mib=('shortfall_mib', 'mean'),
        worst_shortfall_mib=('shortfall_mib', 'max'),
        pinball90_mib=('pinball90_mib', 'mean'),
    )
    aggregate = per_model.groupby(['fold', 'campaign', 'method'],
                                  as_index=False).agg(
        models=('model_id', 'size'), observations=('observations', 'sum'),
        coverage_macro=('coverage', 'mean'),
        mean_excess_mib_macro=('mean_excess_mib', 'mean'),
        mean_shortfall_mib_macro=('mean_shortfall_mib', 'mean'),
        worst_shortfall_mib=('worst_shortfall_mib', 'max'),
        pinball90_mib_macro=('pinball90_mib', 'mean'),
    )
    loomo = per_model.loc[per_model.fold.str.startswith('loomo:')].groupby(
        ['campaign', 'method'], as_index=False).agg(
        models=('model_id', 'size'), observations=('observations', 'sum'),
        coverage_macro=('coverage', 'mean'),
        mean_excess_mib_macro=('mean_excess_mib', 'mean'),
        mean_shortfall_mib_macro=('mean_shortfall_mib', 'mean'),
        worst_shortfall_mib=('worst_shortfall_mib', 'max'),
        pinball90_mib_macro=('pinball90_mib', 'mean'),
    )
    return per_model, aggregate, loomo


def paired_model_bootstrap(per_model: pd.DataFrame) -> pd.DataFrame:
    """Descriptive 8-model uncertainty after exploratory method selection."""
    selected = per_model.loc[per_model.fold.str.startswith('loomo:')]
    rows = []
    for campaign in CAMPAIGNS:
        pivot = selected.loc[selected.campaign == campaign].pivot(
            index='model_id', columns='method', values='pinball90_mib')
        for candidate in ('conditional_h1_q90', 'conditional_h2_q90'):
            for baseline in ('global_ratio_q90', 'task_ratio_q90'):
                difference = (pivot[candidate] -
                              pivot[baseline]).to_numpy(dtype=float)
                generator = np.random.default_rng(42)
                samples = generator.choice(
                    difference, size=(50000, len(difference)), replace=True).mean(axis=1)
                rows.append({
                    'campaign': campaign, 'research_method': candidate,
                    'baseline': baseline, 'models': len(difference),
                    'delta_pinball90_mib': float(difference.mean()),
                    'bootstrap_q025': float(np.quantile(samples, 0.025)),
                    'bootstrap_q975': float(np.quantile(samples, 0.975)),
                    'improved_models': int((difference < 0).sum()),
                    'interpretation': 'exploratory model bootstrap, not a confirmation interval',
                })
    return pd.DataFrame(rows)


def fit_research_estimator(main: pd.DataFrame) -> dict:
    """Fit the fixed h=1 research candidate; refuse unsupported tasks/profiles."""
    if any(main[column].nunique() != 1 for column in PROFILE_COLUMNS):
        raise ValueError('One hardware profile required')
    scores = crossfit_scores(main)
    pipeline = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    pipeline.fit(static_features(main), np.log(main.reserved_peak_gib))
    return {
        'kind': 'research_only_observed_cuda_reserved_peak_conditional',
        'profile': {column: main[column].iloc[0] for column in PROFILE_COLUMNS},
        'model': pipeline,
        'scores': scores,
        'bandwidth': 1.0,
        'bandwidth_name': 'h1',
        'task_model_counts': main.groupby('workload_task').model_id.nunique().to_dict(),
        'training_model_ids': sorted(main.model_id.unique()),
        'registered_parameter_bytes_required': True,
    }


def forecast_with_research_estimator(bundle: dict, target: pd.DataFrame) -> np.ndarray:
    if target.empty:
        raise ValueError('Target frame must be nonempty')
    if set(target.model_id).intersection(bundle['training_model_ids']):
        raise ValueError('Research estimator requires a new model')
    for column, value in bundle['profile'].items():
        if column not in target or not target[column].eq(value).all():
            raise ValueError(f'Unsupported hardware profile: {column}')
    if any(bundle['task_model_counts'].get(task, 0) < 2
           for task in target.workload_task):
        raise ValueError('Unsupported task')
    if target.groupby('model_id').parameter_gib.nunique().max() != 1:
        raise ValueError('Inconsistent parameter size')
    point = np.maximum(target.parameter_gib.to_numpy(dtype=float),
                       np.exp(bundle['model'].predict(static_features(target))))
    prediction = point.copy()
    for model_id, group in target.groupby('model_id'):
        rows = target.model_id.eq(model_id).to_numpy()
        tasks = group.workload_task.unique()
        if len(tasks) != 1:
            raise ValueError('Inconsistent task')
        multiplier = factor(bundle['scores'], tasks[0],
                            float(group.parameter_gib.iloc[0]),
                            bundle['bandwidth'])
        prediction[rows] *= multiplier
    return prediction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    campaigns = selected_measurements(pd.read_parquet(args.snapshot))
    forecasts, selection, abstentions = evaluate(campaigns)
    per_model, aggregate, loomo = summarize(forecasts)
    bootstrap = paired_model_bootstrap(per_model)
    args.output.mkdir(parents=True, exist_ok=True)
    forecasts.to_csv(args.output / 'forecasts.csv', index=False)
    selection.to_csv(args.output / 'donor_selection.csv', index=False)
    abstentions.to_csv(args.output / 'abstentions.csv', index=False)
    per_model.to_csv(args.output / 'per_model_metrics.csv', index=False)
    aggregate.to_csv(args.output / 'aggregate_metrics.csv', index=False)
    loomo.to_csv(args.output / 'supported_loomo_summary.csv', index=False)
    bootstrap.to_csv(args.output / 'paired_model_bootstrap.csv', index=False)
    estimator_path = args.output / 'research_estimator.joblib'
    estimator = fit_research_estimator(campaigns[CAMPAIGNS[0]])
    joblib.dump(estimator, estimator_path)
    selected_full, _ = select_bandwidth(campaigns[CAMPAIGNS[0]])
    manifest = {
        'scope': 'Exploratory observed PyTorch CUDA reserved peak; not minimum VRAM or OOM safety',
        'nominal_quantile': QUANTILE,
        'exploratory_after_inspecting_prior_vram_upper_pilot': True,
        'bandwidth_candidates': [bandwidth_name(value) for value in BANDWIDTHS],
        'bandwidth_selection': 'Inner leave-one-donor-model-out mean 90%-pinball on main campaign only',
        'selected_full_main_bandwidth_diagnostic': bandwidth_name(selected_full),
        'exported_fixed_bandwidth': estimator['bandwidth_name'],
        'exported_candidate_selection': 'Exploratory fixed h=1 candidate chosen after inspecting the same data; not independently validated',
        'support_gate': 'at least two same-task donor models',
        'donor_campaign': CAMPAIGNS[0],
        'evaluation_campaigns': list(CAMPAIGNS),
        'selected_rows': {name: len(frame) for name, frame in campaigns.items()},
        'source_sha256': sha256(args.snapshot),
        'script_sha256': sha256(Path(__file__)),
        'research_estimator_sha256': sha256(estimator_path),
        'static_metadata_assumption': 'model_parameter_bytes is known at registration; source records it after execution',
        'no_outer_target_outcomes_in_training_or_selection': True,
        'numpy_version': np.__version__,
        'pandas_version': pd.__version__,
        'sklearn_version': sklearn_version,
    }
    (args.output / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(loomo.to_string(index=False))
    print(f'Wrote {args.output}')


if __name__ == '__main__':
    main()
