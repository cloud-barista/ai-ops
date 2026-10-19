"""Exploratory, nested model-disjoint CPU inference cascade for static slates.

The target is a batch-benchmark SLO decision under an existing allocation. It
is not a user-SLO, deployment, scale-transition, or action-effect estimator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingClassifier
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from cold_start_policy import (FAMILY, ROOT, STATIC, expand_direct_features,
                               fold_partitions, prepare)
from evaluate_counterfactual_slo import training_threshold_grid


CAMPAIGN = 'khu-hf-local-exhaustive'
TAUS = (0.0, 0.02, 0.05, 0.10, 0.20, 0.40, 0.80, float('inf'))
METHODS = ('cascade', 'always_strong', 'always_cheap', 'direct_hgb', 'random_route')
PROTOCOL = ROOT / 'reports/cascade_inference_protocol.md'
SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
SOURCE_SHA = 'b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sorted_slates(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values(['workload_group', 'replica_count', 'cpu_core_limit']).reset_index(drop=True)
    if len(ordered) % 8 or not (ordered.groupby('workload_group').size() == 8).all():
        raise ValueError('Every workload must contain exactly eight candidates')
    configs = ordered[['replica_count', 'cpu_core_limit']].to_numpy().reshape(-1, 8, 2)
    expected = np.array([(gpu, core) for gpu in (1, 2) for core in (2, 4, 8, 16)])
    if not (configs == expected).all():
        raise ValueError('Candidate order or completeness changed')
    return ordered


def fit_regressors(donors: pd.DataFrame):
    x = donors[list(STATIC)].to_numpy(dtype=np.float32)
    y = donors[['true_log_latency', 'true_log_rps']].to_numpy(dtype=float)
    cheap = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    cheap.fit(x, y)
    strong = ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2,
                                  max_features=1.0, random_state=42, n_jobs=2)
    strong.fit(x, y)
    return cheap, strong


def fit_direct(donors: pd.DataFrame, thresholds: list[dict]):
    x = expand_direct_features(donors, thresholds)
    logs = donors[['true_log_latency', 'true_log_rps']].to_numpy(dtype=float)
    t = np.array([[np.log(x['latency_slo_ms']), np.log(x['target_rps'])]
                  for x in thresholds])
    truth = np.tile(logs, (len(t), 1))
    limits = np.repeat(t, len(donors), axis=0)
    y = ((truth[:, 0] <= limits[:, 0]) & (truth[:, 1] >= limits[:, 1])).astype(int)
    model = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15,
                                            l2_regularization=2.0,
                                            early_stopping=False, random_state=42)
    model.fit(x, y)
    return model


def threshold_logs(thresholds: list[dict]) -> np.ndarray:
    return np.log(np.array([[x['latency_slo_ms'], x['target_rps']]
                            for x in thresholds], dtype=float))


def margin_matrix(logs: np.ndarray, limits: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return latency/RPS margins and their minimum for Q x G x 8 slates."""
    if logs.ndim != 3 or logs.shape[1] != 8 or logs.shape[2] != 2:
        raise ValueError('Expected G x 8 x 2 log predictions')
    a = limits[:, None, None, 0] - logs[None, :, :, 0]
    b = logs[None, :, :, 1] - limits[:, None, None, 1]
    return a, b, np.minimum(a, b)


def first_pass(margin: np.ndarray) -> np.ndarray:
    pass_mask = margin >= 0
    choice = np.argmax(pass_mask, axis=-1).astype(np.int16)
    choice[~pass_mask.any(axis=-1)] = -1
    return choice


