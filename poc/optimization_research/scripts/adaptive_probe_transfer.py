"""Exploratory observation-dependent eight-probe residual transfer.

The first four probes are label-free workload medoids.  Four more are chosen
one at a time, using only the residuals of probes already measured.  The rule
weights posterior variance reduction towards workload regions where the
current residual correction is large.  Hyperparameters are fixed in code;
no held-out labels are used to tune them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import sklearn

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (_absolute, _keyed_anchor,
                                    residual_covariance,
                                    training_oof_anchor_residuals,
                                    choose_covariance_probes, predict_sparse)
from evaluate_counterfactual_slo import score_query, training_threshold_grid
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, group_design,
                                    registered_parameter_bytes,
                                    scenario_metrics, select_probes,
                                    static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('adaptive_cov_transfer', 'zero_observation_cov_transfer',
           'space_cov_transfer', 'frozen_cov_transfer')
INITIAL_PROBES = 4
TOTAL_PROBES = 8
EMPHASIS = 2.0
NOISE_FRACTION = 0.05


def posterior_state(covariance: np.ndarray, selected: list[int],
                    residuals: np.ndarray,
                    noise_fraction: float = NOISE_FRACTION
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Return conditional mean/covariance from *selected* observations only."""
    cov = np.asarray(covariance, dtype=float)
    n = cov.shape[0]
    if cov.shape != (n, n, 2) or len(selected) != len(set(selected)):
        raise ValueError('Invalid covariance or repeated selected probe')
    if residuals.shape != (len(selected), 2):
        raise ValueError('Residual shape disagrees with selected probes')
    mean = np.zeros((n, 2))
    conditional = cov.copy()
    for t in range(2):
        noise = noise_fraction * max(float(np.diag(cov[:, :, t]).mean()), 1e-8)
        if selected:
            cross = cov[:, selected, t]
            system = cov[:, :, t][np.ix_(selected, selected)] + noise * np.eye(len(selected))
            solved = np.linalg.solve(system, cross.T)
            mean[:, t] = cross @ np.linalg.solve(system, residuals[:, t])
            conditional[:, :, t] = cov[:, :, t] - cross @ solved
    return mean, conditional


def choose_adaptive_probes(covariance: np.ndarray, initial: list[int],
                           observe: Callable[[int], np.ndarray],
                           budget: int = TOTAL_PROBES,
                           emphasis: float = EMPHASIS
                           ) -> tuple[list[int], list[dict]]:
    """Observe only chosen indices, then prioritize uncertain large corrections.

    The score of candidate i is weighted integrated posterior variance
    reduction: sum_t,j w_j K[j,i,t]^2 / ((K[i,i,t]+noise_t) scale_t).
    Here w_j = 1 + emphasis * clipped standardized posterior-mean magnitude,
    so later choices depend on actual earlier residual measurements.
    """
    cov = np.asarray(covariance, dtype=float)
    n = cov.shape[0]
    if cov.shape != (n, n, 2) or not np.isfinite(cov).all():
        raise ValueError('Expected finite n x n x 2 covariance')
    if (not 1 <= len(initial) <= budget <= n or
            len(set(initial)) != len(initial) or
            any(i < 0 or i >= n for i in initial) or emphasis < 0):
        raise ValueError('Invalid initial probes, budget or emphasis')
    scales = np.maximum(np.diagonal(cov, axis1=0, axis2=1).mean(axis=1), 1e-8)
    selected: list[int] = []
    values: list[np.ndarray] = []
    trace: list[dict] = []

    def add(index: int, score: float | None) -> None:
        value = np.asarray(observe(index), dtype=float)
        if value.shape != (2,) or not np.isfinite(value).all():
            raise ValueError('Observed residual must be a finite two-vector')
        selected.append(index)
        values.append(value)
        trace.append({'step': len(selected), 'index': index,
                      'selection_score': score,
                      'observed_residual_latency': float(value[0]),
                      'observed_residual_rps': float(value[1])})

    for index in initial:
        add(int(index), None)
    while len(selected) < budget:
        mean, conditional = posterior_state(cov, selected, np.stack(values))
        standardized = np.sqrt(0.5 * np.sum(mean ** 2 / scales[None, :], axis=1))
        weight = 1.0 + emphasis * np.minimum(standardized, 3.0)
        score = np.zeros(n)
        for t in range(2):
            noise = NOISE_FRACTION * scales[t]
            score += np.sum(weight[:, None] * conditional[:, :, t] ** 2,
                            axis=0) / ((np.diag(conditional[:, :, t]) + noise) * scales[t])
        score[selected] = -np.inf
        index = int(np.argmax(score))
        add(index, float(score[index]))
    return selected, trace


