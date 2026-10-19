"""Negative control for condition-specific GPU1 telemetry in GPU2 prediction.

The no-count telemetry model is retrained per outer held-out model using only
other models' main-campaign outcomes. Within the held-out model, complete
GPU1 telemetry vectors are shuffled across workload groups; static features,
observed GPU1 performance, and GPU2 scoring labels stay fixed. This tests
whether telemetry-to-condition alignment helps prediction, not causality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from conditional_response import CAMPAIGNS, prepare
from telemetry_conditioned_scaling import (VARIANTS, add_anchor_telemetry,
                                           fit_telemetry_model,
                                           predict_telemetry)


ROOT = Path(__file__).resolve().parents[1]
FEATURES = VARIANTS['no_count']
MODES = ('all_groups', 'within_batch')
PERMUTATIONS = 32


def shuffle_joint_telemetry(fold: pd.DataFrame, seed: int,
                            mode: str = 'all_groups') -> pd.DataFrame:
    """Permute a full telemetry vector, preserving its empirical joint law."""
    if mode not in MODES:
        raise ValueError('Unknown shuffle mode')
    if fold.workload_group.duplicated().any():
        raise ValueError('Expected one GPU2 candidate per workload group')
    if fold[list(FEATURES)].isna().any().any():
        raise ValueError('Missing observed GPU1 telemetry')
    out = fold.copy()
    rng = np.random.default_rng(seed)
    strata = [fold.sort_values('workload_group').index.to_numpy()]
    if mode == 'within_batch':
        strata = [group.sort_values('workload_group').index.to_numpy()
                  for _, group in fold.groupby('batch_size', sort=True)]
    for receivers in strata:
        sources = rng.permutation(receivers)
        out.loc[receivers, list(FEATURES)] = fold.loc[sources,
                                                       list(FEATURES)].to_numpy()
    return out


def _verify_reference(snapshot: Path, original: Path) -> dict:
    metadata = json.loads((original.parent / 'metrics.json').read_text())
    if metadata['source_snapshot_sha256'] != hashlib.sha256(snapshot.read_bytes()).hexdigest():
        raise ValueError('Original telemetry predictions use another snapshot')
    source = ROOT / 'scripts/telemetry_conditioned_scaling.py'
    if metadata['script_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('Original telemetry code has changed')
    return metadata


def _macro_summary(per_model: pd.DataFrame) -> list[dict]:
    aligned = per_model[per_model.method == 'aligned'].set_index('model_id').rps_male
    out = []
    for mode in MODES:
        shuffled = per_model[per_model.method == mode].pivot(
            index='model_id', columns='permutation', values='rps_male').loc[aligned.index]
        macro_by_seed = shuffled.mean(axis=0).to_numpy(dtype=float)
        model_delta = (aligned - shuffled.mean(axis=1)).to_numpy(dtype=float)
        bootstrap = model_delta[np.random.default_rng(42).integers(
            0, len(model_delta), size=(20000, len(model_delta)))].mean(axis=1)
        out.append({
            'mode': mode,
            'aligned_macro_rps_male': float(aligned.mean()),
            'shuffled_macro_rps_male_mean': float(macro_by_seed.mean()),
            'shuffled_macro_rps_male_min': float(macro_by_seed.min()),
            'shuffled_macro_rps_male_max': float(macro_by_seed.max()),
            'shuffled_macro_rps_male_5_95_percentiles': [
                float(v) for v in np.quantile(macro_by_seed, [0.05, 0.95])],
            'aligned_better_than_shuffle_seeds': int((aligned.mean() < macro_by_seed).sum()),
            'total_shuffle_seeds': int(len(macro_by_seed)),
            'aligned_better_than_shuffled_mean_models': int((model_delta < 0).sum()),
            'model_count': int(len(model_delta)),
            'aligned_minus_shuffled_mean_model_macro': float(model_delta.mean()),
            'descriptive_model_bootstrap_95_interval': [
                float(v) for v in np.quantile(bootstrap, [0.025, 0.975])],
        })
    return out


def run(snapshot: Path, original: Path, output: Path,
        permutations: int = PERMUTATIONS) -> dict:
    if permutations < 1:
        raise ValueError('Need at least one permutation')
    _verify_reference(snapshot, original)
    raw = pd.read_parquet(snapshot)
    main = add_anchor_telemetry(prepare(raw[raw.campaign == CAMPAIGNS[0]]))
    frozen = pd.read_parquet(original)
    original_target = frozen[(frozen.campaign == CAMPAIGNS[0]) &
                             (frozen.execution_id.isin(
                                 main[(main.replica_count == 2) &
                                      (main.cpu_core_limit == 2)].execution_id))]
    target_ids = set(main[(main.replica_count == 2) &
                          (main.cpu_core_limit == 2)].execution_id)
    if original_target.execution_id.duplicated().any() or set(original_target.execution_id) != target_ids:
        raise ValueError('Original telemetry reference does not cover the target slate')
    records, predictions = [], []
    for model_index, held_model in enumerate(sorted(main.model_id.unique())):
        train = main[main.model_id != held_model]
        fold = main[(main.model_id == held_model) &
                    (main.replica_count == 2) & (main.cpu_core_limit == 2)].copy()
        model = fit_telemetry_model(train, 42, FEATURES)
        aligned = predict_telemetry(model, fold, FEATURES)[:, 1]
        reference = original_target[original_target.model_id == held_model].set_index('execution_id')
        if not np.allclose(aligned,
                           reference.loc[fold.execution_id, 'no_count_rps_log'].to_numpy(),
                           atol=1e-10, rtol=0):
            raise AssertionError('Retrained aligned predictor differs from frozen study')
        truth = fold.true_log_requests_per_second.to_numpy(dtype=float)

        def append(method: str, repeat: int, prediction: np.ndarray) -> None:
            records.append({'model_id': held_model, 'campaign': CAMPAIGNS[0],
                            'method': method, 'permutation': repeat,
                            'groups': len(fold),
                            'rps_male': float(np.abs(prediction - truth).mean())})
            predictions.append(pd.DataFrame({
                'campaign': CAMPAIGNS[0], 'model_id': held_model,
                'workload_group': fold.workload_group.to_numpy(),
                'execution_id': fold.execution_id.to_numpy(),
                'method': method, 'permutation': repeat,
                'predicted_rps_log': prediction,
                'true_rps_log': truth,
            }))

        append('aligned', -1, aligned)
        for mode in MODES:
            for repeat in range(permutations):
                seed = 10000 * (model_index + 1) + repeat
                permuted = shuffle_joint_telemetry(fold, seed, mode)
                predicted = predict_telemetry(model, permuted, FEATURES)[:, 1]
                append(mode, repeat, predicted)
        print(f'{held_model}: {len(fold)} groups, {permutations} permutations/mode',
              flush=True)
    per_model = pd.DataFrame(records)
    pred = pd.concat(predictions, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    per_model.to_csv(output / 'per_model_metrics.csv', index=False)
    pred.to_parquet(output / 'predictions.parquet', index=False)
    report = {
        'scope': 'exploratory negative control on one host; no causal or novelty claim',
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'original_prediction_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'training': 'outer leave-one-model-out no-count telemetry ExtraTrees, donor main campaign only',
        'scoring': 'GPU2/CPU2 RPS, with GPU1/CPU2 performance and four telemetry summaries observed',
        'shuffling': (f'joint vector, within held-out model, {permutations} deterministic seeds; '
                      'static and anchor performance unchanged'),
        'permutations_per_mode': permutations,
        'modes': list(MODES),
        'main_campaign': _macro_summary(per_model),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--original-predictions', type=Path,
                        default=ROOT / 'artifacts/telemetry_conditioned_scaling_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/telemetry_permutation_control_v1')
    parser.add_argument('--permutations', type=int, default=PERMUTATIONS)
    args = parser.parse_args()
    run(args.snapshot, args.original_predictions, args.output,
        permutations=args.permutations)
