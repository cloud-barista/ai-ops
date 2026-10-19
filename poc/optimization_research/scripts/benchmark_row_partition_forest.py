"""Frozen full-JSON, paired-time and isolated-RSS ordered-tree study."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v6 import assess_gpu_shape_microbatch_v6
from assess_gpu_shape_row_partition import assess_gpu_shape_row_partition
import row_partition_forest_predict as ordered
from benchmark_gpu_shape_microbatch_v4 import (MODEL, SOURCE, SCENARIOS, canonical,
    ordered_requests, request_blocks, verify_frozen_inputs)
from train_performance import ROOT

PROTOCOL = ROOT / 'reports/decision_inference_row_partition_protocol_v1.json'
OUT = ROOT / 'artifacts/row_partition_forest_v1'
MODES = ['v6', 'row2', 'row4']
REPETITIONS = 9


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_protocol():
    protocol = json.loads(PROTOCOL.read_text())
    for name, expected in protocol['hashes'].items():
        path = Path(name) if name.startswith('/') else ROOT / name
        if sha(path) != expected:
            raise ValueError('frozen_sha256_changed:' + name)
    for name in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
        if os.environ.get(name) != '1':
            raise ValueError('benchmark_requires_one_native_thread:' + name)
    return protocol


def assess(mode, bundle, requests):
    if mode == 'v6':
        return assess_gpu_shape_microbatch_v6(bundle, requests)
    return assess_gpu_shape_row_partition(bundle, requests, int(mode[-1]))


def verify_boundaries(bundle, blocks):
    requests = []
    for block in blocks:
        q = block[0]
        reference = assess('v6', bundle, [q])[0]
        for candidate in reference.get('candidate_assessments', []):
            if 'predicted_batch_latency_p95_ms' not in candidate:
                continue
            ms = candidate['predicted_batch_latency_p95_ms']
            rps = candidate['predicted_requests_per_second']
            for direction in [-np.inf, None, np.inf]:
                limit = ms if direction is None else float(np.nextafter(ms, direction))
                minimum = rps if direction is None else float(np.nextafter(rps, direction))
                requests.append({**q, 'batch_latency_p95_limit_ms': limit, 'min_requests_per_second': minimum})
    expected = canonical(assess('v6', bundle, requests))
    for mode in MODES[1:]:
        if canonical(assess(mode, bundle, requests)) != expected:
            raise AssertionError('one_ulp_SLO_boundary_JSON_difference:' + mode)
    return {'boundary_queries': len(requests), 'modes_exact_at_one_ulp_SLO_boundaries': True}


def verify_retry(bundle, requests):
    expected = canonical(assess('v6', bundle, requests))
    original = ordered.RowPartitionForestPredictor.predict_records
    for kind in ['exception', 'bad_count']:
        def fail(engine, active, candidates):
            if len(candidates) > 8:
                if kind == 'exception':
                    raise MemoryError('injected combined batch limit')
                return []
            return original(engine, active, candidates)
        with patch.object(ordered.RowPartitionForestPredictor, 'predict_records', new=fail):
            if canonical(assess('row4', bundle, requests)) != expected:
                raise AssertionError('per_grid_retry_output_difference')
    return {'combined_exception_retry_exact': True, 'malformed_count_retry_exact': True}


def run_scenario(scenario, manifest, bundle):
    blocks = request_blocks(scenario, manifest)
    active = ordered_requests(blocks, None)
    initial = {m: assess(m, bundle, active) for m in MODES}
    expected = canonical(initial['v6'])
    if any(canonical(initial[m]) != expected for m in MODES):
        raise AssertionError('initial_full_JSON_difference:' + scenario)
    # Above includes a complete warm call for every mode, outside the timer.
    times = {m: {'wall_ms': [], 'cpu_ms': []} for m in MODES}
    ratios = {m: [] for m in MODES[1:]}
    orders = []
    original_jobs = bundle['model'].named_steps['regressor'].n_jobs
    for rep in range(REPETITIONS):
        requests = ordered_requests(blocks, rep)
        order = MODES[rep % len(MODES):] + MODES[:rep % len(MODES)]
        if rep % 2:
            order = order[::-1]
        orders.append(order)
        outputs, walls = {}, {}
        for mode in order:
            start, cpu = time.perf_counter_ns(), time.process_time_ns()
            outputs[mode] = assess(mode, bundle, requests)
            walls[mode] = (time.perf_counter_ns() - start) / 1e6
            times[mode]['wall_ms'].append(walls[mode])
            times[mode]['cpu_ms'].append((time.process_time_ns() - cpu) / 1e6)
        if any(canonical(outputs[m]) != canonical(outputs['v6']) for m in MODES[1:]):
            raise AssertionError(f'paired_full_JSON_difference:{scenario}/{rep}')
        for mode in MODES[1:]:
            ratios[mode].append(walls['v6'] / walls[mode])
    if bundle['model'].named_steps['regressor'].n_jobs != original_jobs or 'predict' in bundle['model'].named_steps['regressor'].__dict__:
        raise AssertionError('shared_model_mutated')
    result = {'requests': len(active), 'grids': len(blocks), 'all_JSON_exact': True,
              'canonical_sha256': hashlib.sha256(expected).hexdigest(), 'orders': orders,
              'timings': times, 'pair_speedups': ratios,
              'median_wall_ms': {m: statistics.median(t['wall_ms']) for m, t in times.items()},
              'median_pair_speedup': {m: statistics.median(values) for m, values in ratios.items()},
              'caller_worker_configuration_unchanged': True}
    print(json.dumps({'scenario_completed': scenario, 'speedups': result['median_pair_speedup']}), flush=True)
    return result


def memory_worker(scenario, mode):
    verify_protocol()
    manifest = verify_frozen_inputs()
    requests = ordered_requests(request_blocks(scenario, manifest), None)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    output = assess(mode, bundle, requests)
    return {'scenario': scenario, 'mode': mode, 'outputs': len(output),
            'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-worker', choices=SCENARIOS)
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    if args.memory_worker:
        print(json.dumps(memory_worker(args.memory_worker, args.mode)))
        return
    if args.output.exists():
        raise ValueError('output_exists_refusing_overwrite')
    protocol = verify_protocol()
    manifest = verify_frozen_inputs()
    before = {'model': sha(MODEL), 'source': sha(SOURCE)}
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    print(json.dumps({'started': True, 'protocol_sha256': sha(PROTOCOL)}), flush=True)
    scenarios = {name: run_scenario(name, manifest, bundle) for name in SCENARIOS}
    boundary = verify_boundaries(bundle, request_blocks('shared_slo', manifest))
    retry = verify_retry(bundle, ordered_requests(request_blocks('unique_grids', manifest), None))
    memory = {}
    for name in SCENARIOS:
        memory[name] = {}
        for mode in MODES:
            result = subprocess.run([sys.executable, str(Path(__file__)), '--memory-worker', name,
                                     '--mode', mode], check=True, capture_output=True, text=True)
            memory[name][mode] = json.loads(result.stdout)
        print(json.dumps({'memory_completed': name}), flush=True)
    gates = {}
    for mode in MODES[1:]:
        checks = {'full_JSON_exact_all_scenarios': all(s['all_JSON_exact'] for s in scenarios.values()),
                  'unique_speedup_at_least_1_10': scenarios['unique_grids']['median_pair_speedup'][mode] >= 1.10,
                  'shared_slowdown_at_most10pct': scenarios['shared_slo']['median_pair_speedup'][mode] >= 1 / 1.10,
                  'copied_slowdown_at_most10pct': scenarios['copied_slo']['median_pair_speedup'][mode] >= 1 / 1.10,
                  'extra_peak_RSS_at_most20MiB': all(memory[n][mode]['peak_rss_mib'] - memory[n]['v6']['peak_rss_mib'] <= 20 for n in SCENARIOS),
                  'boundary_SLO_and_retry_exact': all(boundary[k] for k in boundary if type(boundary[k]) is bool) and all(retry.values())}
        gates[mode] = {'status': 'GO' if all(checks.values()) else 'NO_GO', 'checks': checks}
    after = {'model': sha(MODEL), 'source': sha(SOURCE)}
    if before != after:
        raise ValueError('model_or_source_changed')
    result = {'protocol': protocol, 'repetitions': REPETITIONS, 'scenarios': scenarios, 'memory': memory,
              'gates': gates, 'boundary_tests': boundary, 'retry_tests': retry,
              'provenance': {**before, 'protocol_sha256': sha(PROTOCOL), 'benchmark_sha256': sha(Path(__file__)),
                             'algorithm_sha256': sha(ROOT / 'scripts/row_partition_forest_predict.py'),
                             'assessor_sha256': sha(ROOT / 'scripts/assess_gpu_shape_row_partition.py')},
              'model_and_source_unchanged': True,
              'scope': 'same-host warm offline CPU shape decision; loading/network/app inference/deployment excluded'}
    args.output.mkdir(parents=True)
    (args.output / 'results.json').write_text(json.dumps(result, indent=2)+'\n')
    (args.output / 'PROTOCOL.json').write_text(json.dumps(protocol, indent=2)+'\n')
    print(json.dumps({'complete': True, 'gates': gates}), flush=True)


if __name__ == '__main__':
    main()