def _selected_observer(test_raw: pd.DataFrame, absolute, groups: list[str]
                       ) -> Callable[[int], np.ndarray]:
    """Make a callback that reads an anchor outcome only on an explicit call."""
    anchor = _anchor_rows(test_raw)
    known_size = registered_parameter_bytes(test_raw)
    static = static_features(test_raw, known_size)
    static_anchor = _anchor_rows(static).loc[groups]
    base = np.asarray(absolute.predict(static_anchor[list(BASE_FEATURES)]), dtype=float)

    def observe(index: int) -> np.ndarray:
        if not 0 <= index < len(groups):
            raise IndexError(index)
        row = anchor.loc[groups[index]]
        measured = np.array([np.log(float(row.latency_p95_ms)),
                             np.log(float(row.examples_per_second) /
                                    float(row.batch_size))])
        return measured - base[index]
    return observe


def _paired_metrics(frame: pd.DataFrame) -> list[dict]:
    out = []
    rng = np.random.default_rng(42)
    pivot = frame.pivot(index='heldout_model_id', columns='method',
                        values=['latency_male', 'rps_male',
                                'successful_choices', 'slo_violations',
                                'gpu_wall_probe_seconds'])
    for comparator in METHODS[1:]:
        for metric in ('latency_male', 'rps_male', 'successful_choices',
                       'slo_violations', 'gpu_wall_probe_seconds'):
            delta = (pivot[(metric, METHODS[0])] - pivot[(metric, comparator)])
            values = delta.to_numpy(dtype=float)
            resampled = values[rng.integers(0, len(values),
                                           size=(20_000, len(values)))].mean(axis=1)
            out.append({'comparator': comparator, 'metric': metric,
                        'adaptive_minus_comparator_mean_per_model': float(delta.mean()),
                        'adaptive_minus_comparator_total': float(delta.sum()),
                        'descriptive_model_bootstrap_95_interval_mean': [
                            float(x) for x in np.quantile(resampled, [0.025, 0.975])],
                        'improved_models': int((delta < 0).sum() if metric in
                                               ('latency_male', 'rps_male',
                                                'slo_violations', 'gpu_wall_probe_seconds')
                                               else (delta > 0).sum())})
    return out


def _paired_synthetic(frame: pd.DataFrame) -> list[dict]:
    """Model-level differences; repeated thresholds are not independent."""
    by_model = frame.groupby(['heldout_model_id', 'method'], as_index=False)[
        ['successful_choices', 'false_approvals']].sum()
    pivot = by_model.pivot(index='heldout_model_id', columns='method',
                           values=['successful_choices', 'false_approvals'])
    rows = []
    rng = np.random.default_rng(43)
    for comparator in METHODS[1:]:
        for metric in ('successful_choices', 'false_approvals'):
            difference = pivot[(metric, METHODS[0])] - pivot[(metric, comparator)]
            values = difference.to_numpy(dtype=float)
            resampled = values[rng.integers(0, len(values),
                                           size=(20_000, len(values)))].mean(axis=1)
            rows.append({'comparator': comparator, 'metric': metric,
                         'adaptive_minus_comparator_total': int(difference.sum()),
                         'descriptive_model_bootstrap_95_interval_mean': [
                             float(x) for x in np.quantile(resampled, [0.025, 0.975])],
                         'adaptive_minus_comparator_by_model': {
                             str(model): int(value)
                             for model, value in difference.items()}})
    return rows


