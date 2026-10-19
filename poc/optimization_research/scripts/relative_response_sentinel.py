"""Exploratory cross-resource sentinel test on the frozen AI-MCMP snapshot.

A target model exposes only selected GPU1/core2 anchors and one GPU2/core2
sentinel. GPU2 denotes two independent replicas, not a fixed-job speedup.
Donor-only inner model holdouts produce relative-response residual surfaces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from conditional_response import ANCHOR, BASE_FEATURES, CONDITIONAL_FEATURES, fit_model, prepare
from cross_model_covariance import (_absolute, _coordinates, _keyed_anchor,
                                    predict_sparse, residual_covariance,
                                    training_oof_anchor_residuals)
from cross_model_covariance_external import _base_kernel, continuous_prior
from dual_axis_sparse_probe import GPU2, cheapest_cross_probe, fit_gpu2_cost
from evaluate_counterfactual_slo import score_query, training_threshold_grid
from family_holdout_transfer import FAMILY
from joint_resource_residual import RESOURCES
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS, _anchor_rows,
                                    fit_probe_cost_predictor, group_design,
                                    registered_parameter_bytes, scenario_metrics,
                                    select_probes, static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
KEY_COLUMNS = ('workload_task', 'batch_size', 'max_length',
               'request_concurrency', 'repeats')
SENTINEL_RESOURCE_INDEX = RESOURCES.index(GPU2)
METHODS = ('anchor_only', 'sentinel_only', 'global_gpu2',
           'joint_response', 'reliability_gate', 'anchor8')
RHO = 0.5
RIDGE = 0.1
GATE_THRESHOLDS = (-1.0, 0.5, 1.0, 1.5, 2.0, 3.0, float('inf'))


def _truth_log(rows: pd.DataFrame) -> np.ndarray:
    """Call only for donor outcomes or explicitly selected target probes."""
    return np.column_stack((
        np.log(rows.latency_p95_ms.to_numpy(dtype=float)),
        np.log((rows.examples_per_second / rows.batch_size).to_numpy(dtype=float)),
    ))


def _key(row) -> tuple:
    return tuple(getattr(row, column) for column in KEY_COLUMNS)


def donor_relative_oof(train_prepared: pd.DataFrame, seed: int = 42) -> dict[str, dict]:
    """Each donor's resource contrast is predicted by a model excluding it."""
    out = {}
    for donor_id in sorted(train_prepared.model_id.unique()):
        fit = train_prepared[train_prepared.model_id != donor_id]
        held = train_prepared[train_prepared.model_id == donor_id].copy()
        if fit.empty or donor_id in set(fit.model_id):
            raise ValueError('Invalid inner donor split')
        predictor = fit_model(fit, 'anchor_conditioned', seed)
        expected = np.asarray(predictor.predict(held[list(CONDITIONAL_FEATURES)]),
                              dtype=float)
        observed = held[['latency_residual', 'throughput_residual']].to_numpy(dtype=float)
        held['b_latency'] = (observed - expected)[:, 0]
        held['b_rps'] = (observed - expected)[:, 1]
        held.loc[(held.replica_count == ANCHOR[0]) &
                 (held.cpu_core_limit == ANCHOR[1]), ['b_latency', 'b_rps']] = 0.0
        index = {}
        for row in held.itertuples(index=False):
            point = (_key(row), int(row.replica_count), int(row.cpu_core_limit))
            if point in index:
                raise ValueError('Duplicate donor condition/resource')
            index[point] = (float(row.b_latency), float(row.b_rps))
        anchors = _keyed_anchor(held)
        out[donor_id] = {'task': str(anchors.workload_task.iloc[0]),
                         'anchors': anchors, 'values': index}
    return out


