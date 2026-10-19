"""Pilot of one cross-resource probe in model-wide sparse profiling.

Every outer fold holds out a model. All learned quantities, including the
cross-model anchor covariance and GPU2 probe-cost model, use only other models.
The held-out model supplies six selected GPU1/core2 anchors and, in the dual
variants, one selected GPU2/core2 outcome at a previously anchored workload.
The comparison scores the same complete held-out workload/resource grid.

This is an exploratory cost/benefit test, not a novelty claim.
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

from conditional_response import ANCHOR, BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (ROOT, _absolute, _keyed_anchor,
                                    predict_sparse, residual_covariance,
                                    training_oof_anchor_residuals)
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features,
                                    validate_slates)


METHODS = ('zero_shot', 'anchor6', 'anchor8', 'anchor16', 'anchor32',
           'dual_center', 'dual_cheapest')
GPU2 = (2, 2)


def center_cross_probe(design: pd.DataFrame, six_groups: list[str]) -> str:
    """Pick the measured workload nearest the whole-grid feature centroid."""
    if not six_groups or not set(six_groups).issubset(design.index):
        raise ValueError('Invalid six-anchor subset')
    center = design.to_numpy(dtype=float).mean(axis=0)
    distance = ((design.loc[six_groups].to_numpy(dtype=float) - center) ** 2).sum(axis=1)
    return min(zip(distance, six_groups))[1]


def fit_gpu2_cost(train: pd.DataFrame) -> ExtraTreesRegressor:
    """Estimate GPU2/core2 GPU-wall seconds from training models only."""
    rows = train[(train.replica_count == GPU2[0]) &
                 (train.cpu_core_limit == GPU2[1])]
    if rows.empty or (rows.wall_seconds <= 0).any():
        raise ValueError('GPU2/core2 cost training rows missing')
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                               max_features=1.0, random_state=42, n_jobs=2)
    model.fit(rows[list(BASE_FEATURES)], np.log(2.0 * rows.wall_seconds))
    return model


def cheapest_cross_probe(test_raw: pd.DataFrame, six_groups: list[str],
                         cost_model: ExtraTreesRegressor) -> str:
    """Choose the lowest *predicted* cross-probe cost, never observed cost."""
    static = static_features(test_raw, registered_parameter_bytes(test_raw))
    rows = static[(static.replica_count == GPU2[0]) &
                  (static.cpu_core_limit == GPU2[1]) &
                  (static.workload_group.isin(six_groups))].copy()
    if len(rows) != len(six_groups) or rows.workload_group.duplicated().any():
        raise ValueError('Missing or repeated GPU2 candidate')
    predicted_log_cost = cost_model.predict(rows[list(BASE_FEATURES)])
    return min(zip(predicted_log_cost, rows.workload_group))[1]


def dual_correction(six_prediction: pd.DataFrame, test_raw: pd.DataFrame,
                    cross_group: str) -> np.ndarray:
    """Apply one measured GPU2/core2 log-residual to all GPU2 candidates.

    six_prediction already contains the inferred anchors from the six allowed
    GPU1/core2 observations. Only the selected cross-resource result is read.
    """
    target = test_raw[(test_raw.workload_group == cross_group) &
                      (test_raw.replica_count == GPU2[0]) &
                      (test_raw.cpu_core_limit == GPU2[1])]
    predicted = six_prediction[(six_prediction.workload_group == cross_group) &
                               (six_prediction.replica_count == GPU2[0]) &
                               (six_prediction.cpu_core_limit == GPU2[1])]
    if len(target) != 1 or len(predicted) != 1 or (
            target.execution_id.iloc[0] != predicted.execution_id.iloc[0]):
        raise ValueError('Selected GPU2/core2 probe absent or misaligned')
    measured = np.array([
        np.log(float(target.latency_p95_ms.iloc[0])),
        np.log(float(target.examples_per_second.iloc[0] /
                     target.batch_size.iloc[0])),
    ])
    expected = predicted[['space_cov_transfer_latency_log',
                          'space_cov_transfer_rps_log']].to_numpy(dtype=float)[0]
    corrected = six_prediction[['space_cov_transfer_latency_log',
                                'space_cov_transfer_rps_log']].to_numpy(dtype=float).copy()
    corrected[six_prediction.replica_count.to_numpy() == GPU2[0]] += measured - expected
    selected = ((six_prediction.workload_group == cross_group) &
                (six_prediction.replica_count == GPU2[0]) &
                (six_prediction.cpu_core_limit == GPU2[1])).to_numpy()
    corrected[selected] = measured
    return corrected


def predict_fold(test_raw: pd.DataFrame, covariance: np.ndarray, absolute,
                 conditional, cost_model,
                 budgets: tuple[int, ...] = (6, 8, 16, 32)) -> tuple[pd.DataFrame, dict]:
    """Return all policies on the same held-out rows and a probe manifest."""
    validate_slates(test_raw)
    design = group_design(static_features(test_raw,
                                          registered_parameter_bytes(test_raw)))
    groups = {k: select_probes(design, k, 'medoids') for k in budgets}
    if 6 not in groups or 8 not in groups:
        raise ValueError('Need six- and eight-anchor baselines')
    six_groups = groups[6]
    cross_groups = {
        'dual_center': center_cross_probe(design, six_groups),
        'dual_cheapest': cheapest_cross_probe(test_raw, six_groups, cost_model),
    }
    predictions = {k: predict_sparse(test_raw, g, g, covariance,
                                     absolute, conditional) for k, g in groups.items()}
    six = predictions[6]
    result = six[['campaign', 'model_id', 'workload_group', 'execution_id',
                  'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                  'target_throughput_rps']].copy()

    def add(name: str, values: np.ndarray) -> None:
        result[f'{name}_latency_log'] = values[:, 0]
        result[f'{name}_rps_log'] = values[:, 1]

    add('zero_shot', six[['zero_shot_latency_log', 'zero_shot_rps_log']].to_numpy())
    for k, pred in predictions.items():
        if not np.array_equal(result.execution_id.to_numpy(),
                              pred.execution_id.to_numpy()):
            raise ValueError('Prediction populations differ')
        add(f'anchor{k}', pred[['space_cov_transfer_latency_log',
                                'space_cov_transfer_rps_log']].to_numpy())
    for name, group in cross_groups.items():
        add(name, dual_correction(six, test_raw, group))
    return result, {'anchor_groups': groups, 'cross_groups': cross_groups}


def _probe_cost(test_raw: pd.DataFrame, groups: list[str],
                cross_group: str | None = None) -> float:
    anchor = _anchor_rows(test_raw).loc[groups]
    cost = float((anchor.replica_count * anchor.wall_seconds).sum())
    if cross_group is not None:
        extra = test_raw[(test_raw.workload_group == cross_group) &
                         (test_raw.replica_count == GPU2[0]) &
                         (test_raw.cpu_core_limit == GPU2[1])]
        if len(extra) != 1:
            raise ValueError('Missing GPU2 probe cost')
        cost += float(extra.replica_count.iloc[0] * extra.wall_seconds.iloc[0])
    return cost


def paired_bootstrap(metrics: pd.DataFrame, methods: tuple[str, str],
                     column: str, seed: int = 42, replicates: int = 10000) -> dict:
    pivot = metrics.pivot(index='heldout_model_id', columns='method', values=column)
    difference = (pivot[methods[0]] - pivot[methods[1]]).to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = difference[rng.integers(0, len(difference), size=(replicates, len(difference)))].mean(axis=1)
    return {'method_minus_baseline': float(difference.mean()),
            'bootstrap_95pct_ci': np.quantile(draws, [0.025, 0.975]).tolist(),
            'models': len(difference)}


def run(snapshot: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    metrics, manifests, predictions = [], [], []
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        test_raw = main[main.model_id == held_model]
        train = prepare(train_raw)
        absolute = _absolute().fit(train[list(BASE_FEATURES)],
                                   train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', 42)
        cost_model = fit_gpu2_cost(train)
        oof = training_oof_anchor_residuals(train)
        covariance, info = residual_covariance(_keyed_anchor(test_raw), oof)
        pred, manifest = predict_fold(test_raw, covariance, absolute,
                                      conditional, cost_model)
        scores = scenario_metrics(pred, test_raw, methods=METHODS)
        groups = manifest['anchor_groups']
        crossing = manifest['cross_groups']
        for method, values in scores.items():
            if method == 'zero_shot':
                cost = 0.0
            elif method.startswith('anchor'):
                cost = _probe_cost(test_raw, groups[int(method.removeprefix('anchor'))])
            else:
                cost = _probe_cost(test_raw, groups[6], crossing[method])
            metrics.append({'heldout_model_id': held_model, 'method': method,
                            'covariance_mode': info['mode'],
                            'gpu_wall_probe_seconds': cost, **values})
        manifests.append({'heldout_model_id': held_model,
                          'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                          'anchor_groups_json': json.dumps(groups),
                          'cross_groups_json': json.dumps(crossing),
                          'covariance_mode': info['mode']})
        pred['heldout_model_id'] = held_model
        predictions.append(pred)
        print(f'Finished {held_model}', flush=True)
    frame = pd.DataFrame(metrics)
    frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(output / 'predictions.parquet', index=False)
    macro = frame.groupby('method', as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        missed_feasible_groups=('missed_feasible_groups', 'sum'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'))
    comparisons = {}
    for dual in ('dual_center', 'dual_cheapest'):
        for baseline in ('anchor6', 'anchor8', 'anchor16', 'anchor32'):
            comparisons[f'{dual}_vs_{baseline}'] = {
                metric: paired_bootstrap(frame, (dual, baseline), metric)
                for metric in ('latency_male', 'rps_male', 'successful_choices',
                               'slo_violations', 'gpu_wall_probe_seconds')}
    report = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'dependency_sha256': {
            name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
            for name in ('conditional_response.py', 'cross_model_covariance.py',
                         'sparse_anchor_transfer.py', 'probe_design.py')},
        'sklearn_version': sklearn.__version__,
        'split': 'outer leave-one-model-out; inner leave-one-donor-out covariance',
        'dual_measurements': 'six GPU1/core2 anchors and one GPU2/core2 response at a selected anchored workload',
        'cost_unit': 'sum of observed wall_seconds × GPU count for probes only',
        'selection': 'six/eight/sixteen/thirty-two space-filling medoids; one cross-probe chosen by whole-grid feature centroid or other-model predicted cost',
        'adaptation': 'global additive log latency/RPS correction applied to GPU2 candidates',
        'fixed_scored_population': 'all seven nonanchor configs for error, all eight for SLO choice, every heldout workload',
        'macro': macro.to_dict(orient='records'),
        'comparisons': comparisons,
        'caveats': ['Generated SLOs are throughput-dominated.',
                    'Nine models on one host; Qwen has no aligned same-task covariance donors.',
                    'Model parameter bytes assumed available at registration.'],
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/dual_axis_sparse_probe_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.output)
    for row in result['macro']:
        print(row, flush=True)
