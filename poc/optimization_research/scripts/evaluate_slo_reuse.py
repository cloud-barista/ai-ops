"""Single-threaded, offline exact-reuse benchmark for repeated SLO queries.

Only candidate prediction records are cached. ``assess_gpu_shape`` still checks
each request and constructs every SLO-specific result. The temporary patch is
deliberately local to this research script and is not a service implementation.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import platform
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import sklearn

import assess_gpu_shape as shape
from train_performance import ROOT


ARTIFACT_ROOT = ROOT / 'artifacts/slo_reuse_v1'
PROTOCOL = ROOT / 'reports/scale_decision_feasibility.md'
FROZEN_PROTOCOL = ARTIFACT_ROOT / 'frozen_protocol.md'
MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5)
SHAPES = {(replicas, cores) for replicas in (1, 2)
          for cores in (2, 4, 8, 16)}
ORIGINAL_PREDICT = shape.predict_records


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, default=str)


class CachedPredictor:
    """Reuse only raw support-gated predictions for an identical ordered grid."""

    def __init__(self) -> None:
        self.cache: dict[str, list[dict]] = {}
        self.hits = 0
        self.misses = 0
        self.source_bundle = None
        self.source_model = None

    @staticmethod
    def key(bundle: dict, records: list[dict]) -> str:
        # Include every field considered by prediction or support validation,
        # plus all ordered candidate fields and the verified model identity.
        scope = {
            'verified': bundle.get(shape.VERIFIED_KEY),
            'selected': bundle.get('selected'),
            'features': bundle.get('features'),
            'targets': bundle.get('targets'),
            'categorical_support': bundle.get('categorical_support'),
            'numeric_support': bundle.get('numeric_support'),
            'support_combination_columns': bundle.get('support_combination_columns'),
            'support_combinations': bundle.get('support_combinations'),
            'candidates': records,
        }
        return hashlib.sha256(_stable_json(scope).encode('utf-8')).hexdigest()

    def predict(self, bundle: dict, records: list[dict]) -> list[dict]:
        key = self.key(bundle, records)
        if key in self.cache:
            self.hits += 1
            return copy.deepcopy(self.cache[key])
        result = ORIGINAL_PREDICT(bundle, records)
        self.misses += 1
        self.cache[key] = copy.deepcopy(result)
        return result

    def assess(self, bundle: dict, request: dict) -> dict:
        # The assessor owns all request and SLO checks. Only its internal
        # candidate prediction call is intercepted, in this one thread. The
        # assessor shallow-copies ExtraTrees each time, so keying on the
        # transient predictor model object would prevent every cache hit.
        if bundle is not self.source_bundle or bundle.get('model') is not self.source_model:
            self.cache.clear()
            self.source_bundle = bundle
            self.source_model = bundle.get('model')
        with patch.object(shape, 'predict_records', self.predict):
            return shape.assess_gpu_shape(bundle, request)


def eligible_groups(source_path: Path) -> dict[str, pd.DataFrame]:
    frame = pd.read_parquet(source_path)
    frame = frame[(frame.campaign == 'khu-hf-local-exhaustive')
                  & (frame.device == 'cuda')
                  & frame.eligible & frame.success]
    groups = {str(key): part.copy() for key, part in
              frame.groupby('workload_group', sort=True)}
    if len(frame) != 4600 or len(groups) != 575:
        raise ValueError('unexpected_primary_eight_shape_group_count')
    for group, part in groups.items():
        if len(part) != 8 or set(zip(part.replica_count,
                                     part.cpu_core_limit)) != SHAPES:
            raise ValueError('incomplete_group:' + group)
        if (part[['latency_slo_ms', 'target_throughput_rps']]
                .drop_duplicates().shape[0] != 1):
            raise ValueError('multiple_original_slo_pairs:' + group)
    return groups


def build_manifest(source_path: Path, model_path: Path) -> dict:
    if sha256(source_path) != shape.SOURCE_SHA256:
        raise ValueError('source_sha256_mismatch')
    if sha256(model_path) != shape.MODEL_SHA256:
        raise ValueError('model_sha256_mismatch')
    groups = eligible_groups(source_path)
    by_model: dict[str, list[str]] = {}
    for key, part in groups.items():
        by_model.setdefault(str(part.iloc[0].model_id), []).append(key)
    if len(by_model) != 9:
        raise ValueError('unexpected_model_count')
    chosen = []
    for model_id in sorted(by_model):
        keys = sorted(by_model[model_id],
                      key=lambda key: (hashlib.sha256(key.encode()).hexdigest(), key))[:5]
        if len(keys) != 5:
            raise ValueError('too_few_groups_for_model:' + model_id)
        for key in keys:
            part = groups[key]
            row = part.iloc[0]
            chosen.append({
                'model_id': model_id,
                'workload_group': key,
                'latency_slo_ms': float(row.latency_slo_ms),
                'target_throughput_rps': float(row.target_throughput_rps),
            })
    return {
        'kind': 'slo_reuse_frozen_selection_v1',
        'source_sha256': shape.SOURCE_SHA256,
        'model_sha256': shape.MODEL_SHA256,
        'protocol_sha256': sha256(PROTOCOL),
        'selection': 'per model first 5 by sha256(workload_group), tie by id',
        'slo_multipliers': list(MULTIPLIERS),
        'groups': chosen,
    }


def verify_manifest(manifest: dict, source_path: Path, model_path: Path) -> None:
    expected = build_manifest(source_path, model_path)
    if manifest != expected:
        raise ValueError('manifest_does_not_match_frozen_selection')


def verify_frozen_manifest(manifest: dict, source_path: Path,
                           model_path: Path) -> None:
    """Replay an old manifest against its archived, byte-identical protocol.

    ``verify_manifest`` remains strict about the current protocol document.
    This path checks the archived protocol's original SHA and reconstructs
    every other manifest field from the fixed source and model.
    """
    frozen_sha = sha256(FROZEN_PROTOCOL)
    if manifest.get('protocol_sha256') != frozen_sha:
        raise ValueError('frozen_protocol_sha256_mismatch')
    expected = build_manifest(source_path, model_path)
    expected['protocol_sha256'] = frozen_sha
    if manifest != expected:
        raise ValueError('manifest_does_not_match_frozen_selection')


def make_candidates(part: pd.DataFrame) -> list[dict]:
    candidates = []
    part = part.sort_values(['replica_count', 'cpu_core_limit'])
    for _, row in part.iterrows():
        candidates.append({
            'application_id': 'offline-model:' + str(row.model_id),
            'workload_id': str(row.workload_group),
            'model_id': str(row.model_id),
            'model_revision': str(row.model_revision),
            'device': str(row.device),
            'host': str(row.host),
            'device_name': str(row.device_name),
            'batch_size': int(row.batch_size),
            'max_length': int(row.max_length),
            'request_concurrency': int(row.request_concurrency),
            'cpu_threads': int(row.cpu_threads),
            'cpu_core_limit': int(row.cpu_core_limit),
            'replica_count': int(row.replica_count),
            'repeats': int(row.repeats),
        })
    return candidates


def make_requests(part: pd.DataFrame, record: dict) -> list[dict]:
    candidates = make_candidates(part)
    latency = record['latency_slo_ms']
    rps = record['target_throughput_rps']
    return [{'batch_latency_p95_limit_ms': latency * lm,
             'min_requests_per_second': rps * rm,
             'candidates': candidates}
            for lm in MULTIPLIERS for rm in MULTIPLIERS]


def run_path(bundle: dict, requests: list[dict], cached: bool):
    reuse = CachedPredictor() if cached else None
    outputs, elapsed = [], []
    start_block = time.perf_counter_ns()
    for request in requests:
        start = time.perf_counter_ns()
        result = (reuse.assess(bundle, request) if reuse is not None
                  else shape.assess_gpu_shape(bundle, request))
        elapsed.append((time.perf_counter_ns() - start) / 1e6)
        outputs.append(result)
    block_ms = (time.perf_counter_ns() - start_block) / 1e6
    return outputs, elapsed, block_ms, reuse


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percent / 100
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def evaluate(manifest: dict, source_path: Path, model_path: Path,
             output_dir: Path) -> dict:
    verify_frozen_manifest(manifest, source_path, model_path)
    bundle = shape.load_verified_bundle(model_path, source_path)
    groups = eligible_groups(source_path)
    requests = [(record, make_requests(groups[record['workload_group']], record))
                for record in manifest['groups']]
    if len(requests) != 45 or any(len(q) != 25 for _, q in requests):
        raise ValueError('unexpected_request_grid')
    # Configure the saved model's predict worker and warm it outside timing.
    first_request = requests[0][1][0]
    shape.assess_gpu_shape(bundle, first_request)

    timing_rows = []
    block_rows = []
    unique_digest = hashlib.sha256()
    status_count: Counter[str] = Counter()
    mismatch_count = 0
    cache_hits = cache_misses = 0
    entry_bytes = []
    randomizer = random.Random(42)
    for repetition in range(5):
        order = list(range(len(requests)))
        randomizer.shuffle(order)
        for index in order:
            record, request_grid = requests[index]
            pathways = ['fresh', 'reuse']
            randomizer.shuffle(pathways)
            result_by_path = {}
            for path in pathways:
                outputs, elapsed, block_ms, reuse = run_path(
                    bundle, request_grid, path == 'reuse')
                result_by_path[path] = outputs
                block_rows.append({'repetition': repetition,
                                   'workload_group': record['workload_group'],
                                   'path': path, 'block_ms': block_ms,
                                   'first_ms': elapsed[0]})
                for query_index, time_ms in enumerate(elapsed):
                    timing_rows.append({'repetition': repetition,
                                        'workload_group': record['workload_group'],
                                        'path': path,
                                        'query_index': query_index,
                                        'elapsed_ms': time_ms})
                if reuse is not None:
                    cache_hits += reuse.hits
                    cache_misses += reuse.misses
                    entry_bytes.append(len(_stable_json(reuse.cache).encode()))
            fresh, reused = result_by_path['fresh'], result_by_path['reuse']
            for query_index, (left, right) in enumerate(zip(fresh, reused)):
                if left != right:
                    mismatch_count += 1
                    if mismatch_count <= 3:
                        raise AssertionError('output_mismatch:repetition='
                                             + str(repetition) + ':group='
                                             + record['workload_group'] + ':query='
                                             + str(query_index))
                if repetition == 0:
                    unique_digest.update(_stable_json(left).encode())
                    unique_digest.update(b'\n')
                    status_count[left['status']] += 1
    if mismatch_count:
        raise AssertionError('output_mismatches_present')
    if cache_hits != 5 * 45 * 24 or cache_misses != 5 * 45:
        raise AssertionError('unexpected_cache_use')

    def path_values(rows: list[dict], path: str, field: str) -> list[float]:
        return [row[field] for row in rows if row['path'] == path]

    fresh_blocks = path_values(block_rows, 'fresh', 'block_ms')
    reused_blocks = path_values(block_rows, 'reuse', 'block_ms')
    fresh_first = path_values(block_rows, 'fresh', 'first_ms')
    reuse_first = path_values(block_rows, 'reuse', 'first_ms')
    fresh_warm = [row['elapsed_ms'] for row in timing_rows
                  if row['path'] == 'fresh' and row['query_index'] > 0]
    reuse_warm = [row['elapsed_ms'] for row in timing_rows
                  if row['path'] == 'reuse' and row['query_index'] > 0]
    block_ratio = statistics.median(fresh_blocks) / statistics.median(reused_blocks)
    first_regression = percentile(reuse_first, 95) / percentile(fresh_first, 95)
    metrics = {
        'status': ('PASS_RESEARCH_ONLY' if block_ratio >= 3
                   and first_regression <= 1.2 else 'FAIL'),
        'parity': {
            'unique_queries': 45 * 25,
            'evaluated_query_pairs_including_repeats': 5 * 45 * 25,
            'full_output_mismatches': mismatch_count,
            'fresh_output_sha256_unique_queries': unique_digest.hexdigest(),
            'status_counts_unique_queries': dict(status_count),
        },
        'cache': {
            'hits': cache_hits,
            'misses': cache_misses,
            'hit_rate': cache_hits / (cache_hits + cache_misses),
            'entry_json_bytes_median': statistics.median(entry_bytes),
            'entry_json_bytes_max': max(entry_bytes),
        },
        'timing_ms': {
            'block_median_fresh': statistics.median(fresh_blocks),
            'block_median_reuse': statistics.median(reused_blocks),
            'block_median_speedup': block_ratio,
            'cold_p95_fresh': percentile(fresh_first, 95),
            'cold_p95_reuse': percentile(reuse_first, 95),
            'cold_p95_ratio': first_regression,
            'warm_p50_fresh': statistics.median(fresh_warm),
            'warm_p50_reuse': statistics.median(reuse_warm),
            'warm_p95_fresh': percentile(fresh_warm, 95),
            'warm_p95_reuse': percentile(reuse_warm, 95),
        },
        'gates': {'full_output_exact': True, 'block_speedup_min': 3,
                  'cold_p95_ratio_max': 1.2},
        'provenance': {
            'source_sha256': sha256(source_path),
            'model_sha256': sha256(model_path),
            'manifest_sha256': sha256(output_dir / 'manifest.json'),
            'protocol_sha256': manifest['protocol_sha256'],
            'current_protocol_document_sha256': sha256(PROTOCOL),
            'script_sha256': sha256(Path(__file__)),
            'python': platform.python_version(),
            'sklearn': sklearn.__version__,
            'timing_repetitions': 5,
            'seed': 42,
        },
    }
    with (output_dir / 'timings.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(timing_rows[0]))
        writer.writeheader()
        writer.writerows(timing_rows)
    with (output_dir / 'block_timings.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(block_rows[0]))
        writer.writeheader()
        writer.writerows(block_rows)
    (output_dir / 'metrics.json').write_text(json.dumps(metrics, indent=2,
                                                      sort_keys=True) + '\n')
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('freeze', 'evaluate'))
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--model', type=Path,
                        default=ROOT / 'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--output', type=Path, default=ARTIFACT_ROOT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    if args.mode == 'freeze':
        manifest = build_manifest(args.source, args.model)
        if manifest_path.exists():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ValueError('existing_frozen_manifest_differs')
        else:
            manifest_path.open('x').write(json.dumps(manifest, indent=2,
                                                     sort_keys=True) + '\n')
        print('frozen', manifest_path, sha256(manifest_path),
              len(manifest['groups']), 'groups')
        return
    if not manifest_path.exists():
        raise ValueError('manifest_must_be_frozen_before_scoring')
    manifest = json.loads(manifest_path.read_text())
    print(json.dumps(evaluate(manifest, args.source, args.model, args.output),
                     indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
