#!/usr/bin/env python3
"""Re-score the saved model's fixed external runs after model-specific gating."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from assess_gpu_shape import load_verified_bundle
from predict_performance import CONDITION_BOUNDS, CONDITION_KEYS, predict_records
from train_performance import (FEATURES, PRIMARY, ROOT, TARGETS, assign_splits,
                               baseline_predict)


MODEL = ROOT / 'artifacts/performance_v1/performance_model.joblib'
SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
CAMPAIGNS = ('khu-hf-local-novel-matrix', 'khu-hf-local-space-filling')


def _group_male(frame: pd.DataFrame, predicted: np.ndarray) -> dict:
    answer = {}
    for target_index, target in enumerate(TARGETS):
        log_error = np.abs(np.log(predicted[:, target_index]) -
                           np.log(frame[target].to_numpy(dtype=float)))
        answer[target] = float(pd.Series(log_error).groupby(
            frame.workload_group.to_numpy()).mean().mean())
    answer['mean_two_targets'] = float(np.mean(list(answer.values())))
    return answer


def _joint_training_grid(snapshot: pd.DataFrame) -> dict:
    """Count unmeasured joint inputs even among individually observed levels."""
    primary = snapshot.loc[snapshot.eligible & snapshot.campaign.eq(PRIMARY)].copy()
    primary['split'] = assign_splits(primary, seed=42)
    train = primary.loc[primary.split.eq('train')]
    columns = list(CONDITION_BOUNDS) + ['repeats']
    possible = observed = groups = 0
    for _, group in train.groupby(list(CONDITION_KEYS), dropna=False):
        groups += 1
        product = 1
        for column in columns:
            product *= int(group[column].nunique())
        possible += product
        observed += int(len(group[columns].drop_duplicates()))
    if (possible, observed) != (3806, 441):
        raise AssertionError('training joint-condition grid drift')
    return {'groups': groups,
            'cartesian_combinations_of_observed_levels': possible,
            'observed_joint_tuples': observed,
            'unmeasured_joint_tuples': possible - observed,
            'unmeasured_fraction': (possible - observed) / possible}


def run(output: Path, model_path: Path = MODEL, source_path: Path = SOURCE) -> dict:
    verified = load_verified_bundle(model_path, source_path)
    if 'training_condition_support' not in verified:
        raise ValueError('verified bundle lacks training condition support')
    prior = copy.copy(verified)
    # Current predict_records fails closed if a fitted bundle has no support
    # map. Emulate the *historical* global-only gate with its original numeric
    # limits so this comparison remains reproducible without reviving a raw
    # joblib bypass in the public predictor.
    bounds = {name: tuple(map(int, verified['numeric_support'][name]))
              for name in CONDITION_BOUNDS}
    low, high = map(int, verified['numeric_support']['repeats'])
    prior['training_condition_support'] = {
        key: {**bounds, 'repeats': frozenset(range(low, high + 1))}
        for key in verified['training_condition_support']
    }
    snapshot = pd.read_parquet(source_path)
    primary = snapshot[snapshot.eligible & snapshot.campaign.eq(PRIMARY)]
    reports = {}
    for campaign in (PRIMARY,) + CAMPAIGNS:
        rows = (primary if campaign == PRIMARY else
                snapshot[snapshot.eligible & snapshot.campaign.eq(campaign)]).copy()
        records = rows[FEATURES + ['host', 'device_name']].to_dict('records')
        old = predict_records(prior, records)
        new = predict_records(verified, records)
        old_ok = np.asarray([item['status'] == 'PREDICTED' for item in old])
        new_ok = np.asarray([item['status'] == 'PREDICTED' for item in new])
        if np.any(new_ok & ~old_ok):
            raise AssertionError('new gate approved an old unsupported input')
        retained = rows.iloc[np.flatnonzero(new_ok)].copy()
        predictions = np.asarray([[new[i]['prediction'][target] for target in TARGETS]
                                  for i in np.flatnonzero(new_ok)], dtype=float)
        previous = np.asarray([[old[i]['prediction'][target] for target in TARGETS]
                               for i in np.flatnonzero(new_ok)], dtype=float)
        if not np.array_equal(predictions, previous):
            raise AssertionError('saved model predictions changed for retained rows')
        reports[campaign] = {
            'eligible_rows': int(len(rows)),
            'previous_supported_rows': int(old_ok.sum()),
            'model_condition_supported_rows': int(new_ok.sum()),
            'retained_workload_groups': int(retained.workload_group.nunique()),
            'prediction_exact_parity': True,
        }
        if campaign != PRIMARY:
            baseline = baseline_predict(retained, verified['baseline'])
            reports[campaign]['retained_model_group_male'] = _group_male(
                retained, predictions)
            reports[campaign]['retained_baseline_group_male'] = _group_male(
                retained, baseline)
    if (reports[PRIMARY]['eligible_rows'],
            reports[PRIMARY]['model_condition_supported_rows']) != (4856, 4828):
        raise AssertionError('primary campaign support drift')
    if [reports[name]['previous_supported_rows'] for name in
            (PRIMARY,) + CAMPAIGNS] != [4856, 108, 296]:
        raise AssertionError('historical global support emulation drift')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'model_sha256': verified['_assess_gpu_shape_verified_artifacts'][0],
              'source_sha256': verified['_assess_gpu_shape_verified_artifacts'][1],
              'predictor_sha256': hashlib.sha256(
                  (ROOT / 'scripts/predict_performance.py').read_bytes()).hexdigest(),
              'assessor_sha256': hashlib.sha256(
                  (ROOT / 'scripts/assess_gpu_shape.py').read_bytes()).hexdigest(),
              'evaluation_script_sha256': hashlib.sha256(
                  Path(__file__).read_bytes()).hexdigest(),
              'support_rule': 'train-split model/revision/device-specific numeric ranges and observed repeats',
              'joint_training_grid': _joint_training_grid(snapshot),
              'campaigns': reports}
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/model_condition_support_v1/metrics.json')
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))
