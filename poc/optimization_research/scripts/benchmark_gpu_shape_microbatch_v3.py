"""Paired exact-output benchmark for call-scoped GPU-grid validation reuse."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v2 import assess_gpu_shape_microbatch_v2
from assess_gpu_shape_microbatch_v3 import assess_gpu_shape_microbatch_v3
from evaluate_cross_workload_microbatch import MODEL, SOURCE, MANIFEST, verify_frozen_selection
from evaluate_slo_reuse import eligible_groups, make_candidates, make_requests
from train_performance import ROOT


PROTOCOL = ROOT / 'reports/decision_inference_next_optimization_audit.md'
DEFAULT_OUTPUT = ROOT / 'artifacts/gpu_shape_microbatch_v3/results.json'
EXPECTED_V2_SHA256 = '20e73cdcf39dbe7252399a3bff8eda9387ff592d046ea6483df15b13710e996d'
EXPECTED_PREDICTOR_SHA256 = '46c85961fb4d9388c99bc073522f8cf9c839065a1f4fe67a1b92aa33f3123075'
MAX_GRIDS = 8
REPETITIONS = 7
SCENARIOS = ('shared_slo', 'unique_grids', 'copied_slo')
FUNCTIONS = {
    'v2': assess_gpu_shape_microbatch_v2,
    'v3': assess_gpu_shape_microbatch_v3,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def verify_inputs(expected_assessor_sha256: str) -> dict:
    actual = sha256(ROOT / 'scripts/assess_gpu_shape.py')
    if actual != expected_assessor_sha256:
        raise ValueError('assessor_code_sha256_mismatch')
    if sha256(ROOT / 'scripts/assess_gpu_shape_microbatch_v2.py') != EXPECTED_V2_SHA256:
        raise ValueError('v2_code_sha256_mismatch')
    if sha256(ROOT / 'scripts/predict_performance.py') != EXPECTED_PREDICTOR_SHA256:
        raise ValueError('predictor_code_sha256_mismatch')
    manifest = json.loads(MANIFEST.read_text())
    verify_frozen_selection(manifest)
    if sha256(MODEL) != shape.MODEL_SHA256 or sha256(SOURCE) != shape.SOURCE_SHA256:
        raise ValueError('model_or_source_sha256_mismatch')
    return manifest


def request_blocks(scenario: str, manifest: dict) -> list[list[dict]]:
    groups = eligible_groups(SOURCE)
    if scenario in ('shared_slo', 'copied_slo'):
        blocks = [make_requests(groups[item['workload_group']], item)
                  for item in manifest['groups']]
        if len(blocks) != 45 or any(len(block) != 25 for block in blocks):
            raise ValueError('unexpected_frozen_request_count')
        if scenario == 'copied_slo':
            blocks = [[{**request, 'candidates': copy.deepcopy(request['candidates'])}
                       for request in block] for block in blocks]
        return blocks
    if scenario == 'unique_grids':
        blocks = []
        model_ids = set()
        for key in sorted(groups):
            group = groups[key]
            first = group.iloc[0]
            model_ids.add(str(first.model_id))
            blocks.append([{
                'batch_latency_p95_limit_ms': float(first.latency_slo_ms),
                'min_requests_per_second': float(first.target_throughput_rps),
                'candidates': make_candidates(group),
            }])
        if len(blocks) != 575 or len(model_ids) != 9:
            raise ValueError('unexpected_exhaustive_workload_count')
        return blocks
    raise ValueError('unknown_scenario')


def ordered_requests(blocks: list[list[dict]], repetition: int | None) -> list[dict]:
    order = list(range(len(blocks)))
    if repetition is not None:
        random.Random(42 + repetition).shuffle(order)
    return [request for index in order for request in blocks[index]]


def memory_worker(scenario: str, mode: str, expected_assessor_sha256: str) -> dict:
    manifest = verify_inputs(expected_assessor_sha256)
    blocks = request_blocks(scenario, manifest)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    active = ordered_requests(blocks, None)
    result = FUNCTIONS[mode](bundle, active, MAX_GRIDS)
    return {'scenario': scenario, 'mode': mode, 'outputs': len(result),
            'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)}


def run_memory_worker(scenario: str, mode: str,
                      expected_assessor_sha256: str) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()),
               '--memory-worker', scenario, '--mode', mode,
               '--assessor-sha256', expected_assessor_sha256]
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def benchmark_scenario(scenario: str, manifest: dict, bundle: dict) -> dict:
    blocks = request_blocks(scenario, manifest)
    active = ordered_requests(blocks, None)
    unique_candidate_objects = len({id(item['candidates']) for item in active})
    initial = {}
    predict_calls = {}
    for name, function in FUNCTIONS.items():
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predicted:
            initial[name] = function(bundle, active, MAX_GRIDS)
        predict_calls[name] = predicted.call_count
    if canonical(initial['v2']) != canonical(initial['v3']):
        raise AssertionError('initial_full_json_mismatch:' + scenario)
    if predict_calls['v2'] != predict_calls['v3']:
        raise AssertionError('predict_call_count_mismatch:' + scenario)
    for function in FUNCTIONS.values():
        function(bundle, active, MAX_GRIDS)  # warm the full path outside timing

    timings = {name: {'wall_ms': [], 'cpu_ms': []} for name in FUNCTIONS}
    pair_ratios = []
    for repetition in range(REPETITIONS):
        active = ordered_requests(blocks, repetition)
        order = list(FUNCTIONS)
        random.Random(2042 + repetition).shuffle(order)
        outputs = {}
        this_wall = {}
        for name in order:
            wall_start = time.perf_counter_ns()
            cpu_start = time.process_time_ns()
            outputs[name] = FUNCTIONS[name](bundle, active, MAX_GRIDS)
            cpu_ms = (time.process_time_ns() - cpu_start) / 1e6
            wall_ms = (time.perf_counter_ns() - wall_start) / 1e6
            timings[name]['wall_ms'].append(round(wall_ms, 3))
            timings[name]['cpu_ms'].append(round(cpu_ms, 3))
            this_wall[name] = wall_ms
        if canonical(outputs['v2']) != canonical(outputs['v3']):
            raise AssertionError('shuffled_full_json_mismatch:' + scenario)
        pair_ratios.append(this_wall['v2'] / this_wall['v3'])

    baseline_ms = statistics.median(timings['v2']['wall_ms'])
    candidate_ms = statistics.median(timings['v3']['wall_ms'])
    return {
        'scenario': scenario,
        'requests': len(active),
        'blocks': len(blocks),
        'unique_candidate_list_objects': unique_candidate_objects,
        'predict_calls': predict_calls,
        'status_counts': dict(sorted(Counter(row['status'] for row in initial['v3']).items())),
        'canonical_results_sha256': hashlib.sha256(canonical(initial['v3'])).hexdigest(),
        'exact_full_json_all_pairs': True,
        'timing_ms': timings,
        'median_wall_ms': {'v2': round(baseline_ms, 3), 'v3': round(candidate_ms, 3)},
        'median_pair_speedup_v2_over_v3': round(statistics.median(pair_ratios), 4),
        'pair_speedups_v2_over_v3': [round(value, 4) for value in pair_ratios],
    }


def benchmark(expected_assessor_sha256: str) -> dict:
    manifest = verify_inputs(expected_assessor_sha256)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    scenarios = {name: benchmark_scenario(name, manifest, bundle)
                 for name in SCENARIOS}
    memory = {name: {mode: run_memory_worker(name, mode, expected_assessor_sha256)
                     for mode in FUNCTIONS}
              for name in SCENARIOS}
    gates = {
        'exact_full_json_and_predict_calls': all(
            item['exact_full_json_all_pairs']
            and item['predict_calls']['v2'] == item['predict_calls']['v3']
            for item in scenarios.values()),
        'shared_slo_speedup_at_least_1_20x':
            scenarios['shared_slo']['median_pair_speedup_v2_over_v3'] >= 1.20,
        'unique_grids_slowdown_at_most_10pct':
            scenarios['unique_grids']['median_pair_speedup_v2_over_v3'] >= 1 / 1.10,
        'copied_slo_slowdown_at_most_10pct':
            scenarios['copied_slo']['median_pair_speedup_v2_over_v3'] >= 1 / 1.10,
        'peak_rss_increase_at_most_20_mib': all(
            memory[name]['v3']['peak_rss_mib']
            - memory[name]['v2']['peak_rss_mib'] <= 20
            for name in SCENARIOS),
    }
    return {
        'kind': 'gpu_shape_microbatch_v3_candidate_validation_reuse',
        'max_grids': MAX_GRIDS,
        'repetitions': REPETITIONS,
        'scenarios': scenarios,
        'memory': memory,
        'gates': gates,
        'passes_all_gates': all(gates.values()),
        'provenance': {
            'protocol_sha256': sha256(PROTOCOL),
            'assessor_code_sha256': expected_assessor_sha256,
            'v2_code_sha256': EXPECTED_V2_SHA256,
            'predictor_code_sha256': EXPECTED_PREDICTOR_SHA256,
            'v3_code_sha256': sha256(ROOT / 'scripts/assess_gpu_shape_microbatch_v3.py'),
            'benchmark_code_sha256': sha256(Path(__file__).resolve()),
            'manifest_sha256': sha256(MANIFEST),
            'model_sha256': shape.MODEL_SHA256,
            'source_sha256': shape.SOURCE_SHA256,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assessor-sha256', required=True,
                        help='sha256 of the finished, pinned assessor code')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--memory-worker', choices=SCENARIOS)
    parser.add_argument('--mode', choices=tuple(FUNCTIONS))
    args = parser.parse_args()
    if args.memory_worker:
        if args.mode is None:
            parser.error('--mode is required with --memory-worker')
        print(json.dumps(memory_worker(args.memory_worker, args.mode,
                                       args.assessor_sha256), sort_keys=True))
        return
    result = benchmark(args.assessor_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output), 'passes_all_gates':
                      result['passes_all_gates'], 'gates': result['gates']},
                     sort_keys=True))


if __name__ == '__main__':
    main()
