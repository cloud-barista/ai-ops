"""Measure one eight-candidate research API call against eight serial calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from assess_gpu_shape import load_verified_bundle
from predict_performance import predict_records
from train_performance import ROOT


GROUP = '783f9cfdcd98f2b1017e1329e7130ca0a35064f0f9fbcd583364ccf624e31d55'
MODEL_SHA = '7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161'
SOURCE_SHA = 'b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def summarize(values: list[float]) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    return {
        'mean_ms': float(data.mean()),
        'p50_ms': float(np.median(data)),
        'p95_ms': float(np.quantile(data, .95)),
    }


def run(model_path: Path, source_path: Path, output: Path, repetitions: int = 60) -> dict:
    if sha256(model_path) != MODEL_SHA or sha256(source_path) != SOURCE_SHA:
        raise ValueError('Frozen model or source SHA mismatch')
    if repetitions < 1:
        raise ValueError('Need at least one timing repetition')
    bundle = load_verified_bundle(model_path, source_path)
    frame = pd.read_parquet(source_path)
    slate = frame.loc[
        frame.eligible & frame.campaign.eq('khu-hf-local-exhaustive')
        & frame.device.eq('cuda') & frame.workload_group.eq(GROUP)
    ].sort_values(['replica_count', 'cpu_core_limit'])
    expected = [(gpu, cpu) for gpu in (1, 2) for cpu in (2, 4, 8, 16)]
    actual = list(slate[['replica_count', 'cpu_core_limit']].itertuples(index=False, name=None))
    if actual != expected:
        raise AssertionError('Frozen eight-candidate slate changed')
    records = slate.to_dict('records')

    def batched():
        return predict_records(bundle, records)

    def serial():
        return [predict_records(bundle, [record])[0] for record in records]

    batched_result = batched()
    serial_result = serial()
    if batched_result != serial_result or any(row['status'] != 'PREDICTED' for row in batched_result):
        raise AssertionError('Batch predictions or support decisions differ from serial calls')
    for _ in range(10):
        batched()
        serial()
    rng = np.random.default_rng(42)
    observations = []
    for repeat in range(repetitions):
        for name in rng.permutation(['batched', 'serial']):
            started = time.perf_counter_ns()
            result = batched() if name == 'batched' else serial()
            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
            if result != serial_result:
                raise AssertionError('Timed prediction changed')
            observations.append({'repeat': repeat, 'method': str(name), 'latency_ms': elapsed_ms})
    by_method = {
        name: summarize([row['latency_ms'] for row in observations if row['method'] == name])
        for name in ('batched', 'serial')
    }
    report = {
        'kind': 'research_performance_eight_candidate_batch_inference',
        'protocol_sha256': sha256(ROOT / 'reports/performance_batch_predict_protocol.md'),
        'script_sha256': sha256(Path(__file__)),
        'prediction_script_sha256': sha256(ROOT / 'scripts/predict_performance.py'),
        'model_sha256': MODEL_SHA,
        'source_sha256': SOURCE_SHA,
        'workload_group': GROUP,
        'candidate_count': len(records),
        'all_supported': True,
        'exact_output_parity': True,
        'repetitions_per_method': repetitions,
        'timings': by_method,
        'p95_speedup_serial_over_batch': by_method['serial']['p95_ms'] / by_method['batched']['p95_ms'],
        'passes_frozen_gate': by_method['serial']['p95_ms'] >= 2 * by_method['batched']['p95_ms'],
        'scope': 'warm offline predict_records call; joblib load excluded',
        'runtime_versions': {
            'python': platform.python_version(),
            'sklearn': sklearn.__version__,
            'numpy': np.__version__,
            'pandas': pd.__version__,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(observations).to_csv(output / 'timings.csv', index=False)
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, default=ROOT / 'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--source', type=Path, default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/performance_batch_predict_v1')
    parser.add_argument('--repetitions', type=int, default=60)
    args = parser.parse_args()
    result = run(args.model, args.source, args.output, args.repetitions)
    print(json.dumps(result, indent=2))