def decision_radius(a: np.ndarray, b: np.ndarray, choice: np.ndarray) -> np.ndarray:
    """Max equal log-error that preserves the first feasible candidate.

    For a failed candidate, max(-a,-b) is its distance to becoming feasible:
    at least one constraint still fails under any smaller uniform error.
    """
    if a.shape != b.shape or a.shape[-1] != 8 or choice.shape != a.shape[:-1]:
        raise ValueError('Inconsistent margin/rank dimensions')
    q, g, _ = a.shape
    failure = np.maximum(-a, -b)
    rank = np.arange(8)[None, None, :]
    cheaper = rank < choice[:, :, None]
    preceding_radius = np.min(np.where(cheaper, failure, np.inf), axis=-1)
    selected_rank = np.maximum(choice, 0)
    selected_a = np.take_along_axis(a, selected_rank[:, :, None], axis=-1)[:, :, 0]
    selected_b = np.take_along_axis(b, selected_rank[:, :, None], axis=-1)[:, :, 0]
    approved_radius = np.minimum(np.minimum(selected_a, selected_b), preceding_radius)
    abstain_radius = np.min(failure, axis=-1)
    radius = np.where(choice >= 0, approved_radius, abstain_radius)
    if radius.shape != (q, g) or (radius < -1e-9).any() or not np.isfinite(radius).all():
        raise AssertionError('Invalid decision radius')
    return radius


