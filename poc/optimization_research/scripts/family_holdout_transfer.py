"""Stress-test sparse transfer when registered BERT relatives are withheld.

The selected BERT model IDs are targets only. All other model IDs
provide the absolute predictor, conditional resource response and inner
leave-one-donor-model-out anchor residuals. Every target exposes only eight
selected GPU1/two-core anchors before scoring its remaining outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (_absolute, _anchor_rows, _keyed_anchor,
                                    predict_sparse, residual_covariance,
                                    training_oof_anchor_residuals)
from sparse_anchor_transfer import (CAMPAIGN, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features,
                                    validate_slates)


ROOT = Path(__file__).resolve().parents[1]
FAMILY = (
    'google-bert/bert-base-uncased',
    'google/bert_uncased_L-2_H-128_A-2',
    'google/bert_uncased_L-4_H-256_A-4',
    'google/bert_uncased_L-4_H-512_A-8',
)
FAMILY_WITH_DISTILBERT = FAMILY + ('distilbert/distilbert-base-uncased',)
BUDGET = 8
METHODS = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge',
           'space_global_mean', 'zero_shot', 'full_anchor_reference')


def family_partition(frame: pd.DataFrame,
                     family: tuple[str, ...] = FAMILY) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give the held-out family no labels in any donor training step."""
    available = set(frame.model_id.unique())
    missing = set(family) - available
    if missing:
        raise ValueError(f'Missing exact BERT family members: {sorted(missing)}')
    donors = frame[~frame.model_id.isin(family)].copy()
    targets = frame[frame.model_id.isin(family)].copy()
    if donors.empty or targets.empty:
        raise ValueError('Both donor and target partitions are required')
    if set(donors.model_id) & set(targets.model_id):
        raise AssertionError('Family leaked into donors')
    if set(donors.workload_group) & set(targets.workload_group):
        raise AssertionError('Workload groups overlap partitions')
    return donors, targets


def run(snapshot: Path, output: Path, seed: int = 42,
        family: tuple[str, ...] = FAMILY) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    donors, targets = family_partition(main, family)
    prepared = prepare(donors)
    absolute = _absolute().fit(
        prepared[list(BASE_FEATURES)],
        prepared[['true_log_latency', 'true_log_requests_per_second']])
    conditional = fit_model(prepared, 'anchor_conditioned', seed)
    donor_oof = training_oof_anchor_residuals(prepared)
    if set(donor_oof.model_id) != set(donors.model_id):
        raise AssertionError('Inner residual donors differ from training set')

    summaries, manifests, predictions = [], [], []
    for held_model in family:
        held = targets[targets.model_id == held_model].copy()
        anchors = _keyed_anchor(held)
        covariance, info = residual_covariance(anchors, donor_oof)
        if info['mode'] != 'aligned_empirical_plus_kernel':
            raise AssertionError('Expected non-BERT encoder donors to cover the grid')
        static = static_features(held, registered_parameter_bytes(held))
        probes = select_probes(group_design(static), BUDGET, 'medoids', seed)
        all_groups = anchors.workload_group.tolist()
        pred = predict_sparse(held, probes, probes, covariance,
                              absolute, conditional)
        full = predict_sparse(held, all_groups, all_groups, covariance,
                              absolute, conditional)
        pred['full_anchor_reference_latency_log'] = full.cov_transfer_latency_log.to_numpy()
        pred['full_anchor_reference_rps_log'] = full.cov_transfer_rps_log.to_numpy()
        pred['heldout_model_id'] = held_model
        pred['probe_groups_json'] = json.dumps(probes)
        predictions.append(pred)
        measured = _anchor_rows(held).loc[probes]
        cost = float((measured.replica_count * measured.wall_seconds).sum())
        full_cost = float((_anchor_rows(held).replica_count *
                           _anchor_rows(held).wall_seconds).sum())
        manifests.append({
            'heldout_model_id': held_model,
            'training_model_ids_json': json.dumps(sorted(donors.model_id.unique())),
            'excluded_family_model_ids_json': json.dumps(list(family)),
            'probe_groups_json': json.dumps(probes),
            'probe_budget': BUDGET,
            'total_workload_groups': len(all_groups),
            'aligned_donor_models_json': json.dumps(info['aligned_donor_models']),
            'covariance_mode': info['mode'],
            'gpu_wall_probe_seconds': cost,
            'full_anchor_gpu_wall_seconds': full_cost,
        })
        scores = scenario_metrics(pred, held, methods=METHODS)
        for method, values in scores.items():
            spent = (0.0 if method == 'zero_shot' else
                     full_cost if method == 'full_anchor_reference' else cost)
            summaries.append({
                'heldout_model_id': held_model,
                'method': method,
                'probe_budget': BUDGET,
                'gpu_wall_probe_seconds': spent,
                'aligned_donor_count': len(info['aligned_donor_models']),
                **values,
            })
        print(f'Finished {held_model}: {len(all_groups)} conditions, '
              f'{len(info["aligned_donor_models"])} aligned donors', flush=True)

    output.mkdir(parents=True, exist_ok=True)
    metric_frame = pd.DataFrame(summaries)
    metric_frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    macro = metric_frame.groupby('method', as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        missed_feasible_groups=('missed_feasible_groups', 'sum'),
        mean_gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'))
    result = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__,
        'seed': seed,
        'heldout_family': list(family),
        'donor_models': sorted(donors.model_id.unique()),
        'outer_split': f'all {len(family)} selected BERT relatives held out together',
        'inner_split': 'leave one training donor model out for each anchor residual',
        'heldout_observations': 'eight selected GPU1/two-core anchors per model; all other target labels for scoring only',
        'probe_selector': 'medoids on static workload coordinates',
        'macro': macro.set_index('method').to_dict(orient='index'),
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    print(macro.to_string(index=False), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--include-distilbert', action='store_true')
    args = parser.parse_args()
    family = FAMILY_WITH_DISTILBERT if args.include_distilbert else FAMILY
    output = args.output or ROOT / 'artifacts' / (
        'family_holdout_plus_distilbert_v1' if args.include_distilbert else
        'family_holdout_transfer_v1')
    run(args.snapshot, output, args.seed, family)