def donor_tensor(target_anchors: pd.DataFrame, donors: dict[str, dict],
                 minimum: int = 2
                 ) -> tuple[np.ndarray | None, list[str]]:
    """Align donor contrasts to target conditions; interpolate unseen inputs."""
    target = _keyed_anchor(target_anchors)
    keys = list(target.index)
    task = str(target.workload_task.iloc[0])
    result, names = [], []
    xt = _coordinates(target)
    for name, donor in sorted(donors.items()):
        if donor['task'] != task:
            continue
        ref = donor['anchors']
        ref_keys = list(ref.index)
        xr = _coordinates(ref)
        krr = _base_kernel(xr, xr)
        ktr = _base_kernel(xt, xr)
        weights = np.linalg.solve(krr + RIDGE * np.eye(len(ref_keys)), ktr.T).T
        source = np.array([[donor['values'][(key, gpu, cpu)]
                            for gpu, cpu in RESOURCES] for key in ref_keys], dtype=float)
        surface = (weights @ source.reshape(len(ref_keys), -1)).reshape(
            len(keys), len(RESOURCES), 2)
        exact = {key: i for i, key in enumerate(ref_keys)}
        for i, key in enumerate(keys):
            if key in exact:
                surface[i] = source[exact[key]]
        surface[:, 0] = 0.0
        result.append(surface)
        names.append(name)
    if len(result) < minimum:
        return None, names
    return np.stack(result), names


def sentinel_surprise(donors: np.ndarray, index: int,
                      observed: np.ndarray) -> float:
    values = donors[:, index, SENTINEL_RESOURCE_INDEX]
    center = values.mean(axis=0)
    scale = np.maximum(values.std(axis=0, ddof=1), 0.05)
    return float(np.sqrt(np.mean(((observed - center) / scale) ** 2)))


def response_posterior(donors: np.ndarray, coords: np.ndarray,
                       index: int, observed: np.ndarray) -> np.ndarray:
    """Predict donor relative-response residual conditioned on one contrast."""
    models, conditions, resources, outcomes = donors.shape
    if models < 2 or resources != len(RESOURCES) or outcomes != 2 or coords.shape[0] != conditions:
        raise ValueError('Invalid donor response tensor')
    if not 0 <= index < conditions or observed.shape != (2,):
        raise ValueError('Invalid sentinel observation')
    mean = donors.mean(axis=0)
    centered = donors - mean[None]
    source = centered[:, index, SENTINEL_RESOURCE_INDEX]
    empirical_cross = np.einsum('mcro,mo->cro', centered, source) / (models - 1)
    variance = centered.var(axis=0, ddof=1)
    source_variance = variance[index, SENTINEL_RESOURCE_INDEX]
    distance2 = ((coords - coords[index]) ** 2).sum(axis=1)
    workload_similarity = 0.4 + 0.6 * np.exp(-0.5 * distance2)
    resource_similarity = np.array([
        np.exp(-0.5 * ((np.log(gpu / GPU2[0]) / 0.7) ** 2 +
                        (np.log(cpu / GPU2[1]) / 1.5) ** 2))
        for gpu, cpu in RESOURCES])
    smooth_cross = (np.sqrt(np.maximum(variance * source_variance[None, None, :], 0.0)) *
                    workload_similarity[:, None, None] *
                    resource_similarity[None, :, None])
    cross = (1.0 - RHO) * empirical_cross + RHO * smooth_cross
    denominator = np.maximum(source_variance * 1.05, 0.0025)
    posterior = mean + cross * ((observed - mean[index, SENTINEL_RESOURCE_INDEX]) /
                                denominator)[None, None, :]
    posterior[:, 0] = 0.0
    posterior[index, SENTINEL_RESOURCE_INDEX] = observed
    return posterior


def select_gate_threshold(records: list[dict]) -> float:
    """Choose the conservative tie-break from fully nested donor records."""
    if not records:
        return -1.0
    scored = []
    for threshold in GATE_THRESHOLDS:
        loss = np.mean([row['joint_loss'] if row['surprise'] <= threshold
                        else row['fallback_loss'] for row in records])
        scored.append((float(loss), threshold))
    return float(min(scored, key=lambda pair: (pair[0], pair[1]))[1])


