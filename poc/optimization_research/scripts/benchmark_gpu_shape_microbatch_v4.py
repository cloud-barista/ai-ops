"""Frozen, paired exact-output benchmark of v2/v3/copied-grid v4."""
from __future__ import annotations

import argparse
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
from assess_gpu_shape_microbatch_v4 import assess_gpu_shape_microbatch_v4
from benchmark_gpu_shape_microbatch_v3 import (MODEL, SOURCE, MANIFEST,
                                               request_blocks, ordered_requests,
                                               verify_inputs)
from train_performance import ROOT


PROTOCOL = ROOT / 'reports/decision_inference_microbatch_v4_protocol.md'
DEFAULT_OUTPUT = ROOT / 'artifacts/gpu_shape_microbatch_v4/results.json'
EXPECTED_PROTOCOL_SHA256 = '21e978ddebde09e7a187a7de90cbf585cc1643f28774868d01f83a7fdd29841a'
EXPECTED_ASSSESSOR_SHA256 = 'e904a76d9e7940630e8a53f3f0e5d952bb98760fa74697bd7c42bfadb6a1e30e'
EXPECTED_PREDICTOR_SHA256 = '46c85961fb4d9388c99bc073522f8cf9c839065a1f4fe67a1b92aa33f3123075'
EXPECTED_V2_SHA256 = '20e73cdcf39dbe7252399a3bff8eda9387ff592d046ea6483df15b13710e996d'
EXPECTED_V3_SHA256 = '644326f77be68fdd8b4a4050ab3bc0ee031ad8264519ecc54de0b051409e6299'
EXPECTED_V4_SHA256 = '191bb2b3fd5d4306078bd4e5d2ebd3d1c8f800023e3eccd8963a1af1fc57dbef'
EXPECTED_MANIFEST_SHA256 = 'dcea7d8dc21ab6928da990d0fd3a4cfb6883fe1d01a831462c214d059d8ba679'
MAX_GRIDS = 8
REPETITIONS = 7
SCENARIOS = ('shared_slo', 'copied_slo', 'unique_grids')
FUNCTIONS = {
    'v2': assess_gpu_shape_microbatch_v2,
    'v3': assess_gpu_shape_microbatch_v3,
    'v4': assess_gpu_shape_microbatch_v4,
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


def verify_frozen_inputs() -> dict:
    required = {
        PROTOCOL: EXPECTED_PROTOCOL_SHA256,
        ROOT / 'scripts/assess_gpu_shape.py': EXPECTED_ASSSESSOR_SHA256,
        ROOT / 'scripts/predict_performance.py': EXPECTED_PREDICTOR_SHA256,
        ROOT / 'scripts/assess_gpu_shape_microbatch_v2.py': EXPECTED_V2_SHA256,
        ROOT / 'scripts/assess_gpu_shape_microbatch_v3.py': EXPECTED_V3_SHA256,
        ROOT / 'scripts/assess_gpu_shape_microbatch_v4.py': EXPECTED_V4_SHA256,
        MANIFEST: EXPECTED_MANIFEST_SHA256,
        MODEL: shape.MODEL_SHA256,
        SOURCE: shape.SOURCE_SHA256,
    }
    for path, expected in required.items():
        if sha256(path) != expected:
            raise ValueError('frozen_sha256_mismatch:' + str(path))
    return verify_inputs(EXPECTED_ASSSESSOR_SHA256)


def memory_worker(scenario: str, mode: str) -> dict:
    manifest = verify_frozen_inputs()
    blocks = request_blocks(scenario, manifest)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    result = FUNCTIONS[mode](bundle, ordered_requests(blocks, None), MAX_GRIDS)
    return {'scenario': scenario, 'mode': mode, 'outputs': len(result),
            'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)}


def run_memory_worker(scenario: str, mode: str) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()),
               '--memory-worker', scenario, '--mode', mode]
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def benchmark_scenario(scenario: str, manifest: dict, bundle: dict) -> dict:
    blocks = request_blocks(scenario, manifest)
    active = ordered_requests(blocks, None)
    initial = {}
    prediction_calls = {}
    for name, function in FUNCTIONS.items():
        with patch.object(shape, '_predict_once', wraps=shape._predict_once) as predicted:
            initial[name] = function(bundle, active, MAX_GRIDS)
        prediction_calls[name] = predicted.call_count
    reference_json = canonical(initial['v2'])
    if any(canonical(initial[name]) != reference_json for name in FUNCTIONS):
        raise AssertionError('initial_full_json_mismatch:' + scenario)
    if len(set(prediction_calls.values())) != 1:
        raise AssertionError('prediction_call_count_mismatch:' + scenario)

    # Warm all complete paths, excluding model/source loading and first calls.
    for function in FUNCTIONS.values():
        function(bundle, active, MAX_GRIDS)
    timings = {name: {'wall_ms': [], 'cpu_ms': []} for name in FUNCTIONS}
    pair_ratios = {'v2_over_v4': [], 'v3_over_v4': []}
    for repetition in range(REPETITIONS):
        active = ordered_requests(blocks, repetition)
        order = list(FUNCTIONS)
        random.Random(3042 + repetition).shuffle(order)
        outputs = {}
        walls = {}
        for name in order:
            start_wall = time.perf_counter_ns()
            start_cpu = time.process_time_ns()
            outputs[name] = FUNCTIONS[name](bundle, active, MAX_GRIDS)
            cpu_ms = (time.process_time_ns() - start_cpu) / 1e6
            wall_ms = (time.perf_counter_ns() - start_wall) / 1e6
            timings[name]['wall_ms'].append(round(wall_ms, 3))
            timings[name]['cpu_ms'].append(round(cpu_ms, 3))
            walls[name] = wall_ms
        if any(canonical(outputs[name]) != canonical(outputs['v2'])
               for name in ('v3', 'v4')):
            raise AssertionError('shuffled_full_json_mismatch:' + scenario)
        pair_ratios['v2_over_v4'].append(walls['v2'] / walls['v4'])
        pair_ratios['v3_over_v4'].append(walls['v3'] / walls['v4'])

    return {
        'scenario': scenario,
        'requests': len(active),
        'blocks': len(blocks),
        'unique_candidate_list_objects': len({id(item['candidates']) for item in active}),
        'prediction_calls': prediction_calls,
        'status_counts': dict(sorted(Counter(item['status'] for item in initial['v2']).items())),
        'canonical_results_sha256': hashlib.sha256(reference_json).hexdigest(),
        'exact_full_json_all_pairs': True,
        'timing_ms': timings,
        'median_wall_ms': {name: round(statistics.median(timings[name]['wall_ms']), 3)
                           for name in FUNCTIONS},
        'median_pair_speedup': {name: round(statistics.median(values), 4)
                                for name, values in pair_ratios.items()},
        'pair_speedups': {name: [round(value, 4) for value in values]
                          for name, values in pair_ratios.items()},
    }


