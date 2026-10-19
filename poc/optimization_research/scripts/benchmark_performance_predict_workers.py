"""Compare fitted performance-model inference with one and two workers.

This is a warm local CPU microbenchmark of the research predictor. The saved
model, its training procedure, and deployment decisions are not changed.
"""
from __future__ import annotations

import argparse
import copy
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


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker_copy(pipeline, workers: int):
    """Copy configuration while sharing the already fitted, read-only trees."""
    model = copy.copy(pipeline)
    model.steps = list(pipeline.steps)
    regressor = copy.copy(pipeline.named_steps['regressor'])
    regressor.set_params(n_jobs=workers)
    model.steps[-1] = ('regressor', regressor)
    return model


def run(model_path: Path, source: Path, example_path: Path,
        output: Path, repetitions: int = 60) -> dict:
    bundle = load_verified_bundle(model_path, source)
    if bundle['selected'] != 'extra_trees':
        raise ValueError('Expected selected extra_trees model')
    original = bundle['model']
    if original.named_steps['regressor'].n_jobs != 2:
        raise ValueError('Expected saved two-worker fit configuration')
    two = worker_copy(original, 2)
    one = worker_copy(original, 1)
    raw = pd.read_parquet(source)
    primary = raw.loc[raw.eligible & raw.campaign.eq('khu-hf-local-exhaustive')]
    full = primary[bundle['features']]
    p2, p1 = two.predict(full), one.predict(full)
    max_abs = float(np.max(np.abs(p2 - p1)))
    if not np.allclose(p2, p1, rtol=0, atol=1e-12):
        raise AssertionError('Changing predict workers changed fitted predictions')
    example = json.loads(example_path.read_text())[0]
    historical_example = predict_records({**bundle, 'model': one}, [example])[0]
    if historical_example['status'] == 'ABSTAIN':
        # The original example was an untrained CPU condition. Keep its
        # abstention in the record and compare workers on a measured CUDA row.
        if not any(reason.startswith('outside_model_condition_range:')
                   for reason in historical_example['reasons']):
            raise AssertionError('Historical example abstained for an unexpected reason')
        example = primary.loc[primary.device.eq('cuda')].iloc[0].to_dict()
    records = predict_records({**bundle, 'model': one}, [example])
    if records[0]['status'] != 'PREDICTED':
        raise AssertionError('Supported example failed the actual prediction API')
    x1 = pd.DataFrame([example])[bundle['features']]
    if not np.allclose(list(records[0]['prediction'].values()),
                       np.exp(two.predict(x1)[0]), rtol=0, atol=1e-12):
        raise AssertionError('Prediction API changed the supported example')
    candidate = primary.loc[primary.device.eq('cuda')]
    group = candidate.workload_group.iloc[0]
    x8 = candidate.loc[candidate.workload_group.eq(group), bundle['features']]
    if len(x8) != 8:
        raise AssertionError('Expected an eight-candidate GPU slate')
    inputs = {'one_candidate': x1, 'eight_candidate_batch': x8}
    models = {'one_worker': one, 'two_workers': two}
    rng = np.random.default_rng(42)
    timings = {}
    for name, frame in inputs.items():
        for fitted in models.values():
            for _ in range(20):
                fitted.predict(frame)
        values = {key: [] for key in models}
        for _ in range(repetitions):
            for key in rng.permutation(list(models)):
                start = time.perf_counter_ns()
                models[key].predict(frame)
                values[key].append((time.perf_counter_ns() - start) / 1e6)
        timings[name] = {key: {'mean_ms': float(np.mean(v)),
                               'median_ms': float(np.median(v)),
                               'p95_ms': float(np.quantile(v, .95))}
                         for key, v in values.items()}
    result = {
        'scope': 'warm local CPU model.predict only; excludes joblib load, input parsing and JSON',
        'saved_fit_workers': 2, 'optimized_predict_workers': 1,
        'model_sha256': sha(model_path), 'source_sha256': sha(source),
        'example_sha256': sha(example_path),
        'benchmark_script_sha256': sha(Path(__file__)),
        'prediction_script_sha256': sha(ROOT / 'scripts/predict_performance.py'),
        'parity_primary_candidate_rows': int(len(full)),
        'prediction_max_abs_diff': max_abs,
        'prediction_parity_atol': 1e-12,
        'actual_api_example_status': records[0]['status'],
        'historical_example_status': historical_example['status'],
        'historical_example_reasons': historical_example.get('reasons', []),
        'actual_api_example_prediction_parity': True,
        'repetitions_per_method_input': repetitions,
        'versions': {'python': platform.python_version(),
                     'sklearn': sklearn.__version__, 'numpy': np.__version__},
        'timings': timings,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path,
                        default=ROOT / 'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--example', type=Path,
                        default=ROOT / 'artifacts/performance_v1/example_input.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/performance_predict_workers_v1/metrics.json')
    parser.add_argument('--repetitions', type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(run(args.model, args.source, args.example,
                         args.output, args.repetitions), indent=2))
