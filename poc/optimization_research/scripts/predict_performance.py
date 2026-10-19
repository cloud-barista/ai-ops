"""Offline prediction for a JSON array of known-host benchmark configurations."""
import argparse
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor

from train_performance import (PRIMARY, ROOT, assign_splits,
                               baseline_predict)


CONDITION_KEYS = ('model_id', 'model_revision', 'device')
CONDITION_BOUNDS = ('batch_size', 'max_length', 'request_concurrency')


def derive_training_condition_support(snapshot):
    """Recover model- and device-specific input bounds from the pinned fit split.

    This is an in-memory guard attached to a verified saved bundle. The fitted
    model bytes stay unchanged. The 70/15/15 workload split is the same one
    used by ``train_performance.train`` with seed 42.
    """
    primary = snapshot.loc[snapshot.eligible & snapshot.campaign.eq(PRIMARY)].copy()
    if primary.empty:
        raise ValueError('missing_primary_training_campaign')
    primary['split'] = assign_splits(primary, seed=42)
    train = primary.loc[primary.split.eq('train')]
    support = {}
    for key, group in train.groupby(list(CONDITION_KEYS), dropna=False):
        support[key] = {
            **{name: (int(group[name].min()), int(group[name].max()))
               for name in CONDITION_BOUNDS},
            'repeats': frozenset(int(value) for value in group.repeats.unique()),
        }
    if not support:
        raise ValueError('empty_training_condition_support')
    return support


def _condition_reasons(bundle, record):
    support = bundle.get('training_condition_support')
    if support is None:
        # The frozen fitted bundle predates this field. A raw joblib load must
        # not silently restore its overly broad global-only support decision.
        if bundle.get('selected') in ('extra_trees', 'model_device_median'):
            return ['missing_training_condition_support']
        return []
    key = tuple(record.get(name) for name in CONDITION_KEYS)
    limits = support.get(key)
    if limits is None:
        return ['unsupported_model_condition_group']
    reasons = []
    for name in CONDITION_BOUNDS:
        if not _supported_integer(record.get(name), *limits[name]):
            reasons.append('outside_model_condition_range:' + name)
    if record.get('repeats') not in limits['repeats']:
        reasons.append('unsupported_model_condition_value:repeats')
    return reasons


def configure_predict_workers(bundle):
    """Use one worker for small offline queries without changing saved fit settings.

    The selected ExtraTrees bundle is fitted and serialized with two workers.
    Configuring its fitted regressor after loading changes only predict-time
    parallelism; it does not refit trees or rewrite the artifact.
    """
    if bundle.get('selected') == 'extra_trees':
        regressor = bundle['model'].named_steps['regressor']
        if not isinstance(regressor, ExtraTreesRegressor):
            raise TypeError('extra_trees bundle must contain an ExtraTreesRegressor')
        if regressor.n_jobs != 1:
            bundle['model'].set_params(regressor__n_jobs=1)
    return bundle


def _supported_integer(value, lower, upper):
    """Reject malformed JSON numbers before the model or NumPy sees them."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and lower <= value <= upper and value == int(value)
    except (OverflowError, TypeError, ValueError):
        return False


def predict_records(bundle, records):
    if not isinstance(records, list):
        raise ValueError('records_must_be_array')
    configure_predict_workers(bundle)
    results = [None] * len(records)
    supported = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            results[index] = {'status':'ABSTAIN', 'reasons':['record_must_be_object']}
            continue
        reasons = []
        for key, values in bundle['categorical_support'].items():
            if record.get(key) not in values:
                reasons.append('unsupported_category:'+key)
        for key, (lower, upper) in bundle['numeric_support'].items():
            value = record.get(key)
            if not _supported_integer(value, lower, upper):
                reasons.append('missing_or_out_of_range:'+key)
        combo = tuple(record.get(k) for k in bundle['support_combination_columns'])
        if combo not in bundle['support_combinations']:
            reasons.append('unsupported_model_hardware_replica_combination')
        reasons.extend(_condition_reasons(bundle, record))
        if reasons:
            results[index] = {'status':'ABSTAIN', 'reasons':reasons}
            continue
        supported.append(index)
    if supported:
        frame = pd.DataFrame([records[index] for index in supported])
        values = (baseline_predict(frame, bundle['baseline']) if bundle['selected']=='model_device_median'
                  else np.exp(bundle['model'].predict(frame[bundle['features']])))
        for row, index in enumerate(supported):
            results[index] = {'status':'PREDICTED',
                              'prediction':dict(zip(bundle['targets'], values[row].tolist())),
                              'scope':'offline benchmark estimate; uncalibrated point prediction'}
    return results


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=ROOT/'artifacts/performance_v1/performance_model.joblib')
    parser.add_argument('--source',type=Path,default=ROOT/'data/audit_v1/measurements.parquet')
    parser.add_argument('--input',type=Path,required=True)
    args=parser.parse_args()
    # The CLI is an entry point for the saved research model. Import here to
    # avoid a module-level cycle with the assessor's predictor import.
    from assess_gpu_shape import load_verified_bundle
    bundle = load_verified_bundle(args.model, args.source)
    print(json.dumps(predict_records(bundle, json.loads(args.input.read_text())), indent=2))