def decision_subset_counts(scored: pd.DataFrame, latency_slo,
                           target_rps) -> list[dict]:
    """Break decisions out by genuinely mixed and dual-constraint slates."""
    slate = scored.sort_values(['workload_group', 'replica_count',
                                'cpu_core_limit'])
    latency_limit = np.broadcast_to(np.asarray(latency_slo, dtype=float),
                                    (len(slate),))
    throughput_limit = np.broadcast_to(np.asarray(target_rps, dtype=float),
                                       (len(slate),))
    # Per-row generated thresholds must track the sorted slate, not input order.
    if isinstance(latency_slo, pd.Series):
        latency_limit = latency_slo.loc[slate.index].to_numpy(dtype=float)
    if isinstance(target_rps, pd.Series):
        throughput_limit = target_rps.loc[slate.index].to_numpy(dtype=float)
    true_latency = slate.latency_p95_ms.to_numpy(dtype=float)
    true_rps = (slate.examples_per_second /
                slate.batch_size).to_numpy(dtype=float)
    actual = (true_latency <= latency_limit) & (true_rps >= throughput_limit)
    diagnostic = slate[['workload_group']].copy()
    diagnostic['feasible'] = actual
    diagnostic['latency_only_failure'] = ((true_latency > latency_limit) &
                                          (true_rps >= throughput_limit))
    diagnostic['rps_only_failure'] = ((true_latency <= latency_limit) &
                                      (true_rps < throughput_limit))
    by_group = diagnostic.groupby('workload_group').agg(
        feasible_count=('feasible', 'sum'),
        latency_only_failure=('latency_only_failure', 'any'),
        rps_only_failure=('rps_only_failure', 'any'))
    subsets = {
        'all': np.ones(len(by_group), dtype=bool),
        'nontrivial': ((by_group.feasible_count > 0) &
                       (by_group.feasible_count < 8)).to_numpy(),
        'both_constraints_active': (by_group.latency_only_failure &
                                    by_group.rps_only_failure).to_numpy(),
    }
    rows = []
    for method in METHODS:
        predicted = ((slate[f'{method}_latency_log'].to_numpy() <=
                      np.log(latency_limit)) &
                     (slate[f'{method}_rps_log'].to_numpy() >=
                      np.log(throughput_limit)))
        chosen = slate.loc[predicted, ['workload_group']].copy()
        chosen['actual_feasible'] = actual[predicted]
        chosen = chosen.drop_duplicates('workload_group').set_index('workload_group')
        for subset, mask in subsets.items():
            included = by_group.index[mask]
            selected = chosen.loc[chosen.index.intersection(included)]
            rows.append({'method': method, 'subset': subset,
                         'workload_queries': int(mask.sum()),
                         'oracle_feasible': int((by_group.feasible_count[mask] > 0).sum()),
                         'selected': int(len(selected)),
                         'successful_choices': int(selected.actual_feasible.sum()),
                         'false_approvals': int(len(selected) -
                                                selected.actual_feasible.sum())})
    return rows


