"""Exploratory one-probe transfer benchmark for unseen application models.

Each local fold holds out an entire model. The only outcome observed for a test
workload before choosing a resource is its GPU1 / two-core profiling result.
The relative regressor receives workload and resource settings only: the
profiling result is added to its output, never used as a regressor feature.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor


ROOT = Path(__file__).resolve().parents[1]
PRIMARY = 'khu-hf-local-exhaustive'
EXTERNAL = ('khu-hf-local-novel-matrix', 'khu-hf-local-space-filling')
REMOTE = 'khu-hf-remote-216'
TARGETS = ('latency_p95_ms', 'examples_per_second')
FEATURES = ('batch_size', 'max_length', 'request_concurrency', 'cpu_threads',
            'cpu_core_limit', 'replica_count', 'repeats', 'task_causal')
CONFIGS = {(gpu, cores) for gpu in (1, 2) for cores in (2, 4, 8, 16)}
PREDICTION_COLUMNS = {
    'zero_shot': ('pred_zero_shot_latency_p95_ms', 'pred_zero_shot_examples_per_second'),
    'anchor_shift': ('pred_anchor_shift_latency_p95_ms', 'pred_anchor_shift_examples_per_second'),
    'ratio_template': ('pred_ratio_template_latency_p95_ms', 'pred_ratio_template_examples_per_second'),
    'relative_prior': ('pred_relative_prior_latency_p95_ms', 'pred_relative_prior_examples_per_second'),
}
EXPORT_BASE = (
    'execution_id', 'run_id', 'source_db', 'campaign', 'host', 'model_id',
    'model_revision', 'workload_group', 'condition_group', 'device_name',
    'workload_task', 'input_encoding', 'dataset', 'load_condition',
    'batch_size', 'max_length', 'request_concurrency', 'cpu_threads',
    'cpu_core_limit', 'replica_count', 'repeats', 'latency_slo_ms',
    'target_throughput_rps', 'latency_p95_ms', 'examples_per_second',
    'anchor_latency_p95_ms', 'anchor_examples_per_second', 'is_anchor',
    'true_log_latency_p95_ms', 'true_log_requests_per_second',
    'anchor_log_latency_p95_ms', 'anchor_log_requests_per_second',
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_remote_complete_runs(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Use one complete run per remote workload; repeated runs are not new tasks."""
    selected, run_ids = [], {}
    for group, rows in frame.groupby('workload_group', sort=True):
        runs = rows.groupby('run_id').started_at.min().sort_values()
        for run_id in runs.index:
            candidate = rows[rows.run_id == run_id].sort_values('started_at')
            candidate = candidate.drop_duplicates(['replica_count', 'cpu_core_limit'])
            configs = set(map(tuple, candidate[['replica_count', 'cpu_core_limit']].to_numpy()))
            if configs == CONFIGS:
                selected.append(candidate)
                run_ids[group] = run_id
                break
        else:
            raise ValueError(f'No complete eight-candidate remote run for {group}')
    return pd.concat(selected, ignore_index=True), run_ids