def benchmark() -> dict:
    manifest = verify_frozen_inputs()
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    scenarios = {name: benchmark_scenario(name, manifest, bundle)
                 for name in SCENARIOS}
    memory = {name: {mode: run_memory_worker(name, mode)
                     for mode in FUNCTIONS}
              for name in SCENARIOS}
    speed = {name: scenarios[name]['median_pair_speedup'] for name in SCENARIOS}
    gates = {
        'full_json_and_prediction_call_parity': all(
            item['exact_full_json_all_pairs']
            and len(set(item['prediction_calls'].values())) == 1
            for item in scenarios.values()),
        'copied_at_least_1_20x_over_v2': speed['copied_slo']['v2_over_v4'] >= 1.20,
        'copied_at_least_1_15x_over_v3': speed['copied_slo']['v3_over_v4'] >= 1.15,
        'shared_slowdown_vs_v3_at_most_10pct':
            speed['shared_slo']['v3_over_v4'] >= 1 / 1.10,
        'unique_slowdown_vs_v2_at_most_10pct':
            speed['unique_grids']['v2_over_v4'] >= 1 / 1.10,
        'unique_slowdown_vs_v3_at_most_10pct':
            speed['unique_grids']['v3_over_v4'] >= 1 / 1.10,
        'peak_rss_increase_vs_v2_v3_at_most_20_mib': all(
            memory[name]['v4']['peak_rss_mib']
            - memory[name][baseline]['peak_rss_mib'] <= 20
            for name in SCENARIOS for baseline in ('v2', 'v3')),
    }
    return {
        'kind': 'gpu_shape_microbatch_v4_copied_grid_reuse',
        'max_grids': MAX_GRIDS,
        'repetitions': REPETITIONS,
        'scenarios': scenarios,
        'memory': memory,
        'gates': gates,
        'passes_all_gates': all(gates.values()),
        'provenance': {
            'protocol_sha256': EXPECTED_PROTOCOL_SHA256,
            'assessor_code_sha256': EXPECTED_ASSSESSOR_SHA256,
            'predictor_code_sha256': EXPECTED_PREDICTOR_SHA256,
            'v2_code_sha256': EXPECTED_V2_SHA256,
            'v3_code_sha256': EXPECTED_V3_SHA256,
            'v4_code_sha256': EXPECTED_V4_SHA256,
            'benchmark_code_sha256': sha256(Path(__file__).resolve()),
            'manifest_sha256': EXPECTED_MANIFEST_SHA256,
            'model_sha256': shape.MODEL_SHA256,
            'source_sha256': shape.SOURCE_SHA256,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--memory-worker', choices=SCENARIOS)
    parser.add_argument('--mode', choices=tuple(FUNCTIONS))
    args = parser.parse_args()
    if args.memory_worker:
        if args.mode is None:
            parser.error('--mode is required with --memory-worker')
        print(json.dumps(memory_worker(args.memory_worker, args.mode), sort_keys=True))
        return
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'output': str(args.output), 'passes_all_gates':
                      result['passes_all_gates'], 'gates': result['gates']},
                     sort_keys=True))


if __name__ == '__main__':
    main()
