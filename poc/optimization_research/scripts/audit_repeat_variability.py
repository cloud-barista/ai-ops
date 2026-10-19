"""Audit within-version repeated GPU measurement variability.

The 47 complete v2 live rounds provide repeated observations of 16 fixed
GPU resource cells. We keep collector versions separate and compare a later
same-version matrix campaign only on exactly matching static configurations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
VERSION = 'khu-hf-direct-collector/v2'
KEY = ('model_id', 'workload_task', 'batch_size', 'max_length',
       'request_concurrency', 'repeats', 'replica_count', 'cpu_core_limit')
PAIR_KEY = KEY[:-2]


def live_cells(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    live = frame[(frame.campaign == 'khu-hf-live') &
                 (frame.collector_version == VERSION) &
                 frame.eligible & (frame.device == 'cuda') &
                 (frame.cpu_core_limit == 2)].copy()
    if live.empty or live.duplicated([*KEY, 'run_id']).any():
        raise ValueError('Missing or duplicate live v2 measurements')
    live['round_index'] = live.run_id.str.extract(r'round-(\d+)$')[0].astype(int)
    rounds = sorted(live.round_index.unique())
    if len(rounds) != 47:
        raise ValueError('Expected 47 v2 rounds')
    results = []
    for key, group in live.groupby(list(KEY), sort=True):
        group = group.sort_values('round_index')
        if (len(group) != 47 or group.round_index.nunique() != 47 or
                sorted(group.round_index) != rounds):
            raise ValueError('Incomplete live v2 resource cell')
        row = dict(zip(KEY, key))
        for name, column in [('latency', 'latency_p95_ms'),
                             ('rps', 'examples_per_second')]:
            values = group[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values <= 0).any():
                raise ValueError('Nonpositive or nonfinite outcome')
            logs = np.log(values)
            row[f'{name}_log_sd'] = float(np.std(logs, ddof=1))
            row[f'{name}_median_abs_log_deviation'] = float(
                np.median(np.abs(logs - np.median(logs))))
            row[f'{name}_early_late_log_median_shift'] = float(
                np.median(logs[27:]) - np.median(logs[:27]))
            row[f'{name}_median'] = float(np.median(values))
        results.append(row)
    cells = pd.DataFrame(results)
    if len(cells) != 16:
        raise ValueError('Expected 16 repeated GPU cells')
    return live, cells


def paired_scaling(live: pd.DataFrame) -> pd.DataFrame:
    out = []
    for key, group in live.groupby(list(PAIR_KEY), sort=True):
        one = group[group.replica_count == 1]
        two = group[group.replica_count == 2]
        if one.empty or two.empty:
            continue
        paired = one.merge(two, on=[*PAIR_KEY, 'run_id'],
                           suffixes=('_one', '_two'), validate='one_to_one')
        if len(paired) != 47:
            raise ValueError('Incomplete same-round GPU pair')
        row = dict(zip(PAIR_KEY, key))
        row['rounds'] = len(paired)
        for name, column in [('latency', 'latency_p95_ms'),
                             ('rps', 'examples_per_second')]:
            first = np.log(paired[f'{column}_one'].to_numpy(dtype=float))
            second = np.log(paired[f'{column}_two'].to_numpy(dtype=float))
            paired_var = float(np.var(second - first, ddof=1))
            independent_var = float(np.var(first, ddof=1) +
                                    np.var(second, ddof=1))
            row[f'{name}_same_round_correlation'] = float(
                np.corrcoef(first, second)[0, 1])
            row[f'{name}_pairing_variance_ratio'] = float(
                independent_var / paired_var)
        out.append(row)
    return pd.DataFrame(out)


def later_campaign(frame: pd.DataFrame, cells: pd.DataFrame) -> pd.DataFrame:
    later = frame[(frame.campaign == 'khu-hf-diverse-cpu-matrix') &
                  (frame.collector_version == VERSION) &
                  frame.eligible & (frame.device == 'cuda') &
                  (frame.cpu_core_limit == 2)]
    later = later.groupby(list(KEY), as_index=False).agg(
        later_repetitions=('execution_id', 'size'),
        later_latency_median=('latency_p95_ms', 'median'),
        later_rps_median=('examples_per_second', 'median'),
    )
    merged = cells.merge(later, on=list(KEY), how='inner',
                         validate='one_to_one')
    if len(merged) != 16 or (merged.later_repetitions != 2).any():
        raise ValueError('Expected two later measurements for each live GPU cell')
    for target in ('latency', 'rps'):
        merged[f'{target}_later_vs_live_abs_log'] = np.abs(np.log(
            merged[f'later_{target}_median'] / merged[f'{target}_median']))
    return merged


def run(snapshot: Path, output: Path) -> dict:
    frame = pd.read_parquet(snapshot)
    live, cells = live_cells(frame)
    pairs = paired_scaling(live)
    later = later_campaign(frame, cells)
    output.mkdir(parents=True, exist_ok=True)
    cells.to_csv(output / 'per_cell.csv', index=False)
    pairs.to_csv(output / 'paired_scaling.csv', index=False)
    later.to_csv(output / 'later_campaign_overlap.csv', index=False)
    if len(pairs) != 6:
        raise ValueError('Expected six GPU1/GPU2 paired workload conditions')
    report = {
        'scope': 'same-version repeated GPU benchmark uncertainty; not online SLO',
        'collector_version': VERSION,
        'rounds': int(live.run_id.nunique()),
        'resource_cells': len(cells),
        'paired_workloads': len(pairs),
        'later_exact_cells': len(later),
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'cell_median_log_sd': {
            target: float(cells[f'{target}_log_sd'].median())
            for target in ('latency', 'rps')},
        'cell_p90_log_sd': {
            target: float(cells[f'{target}_log_sd'].quantile(0.9))
            for target in ('latency', 'rps')},
        'early_late_median_abs_log_shift': {
            target: float(cells[f'{target}_early_late_log_median_shift'].abs().median())
            for target in ('latency', 'rps')},
        'paired_variance_ratio_median': {
            target: float(pairs[f'{target}_pairing_variance_ratio'].median())
            for target in ('latency', 'rps')},
        'paired_variance_ratio_range': {
            target: [float(pairs[f'{target}_pairing_variance_ratio'].min()),
                     float(pairs[f'{target}_pairing_variance_ratio'].max())]
            for target in ('latency', 'rps')},
        'later_campaign_median_abs_log_difference': {
            target: float(later[f'{target}_later_vs_live_abs_log'].median())
            for target in ('latency', 'rps')},
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/repeat_variability_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output), indent=2))
