"""Reproduce exact cross-workload GPU-shape microbatch parity and timing."""
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
from collections import Counter, OrderedDict
from pathlib import Path

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch import _grid_key, _predict_grids, assess_gpu_shape_microbatch
from evaluate_slo_reuse import (PROTOCOL as OLD_SLO_PROTOCOL,
                                eligible_groups, make_requests,
                                verify_frozen_manifest)
from train_performance import ROOT


MODEL = ROOT / 'artifacts/performance_v1/performance_model.joblib'
SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
MANIFEST = ROOT / 'artifacts/slo_reuse_v1/manifest.json'
DEFAULT_OUTPUT = ROOT / 'artifacts/cross_workload_microbatch_v1/results.json'
SIZES = (2, 4, 8)
REPETITIONS = 5
FROZEN_SLO_PROTOCOL_SHA256 = 'b81fc60da43e5df7ecb11b3d560b24fd5f89b158c2bad360a1aae448385a817a'


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def split_models(manifest: dict) -> tuple[list[str], list[str]]:
    models = sorted({record['model_id'] for record in manifest['groups']},
                    key=lambda value: (hashlib.sha256(value.encode()).hexdigest(), value))
    if len(models) != 9:
        raise ValueError('expected_nine_models')
    return models[:5], models[5:]


def verify_frozen_selection(manifest: dict) -> str:
    """Verify the frozen selection despite a later edit to its old protocol."""
    if manifest.get('protocol_sha256') != FROZEN_SLO_PROTOCOL_SHA256:
        raise ValueError('unexpected_frozen_slo_protocol_sha256')
    verify_frozen_manifest(manifest, SOURCE, MODEL)
    return shape.sha256(OLD_SLO_PROTOCOL)


def selected_requests(manifest: dict, groups: dict, model_id: str) -> list[list[dict]]:
    records = [record for record in manifest['groups'] if record['model_id'] == model_id]
    if len(records) != 5:
        raise ValueError('expected_five_workloads_per_model')
    return [make_requests(groups[record['workload_group']], record) for record in records]


def run_path(bundle: dict, request_groups: list[list[dict]],
             order: list[int], mode: str, batch_size: int) -> list[dict]:
    if mode == 'baseline':
        return [answer for index in order
                for answer in shape.assess_gpu_shape_many(bundle, request_groups[index])]
    requests = [request for index in order for request in request_groups[index]]
    return assess_gpu_shape_microbatch(bundle, requests, batch_size)


def verify_raw_parity(bundle: dict, request_groups: list[list[dict]],
                      batch_size: int) -> int:
    grids: OrderedDict[str, list[dict]] = OrderedDict()
    for requests in request_groups:
        candidates = requests[0]['candidates']
        grids[_grid_key(candidates)] = candidates
    reference = {key: shape._predict_once(bundle, candidates)
                 for key, candidates in grids.items()}
    batched = _predict_grids(bundle, grids, batch_size)
    if canonical(reference) != canonical(batched):
        raise AssertionError('raw_eight_candidate_mismatch')
    return len(grids) * 8


def worker(model_id: str, mode: str, batch_size: int) -> dict:
    manifest = json.loads(MANIFEST.read_text())
    verify_frozen_selection(manifest)
    groups = eligible_groups(SOURCE)
    request_groups = selected_requests(manifest, groups, model_id)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    shape.assess_gpu_shape(bundle, request_groups[0][0])

    if mode == 'microbatch':
        raw_compared = verify_raw_parity(bundle, request_groups, batch_size)
    else:
        raw_compared = 0
    order = list(range(5))
    expected = run_path(bundle, request_groups, order, 'baseline', 1)
    observed = run_path(bundle, request_groups, order, mode, batch_size)
    if canonical(expected) != canonical(observed):
        raise AssertionError('final_json_mismatch')
    status_counts = Counter(item['status'] for item in observed)

    # Warm the complete chosen path; model loading and reading are excluded.
    run_path(bundle, request_groups, order, mode, batch_size)
    elapsed_ms = []
    for repetition in range(REPETITIONS):
        order = list(range(5))
        random.Random(42 + repetition).shuffle(order)
        start = time.perf_counter_ns()
        actual = run_path(bundle, request_groups, order, mode, batch_size)
        elapsed_ms.append((time.perf_counter_ns() - start) / 1e6)
        expected_order = [expected[index * 25:(index + 1) * 25]
                          for index in order]
        if canonical(actual) != canonical([row for block in expected_order for row in block]):
            raise AssertionError('shuffled_final_json_mismatch')
    return {
        'model_id': model_id, 'mode': mode, 'batch_size': batch_size,
        'workload_groups': 5, 'requests': 125,
        'raw_candidates_compared': raw_compared,
        'final_json_sha256': digest(observed),
        'status_counts': dict(sorted(status_counts.items())),
        'block_ms': [round(value, 3) for value in elapsed_ms],
        'median_block_ms': round(statistics.median(elapsed_ms), 3),
        'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
    }


