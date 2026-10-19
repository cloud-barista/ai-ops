"""Train and evaluate local model/tokenizer loading-time predictors.

`load_seconds` ends before the model is transferred to a GPU.  This is a
cached local model initialization measurement, not VM boot or deployment
completion.  Parameter bytes are used as registration-time metadata by
assumption; all other inputs are available before the execution begins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.optimize import nnls
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'data/audit_v1/measurements.parquet'
OUTPUT = ROOT / 'artifacts/load_time_v1'
CAMPAIGNS = (
    'khu-hf-local-exhaustive',
    'khu-hf-local-novel-matrix',
    'khu-hf-local-space-filling',
)
BERT_FAMILY = (
    'google/bert_uncased_L-2_H-128_A-2',
    'google/bert_uncased_L-4_H-256_A-4',
    'google/bert_uncased_L-4_H-512_A-8',
    'google-bert/bert-base-uncased',
)
DISTILBERT = 'distilbert/distilbert-base-uncased'
METHODS = (
    'donor_model_median',
    'size_affine_nnls',
    'size_log_ridge',
    'size_only_extra_trees',
    'static_log_ridge',
    'static_log_extra_trees',
)
FEATURES = (
    'log_parameter_gib', 'log_batch_size', 'log_max_length',
    'log1p_request_concurrency', 'is_causal_lm',
    'log_batch_x_log_length',
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def select_panels(snapshot: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Select one comparable local host, collector, GPU and CPU profile."""
    panels = {}
    for campaign in CAMPAIGNS:
        panel = snapshot.loc[
            (snapshot.campaign == campaign) &
            (snapshot.host == 'icns') &
            (snapshot.collector_version == 'khu-hf-direct-collector/v2') &
            (snapshot.device == 'cuda') &
            (snapshot.replica_count == 1) &
            (snapshot.cpu_core_limit == 2) &
            (snapshot.success == 1) &
            (snapshot.eligible == True)
        ].copy()
        if panel.empty:
            raise ValueError(f'No comparable selected data for {campaign}')
        panel['parameter_gib'] = panel.result_json.map(
            lambda raw: float(json.loads(raw)['model_parameter_bytes']) / 1024.0**3)
        numeric = ['parameter_gib', 'batch_size', 'max_length',
                   'request_concurrency', 'load_seconds', 'wall_seconds']
        if (panel[numeric].isna().any().any() or
                (~np.isfinite(panel[numeric].to_numpy(dtype=float))).any() or
                (panel[numeric] <= 0).any().any()):
            raise ValueError(f'Nonpositive or missing static input/load time in {campaign}')
        if (panel.load_seconds > panel.wall_seconds).any():
            raise ValueError(f'Load time exceeds total execution wall time in {campaign}')
        if panel.duplicated(['model_id', 'workload_group']).any():
            raise ValueError(f'Duplicate model/workload in {campaign}')
        panels[campaign] = panel
    metadata = pd.concat([p[['model_id', 'model_revision', 'parameter_gib']]
                          for p in panels.values()], ignore_index=True)
    if (metadata.groupby(['model_id', 'model_revision']).parameter_gib.nunique() != 1).any():
        raise ValueError('Parameter metadata changes for a model/revision')
    if (metadata.groupby('model_id').model_revision.nunique() != 1).any():
        raise ValueError('Multiple revisions of one model in selected campaigns')
    return panels


def static_features(frame: pd.DataFrame) -> np.ndarray:
    """Feature matrix using registration and requested-workload fields only."""
    size = np.log(frame.parameter_gib.to_numpy(dtype=float))
    batch = np.log(frame.batch_size.to_numpy(dtype=float))
    length = np.log(frame.max_length.to_numpy(dtype=float))
    concurrency = np.log1p(frame.request_concurrency.to_numpy(dtype=float))
    causal = frame.workload_task.astype(str).eq('causal_lm_forward').to_numpy(dtype=float)
    return np.column_stack((size, batch, length, concurrency, causal,
                            batch * length))


def final_features(frame: pd.DataFrame, method: str) -> np.ndarray:
    if method == 'size_only_extra_trees':
        return np.log(frame.parameter_gib.to_numpy(dtype=float)).reshape(-1, 1)
    return static_features(frame)


