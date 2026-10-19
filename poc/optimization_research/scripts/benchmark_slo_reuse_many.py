"""Compare the public call-scoped SLO-sweep API with repeated scalar calls.

This is a warm, single-threaded offline function benchmark. It reuses the
frozen selection from slo_reuse_v1 without changing that experiment's files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import random
import statistics
import time
from collections import Counter
from pathlib import Path

import assess_gpu_shape as shape
from evaluate_slo_reuse import (PROTOCOL, eligible_groups, make_requests,
                                percentile, sha256, verify_manifest)
from train_performance import ROOT


DEFAULT_OUTPUT = ROOT / 'artifacts/slo_reuse_many_v1'


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def evaluate(source_path: Path, model_path: Path, manifest_path: Path,
             output_dir: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    verify_manifest(manifest, source_path, model_path)
    groups = eligible_groups(source_path)
    requests_by_group = [
        (record['workload_group'],
         make_requests(groups[record['workload_group']], record))
        for record in manifest['groups']
    ]
    if len(requests_by_group) != 45 or any(len(items) != 25
                                           for _, items in requests_by_group):
        raise ValueError('unexpected_frozen_request_grid')
    bundle = shape.load_verified_bundle(model_path, source_path)
    shape.assess_gpu_shape(bundle, requests_by_group[0][1][0])  # warm outside time

    randomizer = random.Random(42)
    first_rows, block_rows = [], []
    status_counts: Counter[str] = Counter()
    unique_digest = hashlib.sha256()
    compared = 0
    for repetition in range(5):
        group_order = list(range(len(requests_by_group)))
        randomizer.shuffle(group_order)
        for group_index in group_order:
            group, requests = requests_by_group[group_index]
            first_outputs = {}
            first_paths = ['scalar', 'many_one_request']
            randomizer.shuffle(first_paths)
            for path in first_paths:
                start = time.perf_counter_ns()
                if path == 'scalar':
                    output = shape.assess_gpu_shape(bundle, requests[0])
                else:
                    output = shape.assess_gpu_shape_many(bundle, requests[:1])[0]
                elapsed_ms = (time.perf_counter_ns() - start) / 1e6
                first_rows.append({'repetition': repetition,
                                   'workload_group': group, 'path': path,
                                   'elapsed_ms': elapsed_ms})
                first_outputs[path] = output
            if first_outputs['scalar'] != first_outputs['many_one_request']:
                raise AssertionError('first_output_mismatch:' + group)

            block_outputs = {}
            block_paths = ['scalar', 'many']
            randomizer.shuffle(block_paths)
            for path in block_paths:
                start = time.perf_counter_ns()
                if path == 'scalar':
                    output = [shape.assess_gpu_shape(bundle, request)
                              for request in requests]
                else:
                    output = shape.assess_gpu_shape_many(bundle, requests)
                elapsed_ms = (time.perf_counter_ns() - start) / 1e6
                block_rows.append({'repetition': repetition,
                                   'workload_group': group, 'path': path,
                                   'block_ms': elapsed_ms})
                block_outputs[path] = output
            if block_outputs['scalar'] != block_outputs['many']:
                raise AssertionError('block_output_mismatch:' + group)
            compared += len(requests)
            if repetition == 0:
                for output in block_outputs['scalar']:
                    status_counts[output['status']] += 1
                    unique_digest.update(_stable_json(output).encode('utf-8'))
                    unique_digest.update(b'\n')

    def values(rows: list[dict], path: str, field: str) -> list[float]:
        return [row[field] for row in rows if row['path'] == path]

    scalar_blocks = values(block_rows, 'scalar', 'block_ms')
    many_blocks = values(block_rows, 'many', 'block_ms')
    scalar_first = values(first_rows, 'scalar', 'elapsed_ms')
    many_first = values(first_rows, 'many_one_request', 'elapsed_ms')
    speedup = statistics.median(scalar_blocks) / statistics.median(many_blocks)
    first_ratio = percentile(many_first, 95) / percentile(scalar_first, 95)
    metrics = {
        'status': ('PASS_RESEARCH_ONLY' if speedup >= 3.0
                   and first_ratio <= 1.2 else 'FAIL'),
        'scope': 'warm offline Python function time; no model load, JSON/API or deployment',
        'parity': {
            'unique_queries': 45 * 25,
            'query_pairs_including_repeats': compared,
            'full_output_mismatches': 0,
            'status_counts_unique_queries': dict(status_counts),
            'scalar_output_sha256_unique_queries': unique_digest.hexdigest(),
        },
        'timing_ms': {
            'block_median_scalar': statistics.median(scalar_blocks),
            'block_median_many': statistics.median(many_blocks),
            'block_median_speedup': speedup,
            'one_request_p95_scalar': percentile(scalar_first, 95),
            'one_request_p95_many': percentile(many_first, 95),
            'one_request_p95_ratio': first_ratio,
        },
        'gates': {'full_output_exact': True, 'block_speedup_min': 3.0,
                  'one_request_p95_ratio_max': 1.2},
        'cache': {'scope': 'one assess_gpu_shape_many invocation',
                  'max_grids': shape.MAX_CACHED_GRIDS},
        'provenance': {
            'source_sha256': sha256(source_path),
            'model_sha256': sha256(model_path),
            'manifest_sha256': sha256(manifest_path),
            'protocol_sha256': sha256(PROTOCOL),
            'assessor_sha256': sha256(Path(shape.__file__)),
            'benchmark_script_sha256': sha256(Path(__file__)),
            'python': platform.python_version(),
            'sklearn': shape.sklearn.__version__,
            'seed': 42,
            'timing_repetitions': 5,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(output_dir / 'first_request_timings.csv', first_rows)
    _write_rows(output_dir / 'block_timings.csv', block_rows)
    (output_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2,
                                                      sort_keys=True) + '\n')
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--model', type=Path,
                        default=ROOT / 'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--manifest', type=Path,
                        default=ROOT / 'artifacts/slo_reuse_v1/manifest.json')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.source, args.model, args.manifest,
                              args.output), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
