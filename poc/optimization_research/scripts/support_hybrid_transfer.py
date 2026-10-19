"""Exploratory support-aware latency/RPS hybrid with donor-only selection.

The physical-response artifact supplies its donor-CV-selected latency blend.
This study selects the RPS anchor-transfer prior by an additional nested
model/condition CV inside each outer training fold. Every inner test model and
its withheld conditions are excluded from fitting the absolute predictor and
from every fitted donor residual surface. The outer model contributes only the
same previously frozen eight main-campaign anchor probes.

This is post-hoc research on a dataset whose external results informed the
method family, not an independent novelty or generalization experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from conditional_response import BASE_FEATURES, prepare
from cross_model_covariance import (_absolute, _keyed_anchor, _smooth_covariance,
                                    posterior_residual, training_oof_anchor_residuals,
                                    KEY_COLUMNS)
from cross_model_covariance_external import continuous_prior
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS,
                                    group_design, registered_parameter_bytes,
                                    scenario_metrics, select_probes,
                                    static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('support_hybrid', 'support_smooth_latency', 'space_cov_transfer',
           'space_kernel_transfer', 'space_ridge')
RPS_METHODS = ('cov', 'kernel', 'ridge')
RPS_COLUMN = {'cov': 'space_cov_transfer_rps_log',
              'kernel': 'space_kernel_transfer_rps_log',
              'ridge': 'space_ridge_rps_log'}
CONDITION_FOLDS = 4


def with_keys(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out['condition_key'] = list(map(tuple, out[list(KEY_COLUMNS)].to_numpy()))
    return out


def select_rps_by_donor_cv(donor_raw: pd.DataFrame) -> tuple[str, dict]:
    """Select a missing-condition RPS prior using only outer donor data.

    For each inner held-out encoder model, fit a baseline and OOF residual
    surfaces on the other encoder models with one quarter of exact condition
    keys removed globally. Eight anchors of the inner target are chosen only
    among retained conditions; errors are scored at the removed conditions.
    The target inner model is excluded from all fitting, including OOF fits.
    """
    keyed = with_keys(donor_raw)
    if set(keyed.workload_task) != {'inference'}:
        raise ValueError('Expected encoder donors only')
    models = sorted(keyed.model_id.unique())
    if len(models) < 5:
        raise ValueError('At least five donor models required for nested CV')
    anchors = keyed[(keyed.replica_count == 1) & (keyed.cpu_core_limit == 2)]
    by_model = {model: keyed[keyed.model_id == model].copy() for model in models}
    common = set.intersection(*(
        set(anchors.loc[anchors.model_id == model, 'condition_key'])
        for model in models))
    keys = sorted(common)
    if len(keys) < 32:
        raise ValueError('Insufficient aligned encoder conditions')
    if any(len(set(anchors.loc[anchors.model_id == model, 'condition_key'])) != len(keys)
           for model in models):
        raise ValueError('Donor condition grid is not aligned')
    errors = {method: [] for method in RPS_METHODS}
    fold_records = []
    for inner_target in models:
        target_raw = by_model[inner_target]
        size = registered_parameter_bytes(target_raw)
        target_static = static_features(target_raw, size)
        static_anchors = _keyed_anchor(target_static)
        target_anchors = _keyed_anchor(target_raw)
        ordered_groups = static_anchors.workload_group.tolist()
        design = group_design(target_static).loc[ordered_groups]
        target_anchors = target_anchors.loc[static_anchors.index]
        for fold in range(CONDITION_FOLDS):
            withheld = set(keys[fold::CONDITION_FOLDS])
            fit_raw = keyed[(keyed.model_id != inner_target) &
                            ~keyed.condition_key.isin(withheld)].drop(
                                columns='condition_key')
            train = prepare(fit_raw)
            absolute = _absolute().fit(
                train[list(BASE_FEATURES)],
                train[['true_log_latency', 'true_log_requests_per_second']])
            donor_oof = training_oof_anchor_residuals(train)
            if inner_target in set(donor_oof.model_id):
                raise AssertionError('Inner target leaked into prior')
            prior, info = continuous_prior(target_static, donor_oof)
            if info['mode'] != 'interpolated_donor_covariance_plus_kernel':
                raise AssertionError('Expected aligned encoder donor grid')
            anchor_static = static_anchors.loc[:, list(BASE_FEATURES)]
            baseline_rps = absolute.predict(anchor_static)[:, 1]
            train_group_keys = static_anchors.loc[
                ~static_anchors.index.isin(withheld), 'workload_group']
            sub_design = design.loc[train_group_keys]
            probes = select_probes(sub_design, 8, 'medoids', seed=42)
            group_index = {group: i for i, group in enumerate(ordered_groups)}
            probe_idx = np.asarray([group_index[group] for group in probes], dtype=int)
            probe_actual = target_anchors.set_index('workload_group').loc[probes]
            observed_rps = np.log((probe_actual.examples_per_second /
                                   probe_actual.batch_size).to_numpy(dtype=float))
            residual = observed_rps - baseline_rps[probe_idx]
            selected = np.asarray([i for i, key in enumerate(static_anchors.index)
                                   if key in withheld], dtype=int)
            if not len(selected) or set(selected) & set(probe_idx):
                raise AssertionError('Invalid hidden-condition/probe split')
            smooth = _smooth_covariance(
                static_anchors,
                np.maximum(np.diagonal(prior, axis1=0, axis2=1).mean(axis=1), 1e-8))
            cov_field = posterior_residual(prior, probe_idx,
                                           np.column_stack((residual, residual)))[:, 1]
            ker_field = posterior_residual(smooth, probe_idx,
                                           np.column_stack((residual, residual)))[:, 1]
            ridge = Ridge(alpha=1.0, fit_intercept=False).fit(
                design.iloc[probe_idx].to_numpy(), residual)
            ridge_field = ridge.predict(design.to_numpy())
            actual = np.log((target_anchors.iloc[selected].examples_per_second /
                             target_anchors.iloc[selected].batch_size).to_numpy(dtype=float))
            for method, field in [('cov', cov_field), ('kernel', ker_field),
                                  ('ridge', ridge_field)]:
                loss = float(np.abs(baseline_rps[selected] + field[selected] - actual).mean())
                errors[method].append(loss)
                fold_records.append({'inner_target_model': inner_target,
                                     'condition_fold': fold, 'method': method,
                                     'withheld_conditions': len(selected),
                                     'probe_count': len(probes),
                                     'rps_anchor_male': loss})
    macro = {method: float(np.mean(values)) for method, values in errors.items()}
    chosen = min(RPS_METHODS, key=lambda method: (macro[method],
                                                  RPS_METHODS.index(method)))
    return chosen, {'method_mean_anchor_rps_male': macro,
                    'inner_models': models,
                    'folds_per_model': CONDITION_FOLDS,
                    'scored_anchor_conditions_per_model': len(keys),
                    'fold_scores': fold_records}


def compose_hybrid(frozen: pd.DataFrame, rps_method: str) -> pd.DataFrame:
    """Combine independently fitted latency and RPS predictions without labels."""
    if rps_method not in RPS_METHODS:
        raise ValueError('Unknown RPS method')
    if frozen.execution_id.duplicated().any():
        raise ValueError('Duplicate frozen prediction ID')
    needed = ['support_smooth_latency_latency_log', 'space_cov_transfer_rps_log',
              RPS_COLUMN[rps_method], 'uses_smooth_latency']
    if not np.isfinite(frozen[needed[:-1]].to_numpy(dtype=float)).all():
        raise ValueError('Nonfinite candidate prediction')
    out = frozen.copy()
    mask = out.uses_smooth_latency.to_numpy(dtype=bool)
    out['support_hybrid_latency_log'] = out.support_smooth_latency_latency_log
    out['support_hybrid_rps_log'] = np.where(
        mask, out[RPS_COLUMN[rps_method]], out.space_cov_transfer_rps_log)
    out['selected_new_condition_rps_method'] = np.where(mask, rps_method, 'cov')
    return out


def _verify_physical_artifact(snapshot: Path, physical: Path) -> dict:
    meta = json.loads((physical.parent / 'metrics.json').read_text())
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if meta['source_snapshot_sha256'] != snapshot_sha:
        raise ValueError('Physical artifact uses a different snapshot')
    source = ROOT / 'scripts/physical_response_transfer.py'
    if meta['script_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('Physical artifact script hash is stale')
    if physical.name != 'predictions.parquet':
        raise ValueError('Expected frozen predictions.parquet')
    return meta


def run(snapshot: Path, physical: Path, output: Path) -> dict:
    reference = _verify_physical_artifact(snapshot, physical)
    raw = pd.read_parquet(snapshot)
    usable = raw[raw.eligible & raw.device.eq('cuda')]
    main = usable[usable.campaign == CAMPAIGN].copy()
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
    validate_slates(main)
    validate_slates(external)
    frozen = pd.read_parquet(physical)
    if (frozen.execution_id.duplicated().any() or
            set(frozen.execution_id) != set(pd.concat((main, external)).execution_id)):
        raise ValueError('Physical artifact does not cover the exact evaluation slate')
    results, predictions, splits = [], [], []
    selections = {}
    for held_model in sorted(main.model_id.unique()):
        outer_train = main[main.model_id != held_model]
        encoder_donors = outer_train[outer_train.workload_task == 'inference']
        method, cv = select_rps_by_donor_cv(encoder_donors)
        selections[held_model] = {'selected_rps_method': method,
                                  'donor_cv': cv}
        held = pd.concat((main[main.model_id == held_model],
                          external[external.model_id == held_model]), ignore_index=True)
        hold_frozen = frozen[frozen.model_id == held_model]
        pred = compose_hybrid(hold_frozen, method)
        for campaign, truth in held.groupby('campaign'):
            p = pred[pred.campaign == campaign]
            if set(p.execution_id) != set(truth.execution_id):
                raise ValueError('Prediction/truth execution IDs differ')
            for name, score in scenario_metrics(p, truth, methods=METHODS).items():
                results.append({'heldout_model_id': held_model,
                                'campaign': campaign, 'method': name,
                                'probe_budget': 8, **score})
        predictions.append(pred)
        splits.append({'heldout_model_id': held_model,
                       'outer_donor_model_ids_json': json.dumps(sorted(outer_train.model_id.unique())),
                       'selected_rps_method': method,
                       'selected_latency_alpha': reference['selected_hyperparameters'][held_model]['alpha'],
                       'selected_latency_blend': reference['selected_hyperparameters'][held_model]['latency_blend']})
        print(f'{held_model}: donor CV selected {method}', flush=True)
    scores = pd.DataFrame(results)
    pred_frame = pd.concat(predictions, ignore_index=True)
    macro = scores.groupby(['campaign', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'))
    output.mkdir(parents=True, exist_ok=True)
    scores.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(splits).to_csv(output / 'split_manifest.csv', index=False)
    pred_frame.to_parquet(output / 'predictions.parquet', index=False)
    report = {
        'scope': 'post-hoc same-host exploratory pilot; generated SLO',
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'physical_prediction_sha256': hashlib.sha256(physical.read_bytes()).hexdigest(),
        'physical_metadata_sha256': hashlib.sha256((physical.parent / 'metrics.json').read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'protocol': 'outer held-out model; donor-only nested model and four-way condition CV for RPS prior; frozen K8 probes; external conditions without external probes',
        'rps_selection_target': 'missing-condition GPU1/core2 anchor RPS MALE, not downstream resource or SLO loss',
        'selections': selections,
        'aggregate': macro.to_dict(orient='records'),
        'new_condition_rows': pred_frame.groupby('campaign').uses_smooth_latency.sum().astype(int).to_dict(),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--physical-predictions', type=Path,
                        default=ROOT / 'artifacts/physical_response_transfer_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/support_hybrid_transfer_v1')
    args = parser.parse_args()
    run(args.snapshot, args.physical_predictions, args.output)
