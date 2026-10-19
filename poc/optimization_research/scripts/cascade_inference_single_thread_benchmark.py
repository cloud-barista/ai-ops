"""Post-score timing correction: preconfigure one-thread ExtraTrees outside timer.

The first cascade experiment changed ``n_jobs`` inside its one-thread timing
window. This separate measurement uses a shallow copy of the same fitted model
with ``n_jobs=1`` set before any timed call. It does not rescore outcomes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cascade_inference import (CAMPAIGN, _one_query_rank, _sha, fit_regressors,
                               sorted_slates, support_force, threshold_logs,
                               ROOT, SOURCE, SOURCE_SHA, STATIC)
from cold_start_policy import fold_partitions, prepare
from evaluate_counterfactual_slo import training_threshold_grid


def timed_score(method: str, cheap, strong2, strong1,
                x8: np.ndarray, slo: np.ndarray, forced: bool,
                tau: float) -> tuple[float, bool]:
    started = time.perf_counter_ns()
    if method == 'always_strong_2':
        _one_query_rank(strong2.predict(x8), slo)
        called = True
    elif method == 'always_strong_1':
        _one_query_rank(strong1.predict(x8), slo)
        called = True
    elif method in ('cascade_2', 'cascade_1'):
        _, radius = _one_query_rank(cheap.predict(x8), slo)
        called = forced or radius < tau
        if called:
            model = strong2 if method == 'cascade_2' else strong1
            _one_query_rank(model.predict(x8), slo)
    else:
        raise ValueError(method)
    return (time.perf_counter_ns() - started) / 1e6, bool(called)


def run(snapshot: Path, original: Path, output: Path) -> dict:
    if _sha(snapshot) != SOURCE_SHA:
        raise ValueError('Frozen source SHA mismatch')
    report = json.loads(original.read_text())
    if report['source_sha256'] != SOURCE_SHA:
        raise ValueError('Original score used a different source')
    scoring_file = ROOT / 'scripts/cascade_inference.py'
    if _sha(scoring_file) != report['script_sha256']:
        raise ValueError('Original score code changed since scoring')
    if _sha(ROOT / 'reports/cascade_inference_protocol.md') != report['protocol_sha256']:
        raise ValueError('Original protocol changed since scoring')
    tau_by_fold = {s['split']: float('inf') if s['tau_infinite'] else float(s['tau'])
                   for s in report['selections']}
    main = sorted_slates(prepare(pd.read_parquet(snapshot).query('campaign == @CAMPAIGN')))
    rows, parity = [], []
    methods = ('always_strong_2', 'always_strong_1', 'cascade_2', 'cascade_1')
    for fold_idx, (fold, donors, held) in enumerate(fold_partitions(main)):
        held = sorted_slates(held)
        thresholds = training_threshold_grid(donors)
        limits = threshold_logs(thresholds)
        cheap, strong2 = fit_regressors(donors)
        strong1 = copy.copy(strong2)
        strong1.set_params(n_jobs=1)
        if strong2.n_jobs != 2 or strong1.n_jobs != 1:
            raise AssertionError('Worker configurations were not independent')
        x = held[list(STATIC)].to_numpy(dtype=np.float32)
        p2, p1 = strong2.predict(x), strong1.predict(x)
        max_abs = float(np.max(np.abs(p2 - p1)))
        if not np.allclose(p2, p1, rtol=0, atol=1e-12):
            raise AssertionError('Changing predict workers changed decisions')
        parity.append({'split': fold, 'candidate_rows': len(x),
                       'prediction_max_abs_diff': max_abs})
        g = len(held) // 8
        x = x.reshape(g, 8, len(STATIC))
        forced = support_force(donors, held)
        groups = held.workload_group.to_numpy().reshape(g, 8)[:, 0]
        seed = 42 + fold_idx
        scenario = np.array([int(hashlib.sha256((str(z) + str(seed)).encode()).hexdigest()[:8], 16)
                             % len(thresholds) for z in groups])
        rng = np.random.default_rng(seed)
        # Warm both configured models with the same 8-row input. No set_params
        # or fitting occurs inside any timed window.
        for j in range(min(g, 20)):
            _one_query_rank(cheap.predict(x[j]), limits[scenario[j]])
            _one_query_rank(strong2.predict(x[j]), limits[scenario[j]])
            _one_query_rank(strong1.predict(x[j]), limits[scenario[j]])
        order = np.tile(np.arange(g), 3)
        rng.shuffle(order)
        tau = tau_by_fold[fold]
        for i in order:
            method_order = rng.permutation(methods)
            for method in method_order:
                elapsed, called = timed_score(method, cheap, strong2, strong1,
                                              x[i], limits[scenario[i]],
                                              bool(forced[i]), tau)
                rows.append({'split': fold, 'workload_group': groups[i],
                             'scenario': int(scenario[i]), 'method': method,
                             'latency_ms': elapsed, 'strong_called': called})
        print('timed', fold, flush=True)
    timings = pd.DataFrame(rows)
    fold_summary = timings.groupby(['split', 'method']).latency_ms.agg(
        mean_ms='mean', p50_ms='median',
        p95_ms=lambda x: float(np.quantile(x, .95))).reset_index()
    primary_ids = set(main.model_id.unique())
    pooled = timings.loc[timings.split.isin(primary_ids)]
    summary = {method: {'mean_ms': float(x.latency_ms.mean()),
                        'p50_ms': float(x.latency_ms.median()),
                        'p95_ms': float(x.latency_ms.quantile(.95)),
                        'observations': int(len(x)),
                        'strong_call_rate': float(x.strong_called.mean())}
               for method, x in pooled.groupby('method')}
    corrected_gate = {
        'cascade_2_not_slower_than_strong_1_mean':
            summary['cascade_2']['mean_ms'] <= summary['always_strong_1']['mean_ms'],
        'cascade_2_not_slower_than_strong_1_p95':
            summary['cascade_2']['p95_ms'] <= summary['always_strong_1']['p95_ms'],
        'cascade_1_not_slower_than_strong_1_mean':
            summary['cascade_1']['mean_ms'] <= summary['always_strong_1']['mean_ms'],
        'cascade_1_not_slower_than_strong_1_p95':
            summary['cascade_1']['p95_ms'] <= summary['always_strong_1']['p95_ms'],
    }
    result = {'source_sha256': _sha(snapshot),
              'original_metrics_sha256': _sha(original),
              'original_protocol_sha256': report['protocol_sha256'],
              'original_scoring_script_sha256': report['script_sha256'],
              'correction_script_sha256': _sha(Path(__file__)),
              'reason': 'Original always_strong_single included set_params(n_jobs=1/2) inside timer',
              'scope': 'Post-score warm CPU microbenchmark; no outcome labels or decision retuning',
              'single_vs_parallel_prediction_parity': parity,
              'primary_pooled': summary, 'corrected_comparison': corrected_gate}
    output.mkdir(parents=True, exist_ok=True)
    timings.to_parquet(output / 'fair_warm_timings.parquet', index=False)
    fold_summary.to_csv(output / 'fair_warm_timing_summary.csv', index=False)
    (output / 'fair_warm_metrics.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SOURCE)
    parser.add_argument('--original', type=Path,
                        default=ROOT / 'artifacts/cascade_inference_v1/metrics.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cascade_inference_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.original, args.output)
    print('corrected_comparison', result['corrected_comparison'], flush=True)
