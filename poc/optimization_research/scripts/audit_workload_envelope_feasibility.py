"""Read-only oracle-support gate for the proposed workload-envelope study.

This script never opens K8 prediction values or fits a model. It counts only
whether the predeclared bundle recommendation problem has enough nontrivial
fully measured examples to justify a later predictive experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_counterfactual_slo import training_threshold_grid
from sparse_anchor_transfer import CAMPAIGN, validate_slates


ROOT = Path(__file__).resolve().parents[1]
MIN_UNPROBED_CONDITIONS = 10
REQUIRED_COVERAGE = 0.90
ORIGINAL_MIN_NONTRIVIAL = 4
ORIGINAL_MIN_MODELS = 3
SYNTHETIC_MIN_NONTRIVIAL = 30
SYNTHETIC_MIN_MODELS = 5
CONFIGS = tuple((g, c) for g in (1, 2) for c in (2, 4, 8, 16))


def demand_bucket(frame: pd.DataFrame) -> np.ndarray:
    high = ((frame.batch_size.to_numpy() >= 12) |
            (frame.max_length.to_numpy() >= 512))
    return np.where(high, 'high', 'ordinary')


def load_probe_manifest(path: Path, models: set[str]) -> dict[str, set[str]]:
    manifest = pd.read_csv(path)
    selected = manifest[(manifest.budget == 8) &
                        (manifest.selector == 'space_filling')]
    if len(selected) != len(models) or set(selected.heldout_model_id) != models:
        raise ValueError('Expected one K8 space-filling row for each model')
    result = {}
    for row in selected.itertuples(index=False):
        probes = json.loads(row.probe_groups_json)
        training_models = set(json.loads(row.training_model_ids_json))
        if (len(probes) != 8 or len(set(probes)) != 8 or
                row.heldout_model_id in training_models or
                training_models != models - {row.heldout_model_id}):
            raise ValueError('Invalid probe or outer training-model split')
        result[row.heldout_model_id] = set(probes)
    return result


def oracle_config_count(bundle: pd.DataFrame, latency_slo,
                        target_rps) -> tuple[int, dict[str, float]]:
    """Number of the eight shapes covering >=90% of unprobed conditions."""
    n = bundle.workload_group.nunique()
    if n < MIN_UNPROBED_CONDITIONS or len(bundle) != 8 * n:
        raise ValueError('Envelope is too small or has an incomplete slate')
    if np.isscalar(latency_slo):
        log_l = math.log(float(latency_slo))
        log_r = math.log(float(target_rps))
        actual = ((np.log(bundle.latency_p95_ms.to_numpy()) <= log_l) &
                  (np.log((bundle.examples_per_second /
                           bundle.batch_size).to_numpy()) >= log_r))
    else:
        latency_slo = np.asarray(latency_slo, dtype=float)
        target_rps = np.asarray(target_rps, dtype=float)
        if latency_slo.shape != (len(bundle),) or target_rps.shape != (len(bundle),):
            raise ValueError('Per-condition SLO arrays have wrong shape')
        actual = ((bundle.latency_p95_ms.to_numpy() <= latency_slo) &
                  ((bundle.examples_per_second /
                    bundle.batch_size).to_numpy() >= target_rps))
    marked = bundle[['replica_count', 'cpu_core_limit']].copy()
    marked['pass'] = actual
    coverage = marked.groupby(['replica_count', 'cpu_core_limit'])['pass'].mean()
    if set(coverage.index) != set(CONFIGS):
        raise ValueError('Incomplete eight-configuration envelope')
    shares = {f'gpu{gpu}_cpu{cpu}': float(coverage.loc[(gpu, cpu)])
              for gpu, cpu in CONFIGS}
    return int(sum(value + 1e-12 >= REQUIRED_COVERAGE
                   for value in shares.values())), shares


def gate_summary(cases: pd.DataFrame) -> dict:
    required = {
        'original': (ORIGINAL_MIN_NONTRIVIAL, ORIGINAL_MIN_MODELS),
        'synthetic': (SYNTHETIC_MIN_NONTRIVIAL, SYNTHETIC_MIN_MODELS),
    }
    result = {}
    for source, (min_cases, min_models) in required.items():
        subset = cases[cases.source == source]
        nontrivial = subset[(subset.feasible_config_count >= 1) &
                            (subset.feasible_config_count <= 7)]
        model_count = nontrivial.model_id.nunique()
        result[source] = {
            'total_cases': int(len(subset)),
            'nontrivial_cases': int(len(nontrivial)),
            'models_with_nontrivial_case': int(model_count),
            'zero_feasible_cases': int((subset.feasible_config_count == 0).sum()),
            'all_eight_feasible_cases': int((subset.feasible_config_count == 8).sum()),
            'minimum_nontrivial_cases': min_cases,
            'minimum_models': min_models,
            'passes': bool(len(nontrivial) >= min_cases and model_count >= min_models),
        }
    result['overall_passes'] = bool(
        result['original']['passes'] and result['synthetic']['passes'])
    return result


def run(snapshot: Path, manifest: Path, output: Path) -> dict:
    raw = pd.read_parquet(snapshot)
    main = raw[(raw.campaign == CAMPAIGN) & raw.eligible &
               (raw.device == 'cuda')].copy()
    validate_slates(main)
    models = set(main.model_id.unique())
    if len(models) != 9 or len(main) != 4600:
        raise ValueError('Frozen main campaign population changed')
    probes = load_probe_manifest(manifest, models)
    rows = []
    for held in sorted(models):
        donor = main[main.model_id != held]
        thresholds = training_threshold_grid(donor)
        if len(thresholds) != 25:
            raise ValueError('Donor threshold grid changed')
        held_rows = main[main.model_id == held].copy()
        if not probes[held].issubset(set(held_rows.workload_group)):
            raise ValueError('Probe outside target model')
        held_rows = held_rows[~held_rows.workload_group.isin(probes[held])].copy()
        held_rows['demand_bucket'] = demand_bucket(held_rows)
        for bucket in ('ordinary', 'high'):
            bundle = held_rows[held_rows.demand_bucket == bucket]
            count = bundle.workload_group.nunique()
            if count < MIN_UNPROBED_CONDITIONS:
                raise ValueError(f'Insufficient unprobed conditions: {held}, {bucket}')
            n, shares = oracle_config_count(
                bundle, bundle.latency_slo_ms.to_numpy(),
                bundle.target_throughput_rps.to_numpy())
            rows.append({'model_id': held, 'demand_bucket': bucket,
                         'source': 'original', 'threshold_index': -1,
                         'unprobed_conditions': int(count),
                         'feasible_config_count': n,
                         'coverage_by_config': json.dumps(shares, sort_keys=True)})
            for index, threshold in enumerate(thresholds):
                n, shares = oracle_config_count(
                    bundle, threshold['latency_slo_ms'],
                    threshold['target_rps'])
                rows.append({
                    'model_id': held, 'demand_bucket': bucket,
                    'source': 'synthetic', 'threshold_index': index,
                    'latency_quantile': threshold['latency_quantile'],
                    'rps_quantile': threshold['rps_quantile'],
                    'unprobed_conditions': int(count),
                    'feasible_config_count': n,
                    'coverage_by_config': json.dumps(shares, sort_keys=True),
                })
    cases = pd.DataFrame(rows)
    if len(cases) != 9 * 2 * 26:
        raise AssertionError('Unexpected number of predeclared cases')
    gate = gate_summary(cases)
    per_model = (cases.assign(nontrivial=cases.feasible_config_count.between(1, 7))
                 .groupby(['model_id', 'source'], as_index=False)
                 .agg(cases=('nontrivial', 'size'),
                      nontrivial_cases=('nontrivial', 'sum'),
                      zero_feasible=('feasible_config_count',
                                     lambda col: int((col == 0).sum())),
                      all_eight=('feasible_config_count',
                                 lambda col: int((col == 8).sum()))))
    output.mkdir(parents=True, exist_ok=True)
    cases.to_csv(output / 'cases.csv', index=False)
    per_model.to_csv(output / 'per_model.csv', index=False)
    report = {
        'scope': 'oracle support audit only; no prediction values or model training',
        'source_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'probe_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'envelopes': ['ordinary: batch<12 and max_length<512',
                      'high: batch>=12 or max_length>=512'],
        'probes_excluded_from_oracle': 8,
        'minimum_unprobed_conditions': MIN_UNPROBED_CONDITIONS,
        'coverage_target': REQUIRED_COVERAGE,
        'thresholds': 'original collector targets plus 25 donor-only quantile pairs per held model',
        'gate': gate,
        'decision': 'STOP_NO_TRAINING' if not gate['overall_passes'] else 'ELIGIBLE_FOR_LATER_STUDY',
        'decision_reason': ('Original generated SLO has fewer than four nontrivial bundles'
                            if not gate['original']['passes'] else None),
        'per_model': per_model.to_dict(orient='records'),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--manifest', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/split_manifest.csv')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/workload_envelope_gate_v1')
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.manifest, args.output)['gate'], indent=2))
