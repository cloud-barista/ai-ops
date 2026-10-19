"""Model-wide sparse profiling study for GPU performance prediction.

The evaluation holds out an entire model. At deployment time the model is
profiled on only k workload conditions at the GPU1/two-core anchor. A ridge
model transfers those anchor residuals to the remaining conditions; an
anchor-conditioned response model then predicts the eight resource choices.

This is exploratory. All training labels are from other models. Test outcome
columns are accessed only for the chosen anchor probes and final evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.cluster import KMeans
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge

from conditional_response import (ANCHOR, BASE_FEATURES, CONDITIONAL_FEATURES,
                                  prepare, fit_model)
from probe_design import (select_integrated_variance, slo_boundary_weights,
                          workload_basis)


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = 'khu-hf-local-exhaustive'
EXTERNAL_CAMPAIGNS = ('khu-hf-local-novel-matrix',
                      'khu-hf-local-space-filling')
CONFIGS = {(gpu, cores) for gpu in (1, 2) for cores in (2, 4, 8, 16)}
METHODS = ('zero_shot', 'global_mean', 'ridge_shift',
           'sparse_conditional', 'full_anchor_reference')
EXTERNAL_METHODS = METHODS[:-1]


def _anchor_rows(frame: pd.DataFrame) -> pd.DataFrame:
    anchors = frame[(frame.replica_count == ANCHOR[0]) &
                    (frame.cpu_core_limit == ANCHOR[1])].copy()
    if anchors.workload_group.duplicated().any():
        raise ValueError('Duplicate anchor in held-out slate')
    return anchors.set_index('workload_group', drop=False)


def registered_parameter_bytes(frame: pd.DataFrame) -> float:
    """Emulate registration-time model metadata from one frozen JSON field.

    Performance outcomes in result_json are never read. A real application
    registry would provide parameter count before profiling begins.
    """
    sizes = [json.loads(raw)['model_parameter_bytes']
             for raw in _anchor_rows(frame).result_json]
    if len(set(sizes)) != 1 or sizes[0] <= 0:
        raise ValueError('Inconsistent or invalid registered model size')
    return float(sizes[0])


def validate_slates(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValueError('Empty held-out fold')
    for group, rows in frame.groupby('workload_group', sort=False):
        configs = list(map(tuple, rows[['replica_count', 'cpu_core_limit']].to_numpy()))
        if len(configs) != 8 or set(configs) != CONFIGS:
            raise ValueError(f'Incomplete or duplicated slate: {group}')
        if rows[['model_id', 'batch_size', 'max_length', 'request_concurrency',
                 'latency_slo_ms', 'target_throughput_rps']].nunique().max() != 1:
            raise ValueError(f'Inconsistent workload metadata: {group}')


def static_features(test_raw: pd.DataFrame, parameter_bytes: float) -> pd.DataFrame:
    """Features available without reading held-out performance outcomes."""
    if parameter_bytes <= 0:
        raise ValueError('Model parameter bytes must be positive')
    out = test_raw[['campaign', 'model_id', 'workload_group', 'execution_id',
                    'workload_task', 'batch_size', 'max_length',
                    'request_concurrency', 'cpu_threads', 'cpu_core_limit',
                    'replica_count', 'repeats', 'latency_slo_ms',
                    'target_throughput_rps']].copy()
    out['log_parameter_bytes'] = np.log(parameter_bytes)
    out['log_batch_size'] = np.log(out.batch_size)
    out['log_max_length'] = np.log(out.max_length)
    out['is_causal_lm'] = out.workload_task.eq('causal_lm_forward').astype(int)
    out['prior_log_throughput_gain'] = np.log(out.replica_count)
    if not np.isfinite(out[list(BASE_FEATURES)].to_numpy(dtype=float)).all():
        raise ValueError('Non-finite static features')
    return out


def group_design(static: pd.DataFrame) -> pd.DataFrame:
    """Seven label-free coordinates shared with the probe-design experiment."""
    anchors = _anchor_rows(static).sort_index()
    coords = workload_basis(anchors)
    return pd.DataFrame(coords, index=anchors.index,
                        columns=['intercept', 'batch', 'length', 'concurrency',
                                 'batch_length', 'batch_concurrency',
                                 'length_concurrency'])


def select_probes(design: pd.DataFrame, budget: int, method: str,
                  seed: int = 42) -> list[str]:
    if budget < 1 or budget > len(design):
        raise ValueError('Invalid probe budget')
    names = list(design.sort_index().index)
    if budget == len(names):
        return names
    if method == 'random':
        choice = np.random.default_rng(seed).choice(len(names), size=budget, replace=False)
        return sorted(names[int(i)] for i in choice)
    if method == 'integrated_variance':
        choice = select_integrated_variance(design.loc[names].to_numpy(), budget, ridge=1.0)
        return sorted(names[int(i)] for i in choice)
    if method != 'medoids':
        raise ValueError(method)
    matrix = design.loc[names].to_numpy()
    centers = KMeans(n_clusters=budget, random_state=seed, n_init=10).fit(matrix).cluster_centers_
    selected: set[int] = set()
    # Greedy nearest unused workload for each centroid; names resolve ties.
    for center in centers:
        distances = np.sum((matrix - center) ** 2, axis=1)
        for i in np.argsort(distances, kind='stable'):
            if int(i) not in selected:
                selected.add(int(i))
                break
    if len(selected) != budget:
        raise AssertionError('Probe selection must be distinct')
    return sorted(names[i] for i in selected)


def fit_predictors(train_prepared: pd.DataFrame, seed: int = 42):
    absolute = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                                   max_features=1.0, random_state=seed, n_jobs=2)
    absolute.fit(train_prepared[list(BASE_FEATURES)],
                 train_prepared[['true_log_latency', 'true_log_requests_per_second']])
    conditional = fit_model(train_prepared, 'anchor_conditioned', seed)
    return absolute, conditional


def fit_probe_cost_predictor(train_prepared: pd.DataFrame, seed: int = 42):
    """Predict GPU-wall time of an anchor, using training models only."""
    anchors = train_prepared[(train_prepared.replica_count == ANCHOR[0]) &
                             (train_prepared.cpu_core_limit == ANCHOR[1])]
    if (anchors.wall_seconds <= 0).any():
        raise ValueError('Probe wall time must be positive')
    model = ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                                max_features=1.0, random_state=seed, n_jobs=2)
    model.fit(anchors[list(BASE_FEATURES)], np.log(anchors.wall_seconds))
    return model


def select_slo_weighted_probes(test_raw: pd.DataFrame, design: pd.DataFrame,
                               budget: int, absolute, cost_model=None,
                               seed: int = 42) -> list[str]:
    """Select probes from static model/workload metadata and zero-shot SLOs."""
    if not 1 <= budget <= len(design):
        raise ValueError('Invalid probe budget')
    size = registered_parameter_bytes(test_raw)
    static = static_features(test_raw, size)
    zero = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    predicted = static[['workload_group', 'replica_count', 'cpu_core_limit',
                        'latency_slo_ms', 'target_throughput_rps']].copy()
    predicted['latency_log'] = zero[:, 0]
    predicted['rps_log'] = zero[:, 1]
    predicted = predicted.sort_values(['workload_group', 'replica_count',
                                       'cpu_core_limit'])
    groups = sorted(design.index)
    latency = np.vstack([predicted.loc[predicted.workload_group == group,
                                       'latency_log'].to_numpy()
                         for group in groups])
    rps = np.vstack([predicted.loc[predicted.workload_group == group,
                                   'rps_log'].to_numpy()
                     for group in groups])
    known = predicted.groupby('workload_group').first().loc[groups]
    importance = slo_boundary_weights(
        latency, rps, known.latency_slo_ms.to_numpy(),
        known.target_throughput_rps.to_numpy())
    weight = importance
    if cost_model is None:
        estimated_cost = None
    else:
        anchors = _anchor_rows(static).loc[groups]
        estimated_cost = np.exp(np.asarray(
            cost_model.predict(anchors[list(BASE_FEATURES)]), dtype=float))
        median = float(np.median(estimated_cost))
        estimated_cost = np.clip(estimated_cost / median, 0.25, 4.0)
    indices = select_integrated_variance(
        design.loc[groups].to_numpy(), budget,
        importance=weight, predicted_cost=estimated_cost, ridge=1.0)
    return sorted(groups[int(i)] for i in indices)


def predict_fold_methods(test_raw: pd.DataFrame, probe_groups: list[str],
                         absolute, conditional) -> pd.DataFrame:
    """Predict with selected anchors; never access unprobed outcome columns."""
    validate_slates(test_raw)
    anchors = _anchor_rows(test_raw)
    if len(probe_groups) == 0 or len(probe_groups) != len(set(probe_groups)):
        raise ValueError('At least one distinct probe is required')
    if not set(probe_groups).issubset(set(anchors.index)):
        raise ValueError('Probe group outside held-out fold')
    probed = anchors.loc[probe_groups]
    static = static_features(test_raw, registered_parameter_bytes(test_raw))
    design = group_design(static)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    zero = pd.DataFrame(baseline, columns=['latency', 'rps'], index=static.index)
    predicted_anchor = zero.loc[(static.replica_count == ANCHOR[0]) &
                                (static.cpu_core_limit == ANCHOR[1])].copy()
    predicted_anchor.index = static.loc[predicted_anchor.index, 'workload_group'].to_numpy()
    observed = pd.DataFrame({
        'latency': np.log(probed.latency_p95_ms.to_numpy(dtype=float)),
        'rps': np.log((probed.examples_per_second / probed.batch_size).to_numpy(dtype=float)),
    }, index=probed.index)
    residual = observed - predicted_anchor.loc[observed.index]
    global_residual = residual.mean(axis=0).to_numpy()
    ridge = Ridge(alpha=1.0, fit_intercept=False)
    ridge.fit(design.loc[probe_groups].to_numpy(), residual[['latency', 'rps']].to_numpy())
    estimated_residual = pd.DataFrame(ridge.predict(design.to_numpy()),
                                      index=design.index, columns=['latency', 'rps'])
    estimated_anchor = predicted_anchor + estimated_residual
    estimated_anchor.loc[observed.index] = observed
    mean_anchor = predicted_anchor + global_residual
    mean_anchor.loc[observed.index] = observed
    group_order = static.workload_group.to_numpy()

    def conditional_prediction(inferred_anchor: pd.DataFrame) -> np.ndarray:
        feed = static.copy()
        feed['anchor_log_latency'] = inferred_anchor.loc[group_order, 'latency'].to_numpy()
        feed['anchor_log_requests_per_second'] = inferred_anchor.loc[group_order, 'rps'].to_numpy()
        delta = np.asarray(conditional.predict(feed[list(CONDITIONAL_FEATURES)]), dtype=float)
        return np.column_stack([
            feed.anchor_log_latency.to_numpy() + delta[:, 0],
            feed.anchor_log_requests_per_second.to_numpy() + delta[:, 1] +
            feed.prior_log_throughput_gain.to_numpy(),
        ])

    correction = estimated_residual.loc[group_order, ['latency', 'rps']].to_numpy()
    outputs = {
        'zero_shot': baseline,
        'global_mean': conditional_prediction(mean_anchor),
        'ridge_shift': baseline + correction,
        'sparse_conditional': conditional_prediction(estimated_anchor),
    }
    # Deployment has the selected anchor measurements in hand. Clamp only
    # those anchor rows, and only for methods that paid for the probes.
    selected_anchor = ((static.replica_count == ANCHOR[0]) &
                       (static.cpu_core_limit == ANCHOR[1]) &
                       (static.workload_group.isin(probe_groups))).to_numpy()
    measured = observed.loc[static.loc[selected_anchor, 'workload_group'],
                            ['latency', 'rps']].to_numpy()
    for name in ('global_mean', 'ridge_shift', 'sparse_conditional'):
        outputs[name][selected_anchor] = measured
    out = static[['campaign', 'model_id', 'workload_group', 'execution_id',
                  'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                  'target_throughput_rps']].copy()
    for name, values in outputs.items():
        out[f'{name}_latency_log'] = values[:, 0]
        out[f'{name}_rps_log'] = values[:, 1]
    return out


def add_full_anchor_reference(predicted: pd.DataFrame, test_raw: pd.DataFrame,
                              absolute, conditional) -> pd.DataFrame:
    """Explicit 100% anchor-measurement ceiling on the same scored conditions."""
    all_groups = sorted(test_raw.workload_group.unique())
    full = predict_fold_methods(test_raw, all_groups, absolute, conditional)
    out = predicted.copy()
    out['full_anchor_reference_latency_log'] = full.sparse_conditional_latency_log.to_numpy()
    out['full_anchor_reference_rps_log'] = full.sparse_conditional_rps_log.to_numpy()
    return out


def scenario_metrics(predicted: pd.DataFrame, truth: pd.DataFrame,
                     methods: tuple[str, ...] = METHODS) -> dict[str, dict]:
    """Fixed-population seven-candidate error and eight-candidate SLO choice."""
    joined = predicted.merge(truth[['execution_id', 'latency_p95_ms',
                                    'examples_per_second', 'batch_size']],
                             on='execution_id', validate='one_to_one')
    scored = joined[~((joined.replica_count == ANCHOR[0]) &
                      (joined.cpu_core_limit == ANCHOR[1]))].copy()
    if scored.empty or scored.groupby('workload_group').size().ne(7).any():
        raise ValueError('Each scored workload requires seven non-anchor candidates')
    true_l = np.log(scored.latency_p95_ms.to_numpy(dtype=float))
    true_t = np.log((scored.examples_per_second / scored.batch_size).to_numpy(dtype=float))
    joined['true_feasible'] = ((joined.latency_p95_ms <= joined.latency_slo_ms) &
                              (joined.examples_per_second / joined.batch_size >=
                               joined.target_throughput_rps))
    total_feasible_groups = int(joined.groupby('workload_group').true_feasible.any().sum())
    results = {}
    for method in methods:
        pred_l = scored[f'{method}_latency_log'].to_numpy(dtype=float)
        pred_t = scored[f'{method}_rps_log'].to_numpy(dtype=float)
        choice = joined.copy()
        choice['pred_feasible'] = ((choice[f'{method}_latency_log'] <=
                                   np.log(choice.latency_slo_ms)) &
                                  (choice[f'{method}_rps_log'] >=
                                   np.log(choice.target_throughput_rps)))
        # Resource cost proxy: prefer fewer GPUs, then fewer CPU cores.
        choice = choice[choice.pred_feasible].sort_values(
            ['workload_group', 'replica_count', 'cpu_core_limit'])
        chosen = choice.drop_duplicates('workload_group')
        success = int(chosen.true_feasible.sum())
        results[method] = {
            'scored_groups': int(scored.workload_group.nunique()),
            'scored_candidates': int(len(scored)),
            'latency_male': float(np.abs(pred_l - true_l).mean()),
            'rps_male': float(np.abs(pred_t - true_t).mean()),
            'feasible_groups': total_feasible_groups,
            'chosen_groups': int(len(chosen)),
            'successful_choices': success,
            'slo_violations': int(len(chosen) - success),
            'missed_feasible_groups': int(total_feasible_groups -
                                          chosen.groupby('workload_group').true_feasible.any().sum()),
        }
    return results


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    external = raw[(raw.campaign.isin(EXTERNAL_CAMPAIGNS)) & raw.eligible &
                   (raw.device == 'cuda')].copy()
    validate_slates(main)
    validate_slates(external)
    if set(main.workload_group) & set(external.workload_group):
        raise ValueError('External workload groups overlap the main campaign')
    records, splits, scored_predictions = [], [], []
    external_records, external_predictions = [], []
    for model_id in sorted(main.model_id.unique()):
        held = main[main.model_id == model_id].copy()
        train_raw = main[main.model_id != model_id].copy()
        if set(held.workload_group) & set(train_raw.workload_group):
            raise ValueError('Train/test workload group overlap')
        trained = prepare(train_raw)
        absolute, conditional = fit_predictors(trained, seed)
        cost_model = fit_probe_cost_predictor(trained, seed)
        # Parameter count emulates static model metadata supplied at app
        # registration; no performance target is used for design.
        design = group_design(static_features(held, registered_parameter_bytes(held)))
        all_groups = sorted(design.index)
        budgets = (4, 8, 16)
        full = predict_fold_methods(held, all_groups, absolute, conditional)
        anchor_rows = _anchor_rows(held)
        for budget in budgets:
            for selector, selector_seed in [('medoids', seed),
                                            ('integrated_variance', seed),
                                            ('slo_integrated_variance', seed),
                                            ('slo_cost_integrated_variance', seed)] + [
                    ('random', seed + i) for i in range(3)]:
                if selector == 'slo_integrated_variance':
                    probes = select_slo_weighted_probes(
                        held, design, budget, absolute, seed=selector_seed)
                elif selector == 'slo_cost_integrated_variance':
                    probes = select_slo_weighted_probes(
                        held, design, budget, absolute, cost_model=cost_model,
                        seed=selector_seed)
                else:
                    probes = select_probes(design, budget, selector, selector_seed)
                predicted = predict_fold_methods(held, probes, absolute, conditional)
                predicted['full_anchor_reference_latency_log'] = (
                    full.sparse_conditional_latency_log.to_numpy())
                predicted['full_anchor_reference_rps_log'] = (
                    full.sparse_conditional_rps_log.to_numpy())
                metrics = scenario_metrics(predicted, held)
                gpu_wall = float((anchor_rows.loc[probes].replica_count *
                                  anchor_rows.loc[probes].wall_seconds).sum())
                full_wall = float((anchor_rows.replica_count * anchor_rows.wall_seconds).sum())
                scenario = f'{model_id}|{selector}|{selector_seed}|{budget}'
                splits.append({
                    'scenario': scenario, 'heldout_model_id': model_id,
                    'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                    'selector': selector, 'selector_seed': selector_seed,
                    'probe_budget': budget, 'total_groups': len(all_groups),
                    'probe_groups_json': json.dumps(probes),
                    'unprobed_groups_json': json.dumps(sorted(set(all_groups) - set(probes))),
                    'gpu_wall_probe_seconds': gpu_wall,
                    'full_anchor_gpu_wall_seconds': full_wall,
                })
                for method, values in metrics.items():
                    records.append({'scenario': scenario, 'heldout_model_id': model_id,
                                    'selector': selector, 'selector_seed': selector_seed,
                                    'probe_budget': budget, 'method': method,
                                    'gpu_wall_probe_seconds': (full_wall if method ==
                                    'full_anchor_reference' else 0.0 if method ==
                                    'zero_shot' else gpu_wall),
                                    **values})
                predicted.insert(0, 'scenario', scenario)
                scored_predictions.append(predicted)
                if budget == 8:
                    for campaign in EXTERNAL_CAMPAIGNS:
                        external_held = external[(external.model_id == model_id) &
                                                 (external.campaign == campaign)].copy()
                        if external_held.empty:
                            continue
                        # Only main-campaign anchor probes are observed. The
                        # external groups contribute known input conditions
                        # but no outcome to the ridge adaptation.
                        combined = pd.concat([held, external_held], ignore_index=True)
                        transferred = predict_fold_methods(
                            combined, probes, absolute, conditional)
                        transferred = transferred[transferred.campaign == campaign].copy()
                        ext_metrics = scenario_metrics(
                            transferred, external_held, methods=EXTERNAL_METHODS)
                        transferred.insert(0, 'scenario', scenario)
                        external_predictions.append(transferred)
                        for method, values in ext_metrics.items():
                            external_records.append({
                                'scenario': scenario, 'campaign': campaign,
                                'heldout_model_id': model_id, 'selector': selector,
                                'selector_seed': selector_seed, 'probe_budget': budget,
                                'method': method,
                                'gpu_wall_probe_seconds': 0.0 if method ==
                                'zero_shot' else gpu_wall,
                                **values,
                            })
        print(f'Finished {model_id}: {len(all_groups)} groups', flush=True)
    metrics_frame = pd.DataFrame(records)
    manifest = pd.DataFrame(splits)
    metrics_frame.to_csv(output / 'per_model_metrics.csv', index=False)
    manifest.to_csv(output / 'split_manifest.csv', index=False)
    pd.concat(scored_predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    external_frame = pd.DataFrame(external_records)
    external_frame.to_csv(output / 'external_per_model_metrics.csv', index=False)
    pd.concat(external_predictions, ignore_index=True).to_parquet(
        output / 'external_predictions.parquet', index=False)
    # Random seeds are repeated measurements of a selector, not independent
    # held-out models: average them within model before the model macro mean.
    within_model = metrics_frame.groupby(['heldout_model_id', 'selector', 'probe_budget',
                                          'method'], as_index=False).mean(numeric_only=True)
    macro = within_model.groupby(['selector', 'probe_budget', 'method']).agg(
        models=('heldout_model_id', 'nunique'),
        scored_groups=('scored_groups', 'mean'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'mean'),
        successful_choices=('successful_choices', 'mean'),
        slo_violations=('slo_violations', 'mean'),
        missed_feasible_groups=('missed_feasible_groups', 'mean'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'),
    ).reset_index()
    external_within_model = external_frame.groupby(
        ['campaign', 'heldout_model_id', 'selector', 'probe_budget', 'method'],
        as_index=False).mean(numeric_only=True)
    external_macro = external_within_model.groupby(
        ['campaign', 'selector', 'probe_budget', 'method']).agg(
        models=('heldout_model_id', 'nunique'),
        scored_groups=('scored_groups', 'mean'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        feasible_groups=('feasible_groups', 'mean'),
        successful_choices=('successful_choices', 'mean'),
        slo_violations=('slo_violations', 'mean'),
        gpu_wall_probe_seconds=('gpu_wall_probe_seconds', 'mean'),
    ).reset_index()
    summary = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'conditional_response_script_sha256': hashlib.sha256(
            (ROOT / 'scripts/conditional_response.py').read_bytes()).hexdigest(),
        'probe_design_script_sha256': hashlib.sha256(
            (ROOT / 'scripts/probe_design.py').read_bytes()).hexdigest(),
        'sklearn_version': sklearn.__version__, 'seed': seed,
        'split': 'leave one model_id out; one-GPU/two-core probes only on selected conditions',
        'method': 'ridge anchor residual transfer then anchor-conditioned relative response',
        'registration_metadata_assumption': ('model_parameter_bytes is known before '
                                            'performance profiling; only this static JSON '
                                            'field is extracted from the frozen DB'),
        'ridge_alpha': 1.0, 'random_selector_seeds': [seed, seed + 1, seed + 2],
        'outcome_unit': {'latency': 'ms p95', 'throughput': 'requests/second'},
        'probe_cost_note': ('GPU wall uses measured wall_seconds × GPU count; '
                            'wall_seconds already includes load_seconds and compute_seconds'),
        'full_anchor_reference_note': 'Uses all held-out anchor outcomes; 100% anchor budget on the same scored subset',
        'macro_per_model': macro.to_dict(orient='records'),
        'external_note': ('Same held-out model and k=8 main-campaign probes; '
                          'no external workload performance probe'),
        'external_macro_per_model': external_macro.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/sparse_anchor_transfer_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    report = run(args.snapshot, args.output, args.seed)
    for row in report['macro_per_model']:
        if row['method'] in ('zero_shot', 'global_mean', 'ridge_shift',
                             'sparse_conditional', 'full_anchor_reference'):
            print(row, flush=True)
