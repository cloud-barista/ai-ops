"""Centered residual-covariance ablation under joint BERT-family holdouts.

The four- and five-model family splits use identical static K8 probes to the
earlier raw-second-moment study. Their donor OOF residuals, absolute model and
conditional resource model are fitted only on non-family models.
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
from covariance_bias_ablation import predict_ablation
from cross_model_covariance import (_absolute, predict_sparse,
                                    training_oof_anchor_residuals)
from family_holdout_transfer import (BUDGET, FAMILY,
                                     FAMILY_WITH_DISTILBERT, family_partition)
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features,
                                    validate_slates)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('raw_second_moment', 'centered_cov_zero_mean',
           'centered_cov_plus_donor_mean', 'smooth_kernel')


def run(snapshot: Path, output: Path, seed: int = 42,
        family: tuple[str, ...] = FAMILY) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               raw.device.eq('cuda')].copy()
    validate_slates(main)
    donors, targets = family_partition(main, family)
    prepared = prepare(donors)
    absolute = _absolute().fit(
        prepared[list(BASE_FEATURES)],
        prepared[['true_log_latency', 'true_log_requests_per_second']])
    conditional = fit_model(prepared, 'anchor_conditioned', seed)
    donor_oof = training_oof_anchor_residuals(prepared)
    if set(donor_oof.model_id) != set(donors.model_id):
        raise AssertionError('Inner OOF donor set differs from training set')

    scores, manifests, predictions = [], [], []
    for held_model in family:
        held = targets[targets.model_id == held_model].copy()
        size = registered_parameter_bytes(held)
        probes = select_probes(group_design(static_features(held, size)),
                               BUDGET, 'medoids', seed)
        predicted, info = predict_ablation(
            held, probes, donor_oof, absolute, conditional,
            known_parameter_bytes=size)
        reference = predict_sparse(held, probes, probes, info_covariance(
            held, donor_oof), absolute, conditional, parameter_bytes=size)
        predicted['smooth_kernel_latency_log'] = reference.space_kernel_transfer_latency_log.to_numpy()
        predicted['smooth_kernel_rps_log'] = reference.space_kernel_transfer_rps_log.to_numpy()
        np.testing.assert_allclose(predicted.raw_second_moment_latency_log,
                                   reference.space_cov_transfer_latency_log,
                                   rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(predicted.raw_second_moment_rps_log,
                                   reference.space_cov_transfer_rps_log,
                                   rtol=1e-12, atol=1e-12)
        predicted['heldout_model_id'] = held_model
        predicted['probe_groups_json'] = json.dumps(probes)
        predictions.append(predicted)
        anchor = _anchor_rows(held).loc[probes]
        cost = float((anchor.replica_count * anchor.wall_seconds).sum())
        manifests.append({
            'heldout_model_id': held_model,
            'training_model_ids_json': json.dumps(sorted(donors.model_id.unique())),
            'excluded_family_model_ids_json': json.dumps(list(family)),
            'probe_groups_json': json.dumps(probes),
            'probe_budget': BUDGET,
            'aligned_donor_models_json': json.dumps(info['aligned_donor_models']),
            'covariance_mode': info['mode'],
            'gpu_wall_probe_seconds': cost,
        })
        metrics = scenario_metrics(predicted, held, methods=METHODS)
        for method, values in metrics.items():
            scores.append({
                'heldout_model_id': held_model,
                'method': method,
                'probe_budget': BUDGET,
                'gpu_wall_probe_seconds': cost,
                'aligned_donor_count': len(info['aligned_donor_models']),
                **values,
            })
        print(f'Finished {held_model}: {len(info["aligned_donor_models"])} aligned donors',
              flush=True)

    output.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(scores)
    frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(manifests).to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    macro = frame.groupby('method', as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'))
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__,
        'seed': seed,
        'budget': BUDGET,
        'heldout_family': list(family),
        'donor_models': sorted(donors.model_id.unique()),
        'donor_residual_split': 'inner leave-one-donor-model-out',
        'probe_selector': 'same static K8 medoids for all methods',
        'unprobed_outcomes_accessed_before_prediction': False,
        'macro': macro.set_index('method').to_dict(orient='index'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    print(macro.to_string(index=False), flush=True)
    return report


def info_covariance(held: pd.DataFrame, donor_oof: pd.DataFrame) -> np.ndarray:
    """The raw covariance passed to the existing zero-mean kernel comparator."""
    from cross_model_covariance import residual_covariance
    covariance, _ = residual_covariance(held, donor_oof)
    return covariance


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
        'family_holdout_centered_bert5_v1' if args.include_distilbert else
        'family_holdout_centered_bert4_v1')
    run(args.snapshot, output, args.seed, family)