def run(snapshot: Path, frozen_predictions: Path, output: Path,
        seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    frozen = pd.read_parquet(frozen_predictions)
    frozen = frozen[frozen.budget == TOTAL_PROBES].copy()
    if len(frozen) != len(main) or frozen.execution_id.duplicated().any():
        raise ValueError('Frozen K8 predictions do not cover the main slate once')
    frozen = frozen.set_index('execution_id')
    summaries, synthetic, subset_scenarios, manifest, predicted_parts = [], [], [], [], []
    for held_model in sorted(main.model_id.unique()):
        test_raw = main[main.model_id == held_model].copy()
        train_raw = main[main.model_id != held_model].copy()
        trained = prepare(train_raw)
        absolute = _absolute().fit(trained[list(BASE_FEATURES)],
                                   trained[['true_log_latency',
                                            'true_log_requests_per_second']])
        conditional = fit_model(trained, 'anchor_conditioned', seed)
        donor_oof = training_oof_anchor_residuals(trained)
        anchors = _keyed_anchor(test_raw)
        covariance, info = residual_covariance(anchors, donor_oof)
        groups = anchors.workload_group.tolist()
        index = {group: i for i, group in enumerate(groups)}
        design = group_design(static_features(test_raw,
                            registered_parameter_bytes(test_raw)))
        initial_groups = select_probes(design, INITIAL_PROBES, 'medoids', seed)
        initial = [index[group] for group in initial_groups]
        adaptive, trace = choose_adaptive_probes(
            covariance, initial, _selected_observer(test_raw, absolute, groups))
        adaptive_groups = [groups[i] for i in adaptive]
        zero_observation, _ = choose_adaptive_probes(
            covariance, initial, lambda _index: np.zeros(2))
        zero_observation_groups = [groups[i] for i in zero_observation]
        space_groups = select_probes(design, TOTAL_PROBES, 'medoids', seed)
        covariance_groups = [groups[int(i)] for i in
                             choose_covariance_probes(covariance, TOTAL_PROBES)]
        if len(adaptive_groups) != TOTAL_PROBES:
            raise AssertionError('Adaptive selection did not meet budget')
        pred = predict_sparse(test_raw, adaptive_groups, space_groups,
                              covariance, absolute, conditional)
        pred = pred.rename(columns={'cov_transfer_latency_log':
                                    'adaptive_cov_transfer_latency_log',
                                    'cov_transfer_rps_log':
                                    'adaptive_cov_transfer_rps_log'})
        no_observation_pred = predict_sparse(
            test_raw, zero_observation_groups, space_groups,
            covariance, absolute, conditional)
        for metric in ('latency_log', 'rps_log'):
            pred[f'zero_observation_cov_transfer_{metric}'] = (
                no_observation_pred[f'cov_transfer_{metric}'].to_numpy())
        baseline = frozen.loc[pred.execution_id]
        for metric in ('latency_log', 'rps_log'):
            np.testing.assert_allclose(pred[f'space_cov_transfer_{metric}'],
                                       baseline[f'space_cov_transfer_{metric}'],
                                       atol=1e-10, rtol=1e-10)
            pred[f'frozen_cov_transfer_{metric}'] = baseline[
                f'cov_transfer_{metric}'].to_numpy()
        result = scenario_metrics(pred, test_raw, methods=METHODS)
        anchor_by_group = _anchor_rows(test_raw)
        selectors = {'adaptive': adaptive_groups,
                     'zero_observation': zero_observation_groups,
                     'space_filling': space_groups,
                     'covariance': covariance_groups}
        names = {'adaptive_cov_transfer': 'adaptive',
                 'zero_observation_cov_transfer': 'zero_observation',
                 'space_cov_transfer': 'space_filling',
                 'frozen_cov_transfer': 'covariance'}
        cost = {name: float((anchor_by_group.loc[chosen].replica_count *
                             anchor_by_group.loc[chosen].wall_seconds).sum())
                for name, chosen in selectors.items()}
        for method, vals in result.items():
            summaries.append({'heldout_model_id': held_model,
                              'method': method, 'selector': names[method],
                              'covariance_mode': info['mode'],
                              'gpu_wall_probe_seconds': cost[names[method]],
                              **vals})
        for selector, selected_groups in selectors.items():
            manifest.append({'heldout_model_id': held_model,
                             'training_model_ids_json': json.dumps(
                                 sorted(train_raw.model_id.unique())),
                             'selector': selector, 'budget': TOTAL_PROBES,
                             'initial_probe_groups_json': json.dumps(initial_groups)
                             if selector in ('adaptive', 'zero_observation') else None,
                             'probe_groups_json': json.dumps(selected_groups),
                             'selection_trace_json': json.dumps(trace)
                             if selector == 'adaptive' else None,
                             'zero_observation_probe_groups_json': json.dumps(
                                 zero_observation_groups)
                             if selector == 'adaptive' else None,
                             'changed_later_probes_vs_zero_observation': len(
                                 set(adaptive_groups) - set(zero_observation_groups))
                             if selector == 'adaptive' else None,
                             'gpu_wall_probe_seconds': cost[selector]})
        scored = pred.merge(test_raw[['execution_id', 'latency_p95_ms',
                                      'examples_per_second', 'batch_size']],
                            on='execution_id', validate='one_to_one')
        for row in decision_subset_counts(
                scored, scored.latency_slo_ms, scored.target_throughput_rps):
            subset_scenarios.append({'source': 'generated',
                                     'heldout_model_id': held_model, **row})
        for threshold in training_threshold_grid(train_raw):
            diagnostic, scored_methods = score_query(scored, threshold, methods=METHODS)
            for row in scored_methods:
                synthetic.append({'heldout_model_id': held_model, **threshold,
                                  **diagnostic, **row})
            for row in decision_subset_counts(
                    scored, threshold['latency_slo_ms'], threshold['target_rps']):
                subset_scenarios.append({
                    'source': 'train_quantile_synthetic',
                    'heldout_model_id': held_model,
                    'latency_quantile': threshold['latency_quantile'],
                    'rps_quantile': threshold['rps_quantile'], **row})
        pred['heldout_model_id'] = held_model
        predicted_parts.append(pred)
        print(f'Finished {held_model}: {info["mode"]}, adaptive wall={cost["adaptive"]:.3f}s',
              flush=True)
    output.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(summaries)
    synthetic_frame = pd.DataFrame(synthetic)
    subset_frame = pd.DataFrame(subset_scenarios)
    summary.to_csv(output / 'per_model_metrics.csv', index=False)
    synthetic_frame.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    subset_frame.to_csv(output / 'per_model_slo_subset.csv', index=False)
    pd.DataFrame(manifest).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predicted_parts, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    aggregate = summary.groupby('method', as_index=False).agg(
        model_count=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'))
    synthetic_aggregate = synthetic_frame.groupby('method', as_index=False).agg(
        oracle_feasible=('oracle_feasible', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        false_approvals=('false_approvals', 'sum'),
        missed_feasible=('missed_feasible', 'sum'))
    subset_aggregate = subset_frame.groupby(
        ['source', 'subset', 'method'], as_index=False).agg(
            workload_queries=('workload_queries', 'sum'),
            oracle_feasible=('oracle_feasible', 'sum'),
            selected=('selected', 'sum'),
            successful_choices=('successful_choices', 'sum'),
            false_approvals=('false_approvals', 'sum'))
    report = {
        'scope': 'exploratory existing-SQLite local host only',
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'frozen_predictions_sha256': hashlib.sha256(
            frozen_predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__, 'seed': seed,
        'outer_split': 'leave one model_id out; donor residuals inner leave-one-donor-model-out',
        'adaptive_rule': {'initial_medoids': INITIAL_PROBES,
                          'total_probes': TOTAL_PROBES,
                          'posterior_mean_weight_emphasis': EMPHASIS,
                          'posterior_mean_clip': 3.0,
                          'observation_noise_fraction': NOISE_FRACTION},
        'heldout_information': 'only selected GPU1/two-core anchor outcomes',
        'main_slo': 'collector-generated, not independent user SLO',
        'synthetic_slo': '25 train-model-quantile threshold pairs per held-out model; correlated queries',
        'aggregate': aggregate.to_dict(orient='records'),
        'paired_main': _paired_metrics(summary),
        'synthetic_aggregate': synthetic_aggregate.to_dict(orient='records'),
        'subset_aggregate': subset_aggregate.to_dict(orient='records'),
        'paired_synthetic': _paired_synthetic(synthetic_frame),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--frozen-predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/adaptive_probe_transfer_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.frozen_predictions, args.output)
    print(json.dumps({'aggregate': result['aggregate'],
                      'synthetic_aggregate': result['synthetic_aggregate']}, indent=2))
