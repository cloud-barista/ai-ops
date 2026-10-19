"""Reproduce the interpretation of GPU replica measurements from frozen data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = Path('/path/to/aiops/khu_policy_agent/live_collector.py')
CAMPAIGN = 'khu-hf-local-exhaustive'


def run(snapshot: Path, output: Path, collector: Path = COLLECTOR) -> dict:
    data = pd.read_parquet(snapshot)
    measured = data[(data.campaign == CAMPAIGN) & data.eligible &
                    data.device.eq('cuda') & data.replica_count.isin((1, 2))].copy()
    keys = ['workload_group', 'cpu_core_limit']
    if measured.duplicated(keys + ['replica_count']).any():
        raise ValueError('Repeated replica measurement for the same workload/core limit')
    base = measured[measured.replica_count.eq(1)].set_index(keys).sort_index()
    replica = measured[measured.replica_count.eq(2)].set_index(keys).sort_index()
    if len(base) != 2300 or not base.index.equals(replica.index):
        raise ValueError('Expected 2,300 paired main-campaign configurations')
    if not (replica.examples == 2 * base.examples).all():
        raise ValueError('GPU2 did not process exactly twice the GPU1 examples')
    aggregated = []
    for raw in replica.result_json:
        payload = json.loads(raw)
        children = payload.get('replica_results', [])
        if len(children) != 2:
            raise ValueError('Expected two independent GPU replica results')
        aggregated.append(children)
    for index, children in enumerate(aggregated):
        row = replica.iloc[index]
        if sum(int(child['inference_examples']) for child in children) != row.examples:
            raise ValueError('Aggregate examples differ from child sum')
        if not np.isclose(sum(float(child['inference_examples_per_second'])
                              for child in children), row.examples_per_second, atol=1e-5):
            raise ValueError('Aggregate throughput differs from child sum')
        for aggregate_key, child_key in [('latency_p95_ms', 'batch_latency_p95_ms'),
                                         ('load_seconds', 'load_seconds'),
                                         ('compute_seconds', 'compute_seconds'),
                                         ('wall_seconds', 'wall_seconds')]:
            if not np.isclose(float(row[aggregate_key]),
                              max(float(child[child_key]) for child in children),
                              atol=1e-5):
                raise ValueError(f'{aggregate_key} differs from child maximum')
    result = {
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'collector_sha256': hashlib.sha256(collector.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'campaign': CAMPAIGN,
        'paired_configurations': len(base),
        'gpu2_examples_exactly_twice_gpu1': int((replica.examples == 2 * base.examples).sum()),
        'gpu2_aggregate_rps_higher': int((replica.examples_per_second > base.examples_per_second).sum()),
        'gpu2_wall_faster': int((replica.wall_seconds < base.wall_seconds).sum()),
        'gpu2_load_faster': int((replica.load_seconds < base.load_seconds).sum()),
        'gpu2_rps_over_2x_gpu1': int((replica.examples_per_second >
                                     2 * base.examples_per_second).sum()),
        'replica_json_aggregation_checked': len(aggregated),
        'interpretation': 'two independent full-profile replicas; sum rates, maximum latency and times',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--collector', type=Path, default=COLLECTOR)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/replica_semantics_v1/metrics.json')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output, args.collector), indent=2))
