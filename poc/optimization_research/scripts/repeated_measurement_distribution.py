"""Exploratory forecast of one future GPU benchmark execution.

The outcome is the distribution of log batch-p95 latency or log request/s for
an already fixed model/workload/resource cell. Neither deployment actions nor
user SLO outcomes are inferred from these repeated batch benchmarks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import betaln
from scipy.stats import norm, t


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {'khu-hf-direct-collector/v1': (63, 43),
            'khu-hf-direct-collector/v2': (47, 27)}
KEY = ('model_id', 'workload_task', 'batch_size', 'max_length',
       'request_concurrency', 'repeats', 'replica_count', 'cpu_core_limit')
TARGETS = ('log_latency', 'log_rps')
K_VALUES = (2, 3, 5, 10)
SCENARIOS = ('immediate_first_k', 'cutoff_last_k')
METHODS = ('donor_global', 'donor_gpu', 'target_local_normal',
           'shrink_global_5', 'shrink_gpu_5', 'target_local_t')
Z90 = float(norm.ppf(.95))
MIN_LOG_SCALE = .001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_panel(source: pd.DataFrame) -> pd.DataFrame:
    """Keep complete same-version live GPU panels in actual time order."""
    panel = source[(source.campaign == 'khu-hf-live') & source.eligible &
                   (source.success == 1) &
                   (source.device == 'cuda') &
                   (source.cpu_core_limit == 2) &
                   source.collector_version.isin(VERSIONS)].copy()
    panel['start_time'] = pd.to_datetime(panel.started_at, utc=True)
    panel['finish_time'] = pd.to_datetime(panel.finished_at, utc=True)
    if panel.empty or panel.start_time.isna().any() or panel.finish_time.isna().any():
        raise ValueError('Missing GPU live timestamps')
    if (panel.finish_time <= panel.start_time).any():
        raise ValueError('Nonpositive observed execution duration')
    if (panel.wall_seconds <= 0).any() or (panel.latency_p95_ms <= 0).any():
        raise ValueError('Nonpositive execution wall or latency')
    if (panel.examples_per_second <= 0).any() or (panel.batch_size <= 0).any():
        raise ValueError('Nonpositive benchmark throughput or batch size')
    if panel.duplicated(['collector_version', 'run_id', *KEY]).any():
        raise ValueError('Duplicate resource cell within a run')
    run_records = []
    for version, (n_rounds, _) in VERSIONS.items():
        subset = panel[panel.collector_version == version]
        runs = (subset.groupby('run_id', as_index=False)
                .agg(start_time=('start_time', 'min'),
                     finish_time=('finish_time', 'max'))
                .sort_values(['start_time', 'run_id']).reset_index(drop=True))
        if len(runs) != n_rounds or len(subset) != n_rounds * 16:
            raise ValueError(f'Incomplete {version} live panel')
        if (runs.finish_time.iloc[:-1].to_numpy() >=
                runs.start_time.iloc[1:].to_numpy()).any():
            raise ValueError('Live collection runs overlap in time')
        runs['round_rank'] = np.arange(n_rounds, dtype=int)
        runs['collector_version'] = version
        run_records.append(runs[['collector_version', 'run_id', 'round_rank']])
    panel = panel.merge(pd.concat(run_records),
                        on=['collector_version', 'run_id'], validate='many_to_one')
    for version, (n_rounds, _) in VERSIONS.items():
        version_panel = panel[panel.collector_version == version]
        groups = version_panel.groupby(list(KEY), sort=False)
        if len(groups) != 16 or not groups.size().eq(n_rounds).all():
            raise ValueError(f'{version} cells are incomplete')
        expected = list(range(n_rounds))
        if any(sorted(g.round_rank.tolist()) != expected for _, g in groups):
            raise ValueError(f'{version} cells have missing rounds')
        if version_panel.model_id.nunique() != 8:
            raise ValueError(f'{version} needs eight model families')
    panel['log_latency'] = np.log(panel.latency_p95_ms.astype(float))
    panel['log_rps'] = np.log(panel.examples_per_second.astype(float) /
                              panel.batch_size.astype(float))
    panel['elapsed_seconds'] = (panel.finish_time - panel.start_time).dt.total_seconds()
    return panel.sort_values(['collector_version', 'round_rank', 'ordinal']).reset_index(drop=True)


def round_slices(version: str, scenario: str, k: int) -> tuple[list[int], list[int], list[int]]:
    n_rounds, n_prefix = VERSIONS[version]
    if k not in K_VALUES or scenario not in SCENARIOS:
        raise ValueError('Unknown scenario or repeat count')
    if scenario == 'immediate_first_k':
        donor_rounds = list(range(k))
        target_rounds = list(range(k))
    else:
        donor_rounds = list(range(n_prefix))
        target_rounds = list(range(n_prefix - k, n_prefix))
    eval_rounds = list(range(n_prefix, n_rounds))
    assert max(donor_rounds + target_rounds) < min(eval_rounds)
    assert len(eval_rounds) == 20
    return donor_rounds, target_rounds, eval_rounds


def donor_prior(version_panel: pd.DataFrame, held_model: str,
                donor_rounds: list[int], target: str) -> tuple[float, dict[int, float], int]:
    """Pool within-cell variance across donor models only."""
    donor = version_panel[(version_panel.model_id != held_model) &
                          version_panel.round_rank.isin(donor_rounds)].copy()
    if donor.model_id.nunique() != 7 or held_model in set(donor.model_id):
        raise ValueError('Held-out model entered the donor prior')
    counts = donor.groupby(list(KEY)).size()
    if counts.empty or not counts.eq(len(donor_rounds)).all():
        raise ValueError('Donor cells are missing earlier rounds')
    donor['squared_deviation'] = (
        donor[target] - donor.groupby(list(KEY))[target].transform('mean')) ** 2
    global_variance = float(donor.squared_deviation.sum() /
                            (len(donor) - len(counts)))
    gpu_variance = {}
    for gpu_count, gpu_group in donor.groupby('replica_count'):
        gpu_cells = gpu_group.groupby(list(KEY)).ngroups
        gpu_variance[int(gpu_count)] = float(
            gpu_group.squared_deviation.sum() / (len(gpu_group) - gpu_cells))
    if global_variance <= 0 or set(gpu_variance) != {1, 2} or min(gpu_variance.values()) <= 0:
        raise ValueError('Insufficient donor variance')
    return global_variance, gpu_variance, len(donor)


def forecast_holdout(panel: pd.DataFrame, version: str, held_model: str,
                     scenario: str, k: int) -> pd.DataFrame:
    """Forecast without reading held-model outcomes outside selected k rounds."""
    version_panel = panel[panel.collector_version == version]
    donor_rounds, target_rounds, eval_rounds = round_slices(version, scenario, k)
    selected_donor = version_panel[(version_panel.model_id != held_model) &
                                   version_panel.round_rank.isin(donor_rounds)]
    selected_target = version_panel[(version_panel.model_id == held_model) &
                                    version_panel.round_rank.isin(target_rounds)]
    eval_rows = version_panel[version_panel.round_rank.isin(eval_rounds)]
    if selected_target.empty or selected_target.groupby(list(KEY)).size().ne(k).any():
        raise ValueError('Target first/last k probes are incomplete')
    if (max(selected_target.finish_time.max(), selected_donor.finish_time.max()) >=
            eval_rows.start_time.min()):
        raise ValueError('Training or probes do not precede the future evaluation window')
    donor_cost = float((selected_donor.replica_count * selected_donor.wall_seconds).sum())
    prior = {target: donor_prior(version_panel, held_model, donor_rounds, target)
             for target in TARGETS}
    rows = []
    for key, group in selected_target.groupby(list(KEY), sort=True):
        replica_count = int(group.replica_count.iloc[0])
        target_cost = float((group.replica_count * group.wall_seconds).sum())
        elapsed = float(group.elapsed_seconds.sum())
        probe_ids = json.dumps(group.sort_values('round_rank').execution_id.tolist())
        base = dict(zip(KEY, key))
        base.update({'scenario': scenario, 'collector_version': version, 'k': k,
                     'probe_execution_ids_json': probe_ids,
                     'probe_gpu_wall_seconds': target_cost,
                     'probe_elapsed_seconds': elapsed,
                     'donor_training_gpu_wall_seconds': donor_cost,
                     'donor_training_rows': int(len(selected_donor)),
                     'donor_last_round_rank': int(max(donor_rounds)),
                     'target_first_round_rank': int(min(target_rounds)),
                     'target_last_round_rank': int(max(target_rounds)),
                     'eval_first_round_rank': int(min(eval_rounds))})
        for target in TARGETS:
            local = float(group[target].var(ddof=1))
            mean = float(group[target].mean())
            global_var, gpu_prior, donor_rows = prior[target]
            if donor_rows != len(selected_donor):
                raise AssertionError('Donor counts differ between targets')
            gpu_var = gpu_prior[replica_count]
            weights = k - 1
            variances = {
                'donor_global': global_var,
                'donor_gpu': gpu_var,
                'target_local_normal': local,
                'shrink_global_5': (5 * global_var + weights * local) / (5 + weights),
                'shrink_gpu_5': (5 * gpu_var + weights * local) / (5 + weights),
            }
            variances['target_local_t'] = local
            for method, variance in variances.items():
                scale = max(float(np.sqrt(max(variance, 0) * (1 + 1 / k))),
                            MIN_LOG_SCALE)
                family = 'student_t' if method == 'target_local_t' else 'normal'
                degrees = k - 1 if family == 'student_t' else -1
                quantile = float(t.ppf(.95, degrees)) if family == 'student_t' else Z90
                rows.append({**base, 'target': target, 'method': method,
                             'distribution': family, 'degrees_freedom': degrees,
                             'mean_log': mean, 'scale_log': scale,
                             'lower90_log': mean - quantile * scale,
                             'upper90_log': mean + quantile * scale,
                             'donor_global_variance': global_var,
                             'donor_gpu_variance': gpu_var,
                             'target_local_variance': local})
    out = pd.DataFrame(rows)
    if out.empty or out[['mean_log', 'scale_log']].isna().any().any():
        raise ValueError('No finite forecasts')
    return out


def normal_crps(y: np.ndarray, mean: float, scale: float) -> np.ndarray:
    z = (y - mean) / scale
    return scale * (z * (2 * norm.cdf(z) - 1) +
                    2 * norm.pdf(z) - 1 / np.sqrt(np.pi))


def student_t_crps(y: np.ndarray, mean: float, scale: float,
                   degrees: int) -> np.ndarray:
    """Closed-form Student-t CRPS, with a separate Cauchy (df=1) limit."""
    if degrees < 1:
        raise ValueError('Student-t degrees of freedom must be positive')
    z = (y - mean) / scale
    if degrees == 1:
        return scale * (2 / np.pi) * (
            z * np.arctan(z) - .5 * np.log1p(z ** 2) + np.log(2))
    beta_term = float(np.exp(np.log(2 * np.sqrt(degrees) / (degrees - 1)) +
                             betaln(.5, degrees - .5) -
                             2 * betaln(.5, degrees / 2)))
    return scale * (z * (2 * t.cdf(z, degrees) - 1) +
                    2 * (degrees + z ** 2) / (degrees - 1) * t.pdf(z, degrees) -
                    beta_term)


def score_forecasts(version_panel: pd.DataFrame, forecasts: pd.DataFrame,
                    eval_rounds: list[int]) -> pd.DataFrame:
    """Join the previously frozen forecast to future outcomes for scoring."""
    future = version_panel[version_panel.round_rank.isin(eval_rounds)]
    groups = {key: frame.sort_values('round_rank')
              for key, frame in future.groupby(list(KEY), sort=False)}
    records = []
    for row in forecasts.itertuples(index=False):
        key = tuple(getattr(row, column) for column in KEY)
        truth = groups[key]
        if len(truth) != 20:
            raise ValueError('Incomplete future target cell')
        y = truth[row.target].to_numpy(dtype=float)
        if row.distribution == 'student_t':
            degrees = int(row.degrees_freedom)
            nll = -t.logpdf(y, df=degrees, loc=row.mean_log, scale=row.scale_log)
            crps = student_t_crps(y, row.mean_log, row.scale_log, degrees)
        else:
            nll = -norm.logpdf(y, loc=row.mean_log, scale=row.scale_log)
            crps = normal_crps(y, row.mean_log, row.scale_log)
        covered = (y >= row.lower90_log) & (y <= row.upper90_log)
        shared = {column: getattr(row, column) for column in KEY}
        shared.update({'scenario': row.scenario, 'collector_version': row.collector_version,
                       'k': row.k, 'target': row.target, 'method': row.method,
                       'distribution': row.distribution, 'mean_log': row.mean_log,
                       'scale_log': row.scale_log, 'lower90_log': row.lower90_log,
                       'upper90_log': row.upper90_log,
                       'probe_gpu_wall_seconds': row.probe_gpu_wall_seconds})
        for index, (_, actual) in enumerate(truth.iterrows()):
            records.append({**shared, 'execution_id': actual.execution_id,
                            'round_rank': int(actual.round_rank),
                            'actual_log': float(y[index]),
                            'crps': float(crps[index]), 'nll': float(nll[index]),
                            'covered90': bool(covered[index]),
                            'interval_width_log': float(row.upper90_log - row.lower90_log)})
    scored = pd.DataFrame(records)
    if not np.isfinite(scored[['crps', 'nll', 'interval_width_log']].to_numpy()).all():
        raise ValueError('Nonfinite future score')
    return scored


def aggregate(predictions: pd.DataFrame, forecasts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    unit = ['scenario', 'collector_version', *KEY, 'k', 'target', 'method']
    cell = (predictions.groupby(unit, as_index=False)
            .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                 coverage90=('covered90', 'mean'),
                 interval_width_log=('interval_width_log', 'mean'),
                 future_repeats=('execution_id', 'size')))
    model_unit = ['scenario', 'collector_version', 'model_id', 'k', 'target', 'method']
    model = (cell.groupby(model_unit, as_index=False)
             .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                  coverage90=('coverage90', 'mean'),
                  interval_width_log=('interval_width_log', 'mean'),
                  resource_cells=('future_repeats', 'size')))
    costs = (forecasts.drop_duplicates(['scenario', 'collector_version', *KEY, 'k'])
             .groupby(['scenario', 'collector_version', 'model_id', 'k'], as_index=False)
             .agg(target_gpu_wall_seconds=('probe_gpu_wall_seconds', 'sum'),
                  target_elapsed_seconds=('probe_elapsed_seconds', 'sum'),
                  donor_training_gpu_wall_seconds=('donor_training_gpu_wall_seconds', 'first'),
                  target_resource_cells=('replica_count', 'size')))
    model = model.merge(costs, on=['scenario', 'collector_version', 'model_id', 'k'],
                        validate='many_to_one')
    summary = (model.groupby(['scenario', 'collector_version', 'k', 'target', 'method'],
                             as_index=False)
               .agg(crps=('crps', 'mean'), nll=('nll', 'mean'),
                    coverage90=('coverage90', 'mean'),
                    interval_width_log=('interval_width_log', 'mean'),
                    model_count=('model_id', 'size'),
                    mean_target_gpu_wall_seconds_per_model=('target_gpu_wall_seconds', 'mean'),
                    mean_target_elapsed_seconds_per_model=('target_elapsed_seconds', 'mean'),
                    mean_donor_training_gpu_wall_seconds_per_fold=(
                        'donor_training_gpu_wall_seconds', 'mean')))
    comparisons = []
    rng = np.random.default_rng(42)
    for (scenario, version, k, target), group in model.groupby(
            ['scenario', 'collector_version', 'k', 'target']):
        pivot = group.pivot(index='model_id', columns='method', values='crps')
        proposed = pivot['shrink_gpu_5']
        for baseline in ['donor_global', 'donor_gpu', 'target_local_normal',
                         'shrink_global_5', 'target_local_t']:
            if baseline not in pivot:
                continue
            differences = (proposed - pivot[baseline]).to_numpy(dtype=float)
            samples = rng.choice(differences, size=(10000, len(differences)), replace=True)
            low, high = np.quantile(samples.mean(axis=1), [.025, .975])
            comparisons.append({'scenario': scenario, 'collector_version': version,
                                'k': k, 'target': target, 'proposed': 'shrink_gpu_5',
                                'baseline': baseline, 'model_count': len(differences),
                                'mean_crps_difference': float(differences.mean()),
                                'improved_models': int((differences < 0).sum()),
                                'model_bootstrap_95_low': float(low),
                                'model_bootstrap_95_high': float(high)})
    return cell, model, summary, pd.DataFrame(comparisons)


def run(snapshot: Path, output: Path) -> dict:
    panel = prepare_panel(pd.read_parquet(snapshot))
    forecasts = []
    scored = []
    splits = []
    for version, (n_rounds, n_prefix) in VERSIONS.items():
        version_panel = panel[panel.collector_version == version]
        ordered_runs = (version_panel[['round_rank', 'run_id']]
                        .drop_duplicates().sort_values('round_rank'))
        splits.append({'collector_version': version, 'rounds': n_rounds,
                       'train_rounds': n_prefix, 'future_rounds': 20,
                       'first_run_id': ordered_runs.run_id.iloc[0],
                       'last_training_run_id': ordered_runs.run_id.iloc[n_prefix - 1],
                       'first_future_run_id': ordered_runs.run_id.iloc[n_prefix],
                       'last_run_id': ordered_runs.run_id.iloc[-1]})
        for scenario in SCENARIOS:
            for k in K_VALUES:
                _, _, eval_rounds = round_slices(version, scenario, k)
                for held_model in sorted(version_panel.model_id.unique()):
                    predicted = forecast_holdout(panel, version, held_model, scenario, k)
                    forecasts.append(predicted)
                    scored.append(score_forecasts(version_panel, predicted, eval_rounds))
    forecast_frame = pd.concat(forecasts, ignore_index=True)
    prediction_frame = pd.concat(scored, ignore_index=True)
    cell, model, summary, comparisons = aggregate(prediction_frame, forecast_frame)
    output.mkdir(parents=True, exist_ok=True)
    forecast_frame.to_csv(output / 'forecasts.csv', index=False)
    prediction_frame.to_parquet(output / 'predictions.parquet', index=False)
    cell.to_csv(output / 'per_cell.csv', index=False)
    model.to_csv(output / 'per_model.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    comparisons.to_csv(output / 'paired_comparisons.csv', index=False)
    report = {'scope': 'single future batch benchmark run at a fixed GPU resource cell',
              'snapshot_sha256': sha256(snapshot),
              'script_sha256': sha256(Path(__file__)),
              'versions': splits, 'models_per_version': 8,
              'resource_cells_per_version': 16,
              'scenarios': list(SCENARIOS), 'k_values': list(K_VALUES),
              'methods': list(METHODS),
              'forecast_rows': len(forecast_frame),
              'future_scored_rows': len(prediction_frame),
              'scoring_unit': 'future rounds averaged per cell, cells averaged per model, models averaged equally',
              'primary_score': 'CRPS of log outcome; lower is better',
              'target_cost': 'sum of replica_count * wall_seconds for k selected target probes',
              'donor_cost': 'same-version earlier donor benchmark runs; shared historical cost, not free if newly collected',
              'cautions': ['Exploratory protocol chosen after inspecting the same SQLite.',
                           'Only one host and 16 fixed GPU cells per version.',
                           'Earlier target outcomes are masked in the cutoff scenario but existed in the source data.',
                           'No real user SLO, VM creation, or scale action outcome is tested.']}
    (output / 'metrics.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/repeated_measurement_distribution_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output), indent=2, ensure_ascii=False))
