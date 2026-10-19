"""Independent, stronger conditional-response baselines for the frozen study.

All variants train only on the eight-candidate exhaustive campaign and hold out
an entire model ID. At evaluation, one measured GPU=1/core=2 anchor is available
per held-out workload. SLOs and unmeasured candidate outcomes are never inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor

from conditional_response import ROOT, CAMPAIGNS, BASE_FEATURES, CONDITIONAL_FEATURES, prepare


VARIANTS = ('random_forest_residual', 'hist_gradient_residual',
            'extra_trees_direct', 'extra_trees_static_anchor_shift')


def fit_variant(train: pd.DataFrame, name: str):
    x = train[BASE_FEATURES if name == 'extra_trees_static_anchor_shift'
              else CONDITIONAL_FEATURES]
    if name in ('extra_trees_direct', 'extra_trees_static_anchor_shift'):
        y = train[['true_log_latency', 'true_log_requests_per_second']]
        model = ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2,
                                   random_state=42, n_jobs=2)
        model.fit(x, y)
        return model
    y = np.column_stack((train.latency_residual,
                         train.true_log_requests_per_second -
                         train.anchor_log_requests_per_second))
    if name == 'random_forest_residual':
        model = RandomForestRegressor(n_estimators=200, min_samples_leaf=2,
                                      random_state=42, n_jobs=2)
        model.fit(x, y)
        return model
    if name == 'hist_gradient_residual':
        models = []
        for target in range(2):
            model = HistGradientBoostingRegressor(max_iter=200, max_leaf_nodes=16,
                                                  l2_regularization=1., random_state=42)
            model.fit(x, y[:, target])
            models.append(model)
        return models
    raise ValueError(name)


def predict_variant(model, test: pd.DataFrame, name: str) -> np.ndarray:
    x = test[BASE_FEATURES if name == 'extra_trees_static_anchor_shift'
             else CONDITIONAL_FEATURES]
    if isinstance(model, list):
        outcome = np.column_stack([m.predict(x) for m in model])
    else:
        outcome = model.predict(x)
    if name == 'extra_trees_static_anchor_shift':
        anchor_mask = (test.replica_count == 1) & (test.cpu_core_limit == 2)
        anchor_rows = test.loc[anchor_mask, ['workload_group', 'anchor_log_latency',
                                             'anchor_log_requests_per_second']]
        if anchor_rows.workload_group.duplicated().any():
            raise ValueError('Exactly one anchor per workload group required')
        raw_anchor_predictions = pd.DataFrame({
            'workload_group': test.loc[anchor_mask, 'workload_group'].to_numpy(),
            'predicted_anchor_latency': outcome[anchor_mask, 0],
            'predicted_anchor_throughput': outcome[anchor_mask, 1]})
        shift = anchor_rows.merge(raw_anchor_predictions, on='workload_group',
                                  validate='one_to_one')
        shift['latency_shift'] = (shift.anchor_log_latency -
                                  shift.predicted_anchor_latency)
        shift['throughput_shift'] = (shift.anchor_log_requests_per_second -
                                     shift.predicted_anchor_throughput)
        shift = test[['workload_group']].merge(
            shift[['workload_group', 'latency_shift', 'throughput_shift']],
            on='workload_group', validate='many_to_one')
        outcome = outcome + shift[['latency_shift', 'throughput_shift']].to_numpy()
    elif name != 'extra_trees_direct':
        outcome = outcome + np.column_stack((test.anchor_log_latency,
                                             test.anchor_log_requests_per_second))
    anchor = (test.replica_count == 1) & (test.cpu_core_limit == 2)
    outcome[anchor] = test.loc[anchor, ['anchor_log_latency',
                                        'anchor_log_requests_per_second']].to_numpy()
    return outcome


def main():
    source = ROOT / 'data/audit_v1/measurements.parquet'
    outdir = ROOT / 'artifacts/strong_conditional_baseline_v1'
    outdir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(source)
    main_data = prepare(raw[raw.campaign == CAMPAIGNS[0]])
    external = {c: prepare(raw[raw.campaign == c]) for c in CAMPAIGNS[1:]}
    rows = []
    for held_model in sorted(main_data.model_id.unique()):
        train = main_data[main_data.model_id != held_model]
        eval_sets = {CAMPAIGNS[0]: main_data[main_data.model_id == held_model]}
        eval_sets.update({c: d[d.model_id == held_model] for c, d in external.items()})
        assert held_model not in set(train.model_id)
        for name in VARIANTS:
            predictor = fit_variant(train, name)
            for campaign, fold in eval_sets.items():
                if fold.empty:
                    continue
                outcome = predict_variant(predictor, fold, name)
                part = fold[['campaign', 'model_id', 'workload_group', 'execution_id',
                             'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                             'target_throughput_rps', 'batch_size', 'true_log_latency',
                             'true_log_requests_per_second']].copy()
                part['method'] = name
                part['predicted_log_latency'] = outcome[:, 0]
                part['predicted_log_requests_per_second'] = outcome[:, 1]
                rows.append(part)
            print(f'{held_model}: {name}', flush=True)
    predictions = pd.concat(rows, ignore_index=True)
    predictions.to_parquet(outdir / 'predictions.parquet', index=False)
    unprobed = predictions[~((predictions.replica_count == 1) &
                              (predictions.cpu_core_limit == 2))].copy()
    unprobed['latency_error'] = (unprobed.predicted_log_latency -
                                 unprobed.true_log_latency).abs()
    unprobed['throughput_error'] = (unprobed.predicted_log_requests_per_second -
                                    unprobed.true_log_requests_per_second).abs()
    per_model = unprobed.groupby(['campaign', 'method', 'model_id']).agg(
        rows=('execution_id', 'size'), groups=('workload_group', 'nunique'),
        latency_mae_log=('latency_error', 'mean'),
        throughput_mae_log=('throughput_error', 'mean')).reset_index()
    per_model.to_csv(outdir / 'per_model_metrics.csv', index=False)

    second = predictions[(predictions.campaign == CAMPAIGNS[0]) &
                         (predictions.replica_count == 2) &
                         (predictions.cpu_core_limit == 2)].copy()
    anchor = main_data[(main_data.replica_count == 1) &
                       (main_data.cpu_core_limit == 2)].copy()
    anchor['anchor_pass'] = (anchor.latency_p95_ms <= anchor.latency_slo_ms) & (
        anchor.examples_per_second / anchor.batch_size >= anchor.target_throughput_rps)
    second = second.merge(anchor[['workload_group', 'anchor_pass']],
                          on='workload_group', validate='many_to_one')
    second = second[~second.anchor_pass].copy()
    second['second_pass'] = (second.true_log_latency <= np.log(second.latency_slo_ms)) & (
        second.true_log_requests_per_second >= np.log(second.target_throughput_rps))
    second['predicted_slack'] = np.minimum(
        np.log(second.latency_slo_ms) - second.predicted_log_latency,
        second.predicted_log_requests_per_second - np.log(second.target_throughput_rps))
    ranking = {}
    for name, group in second.groupby('method'):
        assert len(group) == 326 and group.second_pass.sum() == 108
        group = group.sort_values(['predicted_slack', 'workload_group'],
                                  ascending=[False, True])
        ranking[name] = {str(k): int(group.head(k).second_pass.sum())
                         for k in (25, 50, 100, 150, 200, 326)}
    result = {
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'protocol': 'predeclared alternative learners; 9 model-held-out folds; same one anchor',
        'hyperparameters': {
            'random_forest_residual': '200 trees, leaf 2',
            'hist_gradient_residual': '200 iterations, 16 leaves, L2=1',
            'extra_trees_direct': '200 trees, leaf 2',
            'extra_trees_static_anchor_shift':
            '200 trees, leaf 2; no anchor outcome regressor input; post-hoc anchor residual shift'},
        'macro_prediction': {}, 'verified_second_probe_recoveries': ranking}
    for (campaign, name), group in per_model.groupby(['campaign', 'method']):
        result['macro_prediction'][f'{campaign}/{name}'] = {
            'models': len(group), 'unprobed_rows': int(group.rows.sum()),
            'latency_mae_log': float(group.latency_mae_log.mean()),
            'throughput_mae_log': float(group.throughput_mae_log.mean())}
    (outdir / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
