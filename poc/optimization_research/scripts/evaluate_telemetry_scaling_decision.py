"""Evaluate a static GPU1-to-GPU2 capacity decision under donor-only targets.

These synthetic constraints are diagnostic, not real user SLOs or a replay of
time-ordered scale actions. A workload contributes 25 correlated queries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MAIN = 'khu-hf-local-exhaustive'
CAUSAL_MODEL = 'Qwen/Qwen2.5-0.5B-Instruct'
METHODS = ('baseline', 'telemetry', 'gpu_telemetry', 'no_count',
           'count_only', 'hybrid_no_count')
QUANTILES = np.linspace(0.2, 0.8, 5)


def donor_thresholds(main: pd.DataFrame, held_model: str) -> tuple[np.ndarray, np.ndarray]:
    donors = main[main.model_id != held_model]
    if held_model in set(donors.model_id) or donors.model_id.nunique() < 2:
        raise ValueError('Donor-only thresholds require at least two other models')
    return (np.quantile(donors.true_log_latency, QUANTILES),
            np.quantile(donors.true_log_requests_per_second, QUANTILES))


def score_group(group: pd.DataFrame, latency_levels: np.ndarray,
                rps_levels: np.ndarray) -> list[dict]:
    rows = []
    for latency_limit in latency_levels:
        for minimum_rps in rps_levels:
            current_ok = ((group.anchor_log_latency <= latency_limit) &
                          (group.anchor_log_requests_per_second >= minimum_rps))
            gpu2_ok = ((group.true_log_latency <= latency_limit) &
                       (group.true_log_requests_per_second >= minimum_rps))
            at_risk = ~current_ok
            for method in METHODS:
                latency_method = 'baseline' if method == 'hybrid_no_count' else method
                rps_method = 'no_count' if method == 'hybrid_no_count' else method
                scale = (at_risk &
                         (group[f'{latency_method}_latency_log'] <= latency_limit) &
                         (group[f'{rps_method}_rps_log'] >= minimum_rps))
                rows.append({
                    'method': method,
                    'queries': len(group),
                    'at_risk': int(at_risk.sum()),
                    'beneficial_scale': int((at_risk & gpu2_ok).sum()),
                    'scale_approved': int(scale.sum()),
                    'successful_scale': int((scale & gpu2_ok).sum()),
                    'false_scale_approval': int((scale & ~gpu2_ok).sum()),
                    'missed_beneficial_scale': int((at_risk & gpu2_ok & ~scale).sum()),
                })
    return rows


def run(prediction_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(prediction_path)
    predictions = predictions[predictions.model_id != CAUSAL_MODEL].copy()
    if predictions.duplicated(['campaign', 'model_id', 'workload_group']).any():
        raise ValueError('Expected one GPU2/CPU2 row per workload')
    main = predictions[predictions.campaign == MAIN]
    rows = []
    for (campaign, model_id), group in predictions.groupby(['campaign', 'model_id']):
        latency_levels, rps_levels = donor_thresholds(main, model_id)
        for result in score_group(group, latency_levels, rps_levels):
            rows.append({'campaign': campaign, 'model_id': model_id, **result})
    detailed = pd.DataFrame(rows)
    per_model = detailed.groupby(['campaign', 'model_id', 'method'], as_index=False).sum(
        numeric_only=True)
    per_model.to_csv(output / 'per_model_metrics.csv', index=False)
    aggregate = per_model.groupby(['campaign', 'method'], as_index=False).sum(numeric_only=True)
    aggregate.to_csv(output / 'summary.csv', index=False)
    payload = {
        'prediction_sha256': hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'target_definition': '5 by 5 donor-only GPU2 log latency/RPS quantiles, 20%-80%',
        'scope': 'encoder models only; 25 correlated hypothetical constraints per workload',
        'action': 'if measured GPU1 is infeasible, approve GPU2 when predicted feasible',
        'by_campaign': aggregate.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(payload, indent=2) + '\n')
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--predictions', type=Path,
                        default=ROOT / 'artifacts/telemetry_conditioned_scaling_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/telemetry_scaling_decision_v1')
    args = parser.parse_args()
    result = run(args.predictions, args.output)
    for row in result['by_campaign']:
        print(row)
