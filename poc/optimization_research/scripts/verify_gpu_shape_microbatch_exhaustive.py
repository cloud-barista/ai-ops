"""Compare microbatch output with the original assessor on every tracked grid.

This is an exhaustive equivalence check for the pinned, known-model primary
CUDA campaign. It is not an independent performance or VM-shape evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import assess_gpu_shape_microbatch
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from evaluate_slo_reuse import eligible_groups, make_candidates
from train_performance import ROOT


SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
MODEL = ROOT / 'artifacts/performance_v1/performance_model.joblib'
DEFAULT_OUTPUT = ROOT / 'artifacts/cross_workload_microbatch_v1/exhaustive_parity.json'


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def verify(max_grids: int = 8, implementation: str = 'v1') -> dict:
    choices = {'v1': assess_gpu_shape_microbatch,
               'v2': assess_gpu_shape_microbatch_v2}
    if implementation not in choices:
        raise ValueError('unknown_microbatch_implementation')
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    groups = eligible_groups(SOURCE)
    requests = []
    model_ids = set()
    for key in sorted(groups):
        group = groups[key]
        first = group.iloc[0]
        model_ids.add(str(first.model_id))
        requests.append({
            'batch_latency_p95_limit_ms': float(first.latency_slo_ms),
            'min_requests_per_second': float(first.target_throughput_rps),
            'candidates': make_candidates(group),
        })
    if len(requests) != 575 or len(model_ids) != 9:
        raise ValueError('unexpected_exhaustive_workload_count')
    reference = shape.assess_gpu_shape_many(bundle, requests)
    batched = choices[implementation](bundle, requests, max_grids)
    for index, (expected, actual) in enumerate(zip(reference, batched)):
        if _canonical(expected) != _canonical(actual):
            raise AssertionError('result_mismatch_at_workload:' +
                                 sorted(groups)[index])
    if len(reference) != len(batched):
        raise AssertionError('result_count_mismatch')
    return {
        'kind': f'pinned_primary_cuda_exhaustive_microbatch_parity_{implementation}',
        'implementation': implementation,
        'source_sha256': shape.SOURCE_SHA256,
        'model_sha256': shape.MODEL_SHA256,
        'max_grids_per_predict_call': max_grids,
        'workloads_compared': len(requests),
        'models_compared': len(model_ids),
        'candidate_results_compared': len(requests) * 8,
        'status_counts': dict(sorted(Counter(item['status'] for item in batched).items())),
        'canonical_results_sha256': hashlib.sha256(_canonical(batched)).hexdigest(),
        'exact_match': True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-grids', type=int, default=8)
    parser.add_argument('--implementation', choices=('v1', 'v2'), default='v1')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = verify(args.max_grids, args.implementation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
