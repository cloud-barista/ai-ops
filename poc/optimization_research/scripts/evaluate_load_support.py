"""Evaluate a donor-trained loading-time predictor with an explicit support gate.

Only model registration metadata and request task are available at prediction
time. ``load_seconds`` is local cached model/tokenizer initialization time;
it is neither VM boot nor deployment readiness. This is an exploratory
re-analysis of the same SQLite snapshot used in ``evaluate_load_time.py``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor

from evaluate_load_time import (
    BERT_FAMILY, CAMPAIGNS, DISTILBERT, ROOT, SNAPSHOT,
    fit_predict, select_panels, sha256,
)


OUTPUT = ROOT / 'artifacts/load_support_v1'
METHODS = (
    'donor_model_median', 'size_log_ridge', 'size_only_extra_trees',
    'static_log_ridge', 'support_aware_size_tree',
)
MIN_SAME_TASK_DONORS = 2


def donor_metadata(train: pd.DataFrame) -> pd.DataFrame:
    """Each donor model contributes one load median and one task label."""
    if train.empty or (train.load_seconds <= 0).any():
        raise ValueError('Donor loading measurements must be positive and nonempty')
    if (train.groupby('model_id').parameter_gib.nunique() != 1).any():
        raise ValueError('Parameter size differs within a donor model')
    if (train.groupby('model_id').workload_task.nunique() != 1).any():
        raise ValueError('Task differs within a donor model')
    metadata = train.groupby('model_id', as_index=False).agg(
        parameter_gib=('parameter_gib', 'first'),
        workload_task=('workload_task', 'first'),
        median_load_seconds=('load_seconds', 'median'),
    )
    if (metadata.parameter_gib <= 0).any():
        raise ValueError('Donor parameter size must be positive')
    return metadata


def fit_support_bundle(train: pd.DataFrame) -> dict:
    """Fit the original strong size-only tree plus a task-support descriptor."""
    metadata = donor_metadata(train)
    estimator = ExtraTreesRegressor(
        n_estimators=200, min_samples_leaf=1, random_state=42, n_jobs=2,
    ).fit(
        np.log(metadata.parameter_gib.to_numpy()).reshape(-1, 1),
        np.log(metadata.median_load_seconds.to_numpy()),
    )
    task_support = {
        str(task): {
            'model_count': int(len(group)),
            'min_parameter_gib': float(group.parameter_gib.min()),
            'max_parameter_gib': float(group.parameter_gib.max()),
        }
        for task, group in metadata.groupby('workload_task')
    }
    return {
        'estimator': estimator,
        'method': 'support_aware_size_tree',
        'min_same_task_donors': MIN_SAME_TASK_DONORS,
        'task_support': task_support,
        'training_model_ids': metadata.model_id.astype(str).tolist(),
        'training_execution_ids': sorted(train.execution_id.astype(str).tolist()),
        'scope': 'icns host, direct collector v2, CUDA single replica, CPU affinity 2, cached local model/tokenizer loading',
        'registration_metadata_assumption': 'model_parameter_bytes must be supplied before the request; historical source is result_json',
    }


def predict_support(bundle: dict, test: pd.DataFrame) -> pd.DataFrame:
    """Abstain if fewer than two different donor models share the request task."""
    if (test.parameter_gib <= 0).any() or test.parameter_gib.isna().any():
        raise ValueError('Positive model parameter size required')
    if test.workload_task.isna().any():
        raise ValueError('Workload task required')
    raw_prediction = np.exp(bundle['estimator'].predict(
        np.log(test.parameter_gib.to_numpy(dtype=float)).reshape(-1, 1)))
    if not np.isfinite(raw_prediction).all() or (raw_prediction <= 0).any():
        raise ValueError('Nonpositive or nonfinite loading-time prediction')
    status, counts, extrapolations = [], [], []
    for task, size in zip(test.workload_task, test.parameter_gib):
        support = bundle['task_support'].get(str(task), {})
        count = int(support.get('model_count', 0))
        counts.append(count)
        status.append('supported' if count >= bundle['min_same_task_donors']
                      else 'unsupported_task')
        extrapolations.append(bool(count and (
            size < support['min_parameter_gib'] or
            size > support['max_parameter_gib'])))
    answered = np.array(status) == 'supported'
    return pd.DataFrame({
        'support_status': status,
        'same_task_donor_models': counts,
        'outside_same_task_size_range': extrapolations,
        'answered': answered,
        'predicted_load_seconds': np.where(answered, raw_prediction, np.nan),
    }, index=test.index)


def evaluate(panels: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[dict]]:
    main = panels[CAMPAIGNS[0]]
    models = sorted(main.model_id.unique())
    folds = [(f'loomo:{model}', {model}) for model in models]
    folds.extend([
        ('bert4', set(BERT_FAMILY)),
        ('bert4_plus_distilbert', set(BERT_FAMILY + (DISTILBERT,))),
    ])
    predictions, splits = [], []
    for fold_name, held_models in folds:
        donor = main.loc[~main.model_id.isin(held_models)].copy()
        test = pd.concat((p.loc[p.model_id.isin(held_models)]
                          for p in panels.values()), ignore_index=True)
        if set(donor.model_id) & set(test.model_id):
            raise AssertionError('Held-out model entered donor training')
        bundle = fit_support_bundle(donor)
        support = predict_support(bundle, test)
        baseline = fit_predict(donor, test, held_models)
        splits.append({
            'fold': fold_name,
            'held_model_ids': sorted(held_models),
            'donor_model_ids': sorted(donor.model_id.unique().tolist()),
            'train_execution_ids': sorted(donor.execution_id.astype(str).tolist()),
            'train_campaign': CAMPAIGNS[0],
            'evaluation_campaigns': list(CAMPAIGNS),
            'task_support': bundle['task_support'],
        })
        for method in METHODS:
            estimates = (support.predicted_load_seconds.to_numpy()
                         if method == 'support_aware_size_tree'
                         else baseline[method])
            if method == 'support_aware_size_tree':
                np.testing.assert_allclose(
                    estimates[support.answered.to_numpy()],
                    baseline['size_only_extra_trees'][support.answered.to_numpy()],
                    rtol=0, atol=1e-12,
                )
            for i, row in test.iterrows():
                answered = bool(support.loc[i, 'answered'])
                predictions.append({
                    'fold': fold_name,
                    'campaign': str(row.campaign),
                    'execution_id': str(row.execution_id),
                    'model_id': str(row.model_id),
                    'workload_task': str(row.workload_task),
                    'method': method,
                    'parameter_gib': float(row.parameter_gib),
                    'actual_load_seconds': float(row.load_seconds),
                    'predicted_load_seconds': float(estimates[i]) if answered else np.nan,
                    'answered': answered,
                    'support_status': str(support.loc[i, 'support_status']),
                    'same_task_donor_models': int(support.loc[i, 'same_task_donor_models']),
                    'outside_same_task_size_range': bool(
                        support.loc[i, 'outside_same_task_size_range']),
                })
    return pd.DataFrame(predictions), splits


def summarize(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    accepted = predictions.loc[predictions.answered].copy()
    accepted['abs_log_error'] = np.abs(np.log(
        accepted.predicted_load_seconds / accepted.actual_load_seconds))
    accepted['abs_error_seconds'] = np.abs(
        accepted.predicted_load_seconds - accepted.actual_load_seconds)
    per_model = accepted.groupby(
        ['fold', 'campaign', 'method', 'model_id'], as_index=False,
    ).agg(
        n=('abs_log_error', 'size'),
        male=('abs_log_error', 'mean'),
        mae_seconds=('abs_error_seconds', 'mean'),
        outside_size_range=('outside_same_task_size_range', 'first'),
    )
    per_model['evaluation_group'] = np.where(
        per_model.fold.str.startswith('loomo:'), 'loomo', per_model.fold)
    aggregate = per_model.groupby(
        ['evaluation_group', 'campaign', 'method'], as_index=False,
    ).agg(
        answered_models=('model_id', 'nunique'),
        answered_runs=('n', 'sum'),
        male_macro=('male', 'mean'),
        mae_seconds_macro=('mae_seconds', 'mean'),
        extrapolated_answered_models=('outside_size_range', 'sum'),
    )
    total = predictions.drop_duplicates(['fold', 'campaign', 'execution_id']).copy()
    total['evaluation_group'] = np.where(
        total.fold.str.startswith('loomo:'), 'loomo', total.fold)
    opportunities = total.groupby(['evaluation_group', 'campaign'], as_index=False).agg(
        eligible_models=('model_id', 'nunique'),
        eligible_runs=('execution_id', 'size'),
        abstained_models=('model_id', lambda s: ', '.join(sorted(
            set(s[~total.loc[s.index, 'answered']])))),
    )
    aggregate = aggregate.merge(opportunities,
                                on=['evaluation_group', 'campaign'], validate='many_to_one')
    aggregate['model_coverage'] = aggregate.answered_models / aggregate.eligible_models
    aggregate['run_coverage'] = aggregate.answered_runs / aggregate.eligible_runs
    return per_model, aggregate


def paired_model_bootstrap(per_model: pd.DataFrame) -> pd.DataFrame:
    """Exploratory paired uncertainty over distinct held-out models."""
    rows = []
    for (group, campaign), block in per_model.groupby(
            ['evaluation_group', 'campaign']):
        pivot = block.pivot(index='model_id', columns='method', values='male')
        for baseline in ('donor_model_median', 'size_log_ridge',
                         'static_log_ridge'):
            delta = (pivot['support_aware_size_tree'] - pivot[baseline]).to_numpy()
            rng = np.random.default_rng(42)
            draws = rng.choice(delta, size=(50000, len(delta)), replace=True).mean(axis=1)
            rows.append({
                'evaluation_group': group,
                'campaign': campaign,
                'baseline': baseline,
                'models': len(delta),
                'mean_delta_male': float(delta.mean()),
                'q025': float(np.quantile(draws, 0.025)),
                'q975': float(np.quantile(draws, 0.975)),
                'improved_models': int((delta < 0).sum()),
                'interpretation': 'post-hoc paired model bootstrap; no independent validation',
            })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    panels = select_panels(pd.read_parquet(args.snapshot))
    predictions, splits = evaluate(panels)
    per_model, aggregate = summarize(predictions)
    bootstrap = paired_model_bootstrap(per_model)
    args.output.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output / 'predictions.csv', index=False)
    per_model.to_csv(args.output / 'per_model_metrics.csv', index=False)
    aggregate.to_csv(args.output / 'aggregate_metrics.csv', index=False)
    bootstrap.to_csv(args.output / 'paired_model_bootstrap.csv', index=False)
    with (args.output / 'split_manifest.json').open('w') as handle:
        json.dump(splits, handle, indent=2)
    bundle = fit_support_bundle(panels[CAMPAIGNS[0]])
    model_path = args.output / 'support_aware_size_tree_all_main.joblib'
    joblib.dump(bundle, model_path)
    manifest = {
        'scope': bundle['scope'],
        'exploratory_existing_sqlite': True,
        'predictor': 'size-only ExtraTrees plus same-task donor-count gate',
        'min_same_task_donors': MIN_SAME_TASK_DONORS,
        'size_range_policy': 'advisory flag; no default abstention',
        'registration_metadata_assumption': bundle['registration_metadata_assumption'],
        'main_model_count': int(panels[CAMPAIGNS[0]].model_id.nunique()),
        'snapshot_sha256': sha256(args.snapshot),
        'script_sha256': sha256(Path(__file__)),
        'final_model_sha256': sha256(model_path),
        'sklearn_version': sklearn.__version__,
        'pandas_version': pd.__version__,
        'numpy_version': np.__version__,
    }
    with (args.output / 'manifest.json').open('w') as handle:
        json.dump(manifest, handle, indent=2)
    print(aggregate.to_string(index=False))


if __name__ == '__main__':
    main()