def attach_anchors(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate complete GPU slates and attach one measured anchor per group."""
    frame = frame.copy()
    if frame.empty or frame[list(FEATURES[:-1]) + list(TARGETS)].isna().any().any():
        raise ValueError('Empty frame or missing feature/target')
    if (frame[list(TARGETS)] <= 0).any().any():
        raise ValueError('Performance targets must be positive')
    for group, rows in frame.groupby('workload_group', sort=False):
        configs = list(map(tuple, rows[['replica_count', 'cpu_core_limit']].to_numpy()))
        if len(configs) != 8 or set(configs) != CONFIGS:
            raise ValueError(f'Incomplete or duplicated GPU slate: {group}')
        if rows[['latency_slo_ms', 'target_throughput_rps', 'batch_size']].nunique().max() != 1:
            raise ValueError(f'Inconsistent workload/SLO within slate: {group}')
    anchor = frame[(frame.replica_count == 1) & (frame.cpu_core_limit == 2)]
    if anchor.workload_group.duplicated().any():
        raise ValueError('Ambiguous anchor')
    anchor = anchor.set_index('workload_group')[list(TARGETS)]
    frame['anchor_latency_p95_ms'] = anchor.loc[frame.workload_group, TARGETS[0]].to_numpy()
    frame['anchor_examples_per_second'] = anchor.loc[frame.workload_group, TARGETS[1]].to_numpy()
    frame['task_causal'] = frame.workload_task.eq('causal_lm_forward').astype(int)
    frame['is_anchor'] = ((frame.replica_count == 1) & (frame.cpu_core_limit == 2))
    return frame


def prepare_snapshot(snapshot: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw = pd.read_parquet(snapshot)
    relevant = raw[(raw.eligible) & (raw.device == 'cuda') &
                   (raw.replica_count.isin([1, 2]))].copy()
    local = relevant[relevant.campaign.isin((PRIMARY,) + EXTERNAL)].copy()
    remote_raw = relevant[relevant.campaign == REMOTE].copy()
    remote, run_ids = select_remote_complete_runs(remote_raw)
    local = attach_anchors(local)
    remote = attach_anchors(remote)
    if set(local[local.campaign == PRIMARY].workload_group) & set(
            local[local.campaign.isin(EXTERNAL)].workload_group):
        raise ValueError('External workloads overlap primary training workloads')
    return local, remote, run_ids


def relative_residual(frame: pd.DataFrame) -> np.ndarray:
    """Log response relative to anchor, with ideal replica scaling removed."""
    actual = np.log(frame[list(TARGETS)].to_numpy(dtype=float))
    anchor = np.log(frame[['anchor_latency_p95_ms',
                           'anchor_examples_per_second']].to_numpy(dtype=float))
    residual = actual - anchor
    residual[:, 1] -= np.log(frame.replica_count.to_numpy(dtype=float))
    return residual


def fit_template(train: pd.DataFrame) -> dict[tuple[int, int], np.ndarray]:
    ratio = np.log(train[list(TARGETS)].to_numpy(dtype=float)) - np.log(
        train[['anchor_latency_p95_ms', 'anchor_examples_per_second']].to_numpy(dtype=float))
    values = train[['replica_count', 'cpu_core_limit']].copy()
    values[['latency_ratio', 'throughput_ratio']] = ratio
    medians = values.groupby(['replica_count', 'cpu_core_limit'])[
        ['latency_ratio', 'throughput_ratio']].median()
    if set(medians.index) != CONFIGS:
        raise ValueError('Training slates lack a GPU candidate')
    return {key: row.to_numpy(dtype=float) for key, row in medians.iterrows()}


def make_regressor(seed: int) -> ExtraTreesRegressor:
    return ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2,
                               max_features=1.0, random_state=seed, n_jobs=2)


def fit_and_predict(train: pd.DataFrame, evaluation: pd.DataFrame,
                    seed: int = 42) -> pd.DataFrame:
    """Fit on train only; use evaluation outcomes solely at its fixed anchor."""
    if set(train.workload_group) & set(evaluation.workload_group):
        raise ValueError('Train and evaluation workload groups overlap')
    if set(train.model_id) & set(evaluation.model_id):
        raise ValueError('A held-out model appears in training')
    x_train, x_eval = train[list(FEATURES)], evaluation[list(FEATURES)]
    absolute = make_regressor(seed).fit(x_train, np.log(train[list(TARGETS)]))
    relative = make_regressor(seed).fit(x_train, relative_residual(train))
    template = fit_template(train)

    zero_shot = np.asarray(absolute.predict(x_eval), dtype=float)
    relative_log = np.asarray(relative.predict(x_eval), dtype=float)
    anchor_log = np.log(evaluation[['anchor_latency_p95_ms',
                                     'anchor_examples_per_second']].to_numpy(dtype=float))
    anchor_mask = ((evaluation.replica_count == 1) &
                   (evaluation.cpu_core_limit == 2)).to_numpy()
    predicted_anchor = pd.DataFrame(zero_shot[anchor_mask],
                                     index=evaluation.loc[anchor_mask, 'workload_group'])
    predicted_anchor = predicted_anchor.loc[evaluation.workload_group].to_numpy()
    shifted = zero_shot + anchor_log - predicted_anchor
    template_log = anchor_log + np.vstack([
        template[(int(gpu), int(cores))] for gpu, cores in
        evaluation[['replica_count', 'cpu_core_limit']].to_numpy()])
    relative_log += anchor_log
    relative_log[:, 1] += np.log(evaluation.replica_count.to_numpy(dtype=float))

    out = evaluation.copy()
    out['true_log_latency_p95_ms'] = np.log(out.latency_p95_ms)
    out['true_log_requests_per_second'] = np.log(out.examples_per_second / out.batch_size)
    out['anchor_log_latency_p95_ms'] = np.log(out.anchor_latency_p95_ms)
    out['anchor_log_requests_per_second'] = np.log(
        out.anchor_examples_per_second / out.batch_size)
    for method, log_pred in [('zero_shot', zero_shot), ('anchor_shift', shifted),
                             ('ratio_template', template_log),
                             ('relative_prior', relative_log)]:
        log_pred = log_pred.copy()
        if method != 'zero_shot':
            log_pred[anchor_mask] = anchor_log[anchor_mask]
        values = np.exp(log_pred)
        for index, column in enumerate(PREDICTION_COLUMNS[method]):
            out[column] = values[:, index]
        out[f'pred_{method}_log_latency_p95_ms'] = log_pred[:, 0]
        out[f'pred_{method}_log_requests_per_second'] = (
            log_pred[:, 1] - np.log(out.batch_size.to_numpy(dtype=float)))
    out['is_anchor'] = anchor_mask
    return out


def error_summary(frame: pd.DataFrame) -> dict:
    """Equal workload weight: every complete slate has seven scored rows."""
    rows = frame[~frame.is_anchor]
    if rows.empty or rows.groupby('workload_group').size().ne(7).any():
        raise ValueError('Every evaluated workload must have seven unprobed candidates')
    actual = np.log(rows[list(TARGETS)].to_numpy(dtype=float))
    result = {}
    for method, cols in PREDICTION_COLUMNS.items():
        errors = np.abs(np.log(rows[list(cols)].to_numpy(dtype=float)) - actual)
        result[method] = {'latency_male': float(errors[:, 0].mean()),
                          'throughput_male': float(errors[:, 1].mean())}
    return result


def accuracy_report(predictions: pd.DataFrame, seed: int) -> dict:
    report = {}
    rng = np.random.default_rng(seed)
    for campaign, campaign_rows in predictions.groupby('campaign', sort=True):
        per_model = {model: error_summary(rows) for model, rows in
                     campaign_rows.groupby('model_id', sort=True)}
        methods = list(PREDICTION_COLUMNS)
        macro = {method: {metric: float(np.mean([
            record[method][metric] for record in per_model.values()]))
            for metric in ('latency_male', 'throughput_male')}
            for method in methods}
        result = {'rows': len(campaign_rows),
                  'workload_groups': int(campaign_rows.workload_group.nunique()),
                  'models': len(per_model), 'per_model': per_model, 'macro': macro,
                  'pooled_group_weighted': error_summary(campaign_rows)}
        if len(per_model) >= 3:
            models = sorted(per_model)
            comparisons = {}
            for baseline in ('zero_shot', 'anchor_shift', 'ratio_template'):
                differences = np.array([[
                    per_model[m][baseline][metric] -
                    per_model[m]['relative_prior'][metric]
                    for metric in ('latency_male', 'throughput_male')]
                    for m in models])
                sampled = differences[rng.integers(0, len(models),
                                                    size=(20000, len(models)))].mean(axis=1)
                comparisons[f'{baseline}_minus_relative_prior'] = {
                    'mean': differences.mean(axis=0).tolist(),
                    'model_cluster_bootstrap_95_ci': np.quantile(
                        sampled, [.025, .975], axis=0).T.tolist(),
                    'unit': 'held-out model; descriptive after exploratory method selection'}
            result['paired_differences'] = comparisons
        report[campaign] = result
    return report


def observed_feasible(rows: pd.DataFrame) -> pd.Series:
    return ((rows.latency_p95_ms <= rows.latency_slo_ms) &
            (rows.examples_per_second / rows.batch_size >= rows.target_throughput_rps))


def predicted_feasible(rows: pd.DataFrame, policy: str) -> pd.Series:
    latency, throughput = PREDICTION_COLUMNS[policy]
    return ((rows[latency] <= rows.latency_slo_ms) &
            (rows[throughput] / rows.batch_size >= rows.target_throughput_rps))


def choose_candidate(rows: pd.DataFrame, policy: str) -> pd.Series | None:
    if policy == 'oracle':
        allowed = rows[observed_feasible(rows)]
    elif policy == 'anchor_only':
        allowed = rows[rows.is_anchor & observed_feasible(rows)]
    else:
        allowed = rows[predicted_feasible(rows, policy)]
    if allowed.empty:
        return None
    return allowed.sort_values(['replica_count', 'cpu_core_limit']).iloc[0]


def decision_records(predictions: pd.DataFrame) -> pd.DataFrame:
    records = []
    policies = ('anchor_only',) + tuple(PREDICTION_COLUMNS) + ('oracle',)
    for group, rows in predictions.groupby('workload_group', sort=False):
        anchor = rows[rows.is_anchor]
        if len(anchor) != 1:
            raise ValueError(f'Invalid anchor count: {group}')
        anchor_ok = bool(observed_feasible(anchor).iloc[0])
        oracle_ok = bool(observed_feasible(rows).any())
        for policy in policies:
            selected = choose_candidate(rows, policy)
            record = {'campaign': rows.campaign.iloc[0], 'model_id': rows.model_id.iloc[0],
                      'workload_group': group, 'policy': policy,
                      'anchor_feasible': anchor_ok, 'oracle_feasible': oracle_ok,
                      'chosen': selected is not None,
                      'selected_execution_id': None, 'selected_replica_count': None,
                      'selected_cpu_core_limit': None, 'selected_actual_feasible': None}
            if selected is not None:
                record.update(selected_execution_id=selected.execution_id,
                              selected_replica_count=int(selected.replica_count),
                              selected_cpu_core_limit=int(selected.cpu_core_limit),
                              selected_actual_feasible=bool(observed_feasible(
                                  pd.DataFrame([selected])).iloc[0]))
            records.append(record)
    return pd.DataFrame(records)


def decision_report(choices: pd.DataFrame) -> dict:
    report = {}
    for campaign, rows in choices.groupby('campaign', sort=True):
        report[campaign] = {}
        for subset_name, subset in [('all', rows),
                                    ('anchor_failed', rows[~rows.anchor_feasible])]:
            report[campaign][subset_name] = {}
            for policy, decisions in subset.groupby('policy', sort=True):
                chosen = decisions[decisions.chosen]
                successes = int(chosen.selected_actual_feasible.eq(True).sum())
                violations = int(chosen.selected_actual_feasible.eq(False).sum())
                feasible = int(decisions.oracle_feasible.sum())
                report[campaign][subset_name][policy] = {
                    'groups': len(decisions), 'oracle_feasible_groups': feasible,
                    'chosen': len(chosen), 'successful_choices': successes,
                    'observed_violations': violations,
                    'missed_feasible_groups': feasible - successes,
                    'choices_on_infeasible_groups': int((~chosen.oracle_feasible).sum()),
                    'two_gpu_choices': int(chosen.selected_replica_count.eq(2).sum())}
    return report


def manifest_rows(frame: pd.DataFrame, role: str) -> pd.DataFrame:
    cols = ['campaign', 'host', 'model_id', 'workload_group', 'condition_group',
            'run_id', 'execution_id', 'replica_count', 'cpu_core_limit', 'is_anchor']
    return frame[cols].assign(role=role, heldout_fold=frame.model_id if role != 'remote_stress'
                               else 'remote_stress')


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    local, remote, remote_runs = prepare_snapshot(snapshot)
    primary = local[local.campaign == PRIMARY].copy()
    predictions, splits = [], {}
    for heldout in sorted(primary.model_id.unique()):
        train = primary[primary.model_id != heldout]
        evaluation = local[local.model_id == heldout]
        if set(train.model_id) & set(evaluation.model_id):
            raise AssertionError('Model leakage')
        predictions.append(fit_and_predict(train, evaluation, seed))
        splits[heldout] = {
            'train_main_rows': len(train), 'train_main_groups': int(train.workload_group.nunique()),
            'test_main_rows': int((evaluation.campaign == PRIMARY).sum()),
            'test_main_groups': int(evaluation.loc[evaluation.campaign == PRIMARY,
                                                    'workload_group'].nunique()),
            'test_external_rows': int(evaluation.campaign.isin(EXTERNAL).sum()),
            'test_external_groups': int(evaluation.loc[evaluation.campaign.isin(EXTERNAL),
                                                        'workload_group'].nunique()),
        }
        print(f'held out {heldout}: {splits[heldout]}', flush=True)
    predictions.append(fit_and_predict(primary, remote, seed))
    prediction = pd.concat(predictions, ignore_index=True)
    prediction_columns = list(EXPORT_BASE) + [column for column in prediction
                                               if column.startswith('pred_')]
    prediction = prediction[prediction_columns].copy()
    prediction.to_parquet(output / 'predictions.parquet', index=False)
    manifest = pd.concat([
        manifest_rows(primary, 'primary_leave_one_model_out'),
        manifest_rows(local[local.campaign.isin(EXTERNAL)], 'external_leave_one_model_out'),
        manifest_rows(remote, 'remote_stress')], ignore_index=True)
    manifest.to_csv(output / 'split_manifest.csv', index=False)
    choices = decision_records(prediction)
    choices.to_csv(output / 'choices.csv', index=False)
    result = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'exploratory; same-host and remote stress tests are observational',
        'research_question': 'Can one fixed measured profile support new-model resource response prediction?',
        'method': 'relative log targets with log(replica_count) throughput prior; anchor outcome is additive only',
        'not_a_novelty_claim': 'Relative modeling and a replica prior alone are established ideas.',
        'seed': seed, 'anchor': {'replica_count': 1, 'cpu_core_limit': 2},
        'features': FEATURES, 'targets': TARGETS, 'policies': list(PREDICTION_COLUMNS),
        'training': {'model': 'ExtraTreesRegressor', 'n_estimators': 200,
                     'min_samples_leaf': 2, 'max_features': 1.0, 'n_jobs': 2,
                     'training_main_only': True, 'outer_split': 'leave one model out'},
        'splits': splits, 'remote_selected_run_ids_by_group': remote_runs,
        'source_snapshot': str(snapshot.resolve()), 'snapshot_sha256': sha256_file(snapshot),
        'script_sha256': sha256_file(Path(__file__)),
        'split_manifest_sha256': sha256_file(output / 'split_manifest.csv'),
        'predictions_sha256': sha256_file(output / 'predictions.parquet'),
        'choices_sha256': sha256_file(output / 'choices.csv'),
        'versions': {'python': platform.python_version(), 'numpy': np.__version__,
                     'pandas': pd.__version__, 'sklearn': sklearn.__version__},
        'accuracy': accuracy_report(prediction, seed),
        'decisions': decision_report(choices),
        'limitations': [
            'The main 575 workload groups have one measurement per candidate.',
            'The same-host campaigns share a GPU type; remote changes model, host, GPU and input protocol together.',
            'Remote groups with repeated runs contribute only one complete run each.',
            'Collector-generated SLOs are not user service objectives; latency is batch p95 and throughput is samples/sec.',
            'Decisions use measured samples/sec divided by batch size as requests/sec.',
            'The physical GPU count is on one host, not a multi-VM scale-out measurement.',
            'Model-cluster bootstrap intervals describe these held-out models after exploratory method selection.',
        ],
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/novel_transfer_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    report = run(args.snapshot, args.output, args.seed)
    print(json.dumps({campaign: data['macro'] for campaign, data in
                      report['accuracy'].items()}, indent=2), flush=True)
