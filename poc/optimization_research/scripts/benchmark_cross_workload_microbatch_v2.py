"""Paired benchmark of one-pass validation against exact microbatch v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from collections import Counter
from pathlib import Path

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import assess_gpu_shape_microbatch
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from evaluate_cross_workload_microbatch import (MANIFEST, MODEL, SOURCE,
                                                verify_frozen_selection)
from evaluate_slo_reuse import eligible_groups, make_requests
from train_performance import ROOT


PROTOCOL = ROOT / 'reports/cross_workload_microbatch_v2_protocol.md'
DEFAULT_OUTPUT = ROOT / 'artifacts/cross_workload_microbatch_v2/results.json'
BATCH_SIZE = 8
REPETITIONS = 5


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def benchmark() -> dict:
    manifest = json.loads(MANIFEST.read_text())
    verify_frozen_selection(manifest)
    groups = eligible_groups(SOURCE)
    request_groups = [make_requests(groups[item['workload_group']], item)
                      for item in manifest['groups']]
    if len(request_groups) != 45 or any(len(group) != 25 for group in request_groups):
        raise ValueError('unexpected_frozen_request_count')
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    functions = {
        'v1': assess_gpu_shape_microbatch,
        'v2': assess_gpu_shape_microbatch_v2,
    }
    requests = [item for group in request_groups for item in group]
    first = functions['v1'](bundle, requests, BATCH_SIZE)
    second = functions['v2'](bundle, requests, BATCH_SIZE)
    if canonical(first) != canonical(second):
        raise AssertionError('initial_full_json_mismatch')

    # Both paths have completed one identical warm call before timing.
    timings = {name: [] for name in functions}
    for repetition in range(REPETITIONS):
        group_order = list(range(len(request_groups)))
        random.Random(42 + repetition).shuffle(group_order)
        active = [item for index in group_order for item in request_groups[index]]
        mode_order = list(functions)
        random.Random(2042 + repetition).shuffle(mode_order)
        outputs = {}
        for name in mode_order:
            start = time.perf_counter_ns()
            outputs[name] = functions[name](bundle, active, BATCH_SIZE)
            timings[name].append((time.perf_counter_ns() - start) / 1e6)
        if canonical(outputs['v1']) != canonical(outputs['v2']):
            raise AssertionError('shuffled_full_json_mismatch')

    medians = {name: statistics.median(values)
               for name, values in timings.items()}
    speedup = medians['v1'] / medians['v2']
    return {
        'kind': 'cross_workload_one_pass_microbatch_v2_exploratory',
        'batch_size': BATCH_SIZE,
        'workload_grids': len(request_groups),
        'synthetic_slo_requests': len(requests),
        'repetitions': REPETITIONS,
        'timing_ms': {name: [round(value, 3) for value in values]
                      for name, values in timings.items()},
        'median_ms': {name: round(value, 3) for name, value in medians.items()},
        'median_speedup_v1_over_v2': round(speedup, 3),
        'gates': {
            'exact_full_json': True,
            'speedup_at_least_1_20x': speedup >= 1.20,
        },
        'passes_all_gates': speedup >= 1.20,
        'status_counts': dict(sorted(Counter(item['status'] for item in second).items())),
        'canonical_results_sha256': hashlib.sha256(canonical(second)).hexdigest(),
        'provenance': {
            'protocol_sha256': sha256(PROTOCOL),
            'manifest_sha256': sha256(MANIFEST),
            'source_sha256': shape.SOURCE_SHA256,
            'model_sha256': shape.MODEL_SHA256,
            'v1_code_sha256': sha256(ROOT / 'scripts/assess_gpu_shape_microbatch.py'),
            'v2_code_sha256': sha256(ROOT / 'scripts/assess_gpu_shape_microbatch_v2.py'),
            'assessor_code_sha256': sha256(ROOT / 'scripts/assess_gpu_shape.py'),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output),
                      'median_speedup_v1_over_v2': result['median_speedup_v1_over_v2'],
                      'passes_all_gates': result['passes_all_gates']}, sort_keys=True))


if __name__ == '__main__':
    main()
