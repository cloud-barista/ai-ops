"""Nested donor-only asymmetric approval margins on frozen K8 predictions.

Protocol was written before execution in reports/asymmetric_margin_preregistration.md.
The primary matched-coverage comparison uses no held-out outcomes in ranking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import BASE_FEATURES, fit_model, prepare
from cross_model_covariance import (
    _absolute, _keyed_anchor, predict_sparse, residual_covariance,
    training_oof_anchor_residuals,
)
from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import (
    CAMPAIGN, group_design, registered_parameter_bytes, select_probes,
    static_features, validate_slates,
)


ROOT = Path(__file__).resolve().parents[1]
QUANTILE = 0.75
SHRINK_PSEUDO_MODELS = 4.0
FIXED_SCALAR_LOG = 0.05
SEED = 42
POLICIES = ('conditional_asymmetric', 'global_asymmetric',
            'fixed_scalar', 'unadjusted_k8')


def high_demand(frame: pd.DataFrame) -> np.ndarray:
    return ((frame.batch_size.to_numpy() >= 12) |
            (frame.max_length.to_numpy() >= 512))


def freeze_reference(snapshot: Path, predictions: Path
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    frozen = pd.read_parquet(predictions)
    frozen = frozen[frozen.budget == 8].copy()
    if len(frozen) != len(main) or frozen.execution_id.duplicated().any():
        raise ValueError('Frozen K8 must cover each main measurement once')
    if not (frozen.model_id == frozen.heldout_model_id).all():
        raise ValueError('Frozen outer fold label disagrees')
    reference = frozen.merge(
        main[['execution_id', 'workload_task', 'batch_size', 'max_length',
              'latency_p95_ms', 'examples_per_second', 'wall_seconds']],
        on='execution_id', validate='one_to_one')
    reference['is_probe'] = probe_mask(reference)
    return main, reference


def probe_mask(frame: pd.DataFrame) -> np.ndarray:
    result = np.zeros(len(frame), dtype=bool)
    for _, part in frame.groupby('model_id', sort=False):
        records = part.space_probe_groups_json.unique()
        if len(records) != 1 or len(json.loads(records[0])) != 8:
            raise ValueError('Expected one eight-anchor K8 probe list per model')
        groups = set(json.loads(records[0]))
        result[part.index.to_numpy()] = (
            (part.replica_count.to_numpy() == 1) &
            (part.cpu_core_limit.to_numpy() == 2) &
            part.workload_group.isin(groups).to_numpy())
    return result


def nested_donor_predictions(main: pd.DataFrame, outer_model: str,
                             frozen: pd.DataFrame
                             ) -> tuple[pd.DataFrame, list[dict]]:
    """Two-fold model-level crossfit; each pseudo model and H are excluded."""
    donor_ids = sorted(set(main.model_id) - {outer_model})
    if len(donor_ids) != 8:
        raise ValueError('Expected eight donors')
    pieces, provenance = [], []
    for fold in (0, 1):
        pseudo_ids = donor_ids[fold::2]
        fit_ids = donor_ids[1 - fold::2]
        if set(fit_ids) & set(pseudo_ids) or outer_model in fit_ids:
            raise AssertionError('Nested training split leaked a model')
        train_raw = main[main.model_id.isin(fit_ids)]
        train = prepare(train_raw)
        absolute = _absolute().fit(
            train[list(BASE_FEATURES)],
            train[['true_log_latency', 'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', SEED)
        donor_oof = training_oof_anchor_residuals(train)
        if not set(donor_oof.model_id).issubset(fit_ids):
            raise AssertionError('Prior source outside nested training')
        for pseudo in pseudo_ids:
            test = main[main.model_id == pseudo]
            anchors = _keyed_anchor(test)
            covariance, info = residual_covariance(anchors, donor_oof)
            design = group_design(static_features(
                test, registered_parameter_bytes(test)))
            groups = select_probes(design, 8, 'medoids', seed=SEED)
            expected = frozen[frozen.model_id == pseudo].space_probe_groups_json.unique()
            if len(expected) != 1 or groups != json.loads(expected[0]):
                raise AssertionError('Nested and frozen K8 anchors differ')
            predicted = predict_sparse(
                test, groups, groups, covariance, absolute, conditional)
            predicted = predicted[['execution_id', 'model_id', 'workload_group',
                                   'replica_count', 'cpu_core_limit',
                                   'space_cov_transfer_latency_log',
                                   'space_cov_transfer_rps_log']].merge(
                test[['execution_id', 'batch_size', 'max_length',
                      'latency_p95_ms', 'examples_per_second']],
                on='execution_id', validate='one_to_one')
            predicted['outer_model_id'] = outer_model
            predicted['pseudo_fold'] = fold
            predicted['is_probe'] = (
                (predicted.replica_count == 1) &
                (predicted.cpu_core_limit == 2) &
                predicted.workload_group.isin(groups))
            pieces.append(predicted)
            provenance.append({
                'outer_model_id': outer_model, 'pseudo_model_id': pseudo,
                'fit_model_ids': fit_ids, 'probe_groups': groups,
                'covariance_mode': info['mode'],
            })
    result = pd.concat(pieces, ignore_index=True)
    if set(result.model_id) != set(donor_ids) or outer_model in set(result.model_id):
        raise AssertionError('Nested calibration population leaked H')
    return result, provenance


def _modelwise_quantile(frame: pd.DataFrame, column: str) -> float:
    per_model = frame.groupby('model_id')[column].quantile(QUANTILE)
    if per_model.empty:
        raise ValueError('Missing calibration stratum')
    return max(0.0, float(per_model.median()))


def learned_margins(calibration: pd.DataFrame) -> dict:
    """Model-balanced quantiles, then four-model-equivalent shrinkage."""
    data = calibration[~calibration.is_probe.astype(bool)].copy()
    data['latency_risk'] = (
        np.log(data.latency_p95_ms) -
        data.space_cov_transfer_latency_log)
    data['rps_risk'] = (
        data.space_cov_transfer_rps_log -
        np.log(data.examples_per_second / data.batch_size))
    data['high_demand'] = high_demand(data)
    global_l = _modelwise_quantile(data, 'latency_risk')
    global_r = _modelwise_quantile(data, 'rps_risk')
    margins = {}
    for demand in (False, True):
        for gpu in (1, 2):
            part = data[(data.high_demand == demand) &
                        (data.replica_count == gpu)]
            n_models = part.model_id.nunique()
            if n_models != 8:
                raise ValueError('Each stratum needs eight donor models')
            weight = n_models / (n_models + SHRINK_PSEUDO_MODELS)
            local_l = _modelwise_quantile(part, 'latency_risk')
            local_r = _modelwise_quantile(part, 'rps_risk')
            margins[f'{int(demand)}_gpu{gpu}'] = {
                'latency': weight * local_l + (1 - weight) * global_l,
                'rps': weight * local_r + (1 - weight) * global_r,
                'donor_models': n_models,
            }
    return {'global': {'latency': global_l, 'rps': global_r},
            'strata': margins}


def policy_margins(slate: pd.DataFrame, learned: dict,
                   policy: str) -> tuple[np.ndarray, np.ndarray]:
    if policy not in POLICIES:
        raise ValueError(policy)
    n = len(slate)
    if policy == 'conditional_asymmetric':
        keys = [f'{int(h)}_gpu{int(g)}'
                for h, g in zip(high_demand(slate), slate.replica_count)]
        l = np.asarray([learned['strata'][k]['latency'] for k in keys])
        r = np.asarray([learned['strata'][k]['rps'] for k in keys])
    elif policy == 'global_asymmetric':
        l = np.full(n, learned['global']['latency'])
        r = np.full(n, learned['global']['rps'])
    else:
        value = FIXED_SCALAR_LOG if policy == 'fixed_scalar' else 0.0
        l = np.full(n, value)
        r = np.full(n, value)
    # Exact measured K8 anchors do not need uncertainty margins.
    l[slate.is_probe.to_numpy()] = 0.0
    r[slate.is_probe.to_numpy()] = 0.0
    return l, r


def matched_indices(slack: np.ndarray, count: int
                    ) -> tuple[np.ndarray, float, int]:
    """Approve exactly count workload-query pairs using prediction slack only."""
    if slack.ndim != 3 or slack.shape[2] != 8:
        raise ValueError('Expected query × workload × 8 slates')
    best = slack.max(axis=2).reshape(-1)
    if not 0 <= count <= len(best):
        raise ValueError('Invalid approval count')
    order = np.lexsort((np.arange(len(best)), -best))
    keep = np.zeros(len(best), dtype=bool)
    keep[order[:count]] = True
    cutoff = float(best[order[count - 1]]) if count else float('inf')
    tie_count = int(np.sum(best == cutoff)) if count else 0
    feasible = slack.reshape(-1, 8) >= cutoff - 1e-12
    candidates = feasible.argmax(axis=1)
    candidates[~keep] = -1
    if count and not feasible[keep].any(axis=1).all():
        raise AssertionError('Matched approval lacked eligible candidate')
    return candidates.reshape(slack.shape[:2]), cutoff, tie_count


def validate_manifest(manifest: list[dict], all_models: set[str]) -> None:
    """Audit the two excluded model IDs and the exact eight calibration donors."""
    for held in all_models:
        rows = [row for row in manifest if row['outer_model_id'] == held]
        pseudo = [row['pseudo_model_id'] for row in rows]
        if len(rows) != 8 or set(pseudo) != all_models - {held}:
            raise ValueError('Pseudo-target donor coverage is incomplete')
        for row in rows:
            fit = set(row['fit_model_ids'])
            if held in fit or row['pseudo_model_id'] in fit or len(fit) != 4:
                raise ValueError('Nested fit leaked an excluded model')
            if len(row['probe_groups']) != 8:
                raise ValueError('Nested K8 probe count changed')


def _score(indices: np.ndarray, truth: np.ndarray) -> dict:
    if indices.shape != truth.shape[:2] or truth.shape[2] != 8:
        raise ValueError('Index/truth shape mismatch')
    active = indices >= 0
    chosen = truth.reshape(-1, 8)[
        np.flatnonzero(active.reshape(-1)), indices[active]]
    success = int(chosen.sum())
    selected = int(active.sum())
    feasible = int(truth.any(axis=2).sum())
    return {'selected': selected, 'successful_choices': success,
            'false_approvals': selected - success,
            'oracle_feasible': feasible,
            'missed_feasible': feasible - success}


def _threshold_arrays(slate: pd.DataFrame, thresholds: list[dict],
                      kind: str) -> tuple[np.ndarray, np.ndarray]:
    groups = slate.workload_group.nunique()
    if kind == 'original':
        grouped = slate.groupby('workload_group', sort=True).first()
        return (np.log(grouped.latency_slo_ms.to_numpy()).reshape(1, groups, 1),
                np.log(grouped.target_throughput_rps.to_numpy()).reshape(1, groups, 1))
    return (np.asarray([np.log(t['latency_slo_ms']) for t in thresholds])
            .reshape(-1, 1, 1) * np.ones((1, groups, 1)),
            np.asarray([np.log(t['target_rps']) for t in thresholds])
            .reshape(-1, 1, 1) * np.ones((1, groups, 1)))


def evaluate_fold(slate: pd.DataFrame, learned: dict, thresholds: list[dict],
                  kind: str) -> tuple[list[dict], list[dict]]:
    slate = slate.sort_values(
        ['workload_group', 'replica_count', 'cpu_core_limit'])
    groups = slate.workload_group.nunique()
    if len(slate) != groups * 8 or not (slate.groupby('workload_group').size() == 8).all():
        raise ValueError('Incomplete candidate slates')
    l_slo, r_slo = _threshold_arrays(slate, thresholds, kind)
    l_pred = slate.space_cov_transfer_latency_log.to_numpy().reshape(1, groups, 8)
    r_pred = slate.space_cov_transfer_rps_log.to_numpy().reshape(1, groups, 8)
    l_true = np.log(slate.latency_p95_ms.to_numpy()).reshape(1, groups, 8)
    r_true = np.log((slate.examples_per_second /
                     slate.batch_size).to_numpy()).reshape(1, groups, 8)
    truth = (l_true <= l_slo) & (r_true >= r_slo)
    native, slack_by_policy = {}, {}
    for policy in POLICIES:
        margin_l, margin_r = policy_margins(slate, learned, policy)
        slack = np.minimum(
            l_slo - l_pred - margin_l.reshape(1, groups, 8),
            r_pred - r_slo - margin_r.reshape(1, groups, 8))
        slack_by_policy[policy] = slack
        feasible = slack >= 0
        index = feasible.argmax(axis=2)
        index[~feasible.any(axis=2)] = -1
        native[policy] = index
    target_count = int((native['conditional_asymmetric'] >= 0).sum())
    native_rows, matched_rows = [], []
    for policy in POLICIES:
        native_rows.append({'policy': policy, 'evaluation': kind, 'comparison': 'native',
                            **_score(native[policy], truth)})
        if policy == 'conditional_asymmetric':
            # Its native operating point already defines the common coverage.
            # A second cutoff would change its chosen candidate within a slate.
            indices, cutoff, tie_count = native[policy], 0.0, 0
        else:
            indices, cutoff, tie_count = matched_indices(
                slack_by_policy[policy], target_count)
        metrics = _score(indices, truth)
        if metrics['selected'] != target_count:
            raise AssertionError('Coverage matching failed')
        matched_rows.append({
            'policy': policy, 'evaluation': kind, 'comparison': 'matched',
            'common_log_margin_shift': cutoff, 'cutoff_tie_count': tie_count,
            'target_approvals': target_count, **metrics})
    return native_rows, matched_rows


def run(snapshot: Path, predictions: Path, output: Path,
        reuse_calibration: bool = False) -> dict:
    main, frozen = freeze_reference(snapshot, predictions)
    output.mkdir(parents=True, exist_ok=True)
    calibration_parts, split_rows, margin_rows = [], [], []
    if reuse_calibration:
        prior = json.loads((output / 'metrics.json').read_text())
        if (prior['snapshot_sha256'] != hashlib.sha256(snapshot.read_bytes()).hexdigest() or
                prior['frozen_predictions_sha256'] !=
                hashlib.sha256(predictions.read_bytes()).hexdigest()):
            raise ValueError('Calibration cache input hashes disagree')
        cached = pd.read_parquet(output / 'nested_calibration_predictions.parquet')
        split_rows = json.loads((output / 'nested_split_manifest.json').read_text())
        validate_manifest(split_rows, set(main.model_id))
    result_rows = []
    for held in sorted(main.model_id.unique()):
        if reuse_calibration:
            donor = cached[cached.outer_model_id == held].copy()
            if set(donor.model_id) != set(main.model_id) - {held}:
                raise ValueError('Calibration cache fold coverage disagrees')
        else:
            donor, provenance = nested_donor_predictions(main, held, frozen)
            calibration_parts.append(donor)
            split_rows.extend(provenance)
        margins = learned_margins(donor)
        margin_rows.append({'heldout_model_id': held, **margins})
        held_slate = frozen[frozen.model_id == held]
        thresholds = training_threshold_grid(main[main.model_id != held])
        for kind in ('synthetic', 'original'):
            native, matched = evaluate_fold(held_slate, margins, thresholds, kind)
            result_rows.extend({'heldout_model_id': held, **row}
                               for row in native + matched)
        print(f'Finished nested margins: {held}', flush=True)
    validate_manifest(split_rows, set(main.model_id))
    if not reuse_calibration:
        calibration = pd.concat(calibration_parts, ignore_index=True)
        calibration.to_parquet(output / 'nested_calibration_predictions.parquet', index=False)
    pd.DataFrame(result_rows).to_csv(output / 'per_model_results.csv', index=False)
    (output / 'nested_split_manifest.json').write_text(
        json.dumps(split_rows, indent=2) + '\n')
    (output / 'learned_margins.json').write_text(
        json.dumps(margin_rows, indent=2) + '\n')
    results = pd.DataFrame(result_rows)
    aggregate = results.groupby(
        ['evaluation', 'comparison', 'policy'], as_index=False).agg(
            model_count=('heldout_model_id', 'nunique'),
            selected=('selected', 'sum'),
            successful_choices=('successful_choices', 'sum'),
            false_approvals=('false_approvals', 'sum'),
            oracle_feasible=('oracle_feasible', 'sum'),
            missed_feasible=('missed_feasible', 'sum'))
    aggregate.to_csv(output / 'aggregate.csv', index=False)
    report = {
        'scope': 'exploratory frozen SQLite, generated SLO and donor-only synthetic SLO',
        'protocol': 'reports/asymmetric_margin_preregistration.md',
        'outer': 'leave one model out; frozen K8 target predictions',
        'calibration': 'two-fold donor model crossfit, four fit models per pseudo-target; each prior is OOF on those four',
        'q': QUANTILE, 'shrink_pseudo_models': SHRINK_PSEUDO_MODELS,
        'fixed_scalar_log': FIXED_SCALAR_LOG, 'seed': SEED,
        'matched_count_source': 'native conditional_asymmetric approvals per outer model and SLO type',
        'target_outcome_used_in_matching': False,
        'probe_count_for_every_policy': 8,
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'frozen_predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'calibration_predictions_sha256': hashlib.sha256(
            (output / 'nested_calibration_predictions.parquet').read_bytes()).hexdigest(),
        'nested_manifest_sha256': hashlib.sha256(
            (output / 'nested_split_manifest.json').read_bytes()).hexdigest(),
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
                        default=ROOT / 'artifacts/asymmetric_margin_v1')
    parser.add_argument('--reuse-calibration', action='store_true',
                        help='Use verified nested predictions from a previous full run')
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.snapshot, arguments.predictions,
                         arguments.output, arguments.reuse_calibration)['aggregate'], indent=2))
