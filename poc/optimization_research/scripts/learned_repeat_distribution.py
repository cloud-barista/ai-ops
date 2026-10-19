"""Nested, donor-only calibration of repeated benchmark forecasts.

This exploratory experiment predicts one future batch benchmark result at a
fixed GPU resource cell. The outer target model is completely absent from
hyperparameter selection; neither a user SLO nor a scale action is observed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from repeated_measurement_distribution import (
    KEY, MIN_LOG_SCALE, ROOT, TARGETS, VERSIONS, forecast_holdout,
    normal_crps, prepare_panel, score_forecasts,
)

K_VALUES = (2, 5)
PSEUDO_COUNTS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
SCALE_MULTIPLIERS = (0.75, 1.0, 1.25, 1.5)
BASELINES = ('donor_gpu', 'shrink_gpu_5', 'target_local_normal',
             'target_local_t')
LEARNED_METHODS = {'inner_crps': 'nested_donor_crps',
                   'inner_nll': 'nested_donor_nll'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def gpu_variances(source: pd.DataFrame, eligible_models: set[str],
                  rounds: range, target: str) -> dict[int, float]:
    """Pooled within-cell variance, excluding any model outside eligible_models."""
    donor = source[source.model_id.isin(eligible_models) &
                   source.round_rank.isin(rounds)].copy()
    if not eligible_models or set(donor.model_id) != eligible_models:
        raise ValueError('Incomplete donor models')
    counts = donor.groupby(list(KEY)).size()
    if counts.empty or not counts.eq(len(rounds)).all() or len(rounds) < 2:
        raise ValueError('Incomplete donor rounds')
    donor['sq_dev'] = (donor[target] -
                       donor.groupby(list(KEY))[target].transform('mean')) ** 2
    result = {}
    for gpu, frame in donor.groupby('replica_count'):
        cells = frame.groupby(list(KEY)).ngroups
        result[int(gpu)] = float(frame.sq_dev.sum() / (len(frame) - cells))
    if set(result) != {1, 2} or min(result.values()) <= 0:
        raise ValueError('Missing or zero GPU-specific donor variance')
    return result


def calibration_cells(version_panel: pd.DataFrame, held_model: str | None,
                      target: str, k: int) -> tuple[pd.DataFrame, dict]:
    """Build inner rolling-origin forecasts using only the seven donor models.

    Each inner target uses its last k runs before the inner origin. The other
    six models contribute only runs preceding that origin. The next 20 runs
    give CRPS labels. The outer held model is never read here.
    """
    version = version_panel.collector_version.iloc[0]
    _, outer_prefix = VERSIONS[version]
    inner_origin = outer_prefix - 20
    if inner_origin <= k:
        raise ValueError('Not enough early rounds for nested calibration')
    donors = (version_panel[version_panel.model_id != held_model]
              if held_model is not None else version_panel)
    donor_models = set(donors.model_id)
    expected_models = 7 if held_model is not None else 8
    if len(donor_models) != expected_models:
        raise ValueError('Unexpected donor model count')
    future = donors[donors.round_rank.between(inner_origin, outer_prefix - 1)]
    prior_rows = donors[donors.round_rank < inner_origin]
    if prior_rows.finish_time.max() >= future.start_time.min():
        raise ValueError('Inner donor prior overlaps validation')
    records = []
    for inner_target in sorted(donor_models):
        prior_models = donor_models - {inner_target}
        gpu_var = gpu_variances(donors, prior_models, range(inner_origin), target)
        probe = donors[(donors.model_id == inner_target) &
                       donors.round_rank.between(inner_origin - k, inner_origin - 1)]
        if probe.finish_time.max() >= future.start_time.min():
            raise ValueError('Inner target probes overlap validation')
        pseudo_future = future[future.model_id == inner_target]
        for key, group in probe.groupby(list(KEY), sort=True):
            if len(group) != k:
                raise ValueError('Incomplete inner target probe')
            y = pseudo_future
            for column, value in zip(KEY, key):
                y = y[y[column] == value]
            y = y.sort_values('round_rank')
            if len(y) != 20:
                raise ValueError('Incomplete inner future cell')
            records.append({'inner_target_model': inner_target,
                            'gpu': int(group.replica_count.iloc[0]),
                            'mean': float(group[target].mean()),
                            'local_var': float(group[target].var(ddof=1)),
                            'donor_var': gpu_var[int(group.replica_count.iloc[0])],
                            'future_y': y[target].to_numpy(dtype=float)})
    expected_cells = donors.groupby(list(KEY)).ngroups
    if len(records) != expected_cells:
        raise ValueError('Incomplete inner target resource cells')
    cells = pd.DataFrame(records)
    meta = {'inner_origin_round_rank': inner_origin,
            'inner_validation_first_round_rank': inner_origin,
            'inner_validation_last_round_rank': outer_prefix - 1,
            'inner_validation_rows': int(len(cells) * 20),
            'inner_donor_models': expected_models,
            'outer_model_excluded': held_model}
    return cells, meta


def variance_mix(donor_var: np.ndarray, local_var: np.ndarray,
                 k: int, pseudo_count: float) -> np.ndarray:
    if pseudo_count < 0:
        raise ValueError('pseudo_count must be nonnegative')
    weight = k - 1
    return ((pseudo_count * donor_var + weight * local_var) /
            (pseudo_count + weight))


def candidate_scores(cells: pd.DataFrame, k: int, pseudo_count: float,
                     scale_multiplier: float) -> dict[str, float]:
    mean = cells['mean'].to_numpy(dtype=float)[:, None]
    local = cells.local_var.to_numpy(dtype=float)
    donor = cells.donor_var.to_numpy(dtype=float)
    y = np.stack(cells.future_y.to_numpy())
    variance = variance_mix(donor, local, k, pseudo_count)
    scale = np.maximum(np.sqrt(variance * (1 + 1 / k)) * scale_multiplier,
                       MIN_LOG_SCALE)[:, None]
    crps = normal_crps(y, mean, scale).mean(axis=1)
    nll = (-norm.logpdf(y, loc=mean, scale=scale)).mean(axis=1)
    per_model = pd.DataFrame({'model': cells.inner_target_model,
                              'crps': crps, 'nll': nll}).groupby('model').mean()
    return {'inner_crps': float(per_model.crps.mean()),
            'inner_nll': float(per_model.nll.mean())}


def candidate_crps(cells: pd.DataFrame, k: int, pseudo_count: float,
                   scale_multiplier: float) -> float:
    return candidate_scores(cells, k, pseudo_count, scale_multiplier)['inner_crps']


def select_hyperparameters(cells: pd.DataFrame, k: int,
                           objective: str = 'inner_crps') -> tuple[dict, pd.DataFrame]:
    if objective not in LEARNED_METHODS:
        raise ValueError('Unknown donor calibration objective')
    grid = pd.DataFrame([
        {'pseudo_count': count, 'scale_multiplier': scale,
         **candidate_scores(cells, k, count, scale)}
        for count in PSEUDO_COUNTS for scale in SCALE_MULTIPLIERS
    ])
    grid = grid.sort_values([objective, 'pseudo_count', 'scale_multiplier']).reset_index(drop=True)
    if not np.isfinite(grid[['inner_crps', 'inner_nll']].to_numpy()).all():
        raise ValueError('Nonfinite inner-validation scores')
    return grid.iloc[0].to_dict(), grid


def learned_forecast(baseline: pd.DataFrame, pseudo_count: float,
                     scale_multiplier: float, k: int,
                     method: str = 'nested_donor_crps') -> pd.DataFrame:
    if method not in LEARNED_METHODS.values():
        raise ValueError('Unknown learned method')
    prior = baseline[baseline.method == 'shrink_gpu_5'].copy()
    if prior.empty or prior.target.nunique() != 1:
        raise ValueError('Expected fixed-shrink baseline cells for one target')
    variance = variance_mix(prior.donor_gpu_variance.to_numpy(dtype=float),
                            prior.target_local_variance.to_numpy(dtype=float),
                            k, pseudo_count)
    scale = np.maximum(np.sqrt(variance * (1 + 1 / k)) * scale_multiplier,
                       MIN_LOG_SCALE)
    prior['method'] = method
    prior['distribution'] = 'normal'
    prior['degrees_freedom'] = -1
    prior['scale_log'] = scale
    prior['lower90_log'] = prior.mean_log - norm.ppf(.95) * scale
    prior['upper90_log'] = prior.mean_log + norm.ppf(.95) * scale
    prior['pseudo_count'] = pseudo_count
    prior['scale_multiplier'] = scale_multiplier
    return prior


def fit_full_models(panel: pd.DataFrame) -> list[dict]:
    """Fit reusable donor priors on all eight historical models, before test."""
    fitted = []
    for version, (_, prefix) in VERSIONS.items():
        source = panel[panel.collector_version == version]
        names = sorted(source.model_id.unique())
        for k in K_VALUES:
            for target in TARGETS:
                cells, meta = calibration_cells(source, None, target, k)
                prior = gpu_variances(source, set(names), range(prefix), target)
                for objective, method in LEARNED_METHODS.items():
                    best, _ = select_hyperparameters(cells, k, objective)
                    fitted.append({'collector_version': version,
                                   'method': method, 'objective': objective,
                                   'target': target, 'k': k,
                                   'historical_model_ids': names,
                                   'training_round_rank_end_exclusive': prefix,
                                   'gpu_log_variance': {str(gpu): value
                                                        for gpu, value in prior.items()},
                                   'pseudo_count': best['pseudo_count'],
                                   'scale_multiplier': best['scale_multiplier'],
                                   'inner_crps': best['inner_crps'],
                                   'inner_nll': best['inner_nll'],
                                   'inner_origin_round_rank': meta['inner_origin_round_rank'],
                                   'uses_outer_test_rounds': False})
    return fitted


def predict_from_trained_model(model: dict, gpu_count: int,
                               observed_log_values: list[float]) -> dict:
    """Apply a full historical prior to k new same-cell log measurements."""
    values = np.asarray(observed_log_values, dtype=float)
    k = int(model['k'])
    if len(values) != k or not np.isfinite(values).all():
        raise ValueError(f'Need exactly {k} finite log measurements')
    if str(gpu_count) not in model['gpu_log_variance']:
        raise ValueError('GPU count outside trained support')
    local = float(np.var(values, ddof=1))
    donor = float(model['gpu_log_variance'][str(gpu_count)])
    variance = float(variance_mix(np.array([donor]), np.array([local]), k,
                                  float(model['pseudo_count']))[0])
    scale = max(float(np.sqrt(variance * (1 + 1 / k)) *
                      float(model['scale_multiplier'])), MIN_LOG_SCALE)
    mean = float(values.mean())
    q = float(norm.ppf(.95))
    return {'distribution': 'normal', 'mean_log': mean, 'scale_log': scale,
            'lower90_log': mean - q * scale, 'upper90_log': mean + q * scale}


def evaluate(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame,
                                             pd.DataFrame, pd.DataFrame]:
    forecasts, predictions, selections, grids = [], [], [], []
    for version, (_, outer_prefix) in VERSIONS.items():
        source = panel[panel.collector_version == version]
        for held in sorted(source.model_id.unique()):
            for k in K_VALUES:
                fixed = forecast_holdout(panel, version, held, 'cutoff_last_k', k)
                for target in TARGETS:
                    cells, meta = calibration_cells(source, held, target, k)
                    baseline = fixed[(fixed.target == target) &
                                     fixed.method.isin(BASELINES)].copy()
                    learned = []
                    for objective, method in LEARNED_METHODS.items():
                        best, grid = select_hyperparameters(cells, k, objective)
                        learned.append(learned_forecast(fixed[fixed.target == target],
                                                        best['pseudo_count'],
                                                        best['scale_multiplier'], k, method))
                        if objective == 'inner_crps':
                            grid.insert(0, 'collector_version', version)
                            grid.insert(1, 'held_model', held)
                            grid.insert(2, 'target', target)
                            grid.insert(3, 'k', k)
                            grids.append(grid)
                        donor = source[(source.model_id != held) &
                                       (source.round_rank < outer_prefix)]
                        target_probes = source[(source.model_id == held) &
                                               source.round_rank.between(outer_prefix - k,
                                                                         outer_prefix - 1)]
                        row = {'collector_version': version, 'held_model': held,
                               'target': target, 'k': k, 'method': method,
                               'objective': objective, **meta, **best,
                               'donor_training_rows': int(len(donor)),
                               'donor_gpu_wall_seconds': float((donor.replica_count *
                                                                donor.wall_seconds).sum()),
                               'target_gpu_wall_seconds': float((target_probes.replica_count *
                                                                 target_probes.wall_seconds).sum()),
                               'outer_future_first_round_rank': outer_prefix}
                        selections.append(row)
                    forecast = pd.concat([baseline, *learned], ignore_index=True)
                    if forecast.model_id.nunique() != 1 or set(forecast.model_id) != {held}:
                        raise AssertionError('Target model mismatch')
                    forecasts.append(forecast)
                    predictions.append(score_forecasts(source, forecast,
                                                       list(range(outer_prefix,
                                                                  outer_prefix + 20))))
    forecasts = pd.concat(forecasts, ignore_index=True)
    predictions = pd.concat(predictions, ignore_index=True)
    selections = pd.DataFrame(selections)
    grids = pd.concat(grids, ignore_index=True)
    model = (predictions.groupby(['collector_version', 'model_id', 'k', 'target',
                                  'method'], as_index=False)
             .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                  coverage90=('covered90', 'mean'),
                  interval_width_log=('interval_width_log', 'mean')))
    summary = (model.groupby(['collector_version', 'k', 'target', 'method'],
                             as_index=False)
               .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                    coverage90=('coverage90', 'mean'),
                    interval_width_log=('interval_width_log', 'mean'),
                    model_count=('model_id', 'size')))
    return forecasts, predictions, selections, grids, summary


def run(snapshot: Path, output: Path) -> dict:
    panel = prepare_panel(pd.read_parquet(snapshot))
    forecasts, predictions, selections, grids, summary = evaluate(panel)
    full_models = fit_full_models(panel)
    per_model = (predictions.groupby(['collector_version', 'model_id', 'k',
                                      'target', 'method'], as_index=False)
                 .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                      coverage90=('covered90', 'mean'),
                      interval_width_log=('interval_width_log', 'mean')))
    paired = []
    for (version, k, target), group in per_model.groupby(
            ['collector_version', 'k', 'target']):
        table = group.set_index(['model_id', 'method'])
        for learned in LEARNED_METHODS.values():
            for baseline in BASELINES:
                compared = []
                for model in sorted(group.model_id.unique()):
                    proposed = table.loc[(model, learned)]
                    reference = table.loc[(model, baseline)]
                    compared.append({'crps': proposed.crps - reference.crps,
                                     'nll': proposed.nll - reference.nll,
                                     'coverage90': proposed.coverage90 - reference.coverage90})
                delta = pd.DataFrame(compared)
                paired.append({'collector_version': version, 'k': k, 'target': target,
                               'learned_method': learned, 'baseline': baseline,
                               'model_count': len(delta),
                               'mean_crps_difference': float(delta.crps.mean()),
                               'crps_improved_models': int((delta.crps < 0).sum()),
                               'mean_nll_difference': float(delta.nll.mean()),
                               'mean_coverage90_difference': float(delta.coverage90.mean())})
    paired = pd.DataFrame(paired)
    cost = (selections.drop_duplicates(['collector_version', 'held_model', 'k'])
            .groupby(['collector_version', 'k'], as_index=False)
            .agg(mean_target_gpu_wall_seconds_per_model=('target_gpu_wall_seconds', 'mean'),
                 mean_donor_gpu_wall_seconds_per_fold=('donor_gpu_wall_seconds', 'mean'),
                 mean_donor_training_rows=('donor_training_rows', 'mean')))
    output.mkdir(parents=True, exist_ok=True)
    forecasts.to_csv(output / 'forecasts.csv', index=False)
    predictions.to_parquet(output / 'predictions.parquet', index=False)
    selections.to_csv(output / 'selected_hyperparameters.csv', index=False)
    grids.to_csv(output / 'inner_grid_scores.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    per_model.to_csv(output / 'per_model.csv', index=False)
    paired.to_csv(output / 'paired_comparisons.csv', index=False)
    cost.to_csv(output / 'cost_summary.csv', index=False)
    (output / 'trained_models.json').write_text(json.dumps({
        'snapshot_sha256': digest(snapshot),
        'script_sha256': digest(Path(__file__)),
        'input_schema': {'collector_version': 'separate v1/v2 model; same host',
                         'gpu_count': [1, 2],
                         'observed_log_values': 'exactly k prior runs of log batch p95 latency (ms) or log benchmark requests/s'},
        'output_schema': 'Normal distribution in log outcome for one future run; 90% interval',
        'fitted_models': full_models,
        'scope': 'Exploratory, fixed historical GPU resource cell; no deployment or scale action',
    }, indent=2, ensure_ascii=False) + '\n')
    report = {'research_scope': 'future single-run log latency/RPS distribution at fixed GPU cell',
              'snapshot_sha256': digest(snapshot),
              'script_sha256': digest(Path(__file__)),
              'outer_test_rounds_per_version': 20,
              'inner_validation_rounds_per_version': 20,
              'k_values': list(K_VALUES),
              'candidate_pseudo_counts': list(PSEUDO_COUNTS),
              'candidate_scale_multipliers': list(SCALE_MULTIPLIERS),
              'outer_held_models': 8,
              'forecast_rows': len(forecasts),
              'future_scored_rows': len(predictions),
              'selection_rows': len(selections),
              'full_trained_models': len(full_models),
              'paired_comparison_rows': len(paired),
              'grid_rows': len(grids),
              'main_score': 'CRPS on log outcome, model-balanced; smaller is better',
              'warning': 'Exploratory reuse of the same SQLite; no independent host, application SLO or scale-action outcome.'}
    (output / 'metrics.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/learned_repeat_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output), indent=2, ensure_ascii=False))