def support_force(donors: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """Force strong scoring outside donor task kinds or parameter-byte range."""
    min_size = donors.log_parameter_bytes.min()
    max_size = donors.log_parameter_bytes.max()
    donor_tasks = set(donors.workload_task.unique())
    rows = target.iloc[::8]
    return ((~rows.workload_task.isin(donor_tasks)) |
            (rows.log_parameter_bytes < min_size) |
            (rows.log_parameter_bytes > max_size)).to_numpy(dtype=bool)


def prediction_bundle(donors: pd.DataFrame, target: pd.DataFrame,
                      thresholds: list[dict], models=None) -> dict:
    """All predictions depend only on donor outcomes and target static inputs."""
    if set(donors.model_id).intersection(target.model_id):
        raise ValueError('Target model leaked into donor fit')
    target = sorted_slates(target)
    cheap, strong = fit_regressors(donors) if models is None else models
    x = target[list(STATIC)].to_numpy(dtype=np.float32)
    g = len(target) // 8
    limits = threshold_logs(thresholds)
    cheap_log = cheap.predict(x).reshape(g, 8, 2)
    strong_log = strong.predict(x).reshape(g, 8, 2)
    a, b, cm = margin_matrix(cheap_log, limits)
    _, _, sm = margin_matrix(strong_log, limits)
    cc = first_pass(cm)
    sc = first_pass(sm)
    radius = decision_radius(a, b, cc)
    force = support_force(donors, target)
    return {'target': target, 'cheap_model': cheap, 'strong_model': strong,
            'x': x, 'limits': limits, 'cheap_log': cheap_log,
            'strong_log': strong_log, 'cheap_margin': cm,
            'strong_margin': sm, 'cheap_choice': cc, 'strong_choice': sc,
            'radius': radius, 'support_force': force}


def truth_for(target: pd.DataFrame, limits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    g = len(target) // 8
    true_l = target.true_log_latency.to_numpy().reshape(g, 8)
    true_r = target.true_log_rps.to_numpy().reshape(g, 8)
    feasible = ((true_l[None] <= limits[:, None, 0, None]) &
                (true_r[None] >= limits[:, None, 1, None]))
    return feasible, first_pass(np.where(feasible, 1.0, -1.0))


def measure_choice(choice: np.ndarray, feasible: np.ndarray,
                   oracle: np.ndarray) -> dict[str, int | float]:
    if choice.shape != oracle.shape or feasible.shape != choice.shape + (8,):
        raise ValueError('Choice and truth shapes differ')
    n = choice.size
    approved = choice >= 0
    picked = feasible.reshape(n, 8)[np.arange(n), np.maximum(choice.ravel(), 0)].reshape(choice.shape)
    successful = approved & picked
    false = approved & ~picked
    exact = successful & (choice == oracle)
    return {'queries': n, 'approved': int(approved.sum()), 'abstain': int((~approved).sum()),
            'successful': int(successful.sum()), 'false_approval': int(false.sum()),
            'exact_minimal': int(exact.sum()),
            'oracle_feasible': int((oracle >= 0).sum()),
            'feasible_abstain': int(((oracle >= 0) & ~approved).sum()),
            'false_rate': float(false.sum() / approved.sum()) if approved.any() else 0.0}


def route_choice(bundle: dict, tau: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    force = bundle['support_force'][None, :]
    route = (bundle['radius'] < tau) | force
    choice = np.where(route, bundle['strong_choice'], bundle['cheap_choice'])
    margin = np.where(route[:, :, None], bundle['strong_margin'], bundle['cheap_margin'])
    return choice, margin, route


def inner_record(donors: pd.DataFrame, target: pd.DataFrame, thresholds: list[dict],
                 models=None) -> dict:
    bundle = prediction_bundle(donors, target, thresholds, models)
    feasible, oracle = truth_for(bundle['target'], bundle['limits'])
    return {'bundle': bundle, 'feasible': feasible, 'oracle': oracle}


def select_tau(inner: list[dict]) -> tuple[float, list[dict]]:
    """Select a gate with no outer held-out outcome access."""
    evaluations = []
    baseline = [measure_choice(r['bundle']['strong_choice'], r['feasible'], r['oracle'])
                for r in inner]
    base_false = sum(v['false_approval'] for v in baseline)
    base_exact = sum(v['exact_minimal'] for v in baseline)
    for tau in TAUS:
        rows = []
        routes = []
        for record in inner:
            choice, _, route = route_choice(record['bundle'], tau)
            rows.append(measure_choice(choice, record['feasible'], record['oracle']))
            routes.append(route)
        false_count = sum(v['false_approval'] for v in rows)
        exact = sum(v['exact_minimal'] for v in rows)
        rate = float(sum(r.sum() for r in routes) / sum(r.size for r in routes))
        per_model_ok = all(
            v['false_rate'] <= b['false_rate'] + 0.02 + 1e-12
            for v, b in zip(rows, baseline))
        admissible = (false_count <= base_false and exact >= 0.99 * base_exact
                      and per_model_ok)
        evaluations.append({'tau': None if np.isinf(tau) else tau,
                            'tau_infinite': bool(np.isinf(tau)),
                            'strong_call_rate': rate,
                            'false_approval': false_count,
                            'exact_minimal': exact,
                            'baseline_false_approval': base_false,
                            'baseline_exact_minimal': base_exact,
                            'per_model_ok': per_model_ok,
                            'admissible': bool(admissible)})
    eligible = [e for e in evaluations if e['admissible']]
    selected = min(eligible, key=lambda e: (e['strong_call_rate'],
                                           -float('inf') if e['tau_infinite'] else -e['tau']))
    return float('inf') if selected['tau_infinite'] else float(selected['tau']), evaluations


def equal_approval(choice_margin: np.ndarray, target_n: int,
                   feasible: np.ndarray, oracle: np.ndarray) -> dict:
    """Posthoc equal-count *diagnostic*, never an online cutoff."""
    q, g, _ = choice_margin.shape
    flat = choice_margin.reshape(q * g, 8)
    maxima = flat.max(axis=1)
    order = np.lexsort((np.arange(len(maxima)), -maxima))
    active = np.zeros(len(maxima), dtype=bool)
    active[order[:target_n]] = True
    threshold = maxima[order[target_n - 1]] if target_n else float('inf')
    chosen = np.argmax(flat >= threshold, axis=1).astype(np.int16)
    chosen[~active] = -1
    return measure_choice(chosen.reshape(q, g), feasible, oracle)


def score_direct(model, target: pd.DataFrame, thresholds: list[dict]) -> np.ndarray:
    # Expand in threshold-major order, then align each slate by the pre-sort.
    x = expand_direct_features(target, thresholds)
    q, g = len(thresholds), len(target) // 8
    return model.predict_proba(x)[:, 1].reshape(q, g, 8)


def _random_route(shape: tuple[int, int], count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    order = rng.permutation(shape[0] * shape[1])
    route = np.zeros(shape[0] * shape[1], dtype=bool)
    route[order[:count]] = True
    return route.reshape(shape)


def score_outer(bundle: dict, direct_model, thresholds: list[dict],
                tau: float, seed: int) -> tuple[dict, pd.DataFrame]:
    feasible, oracle = truth_for(bundle['target'], bundle['limits'])
    cascade_choice, cascade_margin, route = route_choice(bundle, tau)
    direct_score = score_direct(direct_model, bundle['target'], thresholds)
    direct_choice = first_pass(direct_score - 0.5)
    rng_route = _random_route(route.shape, int(route.sum()), seed)
    random_choice = np.where(rng_route, bundle['strong_choice'], bundle['cheap_choice'])
    random_margin = np.where(rng_route[:, :, None],
                             bundle['strong_margin'], bundle['cheap_margin'])
    choices = {'cascade': (cascade_choice, cascade_margin),
               'always_strong': (bundle['strong_choice'], bundle['strong_margin']),
               'always_cheap': (bundle['cheap_choice'], bundle['cheap_margin']),
               'direct_hgb': (direct_choice, direct_score - 0.5),
               'random_route': (random_choice, random_margin)}
    results = {}
    target_count = int((bundle['strong_choice'] >= 0).sum())
    for method, (choice, margin) in choices.items():
        metric = measure_choice(choice, feasible, oracle)
        metric['strong_call_rate'] = (1.0 if method == 'always_strong' else
                                      0.0 if method in ('always_cheap', 'direct_hgb') else
                                      float(route.mean()))
        metric['equal_approval'] = equal_approval(margin, target_count, feasible, oracle)
        results[method] = metric
    g = len(bundle['target']) // 8
    query_groups = bundle['target'].workload_group.to_numpy().reshape(g, 8)[:, 0]
    model_ids = bundle['target'].model_id.to_numpy().reshape(g, 8)[:, 0]
    rows = []
    for method, (choice, margin) in choices.items():
        for j in range(len(thresholds)):
            c = choice[j]
            f = feasible[j, np.arange(g), np.maximum(c, 0)]
            rows.append(pd.DataFrame({
                'method': method, 'workload_group': query_groups,
                'model_id': model_ids, 'scenario': j,
                'selected_rank': c, 'oracle_rank': oracle[j],
                'selected': c >= 0, 'successful': (c >= 0) & f,
                'false_approval': (c >= 0) & ~f,
                'exact_minimal': (c >= 0) & f & (c == oracle[j]),
                'strong_called': (route[j] if method == 'cascade' else
                                  rng_route[j] if method == 'random_route' else
                                  np.full(g, method == 'always_strong')),
                'score_max': margin[j].max(axis=1),
            }))
    return results, pd.concat(rows, ignore_index=True)


def _one_query_rank(logs: np.ndarray, limits: np.ndarray) -> tuple[int, float]:
    a = limits[0] - logs[:, 0]
    b = logs[:, 1] - limits[1]
    m = np.minimum(a, b)
    good = m >= 0
    rank = int(np.argmax(good)) if good.any() else -1
    fail = np.maximum(-a, -b)
    if rank < 0:
        return rank, float(fail.min())
    before = float(fail[:rank].min()) if rank else float('inf')
    return rank, float(min(a[rank], b[rank], before))


def benchmark(bundle: dict, direct_model, thresholds: list[dict],
              tau: float, fold: str, seed: int) -> pd.DataFrame:
    """Warm one-workload/one-SLO timing; full 8-row predict on each call."""
    target = bundle['target']
    g = len(target) // 8
    x = bundle['x'].reshape(g, 8, len(STATIC))
    limits = bundle['limits']
    groups = target.workload_group.to_numpy().reshape(g, 8)[:, 0]
    scenario = np.array([int(hashlib.sha256((str(z) + str(seed)).encode()).hexdigest()[:8], 16)
                         % len(thresholds) for z in groups])
    rng = np.random.default_rng(seed)
    order = np.tile(np.arange(g), 3)
    rng.shuffle(order)
    direct_x = expand_direct_features(target, thresholds).reshape(len(thresholds), g, 8, -1)
    # One full warm call for each trained model before recording timings.
    for j in range(min(g, 20)):
        _ = bundle['cheap_model'].predict(x[j])
        _ = bundle['strong_model'].predict(x[j])
        _ = direct_model.predict_proba(direct_x[scenario[j], j])
    strong_model = bundle['strong_model']
    strong_parallel = strong_model.predict(x[0])
    strong_model.set_params(n_jobs=1)
    strong_single = strong_model.predict(x[0])
    strong_model.set_params(n_jobs=2)
    if not np.allclose(strong_parallel, strong_single, rtol=0, atol=1e-12):
        raise AssertionError('Strong 1-thread and 2-thread predictions differ')
    observations = []
    for i in order:
        x8, slo = x[i], limits[scenario[i]]
        forced = bool(bundle['support_force'][i])
        for method in ('always_strong', 'always_strong_single',
                       'always_cheap', 'cascade', 'direct_hgb'):
            started = time.perf_counter_ns()
            strong_called = False
            if method == 'always_strong':
                logs = bundle['strong_model'].predict(x8)
                _one_query_rank(logs, slo)
                strong_called = True
            elif method == 'always_strong_single':
                strong_model.set_params(n_jobs=1)
                try:
                    logs = strong_model.predict(x8)
                finally:
                    strong_model.set_params(n_jobs=2)
                _one_query_rank(logs, slo)
                strong_called = True
            elif method == 'always_cheap':
                logs = bundle['cheap_model'].predict(x8)
                _one_query_rank(logs, slo)
            elif method == 'direct_hgb':
                prob = direct_model.predict_proba(direct_x[scenario[i], i])[:, 1]
                _ = first_pass((prob - 0.5).reshape(1, 1, 8))
            else:
                logs = bundle['cheap_model'].predict(x8)
                _, radius = _one_query_rank(logs, slo)
                if forced or radius < tau:
                    logs = bundle['strong_model'].predict(x8)
                    _one_query_rank(logs, slo)
                    strong_called = True
            elapsed = (time.perf_counter_ns() - started) / 1e6
            observations.append({'split': fold, 'workload_group': groups[i],
                                 'scenario': int(scenario[i]), 'method': method,
                                 'latency_ms': elapsed, 'strong_called': strong_called})
    return pd.DataFrame(observations)


def run(snapshot: Path, output: Path) -> dict:
    if _sha(snapshot) != SOURCE_SHA:
        raise ValueError('Frozen measurement SHA mismatch')
    protocol_sha = _sha(PROTOCOL)
    raw = pd.read_parquet(snapshot)
    main = sorted_slates(prepare(raw.loc[raw.campaign.eq(CAMPAIGN)]))
    model_frames = {name: main.loc[main.model_id.eq(name)].copy()
                    for name in sorted(main.model_id.unique())}
    pair_cache = {}
    all_metrics, all_predictions, all_timings, selections = [], [], [], []
    for fold_idx, (fold, donors, held) in enumerate(fold_partitions(main)):
        held = sorted_slates(held)
        donor_ids = tuple(sorted(donors.model_id.unique()))
        inner = []
        for d in donor_ids:
            if fold in model_frames:  # Pair cache is safe only for one-model outer folds.
                key = tuple(sorted((fold, d)))
                if key not in pair_cache:
                    train = main.loc[~main.model_id.isin(key)]
                    pair_cache[key] = fit_regressors(train)
                inner_models = pair_cache[key]
            else:
                inner_models = None
            inner_train = donors.loc[donors.model_id.ne(d)]
            inner_t = training_threshold_grid(inner_train)
            inner.append(inner_record(inner_train, model_frames[d], inner_t, inner_models))
        tau, candidates = select_tau(inner)
        thresholds = training_threshold_grid(donors)
        models = fit_regressors(donors)
        bundle = prediction_bundle(donors, held, thresholds, models)
        direct = fit_direct(donors, thresholds)
        metrics, pred = score_outer(bundle, direct, thresholds, tau, 42 + fold_idx)
        timing = benchmark(bundle, direct, thresholds, tau, fold, 42 + fold_idx)
        pred.insert(0, 'split', fold)
        all_predictions.append(pred)
        all_timings.append(timing)
        for method, values in metrics.items():
            all_metrics.append({'split': fold, 'method': method, **values})
        selections.append({'split': fold, 'donor_model_ids': list(donor_ids),
                           'heldout_model_ids': sorted(held.model_id.unique()),
                           'tau': None if np.isinf(tau) else tau,
                           'tau_infinite': bool(np.isinf(tau)),
                           'inner_candidates': candidates})
        print('finished', fold, 'tau', tau, flush=True)
    predictions = pd.concat(all_predictions, ignore_index=True)
    timings = pd.concat(all_timings, ignore_index=True)
    timing_summary = timings.groupby(['split', 'method']).latency_ms.agg(
        mean_ms='mean', p50_ms='median',
        p95_ms=lambda s: float(np.quantile(s, 0.95))).reset_index()
    timing_summary['observations'] = timings.groupby(['split', 'method']).size().to_numpy()
    primary = [m for m in all_metrics if m['split'] in model_frames]
    aggregate = {}
    for method in METHODS:
        rows = [m for m in primary if m['method'] == method]
        counts = {key: sum(int(r[key]) for r in rows) for key in
                  ('queries', 'approved', 'abstain', 'successful', 'false_approval',
                   'exact_minimal', 'oracle_feasible', 'feasible_abstain')}
        counts['model_equal_strong_call_rate'] = float(np.mean(
            [r['strong_call_rate'] for r in rows]))
        counts['query_weighted_strong_call_rate'] = float(
            sum(r['strong_call_rate'] * r['queries'] for r in rows) /
            sum(r['queries'] for r in rows))
        counts['equal_approval_false'] = sum(r['equal_approval']['false_approval'] for r in rows)
        counts['equal_approval_exact'] = sum(r['equal_approval']['exact_minimal'] for r in rows)
        aggregate[method] = counts
    all_timing = timings.loc[timings.split.isin(model_frames)]
    pooled_timing = {method: {'mean_ms': float(x.latency_ms.mean()),
                              'p50_ms': float(x.latency_ms.median()),
                              'p95_ms': float(x.latency_ms.quantile(0.95)),
                              'n': int(len(x))}
                     for method, x in all_timing.groupby('method')}
    c, s = aggregate['cascade'], aggregate['always_strong']
    tc, ts = pooled_timing['cascade'], pooled_timing['always_strong']
    t1 = pooled_timing['always_strong_single']
    per_model_rate_ok = all(
        r['false_rate'] <= next(x for x in primary if x['split'] == r['split']
                                and x['method'] == 'always_strong')['false_rate'] + 0.02 + 1e-12
        for r in primary if r['method'] == 'cascade')
    gate = {
        'mean_reduction_at_least_25pct': tc['mean_ms'] <= 0.75 * ts['mean_ms'],
        'p95_reduction_at_least_20pct': tc['p95_ms'] <= 0.80 * ts['p95_ms'],
        'not_slower_than_one_thread_mean': tc['mean_ms'] <= t1['mean_ms'],
        'not_slower_than_one_thread_p95': tc['p95_ms'] <= t1['p95_ms'],
        'natural_false_approval_nonincrease': c['false_approval'] <= s['false_approval'],
        'exact_minimal_at_least_99pct': c['exact_minimal'] >= 0.99 * s['exact_minimal'],
        'equal_approval_false_nonincrease': c['equal_approval_false'] <= s['equal_approval_false'],
        'each_model_false_rate_within_2pp': per_model_rate_ok,
    }
    gate['all_pass'] = bool(all(gate.values()))
    report = {'source_sha256': _sha(snapshot), 'protocol_sha256': protocol_sha,
              'script_sha256': _sha(Path(__file__)),
              'versions': {'python_sklearn': sklearn.__version__,
                           'numpy': np.__version__, 'pandas': pd.__version__},
              'scope': 'Exploratory static batch-SLO decision on known SQLite; no action effect',
              'folds': all_metrics, 'selections': selections,
              'primary_aggregate': aggregate, 'primary_warm_timing': pooled_timing,
              'adoption_gate': gate}
    output.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(output / 'choices.parquet', index=False)
    timings.to_parquet(output / 'warm_timings.parquet', index=False)
    timing_summary.to_csv(output / 'warm_timing_summary.csv', index=False)
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cascade_inference_v1')
    args = parser.parse_args()
    result = run(args.snapshot, args.output)
    print('adoption_gate', result['adoption_gate'], flush=True)
