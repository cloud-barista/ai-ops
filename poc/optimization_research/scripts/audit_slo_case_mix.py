"""Audit synthetic SLO scenario difficulty and recommendation errors.

The frozen predictions use eight profiled workload anchors per held-out model.
Thresholds here are determined only from the other models in each outer fold.
An exact-workload condition variant is available where donor models share the
same input condition; it is an exploratory stress test, not user SLO data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cross_model_covariance import KEY_COLUMNS
from evaluate_counterfactual_slo import QUANTILES, training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, validate_slates


ROOT = Path(__file__).resolve().parents[1]
METHODS = (
    'space_cov_transfer', 'space_kernel_transfer',
    'space_ridge', 'space_global_mean',
)
CASE_BUCKETS = ('none', 'partial', 'all_eight')


def condition_key(row: pd.Series) -> tuple:
    """Static input key; no measured outcome is used for donor matching."""
    return tuple(row[column] for column in KEY_COLUMNS)


def score_slate(slate: pd.DataFrame, threshold: dict,
                measured_probe_groups: set[str],
                methods: tuple[str, ...] = METHODS) -> list[dict]:
    """Choose a minimum resource candidate and separate three oracle cases."""
    ordered = slate.sort_values(['replica_count', 'cpu_core_limit'])
    if len(ordered) != 8 or ordered.workload_group.nunique() != 1:
        raise ValueError('Expected one complete eight-cell workload slate')
    latency_target = float(threshold['latency_slo_ms'])
    rps_target = float(threshold['target_rps'])
    if latency_target <= 0 or rps_target <= 0:
        raise ValueError('SLO targets must be positive')
    truth = ((ordered.latency_p95_ms.to_numpy(dtype=float) <= latency_target) &
             ((ordered.examples_per_second /
               ordered.batch_size).to_numpy(dtype=float) >= rps_target))
    latency_only_failure = (
        (ordered.latency_p95_ms.to_numpy(dtype=float) > latency_target) &
        ((ordered.examples_per_second /
          ordered.batch_size).to_numpy(dtype=float) >= rps_target)
    )
    rps_only_failure = (
        (ordered.latency_p95_ms.to_numpy(dtype=float) <= latency_target) &
        ((ordered.examples_per_second /
          ordered.batch_size).to_numpy(dtype=float) < rps_target)
    )
    both_constraints_active = int(latency_only_failure.any() and
                                  rps_only_failure.any())
    feasible_count = int(truth.sum())
    bucket = ('none' if feasible_count == 0 else
              'all_eight' if feasible_count == 8 else 'partial')
    group = str(ordered.workload_group.iloc[0])
    outcomes = []
    for method in methods:
        predicted = ((ordered[f'{method}_latency_log'].to_numpy(dtype=float) <=
                      np.log(latency_target)) &
                     (ordered[f'{method}_rps_log'].to_numpy(dtype=float) >=
                      np.log(rps_target)))
        candidates = np.flatnonzero(predicted)
        index = int(candidates[0]) if len(candidates) else None
        selected = index is not None
        success = bool(truth[index]) if selected else False
        measured_anchor = bool(
            selected and group in measured_probe_groups and
            ordered.replica_count.iloc[index] == 1 and
            ordered.cpu_core_limit.iloc[index] == 2
        )
        outcomes.append({
            'method': method, 'case_bucket': bucket,
            'feasible_count': feasible_count,
            'both_constraints_active': both_constraints_active,
            'selected': int(selected), 'successful_choices': int(success),
            'false_approvals': int(selected and not success),
            'measured_anchor_selected': int(measured_anchor),
            'measured_anchor_selected_success': int(measured_anchor and success),
        })
    return outcomes


def matched_donor_rows(train: pd.DataFrame,
                       static_key: tuple) -> pd.DataFrame:
    """Return training rows from other models at the exact static workload."""
    mask = np.ones(len(train), dtype=bool)
    for column, value in zip(KEY_COLUMNS, static_key):
        mask &= train[column].to_numpy() == value
    donors = train.loc[mask]
    return donors if donors.model_id.nunique() >= 2 else donors.iloc[:0]


def run(snapshot: Path, predictions: Path, output: Path) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    predictions_frame = pd.read_parquet(predictions)
    predictions_frame = predictions_frame[predictions_frame.budget == 8].copy()
    if predictions_frame.execution_id.duplicated().any() or len(predictions_frame) != len(main):
        raise ValueError('Expected one K8 prediction for each main-campaign cell')
    static_and_truth = main[[
        'execution_id', 'latency_p95_ms', 'examples_per_second', 'batch_size',
        'max_length', 'request_concurrency', 'repeats', 'workload_task',
    ]]
    scored = predictions_frame.merge(static_and_truth, on='execution_id',
                                     validate='one_to_one')
    if len(scored) != len(main) or not (scored.model_id == scored.heldout_model_id).all():
        raise ValueError('Prediction/measurement population mismatch')

    results: list[dict] = []
    matchable_model_groups = []
    for model_id in sorted(main.model_id.unique()):
        train = main[main.model_id != model_id]
        held = scored[scored.model_id == model_id]
        if set(train.model_id) & {model_id}:
            raise AssertionError('Held-out model in training fold')
        global_grid = training_threshold_grid(train)
        probe_json = held.space_probe_groups_json.unique()
        if len(probe_json) != 1:
            raise ValueError('Inconsistent probe manifest within model fold')
        measured = set(json.loads(probe_json[0]))
        if len(measured) != 8:
            raise ValueError('Expected eight measured workload anchors')
        for group, slate in held.groupby('workload_group'):
            key = condition_key(slate.iloc[0])
            if any(condition_key(row) != key for _, row in slate.iterrows()):
                raise ValueError('Inconsistent static workload within slate')
            donors = matched_donor_rows(train, key)
            has_match = not donors.empty
            if has_match:
                matchable_model_groups.append({'heldout_model_id': model_id,
                                               'workload_group': group,
                                               'donor_models': donors.model_id.nunique()})
            grids = [('global_all_models', global_grid)]
            if has_match:
                grids.extend([
                    ('global_matched_population', global_grid),
                    ('exact_workload_donor', training_threshold_grid(donors)),
                ])
            for scenario, grid in grids:
                for threshold in grid:
                    records = score_slate(slate, threshold, measured)
                    results.extend({
                        'threshold_scheme': scenario,
                        'heldout_model_id': model_id,
                        'workload_group': group,
                        'latency_quantile': threshold['latency_quantile'],
                        'rps_quantile': threshold['rps_quantile'],
                        **record,
                    } for record in records)

    detail = pd.DataFrame(results)
    aggregate = detail.groupby(
        ['threshold_scheme', 'case_bucket', 'method'], as_index=False
    ).agg(
        workload_queries=('selected', 'size'),
        selected=('selected', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        false_approvals=('false_approvals', 'sum'),
        both_constraints_active=('both_constraints_active', 'sum'),
        measured_anchor_selected=('measured_anchor_selected', 'sum'),
        measured_anchor_selected_success=('measured_anchor_selected_success', 'sum'),
    )
    if not (aggregate.selected ==
            aggregate.successful_choices + aggregate.false_approvals).all():
        raise AssertionError('Case-mix accounting mismatch')
    per_model = detail.groupby(
        ['threshold_scheme', 'heldout_model_id', 'case_bucket', 'method'],
        as_index=False,
    ).agg(
        workload_queries=('selected', 'size'),
        selected=('selected', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        false_approvals=('false_approvals', 'sum'),
        both_constraints_active=('both_constraints_active', 'sum'),
        measured_anchor_selected_success=('measured_anchor_selected_success', 'sum'),
    )
    output.mkdir(parents=True, exist_ok=True)
    aggregate.to_csv(output / 'case_mix.csv', index=False)
    per_model.to_csv(output / 'per_model_case_mix.csv', index=False)
    matchable = pd.DataFrame(matchable_model_groups)
    report = {
        'scope': 'exploratory synthetic batch-SLO case mix, not user SLOs',
        'thresholds': 'leave-one-model-out training rows only',
        'schemes': {
            'global_all_models': 'all donor model/candidate observations per outer fold',
            'global_matched_population': 'same global thresholds, exact-match eligible held-out workloads only',
            'exact_workload_donor': 'donor observations at exact task/batch/length/concurrency/repeats; >=2 donor models',
        },
        'quantiles': list(QUANTILES),
        'resource_order': 'fewest GPU replicas, then fewest CPU cores',
        'method_probe_count': 8,
        'matchable_workload_groups': int(len(matchable)),
        'matchable_heldout_models': sorted(matchable.heldout_model_id.unique().tolist()),
        'unmatchable_heldout_models': sorted(set(main.model_id.unique()) -
                                             set(matchable.heldout_model_id.unique())),
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'aggregate': aggregate.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/slo_case_mix_v1')
    args = parser.parse_args()
    summary = run(args.snapshot, args.predictions, args.output)
    print(json.dumps(summary['aggregate'], indent=2))
