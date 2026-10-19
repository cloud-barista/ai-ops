"""Nested model-heldout anchor-error calibration (exploratory).

For each outer held-out model, train a static absolute response model on the
other models. A correction model is trained only on inner leave-one-model-out
errors. The outer held-out model contributes only its measured anchor response.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from conditional_response import BASE_FEATURES, CAMPAIGNS, prepare


ROOT = Path(__file__).resolve().parents[1]
ANCHOR = (1, 2)
CAL_FEATURES = BASE_FEATURES + [
    'base_log_latency', 'base_log_requests_per_second',
    'anchor_error_log_latency', 'anchor_error_log_requests_per_second',
]
SEED = 42


def make_regressor() -> ExtraTreesRegressor:
    return ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2,
                               max_features=1.0, random_state=SEED, n_jobs=2)


def true_logs(frame: pd.DataFrame) -> np.ndarray:
    return frame[['true_log_latency', 'true_log_requests_per_second']].to_numpy()


def train_absolute(frame: pd.DataFrame) -> ExtraTreesRegressor:
    return make_regressor().fit(frame[BASE_FEATURES], true_logs(frame))


def attach_anchor_errors(frame: pd.DataFrame, base_prediction: np.ndarray) -> pd.DataFrame:
    """Attach the measured anchor's base error to every candidate in its slate."""
    out = frame.copy()
    out['base_log_latency'] = base_prediction[:, 0]
    out['base_log_requests_per_second'] = base_prediction[:, 1]
    anchor = out[(out.replica_count == ANCHOR[0]) &
                 (out.cpu_core_limit == ANCHOR[1])].copy()
    if len(anchor) != out.workload_group.nunique() or anchor.workload_group.duplicated().any():
        raise ValueError('Each workload must have exactly one anchor')
    anchor['anchor_error_log_latency'] = (
        anchor.true_log_latency - anchor.base_log_latency)
    anchor['anchor_error_log_requests_per_second'] = (
        anchor.true_log_requests_per_second - anchor.base_log_requests_per_second)
    out = out.merge(anchor[['workload_group', 'anchor_error_log_latency',
                            'anchor_error_log_requests_per_second']],
                    on='workload_group', validate='many_to_one')
    return out


def fit_inner_calibrator(outer_training: pd.DataFrame) -> ExtraTreesRegressor:
    """Every correction label comes from a model unseen by its base predictor."""
    fragments = []
    for inner_model in sorted(outer_training.model_id.unique()):
        inner_training = outer_training[outer_training.model_id != inner_model]
        inner_test = outer_training[outer_training.model_id == inner_model]
        if set(inner_training.model_id) & set(inner_test.model_id):
            raise AssertionError('Inner model leakage')
        base = train_absolute(inner_training)
        base_prediction = base.predict(inner_test[BASE_FEATURES])
        fragments.append(attach_anchor_errors(inner_test, base_prediction))
    crossfit = pd.concat(fragments, ignore_index=True)
    base_pred = crossfit[['base_log_latency', 'base_log_requests_per_second']].to_numpy()
    errors = true_logs(crossfit) - base_pred
    return make_regressor().fit(crossfit[CAL_FEATURES], errors)