def fit_predict(train: pd.DataFrame, test: pd.DataFrame,
                held_ids: set[str]) -> dict[str, np.ndarray]:
    """Fit only donor measurements; test outcomes are never model inputs."""
    if train.empty or test.empty:
        raise ValueError('Donor and held-out panels must be nonempty')
    if set(train.model_id).intersection(held_ids):
        raise ValueError('Held-out model entered donor training')
    if not set(test.model_id).issubset(held_ids):
        raise ValueError('Test panel includes an unheld model')
    if set(zip(train.model_id, train.model_revision)) & set(zip(test.model_id, test.model_revision)):
        raise ValueError('Same model/revision appears in train and test')
    if (train.load_seconds <= 0).any():
        raise ValueError('Donor load targets must be positive')

    model_medians = train.groupby('model_id', as_index=False).agg(
        parameter_gib=('parameter_gib', 'first'),
        median_load_seconds=('load_seconds', 'median'))
    train_size = model_medians.parameter_gib.to_numpy(dtype=float)
    test_size = test.parameter_gib.to_numpy(dtype=float)
    donor_log = np.log(model_medians.median_load_seconds.to_numpy(dtype=float))
    x_train = static_features(train)
    x_test = static_features(test)

    # Affine nonnegative loading-time = fixed overhead + bytes / rate.  Each
    # donor model contributes one median, so 64 work shapes do not make one
    # architecture appear to be 64 independent model-loading examples.
    (fixed_overhead, seconds_per_gib), _residual = nnls(
        np.column_stack((np.ones(len(train_size)), train_size)),
        model_medians.median_load_seconds.to_numpy(dtype=float))
    size_log_model = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(
        np.log(train_size).reshape(-1, 1), donor_log)
    size_trees = ExtraTreesRegressor(
        n_estimators=200, min_samples_leaf=1, random_state=42, n_jobs=2).fit(
            np.log(train_size).reshape(-1, 1), donor_log)
    static_ridge = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(
        x_train, np.log(train.load_seconds.to_numpy(dtype=float)))
    static_trees = ExtraTreesRegressor(
        n_estimators=200, min_samples_leaf=2, random_state=42, n_jobs=2).fit(
            x_train, np.log(train.load_seconds.to_numpy(dtype=float)))
    outputs = {
        'donor_model_median': np.full(len(test),
                                      float(np.median(model_medians.median_load_seconds))),
        'size_affine_nnls': np.maximum(fixed_overhead + seconds_per_gib * test_size, 0.01),
        'size_log_ridge': np.exp(size_log_model.predict(np.log(test_size).reshape(-1, 1))),
        'size_only_extra_trees': np.exp(size_trees.predict(
            np.log(test_size).reshape(-1, 1))),
        'static_log_ridge': np.exp(static_ridge.predict(x_test)),
        'static_log_extra_trees': np.exp(static_trees.predict(x_test)),
    }
    for method, values in outputs.items():
        if len(values) != len(test) or not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError(f'Invalid {method} loading-time forecast')
    return outputs


def evaluate(panels: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[dict]]:
    main = panels[CAMPAIGNS[0]]
    models = sorted(main.model_id.unique())
    if set(BERT_FAMILY + (DISTILBERT,)) - set(models):
        raise ValueError('Expected BERT holdout family missing')
    folds = [(f'loomo:{model}', {model}) for model in models]
    folds += [('bert4', set(BERT_FAMILY)),
              ('bert4_plus_distilbert', set(BERT_FAMILY + (DISTILBERT,)))]
    rows = []
    manifest = []
    for fold_name, held_ids in folds:
        train = main.loc[~main.model_id.isin(held_ids)].copy()
        if set(train.model_id) & held_ids:
            raise AssertionError('Held model leaked into donor')
        manifest.append({
            'fold': fold_name,
            'train_campaign': CAMPAIGNS[0],
            'train_model_ids': sorted(train.model_id.unique().tolist()),
            'held_model_ids': sorted(held_ids),
            'train_execution_ids': sorted(train.execution_id.astype(str).tolist()),
            'test_campaigns': list(CAMPAIGNS),
        })
        test = pd.concat((panel.loc[panel.model_id.isin(held_ids)]
                          for panel in panels.values()), ignore_index=True)
        output = fit_predict(train, test, held_ids)
        for method, prediction in output.items():
            rows.extend({
                    'fold': fold_name,
                    'campaign': campaign,
                    'execution_id': execution_id,
                    'model_id': model_id,
                    'model_revision': revision,
                    'workload_group': workload,
                    'method': method,
                    'parameter_gib': float(size),
                    'actual_load_seconds': float(actual),
                    'predicted_load_seconds': float(estimate),
                    'wall_seconds': float(wall),
                } for campaign, execution_id, model_id, revision, workload,
                      size, actual, estimate, wall in zip(
                          test.campaign, test.execution_id, test.model_id,
                          test.model_revision, test.workload_group,
                          test.parameter_gib, test.load_seconds,
                          prediction, test.wall_seconds))
    return pd.DataFrame(rows), manifest


