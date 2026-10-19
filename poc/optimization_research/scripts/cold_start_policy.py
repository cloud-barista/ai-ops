"""Zero-probe, model-disjoint static GPU/core selection experiment.

Labels are retrospective batch-benchmark outcomes. Thresholds in each outer
fold come from donor models only. No held-out measurement is a model input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingClassifier

from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, EXTERNAL_CAMPAIGNS, validate_slates


ROOT = Path(__file__).resolve().parents[1]
FAMILY = (
    'google-bert/bert-base-uncased',
    'google/bert_uncased_L-2_H-128_A-2',
    'google/bert_uncased_L-4_H-256_A-4',
    'google/bert_uncased_L-4_H-512_A-8',
)
STATIC = ('log_parameter_bytes', 'log_batch_size', 'log_max_length',
          'log1p_concurrency', 'cpu_core_limit', 'replica_count', 'repeats',
          'is_causal_lm')
DIRECT = STATIC + ('log_latency_limit', 'log_rps_target')
COVERAGES = (0.25, 0.50, 0.75)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach registration-like model size and static features; validate slates."""
    frame = frame.loc[frame.eligible & frame.device.eq('cuda')].copy()
    validate_slates(frame)
    anchors = frame.loc[frame.replica_count.eq(1) & frame.cpu_core_limit.eq(2),
                        ['model_id', 'result_json']].copy()
    anchors['parameter_bytes'] = anchors.result_json.map(
        lambda raw: json.loads(raw)['model_parameter_bytes'])
    size = anchors.groupby('model_id').parameter_bytes.agg(['min', 'max'])
    if (size['min'] != size['max']).any() or (size['min'] <= 0).any():
        raise ValueError('Parameter metadata must be consistent per model')
    frame['parameter_bytes'] = frame.model_id.map(size['min'])
    frame['log_parameter_bytes'] = np.log(frame.parameter_bytes)
    frame['log_batch_size'] = np.log(frame.batch_size)
    frame['log_max_length'] = np.log(frame.max_length)
    frame['log1p_concurrency'] = np.log1p(frame.request_concurrency)
    frame['is_causal_lm'] = frame.workload_task.eq('causal_lm_forward').astype(int)
    frame['true_log_latency'] = np.log(frame.latency_p95_ms)
    frame['true_log_rps'] = np.log(frame.examples_per_second / frame.batch_size)
    if not np.isfinite(frame[list(STATIC) + ['true_log_latency', 'true_log_rps']]
                       .to_numpy(dtype=float)).all():
        raise ValueError('Nonfinite static features or measured outcomes')
    return frame


def fold_partitions(main: pd.DataFrame):
    models = tuple(sorted(main.model_id.unique()))
    if len(models) != 9 or not set(FAMILY).issubset(models):
        raise ValueError('Expected nine local models and four BERT-family models')
    for model in models:
        donors = main.loc[main.model_id.ne(model)]
        held = main.loc[main.model_id.eq(model)]
        assert not set(donors.model_id).intersection(held.model_id)
        yield model, donors, held
    donors = main.loc[~main.model_id.isin(FAMILY)]
    held = main.loc[main.model_id.isin(FAMILY)]
    assert not set(donors.model_id).intersection(held.model_id)
    yield 'bert_family_4', donors, held


def _threshold_array(thresholds: list[dict]) -> np.ndarray:
    return np.array([[np.log(t['latency_slo_ms']), np.log(t['target_rps'])]
                     for t in thresholds], dtype=float)


def expand_direct_features(frame: pd.DataFrame,
                           thresholds: list[dict]) -> np.ndarray:
    base = frame[list(STATIC)].to_numpy(dtype=np.float32)
    limits = _threshold_array(thresholds).astype(np.float32)
    # Scenario-major order: all candidates for each threshold pair.
    x = np.tile(base, (len(limits), 1))
    limits = np.repeat(limits, len(frame), axis=0)
    return np.column_stack((x, limits))


