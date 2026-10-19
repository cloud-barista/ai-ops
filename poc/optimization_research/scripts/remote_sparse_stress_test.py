"""Frozen local-to-remote stress test for eight-probe anchor transfer.

Train only on the local exhaustive SQLite snapshot. For each of two unseen
remote models, use the first complete measurement of each GPU resource cell,
then reveal eight one-GPU/two-core anchor outcomes. All reported errors score
the same seven nonanchor cells in each of twelve remote workload conditions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from conditional_response import ANCHOR, prepare
from cross_model_covariance import (residual_covariance, predict_sparse,
                                    training_oof_anchor_residuals)
from sparse_anchor_transfer import (CAMPAIGN, _anchor_rows, fit_predictors,
                                    group_design, predict_fold_methods,
                                    registered_parameter_bytes, select_probes,
                                    static_features, validate_slates)


ROOT = Path(__file__).resolve().parents[1]
REMOTE_CAMPAIGN = 'khu-hf-remote-216'
METHODS = ('zero_shot', 'global_mean', 'ridge_shift', 'sparse_conditional',
           'kernel_transfer', 'full_anchor_reference')


def first_complete_remote_slate(raw: pd.DataFrame,
                                aggregation: str = 'first') -> tuple[pd.DataFrame, dict]:
    """Choose a fixed first row, optionally replacing outcomes by cell medians."""
    if aggregation not in ('first', 'median'):
        raise ValueError('Aggregation must be first or median')
    remote = raw[(raw.campaign == REMOTE_CAMPAIGN) & raw.eligible &
                 (raw.run_status == 'complete') & (raw.device == 'cuda') &
                 raw.replica_count.isin((1, 2)) &
                 raw.cpu_core_limit.isin((2, 4, 8, 16))].copy()
    if remote.empty:
        raise ValueError('No eligible remote GPU measurements')
    remote = remote.sort_values(['started_at', 'execution_id'], kind='stable')
    identity = ['workload_group', 'replica_count', 'cpu_core_limit']
    repeated_rows = len(remote) - len(remote.drop_duplicates(identity))
    slate = remote.drop_duplicates(identity, keep='first').copy()
    if aggregation == 'median':
        # This is a sensitivity estimate, not an actual jointly observed run.
        outcome_columns = ['latency_p95_ms', 'examples_per_second', 'wall_seconds']
        medians = remote.groupby(identity, sort=False)[outcome_columns].median()
        replacement = medians.loc[pd.MultiIndex.from_frame(slate[identity])]
        slate.loc[:, outcome_columns] = replacement.to_numpy()
    validate_slates(slate)
    if slate.host.nunique() != 1 or slate.device_name.nunique() != 1:
        raise ValueError('Remote stress test expects one fixed hardware type')
    if slate.model_id.nunique() < 2:
        raise ValueError('Need at least two independent remote model IDs')
    return slate, {
        'selection': ('eligible, complete, CUDA replica 1/2 and CPU core 2/4/8/16; '
                      'first by started_at then execution_id within each '
                      'workload×resource cell'),
        'outcome_aggregation': aggregation,
        'median_note': ('median latency, throughput and wall time within each cell; '
                        'medians may come from different runs' if aggregation == 'median'
                        else None),
        'pre_dedup_rows': len(remote),
        'repeated_rows_removed': repeated_rows,
        'distinct_cells': len(slate),
        'workload_groups': int(slate.workload_group.nunique()),
        'models': sorted(slate.model_id.unique().tolist()),
        'host': slate.host.iloc[0],
        'gpu': slate.device_name.iloc[0],
    }


def method_errors(predicted: pd.DataFrame, truth: pd.DataFrame) -> list[dict]:
    joined = predicted.merge(
        truth[['execution_id', 'latency_p95_ms', 'examples_per_second',
               'batch_size']], on='execution_id', validate='one_to_one')
    scored = joined[~((joined.replica_count == ANCHOR[0]) &
                      (joined.cpu_core_limit == ANCHOR[1]))].copy()
    if len(scored) != 7 * truth.workload_group.nunique():
        raise ValueError('Expected seven scored nonanchor cells per workload')
    true_latency = np.log(scored.latency_p95_ms.to_numpy(dtype=float))
    true_rps = np.log((scored.examples_per_second / scored.batch_size).to_numpy(dtype=float))
    records = []
    for method in METHODS:
        records.append({
            'method': method,
            'workload_groups': int(scored.workload_group.nunique()),
            'scored_nonanchor_cells': len(scored),
            'latency_male': float(np.abs(
                scored[f'{method}_latency_log'].to_numpy(dtype=float) - true_latency).mean()),
            'rps_male': float(np.abs(
                scored[f'{method}_rps_log'].to_numpy(dtype=float) - true_rps).mean()),
        })
    return records


def run(snapshot: Path, output: Path, seed: int = 42, budget: int = 8,
        aggregation: str = 'first') -> dict:
    if budget <= 0:
        raise ValueError('Probe budget must be positive')
    raw = pd.read_parquet(snapshot)
    main_raw = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
                   (raw.device == 'cuda')].copy()
    validate_slates(main_raw)
    remote, selection = first_complete_remote_slate(raw, aggregation)
    if set(main_raw.model_id) & set(remote.model_id):
        raise ValueError('Remote model IDs must be absent from local training')
    if set(main_raw.workload_group) & set(remote.workload_group):
        raise ValueError('Train and stress-test workload groups overlap')
    train = prepare(main_raw)
    absolute, conditional = fit_predictors(train, seed)
    donor_oof = training_oof_anchor_residuals(train)
    predictions, summaries, manifests = [], [], []
    for model_id in sorted(remote.model_id.unique()):
        held = remote[remote.model_id == model_id].copy()
        size = registered_parameter_bytes(held)
        design = group_design(static_features(held, size))
        if budget >= len(design):
            raise ValueError('Sparse budget must be smaller than remote workload count')
        probes = select_probes(design, budget, 'medoids', seed)
        covariance, prior = residual_covariance(_anchor_rows(held), donor_oof)
        if prior['mode'] != 'kernel_fallback_no_aligned_donors':
            raise ValueError('This stress test is only for the frozen pure-kernel fallback')
        kernel = predict_sparse(held, probes, probes, covariance, absolute,
                                conditional, parameter_bytes=size)
        sparse = predict_fold_methods(held, probes, absolute, conditional)
        full = predict_fold_methods(held, sorted(design.index), absolute, conditional)
        scored = sparse.copy()
        scored['kernel_transfer_latency_log'] = kernel.space_kernel_transfer_latency_log.to_numpy()
        scored['kernel_transfer_rps_log'] = kernel.space_kernel_transfer_rps_log.to_numpy()
        scored['full_anchor_reference_latency_log'] = full.sparse_conditional_latency_log.to_numpy()
        scored['full_anchor_reference_rps_log'] = full.sparse_conditional_rps_log.to_numpy()
        for method in METHODS[:4]:
            for outcome in ('latency', 'rps'):
                scored[f'{method}_{outcome}_log'] = sparse[f'{method}_{outcome}_log'].to_numpy()
        records = method_errors(scored, held)
        anchor_rows = _anchor_rows(held)
        probe_gpu_wall = float((anchor_rows.loc[probes].replica_count *
                                anchor_rows.loc[probes].wall_seconds).sum())
        full_gpu_wall = float((anchor_rows.replica_count *
                               anchor_rows.wall_seconds).sum())
        for record in records:
            record.update({'heldout_model_id': model_id, 'probe_budget': budget,
                           'probe_gpu_wall_seconds': (0.0 if record['method'] ==
                              'zero_shot' else full_gpu_wall if record['method'] ==
                              'full_anchor_reference' else probe_gpu_wall)})
        summaries.extend(records)
        manifests.append({'heldout_model_id': model_id,
                          'probe_groups': probes, 'total_groups': len(design),
                          'prior_mode': prior['mode'],
                          'probe_gpu_wall_seconds': probe_gpu_wall,
                          'full_anchor_gpu_wall_seconds': full_gpu_wall})
        scored['heldout_model_id'] = model_id
        predictions.append(scored)
    output.mkdir(parents=True, exist_ok=True)
    metrics = pd.DataFrame(summaries)
    metrics.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_parquet(
        output / 'predictions.parquet', index=False)
    macro = metrics.groupby('method').agg(
        model_count=('heldout_model_id', 'nunique'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        probe_gpu_wall_seconds=('probe_gpu_wall_seconds', 'mean'),
    ).reset_index()
    report = {
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'dependency_sha256': {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                              for name in ('conditional_response.py',
                                           'sparse_anchor_transfer.py',
                                           'cross_model_covariance.py',
                                           'probe_design.py')},
        'sklearn_version': sklearn.__version__,
        'seed': seed, 'budget': budget,
        'remote_selection': selection,
        'local_training_models': sorted(main_raw.model_id.unique().tolist()),
        'training_rows': len(train),
        'scored_population': 'all seven nonanchor GPU choices for each of 12 workloads per remote model',
        'primary_outcomes': ('MALE of batch p95 latency and examples/sec divided by batch size; '
                             'SLO is omitted because collection profiles hard-code it'),
        'transfer_caveat': ('Remote model IDs, GPU hardware, host, and causal-LM input '
                            'conditions shift together; this is a retrospective stress test '
                            'and cannot identify which shift caused an error'),
        'manifest': manifests,
        'per_model': summaries,
        'macro_two_models': macro.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/remote_sparse_stress_v1')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--budget', type=int, default=8)
    parser.add_argument('--aggregation', choices=('first', 'median'), default='first')
    args = parser.parse_args()
    result = run(args.snapshot, args.output, args.seed, args.budget,
                 args.aggregation)
    print(json.dumps(result['macro_two_models'], indent=2))
