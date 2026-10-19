"""Exploratory prediction of observed per-GPU PyTorch reserved-memory peaks.

The target is a measured CUDA allocator peak on one existing GPU, not the
minimum VRAM needed to run the application and not an OOM/feasibility label.
Only local GPU1 / CPU-affinity-2 measurements are used.  Held-out models are
excluded from every fitted model, including when scoring later campaigns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'data/audit_v1/measurements.parquet'
OUTPUT = ROOT / 'artifacts/vram_peak_v1'
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
METHODS = ('parameter_plus_median', 'direct_log_ridge',
           'direct_log_ridge_floor', 'direct_log_extra_trees',
           'direct_log_extra_trees_floor', 'decomposed_overhead_ridge')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def selected_measurements(snapshot: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Select the same physical resource profile from three local campaigns."""
    result = {}
    for campaign in CAMPAIGNS:
        mask = ((snapshot.campaign == campaign) &
                (snapshot.device == 'cuda') &
                (snapshot.replica_count == 1) &
                (snapshot.cpu_core_limit == 2) &
                (snapshot.success == 1) &
                (snapshot.eligible == True))
        frame = snapshot.loc[mask].copy()
        if frame.empty:
            raise ValueError(f'No selected measurements for {campaign}')
        parameter_bytes = frame.result_json.map(
            lambda raw: float(json.loads(raw)['model_parameter_bytes']))
        frame['parameter_gib'] = parameter_bytes / (1024.0 ** 3)
        frame['reserved_peak_gib'] = frame.gpu_memory_reserved_bytes.astype(float) / (1024.0 ** 3)
        if (frame[['parameter_gib', 'reserved_peak_gib', 'batch_size',
                   'max_length', 'request_concurrency']].isna().any().any() or
                (frame[['parameter_gib', 'reserved_peak_gib', 'batch_size',
                        'max_length', 'request_concurrency']] <= 0).any().any()):
            raise ValueError(f'Invalid positive memory or static input in {campaign}')
        if not (frame.reserved_peak_gib > frame.parameter_gib).all():
            raise ValueError(f'Observed memory overhead must be positive in {campaign}')
        sizes = frame.groupby('model_id').parameter_gib.nunique()
        if not (sizes == 1).all():
            raise ValueError(f'Model parameter bytes vary within {campaign}')
        if frame.duplicated(['model_id', 'workload_group']).any():
            raise ValueError(f'Duplicate model/workload in {campaign}')
        result[campaign] = frame
    all_sizes = pd.concat([frame[['model_id', 'parameter_gib']]
                           for frame in result.values()]).groupby('model_id').parameter_gib.nunique()
    if not (all_sizes == 1).all():
        raise ValueError('Model parameter bytes vary across campaigns')
    return result


def static_features(frame: pd.DataFrame) -> np.ndarray:
    """Use only registration and requested-workload fields, never target peaks."""
    weight = np.log(frame.parameter_gib.to_numpy(dtype=float))
    batch = np.log(frame.batch_size.to_numpy(dtype=float))
    length = np.log(frame.max_length.to_numpy(dtype=float))
    concurrency = np.log1p(frame.request_concurrency.to_numpy(dtype=float))
    causal = frame.workload_task.astype(str).str.contains(
        'causal|lm|generation', case=False, regex=True).to_numpy(dtype=float)
    return np.column_stack((weight, batch, length, concurrency, causal,
                            batch * length, weight * batch * length))


def fit_predict(train: pd.DataFrame, test: pd.DataFrame,
                held_model_ids: set[str]) -> dict[str, np.ndarray]:
    """Fit on main-campaign donors and predict static features of held models."""
    if train.empty or test.empty:
        raise ValueError('Training and test frames must be nonempty')
    if set(train.model_id).intersection(held_model_ids):
        raise ValueError('Held model included in donor training rows')
    if not set(test.model_id).issubset(held_model_ids):
        raise ValueError('Test frame contains a donor model')
    if (train.reserved_peak_gib <= train.parameter_gib).any():
        raise ValueError('Donor measured overhead must be positive')
    x_train, x_test = static_features(train), static_features(test)
    weight_test = test.parameter_gib.to_numpy(dtype=float)
    predictions = {
        'parameter_plus_median': weight_test +
        float(np.median(train.reserved_peak_gib - train.parameter_gib)),
        'direct_log_ridge': np.exp(make_pipeline(
            StandardScaler(), Ridge(alpha=10.0)).fit(
                x_train, np.log(train.reserved_peak_gib)).predict(x_test)),
        'direct_log_extra_trees': np.exp(ExtraTreesRegressor(
            n_estimators=200, min_samples_leaf=2, random_state=42,
            n_jobs=2).fit(x_train, np.log(train.reserved_peak_gib)).predict(x_test)),
        'decomposed_overhead_ridge': weight_test + np.exp(make_pipeline(
            StandardScaler(), Ridge(alpha=10.0)).fit(
                x_train, np.log(train.reserved_peak_gib - train.parameter_gib)
            ).predict(x_test)),
    }
    # A fair direct-regression baseline also gets the same known weight floor.
    predictions['direct_log_ridge_floor'] = np.maximum(
        predictions['direct_log_ridge'], weight_test)
    predictions['direct_log_extra_trees_floor'] = np.maximum(
        predictions['direct_log_extra_trees'], weight_test)
    for name, prediction in predictions.items():
        if len(prediction) != len(test) or not np.isfinite(prediction).all() or (prediction <= 0).any():
            raise ValueError(f'Invalid predictions from {name}')
    return predictions


