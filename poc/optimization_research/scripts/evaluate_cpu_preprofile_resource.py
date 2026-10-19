"""Donor-only synthetic SLO audit for CPU preprofiling and GPU probe controls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cpu_preprofile_transfer import METHODS, ROOT
from evaluate_counterfactual_slo import training_threshold_grid
from evaluate_resource_regret import score_threshold
from sparse_anchor_transfer import CAMPAIGN, validate_slates


def partial_workload_groups(frame: pd.DataFrame,
                            threshold: dict) -> set[str]:
    feasible = ((frame.latency_p95_ms <= threshold['latency_slo_ms']) &
                (frame.examples_per_second / frame.batch_size >=
                 threshold['target_rps']))
    counts = feasible.groupby(frame.workload_group).sum()
    return set(counts[(counts > 0) & (counts < 8)].index)


def run(snapshot: Path, predictions: Path, manifest: Path,
        output: Path) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible & raw.device.eq('cuda')]
    validate_slates(main)
    pred = pd.read_parquet(predictions)
    detail = pd.read_csv(manifest)
    held_models = set(detail.heldout_model_id)
    main = main[main.model_id.isin(held_models)]
    if pred.execution_id.duplicated().any() or len(pred) != len(main):
        raise ValueError('Expected one prediction per eight-encoder GPU cell')
    truth = main[['execution_id', 'model_id', 'workload_group',
                  'replica_count', 'cpu_core_limit', 'latency_p95_ms',
                  'examples_per_second', 'batch_size']]
    frame = pred.merge(truth, on=['execution_id', 'model_id',
                                  'workload_group', 'replica_count',
                                  'cpu_core_limit'], validate='one_to_one')
    rows = []
    for model_id in sorted(held_models):
        train = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
                    raw.device.eq('cuda') & raw.model_id.ne(model_id)]
        held = frame[frame.model_id == model_id]
        cpu_groups = set(raw[(raw.campaign == CAMPAIGN) & raw.eligible &
                             raw.device.eq('cpu') &
                             raw.cpu_core_limit.eq(2) &
                             raw.model_id.eq(model_id)].workload_group)
        gpu4_groups = set(json.loads(detail.loc[
            detail.heldout_model_id == model_id,
            'gpu4_groups_json'].iloc[0]))
        if len(cpu_groups) != 8 or len(gpu4_groups) != 4 or not gpu4_groups <= cpu_groups:
            raise ValueError('Incorrect CPU8/GPU4 profile manifest')
        unseen = held[~held.workload_group.isin(cpu_groups)]
        for threshold in training_threshold_grid(train):
            for population, sample in (('all_workloads', held),
                                       ('unprofiled_workloads', unseen)):
                partial = partial_workload_groups(sample, threshold)
                for case, case_frame in (('all_cases', sample),
                                         ('partial_oracle', sample[
                                             sample.workload_group.isin(partial)])):
                    if case_frame.empty:
                        continue
                    rows.extend({
                        'heldout_model_id': model_id,
                        'population': population,
                        'oracle_case': case,
                        'latency_quantile': threshold['latency_quantile'],
                        'rps_quantile': threshold['rps_quantile'],
                        **result,
                    } for result in score_threshold(case_frame, threshold,
                                                     methods=METHODS))
    scenario = pd.DataFrame(rows)
    additive = ['workload_queries', 'oracle_feasible',
                'nontrivial_oracle_feasible', 'oracle_gpu1_feasible',
                'selected', 'successful_choices', 'false_approvals',
                'missed_feasible', 'oracle_cost_matches',
                'successful_but_extra_gpu',
                'successful_but_extra_core_same_gpu',
                'extra_gpu_units_on_success',
                'extra_cpu_cores_same_gpu_on_success',
                'avoidable_gpu2_recommendations',
                'avoidable_gpu2_successes', 'nontrivial_successes',
                'nontrivial_oracle_cost_matches']
    summary = scenario.groupby(['population', 'oracle_case', 'method'],
                               as_index=False)[additive].sum()
    for _, group in summary.groupby(['population', 'oracle_case']):
        if any(group[col].nunique() != 1 for col in
               ['workload_queries', 'oracle_feasible', 'oracle_gpu1_feasible']):
            raise AssertionError('Methods have unequal query denominators')
    output.mkdir(parents=True, exist_ok=True)
    scenario.to_csv(output / 'per_model_slo_scenario.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    result = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'retrospective 8 encoder models; donor-only 5x5 batch-SLO thresholds; correlated hypothetical queries',
        'method_probe_costs': {
            'zero_shot': {'cpu_wall_seconds': 0, 'gpu_wall_seconds': 0},
            'cpu8_transfer': {'cpu_wall_seconds_per_model_mean':
                              float(detail.cpu_wall_seconds.mean()),
                              'gpu_wall_seconds': 0},
            'gpu4_covariance': {'cpu_wall_seconds': 0,
                                'gpu_wall_seconds_per_model_mean':
                                float(detail.gpu4_wall_seconds.mean())},
            'gpu4_cpu8_fusion': {'cpu_wall_seconds_per_model_mean':
                                  float(detail.cpu_wall_seconds.mean()),
                                  'gpu_wall_seconds_per_model_mean':
                                  float(detail.gpu4_wall_seconds.mean())},
            'gpu8_kernel_and_covariance': {'cpu_wall_seconds': 0,
                                           'gpu_wall_seconds_per_model_mean':
                                           float(detail.gpu_wall_seconds.mean())},
        },
        'resource_order': 'one GPU before two GPUs, then fewer CPU cores; this is not money',
        'populations': {'all_workloads': '64 input conditions per held model',
                        'unprofiled_workloads': '56 conditions per held model, excluding all eight CPU/GPU paired probe conditions'},
        'oracle_cases': {'all_cases': 'no feasible, all feasible, and partial slates',
                         'partial_oracle': 'one through seven of eight resource cells feasible'},
        'aggregate': summary.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--predictions', type=Path,
                        default=ROOT / 'artifacts/cpu_preprofile_transfer_v1/predictions.parquet')
    parser.add_argument('--manifest', type=Path,
                        default=ROOT / 'artifacts/cpu_preprofile_transfer_v1/split_manifest.csv')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cpu_preprofile_resource_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.predictions, args.manifest, args.output)
    for record in result['aggregate']:
        if record['population'] == 'unprofiled_workloads' and record['oracle_case'] == 'partial_oracle':
            print(record)