def nested_gate_calibration(target_main: pd.DataFrame,
                            original_donors: dict[str, dict],
                            nested_donors: dict[str, dict[str, dict]],
                            sentinel_group: str) -> tuple[float, list[dict]]:
    """Pseudo-target source residuals exclude the pseudo-target at every fit.

    For pseudo-target j, its own b surface comes from an OOF predictor that
    excluded j. Every source donor i is refit with both i and j excluded.
    The outer target's measured outcomes are never used in this calibration.
    """
    anchors = _keyed_anchor(target_main)
    index = anchors.workload_group.tolist().index(sentinel_group)
    coords = _coordinates(anchors)
    mask = np.ones((len(anchors), len(RESOURCES)), dtype=bool)
    mask[:, 0] = False
    mask[index, SENTINEL_RESOURCE_INDEX] = False
    records = []
    for pseudo, nested in nested_donors.items():
        source, _ = donor_tensor(target_main, nested)
        target, _ = donor_tensor(target_main, {pseudo: original_donors[pseudo]},
                                 minimum=1)
        if source is None or target is None:
            continue
        seen = target[0, index, SENTINEL_RESOURCE_INDEX]
        predicted = response_posterior(source, coords, index, seen)
        truth = target[0]
        scale = np.maximum(source[:, mask].std(axis=(0, 1), ddof=1), 0.05)
        records.append({
            'pseudo_target': pseudo,
            'surprise': sentinel_surprise(source, index, seen),
            'joint_loss': float(np.mean(((predicted[mask] - truth[mask]) / scale) ** 2)),
            'fallback_loss': float(np.mean((truth[mask] / scale) ** 2)),
            'nested_source_count': len(source),
        })
    return select_gate_threshold(records), records


def choose_budgeted_probes(held: pd.DataFrame, anchor_cost_model,
                           gpu2_cost_model, seed: int = 42) -> dict:
    """Predeclared donor-predicted cost rule; held-out wall time is unread."""
    static = static_features(held, registered_parameter_bytes(held))
    design = group_design(static)
    anchor_static = _anchor_rows(static)
    gpu2_static = static[(static.replica_count == GPU2[0]) &
                         (static.cpu_core_limit == GPU2[1])].set_index('workload_group')
    anchor8 = select_probes(design, 8, 'medoids', seed)

    def predicted_cost(groups: list[str], sentinel: str | None = None) -> float:
        cost = float(np.exp(anchor_cost_model.predict(
            anchor_static.loc[groups, list(BASE_FEATURES)])).sum())
        if sentinel is not None:
            cost += float(np.exp(gpu2_cost_model.predict(
                gpu2_static.loc[[sentinel], list(BASE_FEATURES)]))[0])
        return cost

    reference = predicted_cost(anchor8)
    options = []
    for k in (3, 4, 5):
        groups = select_probes(design, k, 'medoids', seed)
        sentinel = cheapest_cross_probe(held, groups, gpu2_cost_model)
        estimated = predicted_cost(groups, sentinel)
        options.append({'anchors': groups, 'sentinel': sentinel, 'count': k,
                        'predicted_gpu_wall': estimated})
    affordable = [x for x in options if x['predicted_gpu_wall'] <= 1.05 * reference]
    if affordable:
        chosen = min(affordable, key=lambda x: (reference - x['predicted_gpu_wall'],
                                                -x['count']))
    else:
        chosen = min(options, key=lambda x: (abs(x['predicted_gpu_wall'] - reference),
                                             -x['count']))
    return {**chosen, 'anchor8': anchor8,
            'predicted_anchor8_gpu_wall': reference,
            'cost_options': options}


def _probe_cost(held: pd.DataFrame, groups: list[str], sentinel: str | None) -> float:
    anchors = _anchor_rows(held).loc[groups]
    cost = float((anchors.replica_count * anchors.wall_seconds).sum())
    if sentinel is not None:
        row = held[(held.workload_group == sentinel) &
                   (held.replica_count == GPU2[0]) &
                   (held.cpu_core_limit == GPU2[1])]
        if len(row) != 1:
            raise ValueError('Missing sentinel cost')
        cost += float(row.replica_count.iloc[0] * row.wall_seconds.iloc[0])
    return cost