def predict_outer(outer_training: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    if set(outer_training.model_id) & set(target.model_id):
        raise ValueError('Outer model leakage')
    base = train_absolute(outer_training)
    calibrator = fit_inner_calibrator(outer_training)
    out = attach_anchor_errors(target, base.predict(target[BASE_FEATURES]))
    base_pred = out[['base_log_latency', 'base_log_requests_per_second']].to_numpy()
    anchor_error = out[['anchor_error_log_latency',
                        'anchor_error_log_requests_per_second']].to_numpy()
    predictions = {
        'crossfit_anchor_error': base_pred + calibrator.predict(out[CAL_FEATURES]),
        'same_static_anchor_shift': base_pred + anchor_error,
    }
    mask = ((out.replica_count == ANCHOR[0]) &
            (out.cpu_core_limit == ANCHOR[1])).to_numpy()
    for prediction in predictions.values():
        prediction[mask] = true_logs(out)[mask]
    common = out[['campaign', 'workload_group', 'execution_id', 'model_id',
                  'replica_count', 'cpu_core_limit', 'batch_size', 'latency_slo_ms',
                  'target_throughput_rps', 'latency_p95_ms', 'examples_per_second',
                  'true_log_latency', 'true_log_requests_per_second']]
    parts = []
    for method, values in predictions.items():
        part = common.copy()
        part['method'] = method
        part['predicted_log_latency'] = values[:, 0]
        part['predicted_log_requests_per_second'] = values[:, 1]
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def summarize(predictions: pd.DataFrame) -> dict:
    unprobed = predictions[~((predictions.replica_count == ANCHOR[0]) &
                             (predictions.cpu_core_limit == ANCHOR[1]))].copy()
    unprobed['latency_error'] = (unprobed.predicted_log_latency -
                                 unprobed.true_log_latency).abs()
    unprobed['throughput_error'] = (unprobed.predicted_log_requests_per_second -
                                    unprobed.true_log_requests_per_second).abs()
    per_model = unprobed.groupby(['campaign', 'method', 'model_id']).agg(
        workload_groups=('workload_group', 'nunique'),
        latency_mae_log=('latency_error', 'mean'),
        throughput_mae_log=('throughput_error', 'mean')).reset_index()
    prediction = {}
    for (campaign, method), group in per_model.groupby(['campaign', 'method']):
        prediction[f'{campaign}/{method}'] = {
            'models': len(group), 'groups': int(group.workload_groups.sum()),
            'latency_macro_mae_log': float(group.latency_mae_log.mean()),
            'throughput_macro_mae_log': float(group.throughput_mae_log.mean()),
        }
    choice_rows = []
    for (campaign, method, workload_group), group in predictions.groupby(
            ['campaign', 'method', 'workload_group']):
        if len(group) != 8 or group[['replica_count', 'cpu_core_limit']].duplicated().any():
            raise ValueError(f'Incomplete or duplicate slate: {workload_group}')
        anchor = group[(group.replica_count == ANCHOR[0]) &
                       (group.cpu_core_limit == ANCHOR[1])].iloc[0]
        second = group[(group.replica_count == 2) &
                       (group.cpu_core_limit == 2)].iloc[0]
        anchor_pass = bool(anchor.latency_p95_ms <= anchor.latency_slo_ms and
                           anchor.examples_per_second / anchor.batch_size >=
                           anchor.target_throughput_rps)
        second_pass = bool(second.latency_p95_ms <= second.latency_slo_ms and
                           second.examples_per_second / second.batch_size >=
                           second.target_throughput_rps)
        predicted_slack = min(np.log(second.latency_slo_ms) - second.predicted_log_latency,
                              second.predicted_log_requests_per_second -
                              np.log(second.target_throughput_rps))
        choice_rows.append({'campaign': campaign, 'method': method,
                            'workload_group': workload_group, 'model_id': anchor.model_id,
                            'anchor_pass': anchor_pass, 'second_pass': second_pass,
                            'predicted_slack': float(predicted_slack)})
    choices = pd.DataFrame(choice_rows)
    rankings = {}
    for (campaign, method), group in choices.groupby(['campaign', 'method']):
        anchor_fail = group[~group.anchor_pass].sort_values(
            ['predicted_slack', 'workload_group'], ascending=[False, True])
        rankings[f'{campaign}/{method}'] = {
            'anchor_fail': len(anchor_fail),
            'second_probe_feasible': int(anchor_fail.second_pass.sum()),
            'top_k_recoveries': {str(k): int(anchor_fail.head(k).second_pass.sum())
                                 for k in [25, 50, 100, 150, 200, 326]
                                 if k <= len(anchor_fail)},
        }
    return {'per_model': per_model, 'choices': choices,
            'prediction': prediction, 'ranking': rankings}


def run(snapshot: Path = ROOT / 'data/audit_v1/measurements.parquet',
        output: Path = ROOT / 'artifacts/crossfit_anchor_calibration_v1') -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    main = prepare(raw[raw.campaign == CAMPAIGNS[0]])
    external = {name: prepare(raw[raw.campaign == name]) for name in CAMPAIGNS[1:]}
    parts = []
    for held_model in sorted(main.model_id.unique()):
        training = main[main.model_id != held_model]
        targets = [main[main.model_id == held_model]]
        targets.extend(external[name][external[name].model_id == held_model]
                       for name in CAMPAIGNS[1:])
        target = pd.concat(targets, ignore_index=True)
        parts.append(predict_outer(training, target))
        print(f'{held_model}: {target.workload_group.nunique()} groups', flush=True)
    predictions = pd.concat(parts, ignore_index=True)
    predictions.to_parquet(output / 'predictions.parquet', index=False)
    report = summarize(predictions)
    report['per_model'].to_csv(output / 'per_model.csv', index=False)
    report['choices'].to_csv(output / 'choices.csv', index=False)
    metrics = {'source_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
               'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'outer_split': 'leave one model_id out',
               'inner_split': 'leave one training model_id out for calibration labels',
               'static_features': BASE_FEATURES, 'calibration_features': CAL_FEATURES,
               'regressor': {'type': 'ExtraTreesRegressor', 'trees': 100,
                             'min_samples_leaf': 2, 'seed': SEED},
               'prediction': report['prediction'], 'ranking': report['ranking'],
               'scope': 'exploratory same-host observed benchmark outcomes'}
    (output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
    print(json.dumps({'main_prediction': {k: v for k, v in report['prediction'].items()
                                          if k.startswith(CAMPAIGNS[0])},
                      'main_ranking': {k: v for k, v in report['ranking'].items()
                                       if k.startswith(CAMPAIGNS[0])}}, indent=2), flush=True)
    return metrics


if __name__ == '__main__':
    run()