def run_worker(model_id: str, mode: str, batch_size: int) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()),
               '--worker-model', model_id, '--worker-mode', mode,
               '--worker-batch-size', str(batch_size)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def boundary_parity(bundle: dict, manifest: dict, groups: dict, batch_size: int) -> dict:
    first, second = manifest['groups'][0], manifest['groups'][-1]
    a = make_requests(groups[first['workload_group']], first)[0]
    b = make_requests(groups[second['workload_group']], second)[0]
    invalid_slo = copy.deepcopy(a)
    invalid_slo['min_requests_per_second'] = 0
    invalid_grid = copy.deepcopy(a)
    invalid_grid['candidates'].pop()
    unsupported = copy.deepcopy(a)
    for row in unsupported['candidates']:
        row['model_revision'] = 'not-in-training-support'
    reordered = copy.deepcopy(b)
    reordered['candidates'].reverse()
    requests = [a, b, invalid_slo, unsupported, reordered, None,
                copy.deepcopy(a), invalid_grid, copy.deepcopy(unsupported)]
    reference = shape.assess_gpu_shape_many(bundle, requests)
    actual = assess_gpu_shape_microbatch(bundle, requests, batch_size)
    if canonical(reference) != canonical(actual):
        raise AssertionError('boundary_json_mismatch')
    if actual[3]['status'] != 'ABSTAIN' or actual[6] != actual[0]:
        raise AssertionError('boundary_status_mismatch')
    return {'requests_compared': len(requests),
            'statuses': [item['status'] for item in actual],
            'final_json_sha256': digest(actual)}


def verify_all_models_mixed(batch_size: int) -> dict:
    """Check one call containing all 45 grids and 1,125 SLO requests."""
    manifest = json.loads(MANIFEST.read_text())
    verify_frozen_selection(manifest)
    groups = eligible_groups(SOURCE)
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    request_groups = [make_requests(groups[item['workload_group']], item)
                      for item in manifest['groups']]
    expected = [result for requests in request_groups
                for result in shape.assess_gpu_shape_many(bundle, requests)]
    requests = [request for group in request_groups for request in group]
    actual = assess_gpu_shape_microbatch(bundle, requests, batch_size)
    if canonical(expected) != canonical(actual):
        raise AssertionError('all_models_mixed_json_mismatch')
    return {'grids_compared': len(request_groups),
            'requests_compared': len(requests),
            'batch_size': batch_size,
            'final_json_sha256': digest(actual)}


