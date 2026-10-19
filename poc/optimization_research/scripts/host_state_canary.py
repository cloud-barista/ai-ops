"""Exploratory, time-forward test of a cross-application host-state canary.

The question is whether a known application's current one-GPU measurement adds
information when predicting a different, unseen model's two-GPU response from
that model's current one-GPU measurement. The live campaign repeats a fixed
24-condition matrix. This script never trains a cold-start method on the held
model's target outcomes, and it checks that both probes precede the target.

The campaign crosses collector v1/v2; results spanning that boundary are a
diagnostic for instrumentation drift, not evidence of an operational benefit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path('/path/to/aiops/runs/khu-hf-live/executions.sqlite3')
DEFAULT_OUTPUT = ROOT / 'artifacts' / 'host_state_canary_v1'
CANARY_PROFILE = 'bert-tiny-long-burst'
BASE_FEATURES = ['log_params', 'log_batch', 'log_length', 'log_concurrency',
                 'anchor_log_latency', 'anchor_log_rps']
CANARY_FEATURES = BASE_FEATURES + ['canary_log_latency', 'canary_log_rps']


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_pairs(db: Path) -> pd.DataFrame:
    uri = f'file:{db}?mode=ro'
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        raw = pd.read_sql_query(
            '''SELECT e.execution_id, e.run_id, e.ordinal, e.profile_name,
                      e.model_id, e.device, e.replica_count, e.batch_size,
                      e.max_length, e.request_concurrency, e.repeats,
                      e.target_throughput_rps, e.latency_slo_ms,
                      e.started_at, e.finished_at, e.wall_seconds,
                      e.latency_p95_ms, e.examples_per_second, e.result_json,
                      e.success, r.started_at AS run_started_at,
                      r.collector_version
               FROM execution_measurements e
               JOIN collection_runs r ON e.run_id = r.run_id''', connection)
    assert raw.success.eq(1).all(), 'The repeated live campaign must be complete'
    assert raw.groupby('run_id').size().eq(24).all(), 'Each run needs 24 conditions'
    assert not raw.duplicated(['run_id', 'profile_name', 'device', 'replica_count']).any()
    assert raw.groupby('run_id').collector_version.nunique().eq(1).all()
    for name, rows in raw.groupby(['profile_name', 'device', 'replica_count']):
        cols = ['model_id', 'batch_size', 'max_length', 'request_concurrency',
                'repeats', 'target_throughput_rps', 'latency_slo_ms']
        assert rows[cols].nunique(dropna=False).max() == 1, f'Changed condition: {name}'
    run_order = (raw[['run_id', 'run_started_at', 'collector_version']]
                 .drop_duplicates().sort_values('run_started_at').reset_index(drop=True))
    run_order['round_index'] = np.arange(len(run_order))
    raw = raw.merge(run_order[['run_id', 'round_index']], on='run_id', validate='many_to_one')
    canary = raw[(raw.profile_name == CANARY_PROFILE) &
                 (raw.device == 'cuda') & (raw.replica_count == 1)].copy()
    assert len(canary) == len(run_order)
    assert canary.model_id.nunique() == 1
    targets = raw[(raw.device == 'cuda') & (raw.replica_count == 2)]
    paired_profiles = sorted(set(targets.profile_name) - {CANARY_PROFILE})
    assert len(paired_profiles) == 5, paired_profiles
    assert targets[targets.profile_name.isin(paired_profiles)].groupby('profile_name').model_id.nunique().eq(1).all()
    assert targets[targets.profile_name.isin(paired_profiles)].model_id.nunique() == len(paired_profiles)
    assert canary.model_id.iloc[0] not in targets[targets.profile_name.isin(paired_profiles)].model_id.unique()
    anchor = raw[(raw.device == 'cuda') & (raw.replica_count == 1) &
                 raw.profile_name.isin(paired_profiles)].copy()
    target = targets[targets.profile_name.isin(paired_profiles)].copy()
    assert len(anchor) == len(target) == len(run_order) * len(paired_profiles)
    a = anchor.rename(columns={x: 'anchor_' + x for x in ['execution_id', 'ordinal',
        'started_at', 'finished_at', 'wall_seconds', 'latency_p95_ms', 'examples_per_second',
        'result_json']})
    t = target.rename(columns={x: 'target_' + x for x in ['execution_id', 'ordinal',
        'started_at', 'finished_at', 'wall_seconds', 'latency_p95_ms', 'examples_per_second']})
    keys = ['run_id', 'profile_name', 'model_id', 'round_index', 'collector_version',
            'batch_size', 'max_length', 'request_concurrency', 'repeats',
            'target_throughput_rps', 'latency_slo_ms']
    cols_a = keys + [x for x in a if x.startswith('anchor_')]
    cols_t = keys + [x for x in t if x.startswith('target_') and x not in keys]
    pairs = a[cols_a].merge(t[cols_t], on=keys, validate='one_to_one')
    c = canary.rename(columns={x: 'canary_' + x for x in ['execution_id', 'ordinal',
        'started_at', 'finished_at', 'wall_seconds', 'latency_p95_ms', 'examples_per_second',
        'batch_size']})
    pairs = pairs.merge(c[['run_id'] + [x for x in c if x.startswith('canary_')]],
                        on='run_id', validate='many_to_one')
    assert len(pairs) == len(run_order) * len(paired_profiles)
    assert (pairs.anchor_ordinal < pairs.target_ordinal).all()
    assert (pairs.canary_ordinal < pairs.target_ordinal).all()
    assert (pd.to_datetime(pairs.anchor_finished_at) < pd.to_datetime(pairs.target_started_at)).all()
    assert (pd.to_datetime(pairs.canary_finished_at) < pd.to_datetime(pairs.target_started_at)).all()
    for prefix in ['anchor', 'canary']:
        pairs[f'{prefix}_observed_elapsed_seconds'] = (
            pd.to_datetime(pairs[f'{prefix}_finished_at']) -
            pd.to_datetime(pairs[f'{prefix}_started_at'])).dt.total_seconds()
        assert (pairs[f'{prefix}_observed_elapsed_seconds'] > 0).all()
    params = pairs.anchor_result_json.map(lambda payload: json.loads(payload)['model_parameter_bytes'])
    assert params.notna().all() and (params > 0).all()
    pairs['log_params'] = np.log(params.astype(float))
    for raw_col, log_col in [('batch_size', 'log_batch'), ('max_length', 'log_length'),
                             ('request_concurrency', 'log_concurrency')]:
        pairs[log_col] = np.log(pairs[raw_col])
    for prefix in ['anchor', 'target', 'canary']:
        assert (pairs[f'{prefix}_latency_p95_ms'] > 0).all()
        assert (pairs[f'{prefix}_examples_per_second'] > 0).all()
        pairs[f'{prefix}_log_latency'] = np.log(pairs[f'{prefix}_latency_p95_ms'])
        divisor = pairs.canary_batch_size if prefix == 'canary' else pairs.batch_size
        pairs[f'{prefix}_log_rps'] = np.log(pairs[f'{prefix}_examples_per_second'] / divisor)
    pairs['delta_log_latency'] = pairs.target_log_latency - pairs.anchor_log_latency
    pairs['delta_log_rps'] = pairs.target_log_rps - pairs.anchor_log_rps
    pairs['is_v2'] = pairs.collector_version.str.endswith('/v2').astype(int)
    assert set(pairs.collector_version) == {'khu-hf-direct-collector/v1',
                                             'khu-hf-direct-collector/v2'}
    return pairs.sort_values(['round_index', 'profile_name']).reset_index(drop=True)


def make_model(method: str, seed: int):
    if method.endswith('ridge'):
        return make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    return ExtraTreesRegressor(n_estimators=100, min_samples_leaf=3,
                               random_state=seed, n_jobs=2)


def predict_method(method: str, train: pd.DataFrame, test: pd.DataFrame,
                   label: str, seed: int) -> np.ndarray:
    anchor = 'anchor_log_latency' if label == 'delta_log_latency' else 'anchor_log_rps'
    if method == 'ratio_global':
        return test[anchor].to_numpy() + np.full(len(test), train[label].median())
    if method == 'ratio_canary_linear':
        canary = 'canary_log_latency' if label == 'delta_log_latency' else 'canary_log_rps'
        x = train[canary].to_numpy()
        y = train[label].to_numpy()
        slope = np.cov(x, y, ddof=0)[0, 1] / max(np.var(x), 1e-12)
        slope = float(np.clip(slope, -2.0, 2.0))
        return test[anchor].to_numpy() + np.median(y) + slope * (test[canary].to_numpy() - np.median(x))
    if method == 'history_last_ratio':
        return test[anchor].to_numpy() + test['previous_' + label].to_numpy()
    if method == 'history_rolling5_ratio':
        return test[anchor].to_numpy() + test['rolling5_' + label].to_numpy()
    if method == 'anchor_et':
        features = BASE_FEATURES
    elif method == 'anchor_version_et':
        features = BASE_FEATURES + ['is_v2']
    elif method == 'canary_only_et':
        features = ['log_params', 'log_batch', 'log_length', 'log_concurrency',
                    'canary_log_latency', 'canary_log_rps']
    elif method in ('anchor_canary_et', 'anchor_canary_ridge'):
        features = CANARY_FEATURES if 'canary' in method else BASE_FEATURES
    else:
        raise ValueError(method)
    model = make_model(method, seed)
    if method == 'canary_only_et':
        absolute_label = 'target_log_latency' if label == 'delta_log_latency' else 'target_log_rps'
        model.fit(train[features], train[absolute_label])
        return model.predict(test[features])
    model.fit(train[features], train[label])
    return test[anchor].to_numpy() + model.predict(test[features])


def scenarios(pairs: pd.DataFrame) -> dict[str, tuple[list[int], list[int]]]:
    counts = pairs[['round_index', 'collector_version']].drop_duplicates().sort_values('round_index')
    v1 = counts[counts.collector_version.str.endswith('/v1')].round_index.tolist()
    v2 = counts[counts.collector_version.str.endswith('/v2')].round_index.tolist()
    assert len(v1) == 63 and len(v2) == 47
    assert v1 == list(range(63)) and v2 == list(range(63, 110))
    return {
        'mixed_70_40': (list(range(70)), list(range(70, 110))),
        'v1_forward_43_20': (v1[:43], v1[43:]),
        'v2_forward_27_20': (v2[:27], v2[27:]),
        'v1_to_v2': (v1, v2),
    }


def predict_rolling_calibration(train: pd.DataFrame, test: pd.DataFrame,
                                reference: pd.DataFrame, label: str,
                                window: int, seed: int) -> np.ndarray:
    """Correct a frozen anchor model from recent *other-model* observations."""
    anchor = 'anchor_log_latency' if label == 'delta_log_latency' else 'anchor_log_rps'
    model = make_model('anchor_et', seed)
    model.fit(train[BASE_FEATURES], train[label])
    base = test[anchor].to_numpy() + model.predict(test[BASE_FEATURES])
    ref = reference[['round_index', label] + BASE_FEATURES].copy()
    ref['residual'] = ref[label] - model.predict(ref[BASE_FEATURES])
    corrections = []
    for round_index in test.round_index:
        recent = ref[(ref.round_index < round_index) &
                     (ref.round_index >= round_index - window)]
        assert recent.round_index.lt(round_index).all()
        corrections.append(float(recent.residual.median()) if len(recent) else 0.0)
    return base + np.asarray(corrections)


def run(db: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    pairs = load_pairs(db)
    for label in ['delta_log_latency', 'delta_log_rps']:
        pairs['previous_' + label] = pairs.groupby('profile_name')[label].shift(1)
        pairs['rolling5_' + label] = (pairs.groupby('profile_name')[label]
                                      .transform(lambda x: x.shift(1).rolling(5, min_periods=1).median()))
    methods = ['ratio_global', 'ratio_canary_linear', 'anchor_et',
               'anchor_version_et', 'anchor_canary_et', 'anchor_canary_ridge',
               'canary_only_et', 'anchor_et_rolling_cal10', 'anchor_et_rolling_cal20',
               'history_last_ratio', 'history_rolling5_ratio']
    all_predictions = []
    split_records = []
    for scenario, (train_rounds, test_rounds) in scenarios(pairs).items():
        assert max(train_rounds) < min(test_rounds)
        for held_model in sorted(pairs.model_id.unique()):
            train = pairs[pairs.round_index.isin(train_rounds) & (pairs.model_id != held_model)]
            test = pairs[pairs.round_index.isin(test_rounds) & (pairs.model_id == held_model)]
            assert len(test) == len(test_rounds)
            assert train.model_id.nunique() == 4
            assert held_model not in set(train.model_id)
            assert train.round_index.max() < test.round_index.min()
            assert not set(train.target_execution_id) & set(test.target_execution_id)
            split_records.append({'scenario': scenario, 'held_model': held_model,
                                  'train_rows': len(train), 'test_rows': len(test),
                                  'train_round_max': int(train.round_index.max()),
                                  'test_round_min': int(test.round_index.min()),
                                  'train_version_counts': train.collector_version.value_counts().to_dict(),
                                  'test_version_counts': test.collector_version.value_counts().to_dict()})
            for method in methods:
                predictions = test[['run_id', 'round_index', 'collector_version',
                                    'profile_name', 'model_id', 'anchor_execution_id',
                                    'canary_execution_id', 'target_execution_id',
                                    'anchor_wall_seconds', 'canary_wall_seconds',
                                    'anchor_observed_elapsed_seconds',
                                    'canary_observed_elapsed_seconds',
                                    'target_wall_seconds', 'target_log_latency',
                                    'target_log_rps']].copy()
                predictions['scenario'] = scenario
                predictions['held_model'] = held_model
                predictions['method'] = method
                for label, column in [('delta_log_latency', 'pred_log_latency'),
                                      ('delta_log_rps', 'pred_log_rps')]:
                    if method.startswith('anchor_et_rolling_cal'):
                        window = int(method.rsplit('cal', 1)[1])
                        allowed_rounds = set(train_rounds + test_rounds)
                        reference = pairs[(pairs.model_id != held_model) &
                                          (pairs.round_index.isin(allowed_rounds))]
                        assert held_model not in set(reference.model_id)
                        predictions[column] = predict_rolling_calibration(
                            train, test, reference, label, window, seed)
                    else:
                        predictions[column] = predict_method(method, train, test, label, seed)
                all_predictions.append(predictions)
            print(f'{scenario}: {held_model} done', flush=True)
    pred = pd.concat(all_predictions, ignore_index=True)
    assert pred[['pred_log_latency', 'pred_log_rps']].notna().all().all()
    pred.to_csv(output / 'predictions.csv', index=False)
    pred['abs_log_latency_error'] = np.abs(pred.pred_log_latency - pred.target_log_latency)
    pred['abs_log_rps_error'] = np.abs(pred.pred_log_rps - pred.target_log_rps)
    per_model = (pred.groupby(['scenario', 'method', 'held_model'], as_index=False)
                 [['abs_log_latency_error', 'abs_log_rps_error']].mean())
    per_model.to_csv(output / 'per_model.csv', index=False)
    metrics = {}
    for (scenario, method), frame in pred.groupby(['scenario', 'method']):
        macro = per_model[(per_model.scenario == scenario) & (per_model.method == method)]
        n_rounds = frame.round_index.nunique()
        requires_anchor = True
        requires_canary = method in {'ratio_canary_linear', 'anchor_canary_et',
                                     'anchor_canary_ridge', 'canary_only_et'}
        history_privileged = method.startswith('history_')
        metrics.setdefault(scenario, {})[method] = {
            'test_rows': int(len(frame)), 'unique_test_rounds': int(n_rounds),
            'model_macro_latency_mae_log': float(macro.abs_log_latency_error.mean()),
            'model_macro_rps_mae_log': float(macro.abs_log_rps_error.mean()),
            'cold_start_eligible': not history_privileged,
            'probes_per_independent_decision': int(requires_anchor) + int(requires_canary),
            'anchor_gpu_wall_seconds_per_decision_mean': float(frame.anchor_wall_seconds.mean()),
            'anchor_observed_elapsed_seconds_per_decision_mean':
                float(frame.anchor_observed_elapsed_seconds.mean()),
            'extra_canary_gpu_wall_seconds_per_decision_mean':
                float(frame.canary_wall_seconds.mean()) if requires_canary else 0.0,
            'extra_canary_observed_elapsed_seconds_per_decision_mean':
                float(frame.canary_observed_elapsed_seconds.mean()) if requires_canary else 0.0,
            'total_extra_canary_gpu_wall_seconds_as_independent_decisions':
                float(frame.canary_wall_seconds.sum()) if requires_canary else 0.0,
            'total_extra_canary_observed_elapsed_seconds_as_independent_decisions':
                float(frame.canary_observed_elapsed_seconds.sum()) if requires_canary else 0.0,
            'canary_gpu_wall_seconds_if_shared_once_per_round':
                float(frame.drop_duplicates('round_index').canary_wall_seconds.sum()) if requires_canary else 0.0,
        }
    versions = (pairs.groupby('collector_version').round_index.nunique().to_dict())
    metadata = {'input_db': str(db), 'input_db_sha256': sha256(db),
                'script_sha256': sha256(Path(__file__)), 'canary_profile': CANARY_PROFILE,
                'collector_version_rounds': versions, 'paired_target_profiles':
                sorted(pairs.profile_name.unique()), 'splits': split_records,
                'metrics': metrics,
                'cautions': ['mixed_70_40 and v1_to_v2 cross an observed collector version change.',
                             'The live campaign is one host, fixed order, and fixed workloads.',
                             'The canary adds one current one-GPU execution per independent decision.',
                             'History baselines use target outcomes from previous rounds and are unavailable for a truly new application.',
                             'Current generated SLOs nearly never flip; this is a prediction experiment, not a scale policy evaluation.']}
    (output / 'metrics.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = run(args.db, args.output, args.seed)
    for scenario, values in result['metrics'].items():
        print(scenario, {method: round(row['model_macro_latency_mae_log'], 3)
                         for method, row in values.items()})