def predict_all(campaigns: dict[str, pd.DataFrame]) -> pd.DataFrame:
    main = campaigns[CAMPAIGNS[0]]
    models = sorted(main.model_id.unique())
    if set(BERT_FAMILY + (DISTILBERT,)) - set(models):
        raise ValueError('Expected family holdout model is missing')
    folds = [(f'loomo:{model}', {model}) for model in models]
    folds.extend((('bert4', set(BERT_FAMILY)),
                  ('bert4_plus_distilbert', set(BERT_FAMILY + (DISTILBERT,)))))
    rows = []
    for fold, held_ids in folds:
        train = main.loc[~main.model_id.isin(held_ids)]
        for campaign, frame in campaigns.items():
            test = frame.loc[frame.model_id.isin(held_ids)]
            if test.empty:
                continue
            predictions = fit_predict(train, test, held_ids)
            for method, estimate in predictions.items():
                rows.extend({
                    'fold': fold,
                    'campaign': campaign,
                    'model_id': model_id,
                    'workload_group': workload,
                    'method': method,
                    'parameter_gib': float(parameter),
                    'observed_peak_gib': float(observed),
                    'predicted_peak_gib': float(predicted),
                } for model_id, workload, parameter, observed, predicted in zip(
                    test.model_id, test.workload_group, test.parameter_gib,
                    test.reserved_peak_gib, estimate))
    return pd.DataFrame(rows)


def summarize(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = predictions.copy()
    frame['abs_log_error'] = np.abs(np.log(frame.predicted_peak_gib / frame.observed_peak_gib))
    frame['abs_error_mib'] = np.abs(frame.predicted_peak_gib - frame.observed_peak_gib) * 1024.0
    frame['underestimated'] = (frame.predicted_peak_gib < frame.observed_peak_gib).astype(float)
    frame['shortfall_mib'] = np.maximum(frame.observed_peak_gib - frame.predicted_peak_gib, 0.0) * 1024.0
    per_model = frame.groupby(['fold', 'campaign', 'method', 'model_id'], as_index=False).agg(
        observations=('abs_log_error', 'size'),
        male=('abs_log_error', 'mean'),
        mean_abs_error_mib=('abs_error_mib', 'mean'),
        underestimation_fraction=('underestimated', 'mean'),
        mean_shortfall_mib=('shortfall_mib', 'mean'),
        max_shortfall_mib=('shortfall_mib', 'max'),
    )
    aggregate = per_model.groupby(['fold', 'campaign', 'method'], as_index=False).agg(
        models=('model_id', 'size'),
        observations=('observations', 'sum'),
        male_macro=('male', 'mean'),
        mean_abs_error_mib_macro=('mean_abs_error_mib', 'mean'),
        underestimation_fraction_macro=('underestimation_fraction', 'mean'),
        mean_shortfall_mib_macro=('mean_shortfall_mib', 'mean'),
        worst_shortfall_mib=('max_shortfall_mib', 'max'),
    )
    return per_model, aggregate


def paired_model_bootstrap(per_model: pd.DataFrame) -> pd.DataFrame:
    """Descriptive model-level uncertainty for the selected pilot comparison."""
    main = per_model.loc[per_model.fold.str.startswith('loomo:')]
    pivot = main.pivot(index='model_id', columns=['campaign', 'method'], values='male')
    rows = []
    for campaign in CAMPAIGNS:
        difference = (pivot[(campaign, 'decomposed_overhead_ridge')] -
                      pivot[(campaign, 'direct_log_ridge_floor')]).to_numpy(dtype=float)
        rng = np.random.default_rng(42)
        samples = rng.choice(difference, size=(50000, len(difference)), replace=True).mean(axis=1)
        rows.append({
            'campaign': campaign,
            'models': len(difference),
            'mean_delta_male': float(difference.mean()),
            'bootstrap_q025': float(np.quantile(samples, 0.025)),
            'bootstrap_q975': float(np.quantile(samples, 0.975)),
            'improved_models': int((difference < 0).sum()),
            'interpretation': 'exploratory paired model bootstrap; not a confirmatory interval',
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    snapshot = pd.read_parquet(args.snapshot)
    campaigns = selected_measurements(snapshot)
    predictions = predict_all(campaigns)
    per_model, aggregate = summarize(predictions)
    bootstrap = paired_model_bootstrap(per_model)
    args.output.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output / 'predictions.csv', index=False)
    per_model.to_csv(args.output / 'per_model_metrics.csv', index=False)
    aggregate.to_csv(args.output / 'aggregate_metrics.csv', index=False)
    bootstrap.to_csv(args.output / 'paired_model_bootstrap.csv', index=False)
    manifest = {
        'research_scope': 'observed per-GPU PyTorch reserved peak only; not minimum VRAM or OOM feasibility',
        'exploratory_after_pilot': True,
        'snapshot_sha256': sha256(args.snapshot),
        'script_sha256': sha256(Path(__file__)),
        'pandas_version': pd.__version__,
        'numpy_version': np.__version__,
        'sklearn_version': sklearn.__version__,
        'campaign_rows': {name: len(frame) for name, frame in campaigns.items()},
        'model_count': campaigns[CAMPAIGNS[0]].model_id.nunique(),
        'folds': sorted(predictions.fold.unique()),
        'methods': list(METHODS),
        'metric_definition': 'Per-model mean absolute log error and mean absolute MiB error; macro average across held models.',
        'static_metadata_assumption': 'model_parameter_bytes from registration is known before an execution; dataset stores it in result_json and values are constant per model across campaigns.',
        'no_target_outcomes_in_predictors': True,
    }
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(aggregate.to_string(index=False))
    print(f'Wrote {args.output}')


if __name__ == '__main__':
    main()