def fit_predict(donors: pd.DataFrame, target: pd.DataFrame,
                thresholds: list[dict], seed: int = 42) -> dict[str, np.ndarray]:
    if set(donors.model_id).intersection(target.model_id):
        raise ValueError('Held-out model leakage')
    x_train = donors[list(STATIC)].to_numpy(dtype=np.float32)
    y_train = donors[['true_log_latency', 'true_log_rps']].to_numpy(dtype=float)
    x_test = target[list(STATIC)].to_numpy(dtype=np.float32)
    regression = ExtraTreesRegressor(n_estimators=200, min_samples_leaf=2,
                                      max_features=1.0, random_state=seed,
                                      n_jobs=2)
    regression.fit(x_train, y_train)
    pred_log = regression.predict(x_test)
    limits = _threshold_array(thresholds)
    scale = np.maximum(np.std(y_train - regression.predict(x_train), axis=0), 0.10)
    raw_margin = np.minimum(
        limits[:, None, 0] - pred_log[None, :, 0],
        pred_log[None, :, 1] - limits[:, None, 1],
    )
    # Residual scales are donor-only. This ranks queries on a dimensionless
    # conservative margin; no held-out outcomes enter the policy.
    reg_score = np.minimum(
        (limits[:, None, 0] - pred_log[None, :, 0]) / scale[0],
        (pred_log[None, :, 1] - limits[:, None, 1]) / scale[1],
    )
    x_direct = expand_direct_features(donors, thresholds)
    truth = np.tile(y_train, (len(thresholds), 1))
    repeated_limits = np.repeat(limits, len(donors), axis=0)
    labels = ((truth[:, 0] <= repeated_limits[:, 0]) &
              (truth[:, 1] >= repeated_limits[:, 1])).astype(int)
    direct = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15,
                                             l2_regularization=2.0,
                                             early_stopping=False,
                                             random_state=seed)
    direct.fit(x_direct, labels)
    x_target = expand_direct_features(target, thresholds)
    probability = direct.predict_proba(x_target)[:, 1].reshape(len(thresholds), len(target))
    return {'direct_classifier': probability,
            'extra_trees_log_margin': raw_margin,
            'extra_trees_scaled_margin': reg_score}


def select_at_coverage(scores: np.ndarray, coverage: float | None,
                       natural_cutoff: float) -> np.ndarray:
    """Select query-wise cheapest candidate, with exact prediction-only yield."""
    if scores.ndim != 2 or scores.shape[1] != 8:
        raise ValueError('Expected query by eight ordered candidates')
    n = len(scores)
    maxima = scores.max(axis=1)
    if coverage is None:
        active = maxima >= natural_cutoff
        cutoff = natural_cutoff
    else:
        if not 0 < coverage <= 1:
            raise ValueError('Coverage must be in (0, 1]')
        k = int(round(coverage * n))
        ranking = np.lexsort((np.arange(n), -maxima))
        active = np.zeros(n, dtype=bool)
        active[ranking[:k]] = True
        cutoff = float(maxima[ranking[k - 1]])
    approved = scores >= cutoff
    # Tied boundary queries are resolved by stable query order, independent
    # of measured outcomes. Every active query has at least one candidate.
    choice = np.argmax(approved, axis=1).astype(int)
    choice[~active] = -1
    if (choice[active] < 0).any() or (coverage is not None and active.sum() != k):
        raise AssertionError('Selection count/candidate invariant failed')
    return choice


def evaluate_scores(target: pd.DataFrame, thresholds: list[dict],
                    predicted_scores: dict[str, np.ndarray],
                    split: str, campaign: str) -> pd.DataFrame:
    slate = target.sort_values(['workload_group', 'replica_count',
                                'cpu_core_limit']).reset_index(drop=True)
    validate_slates(slate)
    groups = slate.workload_group.drop_duplicates().to_numpy()
    g = len(groups)
    if len(slate) != 8 * g:
        raise AssertionError('Eight candidates per workload')
    if slate.groupby('workload_group').model_id.nunique().max() != 1:
        raise ValueError('Model changed within a workload')
    ordered_ids = slate.execution_id.to_numpy()
    target_index = target.reset_index(drop=True).set_index('execution_id').index
    positional = target_index.get_indexer(ordered_ids)
    if (positional < 0).any() or len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError('Target rows cannot be aligned to candidate slate')
    true_l = slate.true_log_latency.to_numpy().reshape(g, 8)
    true_r = slate.true_log_rps.to_numpy().reshape(g, 8)
    limits = _threshold_array(thresholds)
    feasible = ((true_l[None] <= limits[:, None, 0, None]) &
                (true_r[None] >= limits[:, None, 1, None]))
    feasible = feasible.reshape(-1, 8)
    oracle = np.argmax(feasible, axis=1).astype(int)
    oracle[~feasible.any(axis=1)] = -1
    q = len(thresholds) * g
    model_ids = np.tile(slate.model_id.to_numpy().reshape(g, 8)[:, 0], len(thresholds))
    query_groups = np.tile(groups, len(thresholds))
    scenario = np.repeat(np.arange(len(thresholds)), g)
    configs = slate[['replica_count', 'cpu_core_limit']].to_numpy().reshape(g, 8, 2)
    configs = np.tile(configs, (len(thresholds), 1, 1))
    rows = []
    for method, matrix in predicted_scores.items():
        if matrix.shape != (len(thresholds), len(target)):
            raise ValueError('Prediction and target mismatch')
        score = matrix[:, positional].reshape(q, 8)
        if not np.isfinite(score).all():
            raise ValueError('Nonfinite prediction')
        for coverage in (None,) + COVERAGES:
            cutoff = 0.5 if method == 'direct_classifier' else 0.0
            choice = select_at_coverage(score, coverage, cutoff)
            selected = choice >= 0
            correct = np.zeros(q, dtype=bool)
            correct[selected] = feasible[np.flatnonzero(selected), choice[selected]]
            chosen_cfg = np.full((q, 2), -1, dtype=int)
            chosen_cfg[selected] = configs[np.flatnonzero(selected), choice[selected]]
            oracle_cfg = np.full((q, 2), -1, dtype=int)
            present = oracle >= 0
            oracle_cfg[present] = configs[np.flatnonzero(present), oracle[present]]
            rows.append(pd.DataFrame({
                'split': split, 'campaign': campaign, 'model_id': model_ids,
                'workload_group': query_groups, 'scenario': scenario,
                'latency_slo_ms': [thresholds[i]['latency_slo_ms'] for i in scenario],
                'target_rps': [thresholds[i]['target_rps'] for i in scenario],
                'method': method,
                'selection_mode': ('natural' if coverage is None else f'coverage_{coverage:.2f}'),
                'selected_rank': choice, 'oracle_rank': oracle,
                'selected_gpu': chosen_cfg[:, 0], 'selected_cores': chosen_cfg[:, 1],
                'oracle_gpu': oracle_cfg[:, 0], 'oracle_cores': oracle_cfg[:, 1],
                'selected': selected, 'successful': correct,
                'false_approval': selected & ~correct,
                'exact_minimal': correct & (choice == oracle),
                'oracle_feasible': present,
            }))
    return pd.concat(rows, ignore_index=True)


