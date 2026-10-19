"""Audit whether GPU1 timing can support a capped GPU1-to-GPU2 response law.

This is a measurement-protocol and mathematical-bound audit, not a predictor.
The only proposed estimate is the fixed-efficiency 2x GPU1 throughput rule.
Its lower bound applies to *any* prediction capped at that value. Existing
leave-one-model-out predictions are compared on exactly the same target rows.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'data/audit_v1/measurements.parquet'
ARTIFACTS = {
    'model_holdout': ROOT / 'artifacts/telemetry_conditioned_scaling_v1/predictions.parquet',
    'bert4_holdout': ROOT / 'artifacts/telemetry_family_holdout_v1/predictions.parquet',
    'bert5_holdout': ROOT / 'artifacts/telemetry_family_plus_distilbert_v1/predictions.parquet',
}
OUTPUT = ROOT / 'artifacts/replica_bottleneck_identifiability_v1/metrics.json'
CAMPAIGNS = (
    'khu-hf-local-exhaustive',
    'khu-hf-local-novel-matrix',
    'khu-hf-local-space-filling',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs(snapshot: Path) -> pd.DataFrame:
    raw = pd.read_parquet(snapshot)
    frame = raw[(raw.campaign.isin(CAMPAIGNS)) & raw.eligible &
                (raw.device == 'cuda') & (raw.cpu_core_limit == 2)]
    one = frame[frame.replica_count == 1]
    two = frame[frame.replica_count == 2]
    pair = one.merge(two, on=['campaign', 'workload_group'], suffixes=('_one', '_two'),
                     validate='one_to_one')
    if len(pair) != len(one) or len(pair) != len(two):
        raise ValueError('Incomplete GPU1/GPU2 pairs')
    for key in ('model_id', 'batch_size', 'max_length', 'request_concurrency'):
        if not (pair[f'{key}_one'] == pair[f'{key}_two']).all():
            raise ValueError(f'Mismatched pair metadata: {key}')
    if not np.allclose(pair.examples_two, 2 * pair.examples_one):
        raise ValueError('Expected each GPU2 worker to execute the full profile')
    for row in pair.itertuples():
        children = json.loads(row.result_json_two)['replica_results']
        if len(children) != 2 or not np.isclose(
                row.examples_per_second_two,
                sum(child['inference_examples_per_second'] for child in children),
                rtol=1e-8):
            raise ValueError('Unexpected GPU2 throughput aggregation')
    return pair


def protocol_summary(pair: pd.DataFrame) -> list[dict]:
    rows = []
    for campaign, group in pair.groupby('campaign', sort=False):
        scaling = group.examples_per_second_two / group.examples_per_second_one
        gap = (pd.to_datetime(group.started_at_two, utc=True) -
               pd.to_datetime(group.finished_at_one, utc=True)).dt.total_seconds()
        rows.append({
            'campaign': campaign,
            'pairs': int(len(group)),
            'gpu2_examples_exactly_double': int(np.isclose(group.examples_two,
                                                            2 * group.examples_one).sum()),
            'gpu2_starts_after_gpu1_ends': int((gap > 0).sum()),
            'gpu2_to_gpu1_rps_median': float(scaling.median()),
            'gpu2_to_gpu1_rps_p90': float(scaling.quantile(.9)),
            'gpu2_to_gpu1_rps_above_two': int((scaling > 2).sum()),
            'gpu1_cpu_seconds_over_wall_median': float(
                (group.cpu_seconds_one / group.wall_seconds_one).median()),
            'gpu1_compute_seconds_over_wall_median': float(
                (group.compute_seconds_one / group.wall_seconds_one).median()),
            'gpu1_load_seconds_over_wall_median': float(
                (group.load_seconds_one / group.wall_seconds_one).median()),
        })
    return rows


def prediction_summary(pair: pd.DataFrame, predictions: Path, split: str) -> list[dict]:
    observed = pair[['campaign', 'workload_group', 'model_id_one',
                     'examples_per_second_one', 'examples_per_second_two',
                     'batch_size_one']].rename(columns={'model_id_one': 'model_id'})
    pred = pd.read_parquet(predictions)
    frame = pred.merge(observed, on=['campaign', 'workload_group', 'model_id'],
                       validate='one_to_one')
    if len(frame) != len(pred):
        raise ValueError(f'Unmatched baseline predictions: {split}')
    actual_anchor = np.log(frame.examples_per_second_one / frame.batch_size_one)
    actual_target = np.log(frame.examples_per_second_two / frame.batch_size_one)
    if not np.allclose(frame.anchor_log_requests_per_second, actual_anchor, atol=1e-6):
        raise ValueError('Baseline anchor does not match frozen GPU1 observation')
    if not np.allclose(frame.true_log_requests_per_second, actual_target, atol=1e-6):
        raise ValueError('Baseline target does not match frozen GPU2 outcome')
    cap = actual_anchor + np.log(2)
    frame['ideal_two_mae'] = np.abs(cap - actual_target)
    frame['capped_oracle_lower_bound'] = np.maximum(actual_target - cap, 0)
    frame['above_two'] = actual_target > cap
    for method in ('baseline', 'no_count', 'telemetry'):
        frame[f'{method}_mae'] = np.abs(frame[f'{method}_rps_log'] - actual_target)
    columns = ('ideal_two_mae', 'capped_oracle_lower_bound', 'above_two',
               'baseline_mae', 'no_count_mae', 'telemetry_mae')
    per_model = frame.groupby(['campaign', 'model_id'])[list(columns)].mean().reset_index()
    result = []
    for campaign, group in per_model.groupby('campaign', sort=False):
        result.append({
            'split': split, 'campaign': campaign,
            'models': int(len(group)),
            'pairs': int((frame.campaign == campaign).sum()),
            'macro': {key: float(group[key].mean()) for key in columns},
        })
    return result


def run() -> dict:
    pair = pairs(SNAPSHOT)
    result = {
        'snapshot_sha256': sha256(SNAPSHOT),
        'script_sha256': sha256(Path(__file__)),
        'baseline_artifact_sha256': {key: sha256(path)
                                     for key, path in ARTIFACTS.items()},
        'protocol': 'GPU2 uses two concurrent workers, each running the full profile; reported RPS is the sum of worker RPS; CPU seconds include load; GPU2 follows GPU1',
        'metric': 'model-macro mean absolute log error of requests per second',
        'capped_oracle_lower_bound': 'mean(max(log(R2)-log(2*R1), 0)); best possible for any predictor constrained to R2hat <= 2*R1',
        'protocol_summary': protocol_summary(pair),
        'prediction_summary': [row for split, path in ARTIFACTS.items()
                               for row in prediction_summary(pair, path, split)],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
