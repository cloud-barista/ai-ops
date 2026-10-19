"""Predict a separately benchmarked GPU1 shape from an observed GPU2 shape.

This is an offline, static capacity comparison. The SQLite data do not contain
an executed scale-in transition or a user-facing service SLO.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor


ROOT = Path(__file__).resolve().parents[1]
MAIN = 'khu-hf-local-exhaustive'
LOCAL_CAMPAIGNS = (MAIN, 'khu-hf-local-novel-matrix',
                   'khu-hf-local-space-filling')
REMOTE = 'khu-hf-remote-216'
BERT_FAMILY = frozenset({
    'google-bert/bert-base-uncased',
    'google/bert_uncased_L-2_H-128_A-2',
    'google/bert_uncased_L-4_H-256_A-4',
    'google/bert_uncased_L-4_H-512_A-8',
})
STATIC_FEATURES = ('current_log_latency', 'current_log_rps', 'log_batch',
                   'log_length', 'log_concurrency', 'log_parameter_bytes',
                   'is_causal_lm')
TELEMETRY_KEYS = ('gpu_utilization_percent_mean',
                  'gpu_utilization_percent_max', 'gpu_memory_used_mib_max',
                  'cpu_percent_mean')
TELEMETRY_FEATURES = tuple('current_log1p_' + k for k in TELEMETRY_KEYS)
METHODS = ('ratio', 'donor_median', 'learned_static', 'learned_telemetry')
MARGINS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2)
LATENCY_RATIOS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)
RPS_DIVISORS = (2.0, 2.25, 2.5, 3.0)
MAX_DONOR_FALSE_APPROVAL_FRACTION = 0.05


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_pairs(raw: pd.DataFrame, campaign: str) -> pd.DataFrame:
    """Keep first complete measurement for each shape and join only its label."""
    source = raw[(raw.campaign == campaign) & (raw.eligible) &
                 (raw.success == 1) & (raw.device == 'cuda') &
                 (raw.cpu_core_limit == 2) &
                 (raw.replica_count.isin([1, 2]))].copy()
    source = source.sort_values(['started_at', 'execution_id']).drop_duplicates(
        ['workload_group', 'replica_count'], keep='first')
    current = source[source.replica_count == 2].copy()
    target = source[source.replica_count == 1].copy()
    if current.workload_group.duplicated().any() or target.workload_group.duplicated().any():
        raise ValueError('Repeated physical shape after first-run selection')
    current = current.set_index('workload_group', verify_integrity=True)
    target = target.set_index('workload_group', verify_integrity=True)
    if set(current.index) != set(target.index):
        raise ValueError('Unpaired GPU1/GPU2 workload group')
    target = target.loc[current.index]
    for key in ('model_id', 'model_revision', 'batch_size', 'max_length',
                'request_concurrency', 'dataset', 'workload_task',
                'target_throughput_rps', 'latency_slo_ms'):
        if not (current[key].to_numpy() == target[key].to_numpy()).all():
            raise ValueError(f'GPU1/GPU2 mismatch: {key}')
    if (current[['latency_p95_ms', 'examples_per_second', 'batch_size',
                 'latency_slo_ms', 'target_throughput_rps']] <= 0).any().any():
        raise ValueError('Nonpositive current performance or target')
    if (target[['latency_p95_ms', 'examples_per_second', 'batch_size']] <= 0).any().any():
        raise ValueError('Nonpositive target performance')
    features = current_features(current)
    features.insert(0, 'campaign', campaign)
    features.insert(1, 'workload_group', current.index.to_numpy())
    features.insert(2, 'model_id', current.model_id.to_numpy())
    features.insert(3, 'current_execution_id', current.execution_id.to_numpy())
    features['target_execution_id'] = target.execution_id.to_numpy()
    features['log_latency_slo'] = np.log(current.latency_slo_ms.to_numpy(dtype=float))
    features['log_rps_min'] = np.log(current.target_throughput_rps.to_numpy(dtype=float))
    features['target_log_latency'] = np.log(target.latency_p95_ms.to_numpy(dtype=float))
    features['target_log_rps'] = np.log(target.examples_per_second.to_numpy(dtype=float) /
                                    target.batch_size.to_numpy(dtype=float))
    features['delta_latency'] = features.target_log_latency - features.current_log_latency
    features['delta_rps'] = features.target_log_rps - features.current_log_rps
    return features.reset_index(drop=True)


def current_features(current: pd.DataFrame) -> pd.DataFrame:
    """Construct features exclusively from the observed GPU2 row."""
    result = pd.DataFrame(index=current.index)
    result['current_log_latency'] = np.log(current.latency_p95_ms.astype(float))
    result['current_log_rps'] = np.log(current.examples_per_second.astype(float) /
                                       current.batch_size.astype(float))
    result['log_batch'] = np.log(current.batch_size.astype(float))
    result['log_length'] = np.log(current.max_length.astype(float))
    result['log_concurrency'] = np.log(current.request_concurrency.astype(float))
    result['log_parameter_bytes'] = [np.log(float(json.loads(raw)['model_parameter_bytes']))
                                     for raw in current.result_json]
    result['is_causal_lm'] = (current.workload_task == 'causal_lm_forward').astype(float)
    summaries = [json.loads(raw) for raw in current.node_summary_json]
    for key, column in zip(TELEMETRY_KEYS, TELEMETRY_FEATURES):
        result[column] = np.log1p([float(item[key]) for item in summaries])
    if not np.isfinite(result[list(STATIC_FEATURES) + list(TELEMETRY_FEATURES)].to_numpy()).all():
        raise ValueError('Invalid GPU2 feature')
    return result


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, method: str) -> np.ndarray:
    current = test[['current_log_latency', 'current_log_rps']].to_numpy(dtype=float)
    if method == 'ratio':
        return current + np.array([0.0, -np.log(2.0)])
    if method == 'donor_median':
        correction = train[['delta_latency', 'delta_rps']].median().to_numpy(dtype=float)
        return current + correction
    if method in ('learned_static', 'learned_telemetry'):
        features = list(STATIC_FEATURES)
        if method == 'learned_telemetry':
            features += list(TELEMETRY_FEATURES)
        model = ExtraTreesRegressor(n_estimators=128, min_samples_leaf=5,
                                    max_features=1.0, random_state=42, n_jobs=2)
        model.fit(train[features], train[['delta_latency', 'delta_rps']])
        return current + np.asarray(model.predict(test[features]), dtype=float)
    raise ValueError(method)


def donor_crossfit(train: pd.DataFrame) -> pd.DataFrame:
    if train.model_id.nunique() < 3:
        raise ValueError('At least three donor models required for model CV')
    pieces = []
    for held in sorted(train.model_id.unique()):
        donor = train[train.model_id != held]
        val = train[train.model_id == held].copy()
        for method in METHODS:
            prediction = fit_predict(donor, val, method)
            val[f'{method}_log_latency'] = prediction[:, 0]
            val[f'{method}_log_rps'] = prediction[:, 1]
        pieces.append(val)
    return pd.concat(pieces, ignore_index=True)


def decision_counts(frame: pd.DataFrame, pred_latency: np.ndarray,
                    pred_rps: np.ndarray, margin: float) -> dict[str, int | float]:
    current_ok = ((frame.current_log_latency.to_numpy() <= frame.log_latency_slo.to_numpy()) &
                  (frame.current_log_rps.to_numpy() >= frame.log_rps_min.to_numpy()))
    target_ok = ((frame.target_log_latency.to_numpy() <= frame.log_latency_slo.to_numpy()) &
                 (frame.target_log_rps.to_numpy() >= frame.log_rps_min.to_numpy()))
    approve = (current_ok & (pred_latency + margin <= frame.log_latency_slo.to_numpy()) &
               (pred_rps - margin >= frame.log_rps_min.to_numpy()))
    safe = int((approve & target_ok).sum())
    false = int((approve & ~target_ok).sum())
    return {'eligible': int(current_ok.sum()), 'possible_safe': int((current_ok & target_ok).sum()),
            'approved': int(approve.sum()), 'safe_approved': safe, 'false_approved': false,
            'false_fraction': false / max(int(approve.sum()), 1)}


def select_rule(cv: pd.DataFrame, method: str) -> dict:
    candidates = []
    if method == 'ratio':
        for ratio in LATENCY_RATIOS:
            for divisor in RPS_DIVISORS:
                pred_lat = cv.current_log_latency.to_numpy() + np.log(ratio)
                pred_rps = cv.current_log_rps.to_numpy() - np.log(divisor)
                for margin in MARGINS:
                    counts = decision_counts(cv, pred_lat, pred_rps, margin)
                    if counts['false_fraction'] <= MAX_DONOR_FALSE_APPROVAL_FRACTION:
                        candidates.append({'latency_ratio': ratio, 'rps_divisor': divisor,
                                           'margin': margin, **counts})
    else:
        pred_lat = cv[f'{method}_log_latency'].to_numpy()
        pred_rps = cv[f'{method}_log_rps'].to_numpy()
        for margin in MARGINS:
            counts = decision_counts(cv, pred_lat, pred_rps, margin)
            if counts['false_fraction'] <= MAX_DONOR_FALSE_APPROVAL_FRACTION:
                candidates.append({'margin': margin, **counts})
    if not candidates:
        raise ValueError(f'No donor rule meets the risk constraint: {method}')
    # Avoid a zero-approval winner; prioritize observed safe approvals on donor CV.
    best = sorted(candidates, key=lambda x: (-x['safe_approved'], x['false_approved'],
                                             x['approved'], x['margin'],
                                             x.get('latency_ratio', 0),
                                             x.get('rps_divisor', 0)))[0]
    return best


def predict_fold(train: pd.DataFrame, tests: list[pd.DataFrame], fold_name: str) -> tuple[pd.DataFrame, dict]:
    cv = donor_crossfit(train)
    rules = {method: select_rule(cv, method) for method in METHODS}
    combined = pd.concat(tests, ignore_index=True)
    out = combined[['campaign', 'workload_group', 'model_id', 'current_execution_id',
                    'target_execution_id', 'current_log_latency', 'current_log_rps',
                    'target_log_latency', 'target_log_rps', 'log_latency_slo',
                    'log_rps_min']].copy()
    out.insert(0, 'fold', fold_name)
    for method in METHODS:
        pred = fit_predict(train, combined, method)
        out[f'{method}_log_latency'] = pred[:, 0]
        out[f'{method}_log_rps'] = pred[:, 1]
        if method == 'ratio':
            rule = rules[method]
            rule_lat = combined.current_log_latency.to_numpy() + np.log(rule['latency_ratio'])
            rule_rps = combined.current_log_rps.to_numpy() - np.log(rule['rps_divisor'])
        else:
            rule_lat, rule_rps = pred[:, 0], pred[:, 1]
        out[f'{method}_rule_log_latency'] = rule_lat
        out[f'{method}_rule_log_rps'] = rule_rps
        out[f'{method}_margin'] = rules[method]['margin']
        out[f'{method}_approve'] = ((combined.current_log_latency.to_numpy() <=
                                    combined.log_latency_slo.to_numpy()) &
                                   (combined.current_log_rps.to_numpy() >=
                                    combined.log_rps_min.to_numpy()) &
                                   (rule_lat + rules[method]['margin'] <=
                                    combined.log_latency_slo.to_numpy()) &
                                   (rule_rps - rules[method]['margin'] >=
                                    combined.log_rps_min.to_numpy()))
    return out, rules


def summarize(pred: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for (fold, campaign, model_id), group in pred.groupby(['fold', 'campaign', 'model_id']):
        truth = ((group.target_log_latency <= group.log_latency_slo) &
                 (group.target_log_rps >= group.log_rps_min)).to_numpy()
        eligible = ((group.current_log_latency <= group.log_latency_slo) &
                    (group.current_log_rps >= group.log_rps_min)).to_numpy()
        for method in METHODS:
            approved = group[f'{method}_approve'].to_numpy(dtype=bool)
            rows.append({
                'fold': fold, 'campaign': campaign, 'model_id': model_id, 'method': method,
                'groups': len(group), 'eligible': int(eligible.sum()),
                'possible_safe': int((eligible & truth).sum()),
                'approved': int(approved.sum()),
                'safe_approved': int((approved & truth).sum()),
                'false_approved': int((approved & ~truth).sum()),
                'latency_male': float(np.mean(np.abs(group[f'{method}_log_latency'] -
                                                      group.target_log_latency))),
                'rps_male': float(np.mean(np.abs(group[f'{method}_log_rps'] -
                                                  group.target_log_rps))),
            })
    per_model = pd.DataFrame(rows)
    aggregate = per_model.groupby(['fold', 'campaign', 'method'], as_index=False).agg(
        models=('model_id', 'nunique'), groups=('groups', 'sum'), eligible=('eligible', 'sum'),
        possible_safe=('possible_safe', 'sum'), approved=('approved', 'sum'),
        safe_approved=('safe_approved', 'sum'), false_approved=('false_approved', 'sum'),
        latency_model_macro_male=('latency_male', 'mean'),
        rps_model_macro_male=('rps_male', 'mean'))
    aggregate['test_false_fraction'] = aggregate.false_approved / aggregate.approved.clip(lower=1)
    return per_model, aggregate


def frontier(pred: pd.DataFrame) -> pd.DataFrame:
    """Post-hoc diagnostic; never use the test-set frontier to select an action."""
    rows = []
    for (fold, campaign), group in pred.groupby(['fold', 'campaign']):
        for method in METHODS:
            pred_lat = group[f'{method}_rule_log_latency'].to_numpy()
            pred_rps = group[f'{method}_rule_log_rps'].to_numpy()
            for margin in MARGINS:
                rows.append({'fold': fold, 'campaign': campaign, 'method': method,
                             'margin': margin, **decision_counts(group, pred_lat, pred_rps, margin)})
    return pd.DataFrame(rows)


def run(snapshot: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(snapshot)
    campaigns = {name: prepare_pairs(raw, name) for name in (*LOCAL_CAMPAIGNS, REMOTE)}
    expected = {MAIN: 575, LOCAL_CAMPAIGNS[1]: 18, LOCAL_CAMPAIGNS[2]: 44, REMOTE: 24}
    for name, frame in campaigns.items():
        if len(frame) != expected[name]:
            raise ValueError(f'{name}: expected {expected[name]} pairs, got {len(frame)}')
    main = campaigns[MAIN]
    predictions = []
    selections = {}
    for held in sorted(main.model_id.unique()):
        train = main[main.model_id != held]
        tests = [campaigns[name][campaigns[name].model_id == held] for name in LOCAL_CAMPAIGNS]
        pred, rules = predict_fold(train, tests, f'LOSO:{held}')
        predictions.append(pred)
        selections[f'LOSO:{held}'] = rules
    family_train = main[~main.model_id.isin(BERT_FAMILY)]
    family_tests = [campaigns[name][campaigns[name].model_id.isin(BERT_FAMILY)]
                    for name in LOCAL_CAMPAIGNS]
    family_pred, family_rules = predict_fold(family_train, family_tests, 'BERT4')
    predictions.append(family_pred)
    selections['BERT4'] = family_rules
    remote_pred, remote_rules = predict_fold(main, [campaigns[REMOTE]], 'REMOTE')
    predictions.append(remote_pred)
    selections['REMOTE'] = remote_rules
    pred = pd.concat(predictions, ignore_index=True)
    pred.to_parquet(output / 'predictions.parquet', index=False)
    per_model, aggregate = summarize(pred)
    per_model.to_csv(output / 'per_model.csv', index=False)
    aggregate.to_csv(output / 'summary.csv', index=False)
    frontier(pred).to_csv(output / 'frontier_posthoc.csv', index=False)
    (output / 'donor_selected_rules.json').write_text(json.dumps(selections, indent=2) + '\n')
    payload = {
        'snapshot_sha256': sha256(snapshot),
        'protocol_sha256': sha256(ROOT / 'reports/static_scale_in_protocol.md'),
        'script_sha256': sha256(Path(__file__)),
        'source_pair_counts': {name: len(frame) for name, frame in campaigns.items()},
        'meaning': 'Separately benchmarked GPU1 static shape from observed GPU2; not VM scale-in',
        'selection': 'donor-model crossfit; <=5% false among donor approved; maximize donor safe approvals',
        'by_fold_campaign_method': aggregate.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(payload, indent=2) + '\n')
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/static_scale_in_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.output)
    for row in result['by_fold_campaign_method']:
        if row['campaign'] == MAIN and row['fold'] in ('BERT4', 'REMOTE'):
            print(row)