def summarize(choices: pd.DataFrame) -> pd.DataFrame:
    keys = ['split', 'campaign', 'method', 'selection_mode']
    result = choices.groupby(keys, as_index=False).agg(
        queries=('selected', 'size'), selected=('selected', 'sum'),
        successful=('successful', 'sum'), false_approvals=('false_approval', 'sum'),
        exact_minimal=('exact_minimal', 'sum'), oracle_feasible=('oracle_feasible', 'sum'))
    result['false_approval_rate'] = result.false_approvals / result.selected.replace(0, np.nan)
    result['exact_minimal_rate_among_successful'] = (
        result.exact_minimal / result.successful.replace(0, np.nan))
    result['successful_fraction_of_oracle'] = (
        result.successful / result.oracle_feasible.replace(0, np.nan))
    return result


def run(snapshot: Path, output: Path, seed: int = 42) -> dict:
    raw = pd.read_parquet(snapshot)
    main = prepare(raw.loc[raw.campaign.eq(CAMPAIGN)])
    external = {name: prepare(raw.loc[raw.campaign.eq(name)])
                for name in EXTERNAL_CAMPAIGNS}
    parts = []
    fold_info = []
    for fold_name, donors, main_target in fold_partitions(main):
        thresholds = training_threshold_grid(donors)
        target_models = set(main_target.model_id)
        assert not target_models.intersection(donors.model_id)
        campaigns = {CAMPAIGN: main_target}
        campaigns.update({name: data.loc[data.model_id.isin(target_models)]
                          for name, data in external.items()})
        combined = pd.concat(campaigns.values(), ignore_index=True)
        predictions = fit_predict(donors, combined, thresholds, seed)
        for campaign, target in campaigns.items():
            if target.empty:
                continue
            selected = combined.campaign.eq(campaign).to_numpy()
            campaign_predictions = {name: matrix[:, selected]
                                    for name, matrix in predictions.items()}
            parts.append(evaluate_scores(target, thresholds, campaign_predictions,
                                         fold_name, campaign))
        fold_info.append({'split': fold_name, 'train_models': sorted(donors.model_id.unique()),
                          'test_models': sorted(target_models),
                          'threshold_pairs': len(thresholds)})
        print('finished', fold_name, flush=True)
    choices = pd.concat(parts, ignore_index=True)
    aggregate = summarize(choices)
    output.mkdir(parents=True, exist_ok=True)
    choices.to_parquet(output / 'choices.parquet', index=False)
    aggregate.to_csv(output / 'aggregate.csv', index=False)
    metadata = {
        'source_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'python_packages': {'sklearn': sklearn.__version__, 'pandas': pd.__version__,
                            'numpy': np.__version__},
        'split': 'leave-one-model-ID-out and four-BERT-family joint holdout',
        'folds': fold_info,
        'threshold_source': 'five donor-only quantiles on each axis, 25 correlated synthetic batch-SLO pairs',
        'selection': 'GPU count then CPU affinity cores; no price',
        'static_model_size_assumption': 'model_parameter_bytes parsed from historical result_json, treated as registration-time metadata',
        'scope': 'one local host; two physical GPUs are not two VMs; zero held-out probes',
        'aggregation_caveat': 'threshold pairs and workloads from the same model are correlated; no independent user-SLO claim',
    }
    (output / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/cold_start_policy_v1')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    run(args.snapshot, args.output, args.seed)