def predict_target(held_main: pd.DataFrame, held_external: pd.DataFrame,
                   covariance_main: np.ndarray, covariance_all: np.ndarray | None,
                   absolute, conditional, donors: dict[str, dict],
                   probes: dict, gate_threshold: float,
                   gate_records: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Read target labels only at paid main anchors and the paid sentinel."""
    all_target = (pd.concat([held_main, held_external], ignore_index=True)
                  if not held_external.empty else held_main)
    known_size = registered_parameter_bytes(held_main)
    prior = covariance_all if covariance_all is not None else covariance_main
    fewer = predict_sparse(all_target, probes['anchors'], probes['anchors'],
                           prior, absolute, conditional, parameter_bytes=known_size)
    eight = predict_sparse(all_target, probes['anchor8'], probes['anchor8'],
                           prior, absolute, conditional, parameter_bytes=known_size)
    if not np.array_equal(fewer.execution_id, eight.execution_id):
        raise ValueError('Prediction grids differ')
    # Preserve the established exact-grid covariance baseline on main rows.
    # The continuous prior is needed only for unseen external conditions.
    if not held_external.empty:
        main_fewer = predict_sparse(held_main, probes['anchors'], probes['anchors'],
                                    covariance_main, absolute, conditional,
                                    parameter_bytes=known_size)
        main_eight = predict_sparse(held_main, probes['anchor8'], probes['anchor8'],
                                    covariance_main, absolute, conditional,
                                    parameter_bytes=known_size)
        for combined, exact in ((fewer, main_fewer), (eight, main_eight)):
            columns = ['space_cov_transfer_latency_log',
                       'space_cov_transfer_rps_log']
            if not set(exact.execution_id).issubset(set(combined.execution_id)):
                raise ValueError('Main exact predictions are not in joint grid')
            positions = combined.set_index('execution_id').index.get_indexer(
                exact.execution_id)
            combined.loc[combined.index[positions], columns] = exact[columns].to_numpy()
    base = fewer[['space_cov_transfer_latency_log',
                  'space_cov_transfer_rps_log']].to_numpy(dtype=float)
    eight_values = eight[['space_cov_transfer_latency_log',
                          'space_cov_transfer_rps_log']].to_numpy(dtype=float)
    sentinel = probes['sentinel']
    sentinel_mask = ((fewer.workload_group == sentinel) &
                     (fewer.replica_count == GPU2[0]) &
                     (fewer.cpu_core_limit == GPU2[1])).to_numpy()
    paid = held_main.loc[((held_main.workload_group == sentinel) &
                          (held_main.replica_count == GPU2[0]) &
                          (held_main.cpu_core_limit == GPU2[1]))]
    if sentinel_mask.sum() != 1 or len(paid) != 1:
        raise ValueError('Sentinel must appear exactly once in main target')
    observed = _truth_log(paid)[0]
    b_observed = observed - base[sentinel_mask][0]
    sentinel_only = base.copy()
    sentinel_only[sentinel_mask] = observed
    global_gpu2 = base.copy()
    global_gpu2[fewer.replica_count.to_numpy() == GPU2[0]] += b_observed
    global_gpu2[sentinel_mask] = observed

    anchors = _keyed_anchor(all_target)
    names = anchors.workload_group.tolist()
    source_index = names.index(sentinel)
    tensor, donor_names = donor_tensor(all_target, donors)
    if tensor is None:
        joint = sentinel_only.copy()
        gated = sentinel_only.copy()
        target_surprise, gate_open = None, False
    else:
        coords = _coordinates(anchors)
        correction = response_posterior(tensor, coords, source_index, b_observed)
        by_group = {group: i for i, group in enumerate(names)}
        resource_index = {pair: j for j, pair in enumerate(RESOURCES)}
        row_index = np.asarray([by_group[g] for g in fewer.workload_group])
        config_index = np.asarray([resource_index[(int(g), int(c))]
                                   for g, c in zip(fewer.replica_count,
                                                   fewer.cpu_core_limit)])
        correction_rows = correction[row_index, config_index]
        joint = base + correction_rows
        joint[sentinel_mask] = observed
        target_surprise = sentinel_surprise(tensor, source_index, b_observed)
        gate_open = target_surprise <= gate_threshold
        gated = joint.copy() if gate_open else sentinel_only.copy()
    out = fewer[['campaign', 'model_id', 'workload_group', 'execution_id',
                 'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                 'target_throughput_rps']].copy()
    values = {'anchor_only': base, 'sentinel_only': sentinel_only,
              'global_gpu2': global_gpu2, 'joint_response': joint,
              'reliability_gate': gated, 'anchor8': eight_values}
    for name, array in values.items():
        out[f'{name}_latency_log'] = array[:, 0]
        out[f'{name}_rps_log'] = array[:, 1]
    return out, {'aligned_donor_models': donor_names,
                 'sentinel_b_latency': float(b_observed[0]),
                 'sentinel_b_rps': float(b_observed[1]),
                 'gate_threshold': gate_threshold,
                 'target_surprise': target_surprise,
                 'gate_open': gate_open,
                 'gate_calibration': gate_records}


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    usable = raw[raw.eligible & raw.device.eq('cuda')]
    main = usable[usable.campaign == CAMPAIGN].copy()
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError('Main and external groups overlap')
    partitions = [(f'loomo:{model}', (model,), main[main.model_id != model])
                  for model in sorted(main.model_id.unique())]
    partitions.append(('bert_family', FAMILY, main[~main.model_id.isin(FAMILY)]))
    metrics, synthetic, manifests, predictions = [], [], [], []
    for split_name, target_ids, train_raw in partitions:
        train = prepare(train_raw)
        absolute = _absolute().fit(train[list(BASE_FEATURES)],
                                   train[['true_log_latency',
                                          'true_log_requests_per_second']])
        conditional = fit_model(train, 'anchor_conditioned', seed)
        anchor_oof = training_oof_anchor_residuals(train)
        response_oof = donor_relative_oof(train, seed)
        # Full nesting for the reliability calibration: pseudo-target j is
        # removed before obtaining any source donor's OOF response surface.
        needs_encoder_gate = any(
            main[main.model_id == target_id].workload_task.iloc[0] ==
            'sequence_classification' for target_id in target_ids)
        nested_response = ({
            pseudo: donor_relative_oof(train[train.model_id != pseudo], seed)
            for pseudo, source in response_oof.items()
            if source['task'] == 'sequence_classification'
        } if needs_encoder_gate else {})
        anchor_cost_model = fit_probe_cost_predictor(train, seed)
        gpu2_cost_model = fit_gpu2_cost(train)
        for target_id in target_ids:
            held_main = main[main.model_id == target_id].copy()
            held_external = external[external.model_id == target_id].copy()
            if held_main.empty or target_id in set(train_raw.model_id):
                raise ValueError('Invalid target split')
            anchor_main = _keyed_anchor(held_main)
            covariance_main, _ = residual_covariance(anchor_main, anchor_oof)
            if held_external.empty:
                covariance_all = None
            else:
                covariance_all, _ = continuous_prior(
                    pd.concat([held_main, held_external], ignore_index=True),
                    anchor_oof)
            probes = choose_budgeted_probes(held_main, anchor_cost_model,
                                            gpu2_cost_model, seed)
            gate_threshold, gate_records = nested_gate_calibration(
                held_main, response_oof, nested_response, probes['sentinel'])
            predicted, gate_info = predict_target(
                held_main, held_external, covariance_main, covariance_all,
                absolute, conditional, response_oof, probes,
                gate_threshold, gate_records)
            measured_cost = _probe_cost(held_main, probes['anchors'],
                                        probes['sentinel'])
            anchor_cost = _probe_cost(held_main, probes['anchors'], None)
            reference_cost = _probe_cost(held_main, probes['anchor8'], None)
            manifest = {
                'split': split_name, 'target_model': target_id,
                'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                'anchor_groups_json': json.dumps(probes['anchors']),
                'sentinel_group': probes['sentinel'],
                'anchor8_groups_json': json.dumps(probes['anchor8']),
                'anchor_count': probes['count'],
                'predicted_dual_gpu_wall': probes['predicted_gpu_wall'],
                'predicted_anchor8_gpu_wall': probes['predicted_anchor8_gpu_wall'],
                'actual_dual_gpu_wall': measured_cost,
                'actual_anchor_only_gpu_wall': anchor_cost,
                'actual_anchor8_gpu_wall': reference_cost,
                'dual_minus_anchor8_gpu_wall': measured_cost - reference_cost,
                'aligned_donor_count': len(gate_info['aligned_donor_models']),
                'gate_threshold': gate_info['gate_threshold'],
                'target_surprise': gate_info['target_surprise'],
                'gate_open': gate_info['gate_open'],
                'gate_calibration_json': json.dumps(gate_records),
            }
            manifests.append(manifest)
            for campaign in (CAMPAIGN,) + EXTERNAL_CAMPAIGNS:
                truth = (held_main if campaign == CAMPAIGN else
                         held_external[held_external.campaign == campaign])
                if truth.empty:
                    continue
                part = predicted[predicted.campaign == campaign].copy()
                for method, values in scenario_metrics(part, truth, methods=METHODS).items():
                    cost = (anchor_cost if method == 'anchor_only' else
                            reference_cost if method == 'anchor8' else measured_cost)
                    metrics.append({'split': split_name, 'target_model': target_id,
                                    'campaign': campaign, 'method': method,
                                    'gpu_wall_probe_seconds': cost, **values})
                if campaign == CAMPAIGN:
                    scored = part.merge(truth[['execution_id', 'latency_p95_ms',
                                               'examples_per_second', 'batch_size']],
                                        on='execution_id', validate='one_to_one')
                    for threshold in training_threshold_grid(train_raw):
                        info, outcomes = score_query(scored, threshold, methods=METHODS)
                        for outcome in outcomes:
                            synthetic.append({'split': split_name,
                                              'target_model': target_id,
                                              **threshold, **info, **outcome})
            predicted['split'] = split_name
            predicted['target_model'] = target_id
            predictions.append(predicted)
            print(f'Finished {split_name}/{target_id}: '
                  f'{probes["count"]}+1 vs 8 probes, cost '
                  f'{measured_cost:.2f}/{reference_cost:.2f} GPU-wall s, '
                  f'gate {gate_info["gate_open"]}', flush=True)
    output.mkdir(parents=True, exist_ok=True)
    metric_frame = pd.DataFrame(metrics)
    synthetic_frame = pd.DataFrame(synthetic)
    manifest_frame = pd.DataFrame(manifests)
    metric_frame.to_csv(output / 'per_model_metrics.csv', index=False)
    synthetic_frame.to_csv(output / 'synthetic_slo_scenarios.csv', index=False)
    manifest_frame.to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(output / 'predictions.parquet', index=False)
    macro = metric_frame.groupby(['split', 'campaign', 'method'], as_index=False).agg(
        models=('target_model', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'sum'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        mean_gpu_wall=('gpu_wall_probe_seconds', 'mean'))
    summary = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'dependency_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                              for name in ('conditional_response.py', 'cross_model_covariance.py',
                                           'cross_model_covariance_external.py',
                                           'dual_axis_sparse_probe.py',
                                           'sparse_anchor_transfer.py',
                                           'joint_resource_residual.py',
                                           'evaluate_counterfactual_slo.py')},
        'protocol': 'outer model and joint BERT-family holdout; donor inner model holdout; 3-5 GPU1/core2 anchors plus one GPU2/core2 sentinel chosen with donor-predicted GPU-wall to match 8-anchor cost',
        'semantics': 'GPU2 is two independent replicas; rps is examples_per_second / batch_size and each replica sees full workload profile',
        'metric_note': 'latency_p95_ms is batch p95; SLOs are generated, synthetic grid is donor-only and repeated queries are correlated',
        'methods': list(METHODS),
        'macro': macro.to_dict(orient='records'),
        'synthetic_summary': synthetic_frame.groupby(
            ['split', 'method'], as_index=False)[
                ['oracle_feasible', 'successful_choices',
                 'false_approvals']].sum().to_dict(orient='records'),
        'cost_by_model': manifest_frame.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/relative_response_sentinel_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    run(args.snapshot, args.output, args.seed)