def evaluate(output_path: Path) -> dict:
    manifest = json.loads(MANIFEST.read_text())
    current_old_protocol_sha256 = verify_frozen_selection(manifest)
    development, heldout = split_models(manifest)
    runs: dict[str, dict[str, dict]] = {}
    for model_id in development:
        runs[model_id] = {'baseline': run_worker(model_id, 'baseline', 1)}
        for size in SIZES:
            runs[model_id][str(size)] = run_worker(model_id, 'microbatch', size)
            if runs[model_id][str(size)]['final_json_sha256'] != runs[model_id]['baseline']['final_json_sha256']:
                raise AssertionError('development_output_digest_mismatch:' + model_id)
        print('development', model_id, flush=True)

    scores = {size: statistics.mean(runs[model_id][str(size)]['median_block_ms']
                                    for model_id in development)
              for size in SIZES}
    chosen = min(SIZES, key=lambda size: (scores[size], size))
    print('chosen_batch_size', chosen, flush=True)
    for model_id in heldout:
        runs[model_id] = {'baseline': run_worker(model_id, 'baseline', 1),
                          str(chosen): run_worker(model_id, 'microbatch', chosen)}
        if runs[model_id][str(chosen)]['final_json_sha256'] != runs[model_id]['baseline']['final_json_sha256']:
            raise AssertionError('heldout_output_digest_mismatch:' + model_id)
        print('heldout', model_id, flush=True)

    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    groups = eligible_groups(SOURCE)
    boundary = boundary_parity(bundle, manifest, groups, chosen)
    old_mean = statistics.mean(runs[model_id]['baseline']['median_block_ms']
                               for model_id in heldout)
    new_mean = statistics.mean(runs[model_id][str(chosen)]['median_block_ms']
                               for model_id in heldout)
    ratios = {model_id: runs[model_id][str(chosen)]['peak_rss_mib']
             / runs[model_id]['baseline']['peak_rss_mib'] for model_id in heldout}
    full_parity = all(runs[model_id][str(chosen)]['raw_candidates_compared'] == 40
                      for model_id in heldout)
    full_parity &= all(runs[model_id][str(chosen)]['final_json_sha256']
                       == runs[model_id]['baseline']['final_json_sha256']
                       for model_id in heldout)
    full_parity &= all(runs[model_id][str(size)]['raw_candidates_compared'] == 40
                       and runs[model_id][str(size)]['final_json_sha256']
                           == runs[model_id]['baseline']['final_json_sha256']
                       for model_id in development for size in SIZES)
    gates = {
        'exact_raw_and_final_json_and_boundaries': full_parity and boundary['requests_compared'] == 9,
        'each_heldout_model_faster': all(runs[model_id][str(chosen)]['median_block_ms']
                                         < runs[model_id]['baseline']['median_block_ms']
                                         for model_id in heldout),
        'heldout_mean_median_speedup_at_least_2x': old_mean / new_mean >= 2,
        'each_heldout_peak_rss_at_most_2x': all(ratio <= 2 for ratio in ratios.values()),
    }
    result = {
        'kind': 'cross_workload_exact_microbatch_v1',
        'source_sha256': shape.SOURCE_SHA256,
        'model_sha256': shape.MODEL_SHA256,
        'manifest_sha256': shape.sha256(MANIFEST),
        'protocol_sha256': shape.sha256(ROOT / 'reports/cross_workload_microbatch_protocol.md'),
        'frozen_slo_protocol_sha256': FROZEN_SLO_PROTOCOL_SHA256,
        'current_old_slo_protocol_sha256': current_old_protocol_sha256,
        'model_split_method': 'sha256(model_id) ascending: first five development, last four heldout',
        'development_models': development,
        'heldout_models': heldout,
        'development_mean_median_block_ms_by_size': {str(k): round(v, 3) for k, v in scores.items()},
        'chosen_batch_size': chosen,
        'runs': runs,
        'boundary_parity': boundary,
        'heldout_baseline_mean_median_block_ms': round(old_mean, 3),
        'heldout_microbatch_mean_median_block_ms': round(new_mean, 3),
        'heldout_speedup': round(old_mean / new_mean, 3),
        'heldout_peak_rss_ratios': {k: round(v, 3) for k, v in ratios.items()},
        'gates': gates,
        'passes_all_gates': all(gates.values()),
        'scope': shape.SCOPE,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--worker-model')
    parser.add_argument('--worker-mode', choices=('baseline', 'microbatch'))
    parser.add_argument('--worker-batch-size', type=int, default=1)
    parser.add_argument('--verify-mixed', action='store_true')
    args = parser.parse_args()
    if args.verify_mixed:
        result = json.loads(args.output.read_text())
        print(json.dumps(verify_all_models_mixed(result['chosen_batch_size']),
                         indent=2, sort_keys=True))
    elif args.worker_model:
        if args.worker_mode is None:
            parser.error('--worker-mode is required with --worker-model')
        print(json.dumps(worker(args.worker_model, args.worker_mode,
                                args.worker_batch_size), sort_keys=True))
    else:
        result = evaluate(args.output)
        print(json.dumps({'output': str(args.output), 'chosen_batch_size': result['chosen_batch_size'],
                          'heldout_speedup': result['heldout_speedup'],
                          'passes_all_gates': result['passes_all_gates']}, sort_keys=True))


if __name__ == '__main__':
    main()