def summarize(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = predictions.copy()
    p['abs_log_error'] = np.abs(np.log(p.predicted_load_seconds / p.actual_load_seconds))
    p['abs_error_seconds'] = np.abs(p.predicted_load_seconds - p.actual_load_seconds)
    p['underestimated'] = (p.predicted_load_seconds < p.actual_load_seconds).astype(float)
    per_model = p.groupby(['fold', 'campaign', 'method', 'model_id'], as_index=False).agg(
        n=('abs_log_error', 'size'),
        male=('abs_log_error', 'mean'),
        mae_seconds=('abs_error_seconds', 'mean'),
        p90_abs_error_seconds=('abs_error_seconds', lambda x: float(x.quantile(0.9))),
        underestimated_fraction=('underestimated', 'mean'),
        median_actual_seconds=('actual_load_seconds', 'median'),
    )
    aggregate = per_model.groupby(['fold', 'campaign', 'method'], as_index=False).agg(
        models=('model_id', 'nunique'),
        n=('n', 'sum'),
        male_macro=('male', 'mean'),
        mae_seconds_macro=('mae_seconds', 'mean'),
        underestimated_fraction_macro=('underestimated_fraction', 'mean'),
    )
    return per_model, aggregate


def paired_model_bootstrap(per_model: pd.DataFrame) -> pd.DataFrame:
    """Exploratory model-level uncertainty, preserving the nine holdout units."""
    selected = per_model.loc[per_model.fold.str.startswith('loomo:')]
    pivot = selected.pivot(index='model_id', columns=['campaign', 'method'], values='male')
    rows = []
    for campaign in CAMPAIGNS:
        for candidate in ('size_only_extra_trees', 'static_log_extra_trees'):
            for baseline in ('donor_model_median', 'static_log_ridge',
                             'size_log_ridge', 'size_only_extra_trees'):
                if candidate == baseline:
                    continue
                delta = (pivot[(campaign, candidate)] -
                         pivot[(campaign, baseline)]).to_numpy(dtype=float)
                rng = np.random.default_rng(42)
                resamples = rng.choice(delta, size=(50000, len(delta)), replace=True).mean(axis=1)
                rows.append({
                    'campaign': campaign,
                    'candidate': candidate,
                    'baseline': baseline,
                    'models': len(delta),
                    'mean_delta_male': float(delta.mean()),
                    'q025': float(np.quantile(resamples, .025)),
                    'q975': float(np.quantile(resamples, .975)),
                    'improved_models': int((delta < 0).sum()),
                    'interpretation': 'exploratory paired model bootstrap, not independent validation',
                })
    return pd.DataFrame(rows)


def fit_final_main_model(main: pd.DataFrame, method: str = 'static_log_ridge') -> dict:
    """Persist a fixed learner, distinct from every held-model evaluation fold."""
    if method == 'size_only_extra_trees':
        estimator = ExtraTreesRegressor(
            n_estimators=200, min_samples_leaf=1, random_state=42, n_jobs=2)
        training = main.groupby('model_id', as_index=False).agg(
            parameter_gib=('parameter_gib', 'first'),
            load_seconds=('load_seconds', 'median'))
        feature_names = ('log_parameter_gib',)
    elif method == 'static_log_ridge':
        estimator = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
        training = main
        feature_names = FEATURES
    elif method == 'static_log_extra_trees':
        estimator = ExtraTreesRegressor(
            n_estimators=200, min_samples_leaf=2, random_state=42, n_jobs=2)
        training = main
        feature_names = FEATURES
    else:
        raise ValueError(f'Unsupported final method: {method}')
    estimator.fit(final_features(training, method),
                  np.log(training.load_seconds.to_numpy(dtype=float)))
    return {
        'estimator': estimator,
        'method': method,
        'feature_names': feature_names,
        'target_transform': 'log(load_seconds); prediction = exp(estimator.predict(features))',
        'scope': 'icns host, collector v2, CUDA single replica, CPU affinity 2; cached local model/tokenizer load only',
        'model_ids': sorted(main.model_id.unique().tolist()),
        'training_execution_ids': sorted(main.execution_id.astype(str).tolist()),
        'training_examples': len(training),
    }


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
    model_hashes = {}
    for method in ('size_only_extra_trees', 'static_log_ridge',
                   'static_log_extra_trees'):
        bundle = fit_final_main_model(panels[CAMPAIGNS[0]], method)
        model_path = args.output / f'{method}_all_main.joblib'
        joblib.dump(bundle, model_path)
        model_hashes[method] = sha256(model_path)
    manifest = {
        'scope': 'icns host, collector v2, CUDA single replica, CPU affinity 2; cached local model/tokenizer load only',
        'exploratory_existing_sqlite': True,
        'registration_metadata_assumption': 'model_parameter_bytes is known when the application is registered; historical source field is result_json',
        'feature_names': FEATURES,
        'methods': METHODS,
        'panel_counts': {campaign: len(panel) for campaign, panel in panels.items()},
        'main_models': sorted(panels[CAMPAIGNS[0]].model_id.unique().tolist()),
        'main_median_load_to_wall_fraction': float((panels[CAMPAIGNS[0]].load_seconds / panels[CAMPAIGNS[0]].wall_seconds).median()),
        'snapshot_sha256': sha256(args.snapshot),
        'script_sha256': sha256(Path(__file__)),
        'final_model_sha256': model_hashes,
        'pandas_version': pd.__version__,
        'numpy_version': np.__version__,
        'sklearn_version': sklearn.__version__,
    }
    with (args.output / 'manifest.json').open('w') as handle:
        json.dump(manifest, handle, indent=2)
    print(aggregate.loc[aggregate.fold.str.startswith('loomo:')].groupby(
        ['campaign', 'method'], as_index=False)[['male_macro', 'mae_seconds_macro']].mean().to_string(index=False))


if __name__ == '__main__':
    main()
