"""Exploratory, support-gated resource-response test on frozen K8 probes.

The held-out model supplies only the eight previously selected GPU1/core2
measurements through frozen sparse predictions. All fitted response surfaces
and support rules use other models' main-campaign outcomes. A quadratic
response is blended with the incumbent latency response only where the
workload condition has no exact same-task donor support. CPU monotonicity is
deliberately not imposed because the collected measurements contradict it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from conditional_response import fit_model, predict as conditional_predict, prepare
from sparse_anchor_transfer import (CAMPAIGN, EXTERNAL_CAMPAIGNS,
                                    registered_parameter_bytes,
                                    scenario_metrics, static_features,
                                    validate_slates)


ROOT = Path(__file__).resolve().parents[1]
BASELINES = ('space_cov_transfer', 'space_kernel_transfer', 'space_ridge')
METHODS = ('support_smooth_latency', 'full_smooth_response') + BASELINES
ALPHA = 1.0
LATENCY_BLEND = 0.5
CONFIGS = tuple((gpu, cores) for gpu in (1, 2) for cores in (2, 4, 8, 16))
CONDITION = ('workload_task', 'batch_size', 'max_length',
             'request_concurrency', 'repeats')


def diagnose_resource_response(main: pd.DataFrame) -> list[dict]:
    """Observed pair directions and ratios; no learned monotonic assumption."""
    validate_slates(main)
    response = main.copy()
    response['rps'] = response.examples_per_second / response.batch_size
    pivot = response.set_index(['workload_group', 'replica_count', 'cpu_core_limit'])
    result = []
    comparisons = ([('gpu', 1, cores, 2, cores) for cores in (2, 4, 8, 16)] +
                   [('cpu', gpu, c1, gpu, c2)
                    for gpu in (1, 2) for c1, c2 in ((2, 4), (4, 8), (8, 16), (2, 16))])
    for axis, g1, c1, g2, c2 in comparisons:
        first = pivot.loc[(slice(None), g1, c1)]
        second = pivot.loc[(slice(None), g2, c2)]
        if not first.index.equals(second.index):
            raise ValueError('Resource comparison has misaligned workloads')
        l_ratio = (second.latency_p95_ms / first.latency_p95_ms).to_numpy(dtype=float)
        r_ratio = (second.rps / first.rps).to_numpy(dtype=float)
        result.append({'axis': axis, 'from_gpu': g1, 'from_cpu': c1,
                       'to_gpu': g2, 'to_cpu': c2, 'pairs': len(l_ratio),
                       'median_latency_ratio': float(np.median(l_ratio)),
                       'median_rps_ratio': float(np.median(r_ratio)),
                       'latency_decrease_count': int((l_ratio <= 1).sum()),
                       'rps_increase_count': int((r_ratio >= 1).sum())})
    return result


def response_coordinates(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack((frame.log_parameter_bytes.to_numpy(dtype=float),
                            frame.log_batch_size.to_numpy(dtype=float),
                            frame.log_max_length.to_numpy(dtype=float),
                            np.log1p(frame.request_concurrency.to_numpy(dtype=float)),
                            frame.anchor_log_latency.to_numpy(dtype=float),
                            frame.anchor_log_requests_per_second.to_numpy(dtype=float)))


class SmoothResponse:
    """Quadratic workload response with separate coefficients for each slate cell."""

    def __init__(self, alpha: float = ALPHA):
        self.alpha = alpha
        self.scaler = StandardScaler()
        self.poly = PolynomialFeatures(degree=2, include_bias=True)
        self.regressor = Ridge(alpha=alpha, fit_intercept=False)

    def _design(self, frame: pd.DataFrame) -> np.ndarray:
        coordinates = np.clip(self.scaler.transform(response_coordinates(frame)), -4, 4)
        basis = self.poly.transform(coordinates)
        cell = np.asarray([(int(gpu) - 1) * 4 + {2: 0, 4: 1, 8: 2, 16: 3}[int(cpu)]
                           for gpu, cpu in zip(frame.replica_count, frame.cpu_core_limit)], dtype=int)
        matrix = np.zeros((len(frame), 8 * basis.shape[1]))
        matrix[np.arange(len(frame))[:, None],
               cell[:, None] * basis.shape[1] + np.arange(basis.shape[1])] = basis
        return matrix

    def fit(self, donor: pd.DataFrame) -> 'SmoothResponse':
        if donor.empty or set(donor.workload_task) != {'inference'}:
            raise ValueError('Smooth response requires encoder donor measurements')
        if donor.model_id.nunique() < 2:
            raise ValueError('At least two donor models required')
        self.scaler.fit(response_coordinates(donor))
        self.poly.fit(self.scaler.transform(response_coordinates(donor)))
        self.regressor.fit(self._design(donor),
                           donor[['latency_residual', 'throughput_residual']].to_numpy())
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.regressor.predict(self._design(frame)), dtype=float)


def select_inner_hyperparameters(donor: pd.DataFrame) -> tuple[float, float, dict]:
    """Select response regularization/blend using donor conditions only.

    Every fourth exact condition is hidden across all donor models; the
    validation outcomes remain entirely within the outer training fold.
    Anchor measurements in these donor validation conditions are available to
    isolate the resource-response component from anchor-transfer error.
    """
    if set(donor.workload_task) != {'inference'} or donor.model_id.nunique() < 2:
        raise ValueError('Inner CV requires at least two encoder donor models')
    condition_fields = CONDITION[1:]
    keyed = donor.copy()
    keyed['condition_key'] = list(map(tuple, keyed[list(condition_fields)].to_numpy()))
    keys = sorted(keyed.condition_key.unique())
    if len(keys) < 8:
        raise ValueError('Insufficient donor conditions for four-way inner CV')
    alphas = (1.0, 10.0, 100.0)
    weights = (0.0, 0.5, 1.0)
    error = {(alpha, weight): [] for alpha in alphas for weight in weights}
    for fold in range(4):
        withheld = set(keys[fold::4])
        fit = keyed[~keyed.condition_key.isin(withheld)]
        val = keyed[keyed.condition_key.isin(withheld)]
        incumbent = fit_model(fit, 'anchor_conditioned', 42)
        et_latency = conditional_predict(incumbent, val, 'anchor_conditioned')[:, 0]
        actual = val.true_log_latency.to_numpy()
        scored = ~((val.replica_count == 1) & (val.cpu_core_limit == 2)).to_numpy()
        for alpha in alphas:
            response = SmoothResponse(alpha).fit(fit)
            smooth_latency = (val.anchor_log_latency.to_numpy() +
                              response.predict(val)[:, 0])
            for weight in weights:
                prediction = (1 - weight) * et_latency + weight * smooth_latency
                error[(alpha, weight)].append(
                    float(np.abs(prediction[scored] - actual[scored]).mean()))
    macro = {pair: float(np.mean(values)) for pair, values in error.items()}
    selected_alpha, selected_weight = min(macro, key=lambda pair: (macro[pair], pair))
    details = {f'alpha={alpha:g};blend={weight:g}': score
               for (alpha, weight), score in macro.items()}
    return selected_alpha, selected_weight, details


def donor_support(train: pd.DataFrame) -> tuple[dict[tuple, int], dict[str, int]]:
    """Count donor model IDs per exact static condition and per task."""
    anchors = train[(train.replica_count == 1) & (train.cpu_core_limit == 2)]
    exact = anchors.groupby(list(CONDITION), dropna=False).model_id.nunique().to_dict()
    task = anchors.groupby('workload_task').model_id.nunique().to_dict()
    return exact, task


def predict_response(static: pd.DataFrame, frozen: pd.DataFrame,
                     model: SmoothResponse | None,
                     support: tuple[dict[tuple, int], dict[str, int]],
                     latency_blend: float = LATENCY_BLEND) -> pd.DataFrame:
    """Predict from metadata and frozen K8 results, without target outcomes."""
    if not 0 <= latency_blend <= 1:
        raise ValueError('Latency blend must lie in [0,1]')
    if static.execution_id.duplicated().any() or frozen.execution_id.duplicated().any():
        raise ValueError('Duplicate candidate ID')
    columns = ['execution_id'] + [f'{method}_{metric}_log'
                                  for method in BASELINES for metric in ('latency', 'rps')]
    joined = static.merge(frozen[columns], on='execution_id', validate='one_to_one')
    if len(joined) != len(static):
        raise ValueError('Frozen and static rows do not match')
    if not np.isfinite(joined[[c for c in columns if c != 'execution_id']].to_numpy()).all():
        raise ValueError('Nonfinite frozen predictions')
    exact, task = support
    keys = list(map(tuple, joined[list(CONDITION)].to_numpy()))
    use_smooth = np.asarray([(name == 'inference' and task.get(name, 0) >= 2 and
                              exact.get(key, 0) < 2)
                             for name, key in zip(joined.workload_task, keys)], dtype=bool)
    need_response = joined.workload_task.eq('inference').to_numpy()
    if need_response.any() and model is None:
        raise ValueError('Encoder response model is missing')
    out = joined[['campaign', 'model_id', 'workload_group', 'execution_id',
                  'replica_count', 'cpu_core_limit', 'latency_slo_ms',
                  'target_throughput_rps']].copy()
    for method in BASELINES:
        for metric in ('latency', 'rps'):
            out[f'{method}_{metric}_log'] = joined[f'{method}_{metric}_log'].to_numpy()
    out['support_smooth_latency_latency_log'] = joined.space_cov_transfer_latency_log.to_numpy()
    out['support_smooth_latency_rps_log'] = joined.space_cov_transfer_rps_log.to_numpy()
    out['full_smooth_response_latency_log'] = joined.space_cov_transfer_latency_log.to_numpy()
    out['full_smooth_response_rps_log'] = joined.space_cov_transfer_rps_log.to_numpy()
    if need_response.any():
        subset = joined.loc[need_response].copy()
        anchors = joined[(joined.replica_count == 1) &
                         (joined.cpu_core_limit == 2)].set_index('workload_group')
        if anchors.index.duplicated().any() or subset.workload_group.isin(anchors.index).sum() != len(subset):
            raise ValueError('Expected one frozen anchor per workload')
        subset['anchor_log_latency'] = anchors.loc[
            subset.workload_group, 'space_cov_transfer_latency_log'].to_numpy()
        subset['anchor_log_requests_per_second'] = anchors.loc[
            subset.workload_group, 'space_cov_transfer_rps_log'].to_numpy()
        delta = model.predict(subset)
        physical_l = subset.anchor_log_latency.to_numpy() + delta[:, 0]
        physical_r = (subset.anchor_log_requests_per_second.to_numpy() +
                      np.log(subset.replica_count.to_numpy(dtype=float)) + delta[:, 1])
        baseline_l = subset.space_cov_transfer_latency_log.to_numpy()
        mask = use_smooth[need_response]
        hybrid = np.where(mask,
                          (1 - latency_blend) * baseline_l + latency_blend * physical_l,
                          baseline_l)
        out.loc[need_response, 'support_smooth_latency_latency_log'] = hybrid
        out.loc[need_response, 'full_smooth_response_latency_log'] = physical_l
        out.loc[need_response, 'full_smooth_response_rps_log'] = physical_r
        # Anchor identities are exact, including the eight measured anchors.
        anchor_mask = ((subset.replica_count == 1) &
                       (subset.cpu_core_limit == 2)).to_numpy()
        indices = subset.index[anchor_mask]
        out.loc[indices, 'full_smooth_response_latency_log'] = baseline_l[anchor_mask]
        out.loc[indices, 'full_smooth_response_rps_log'] = subset.space_cov_transfer_rps_log.to_numpy()[anchor_mask]
    out['uses_smooth_latency'] = use_smooth
    return out


def _verify_reference(reference: Path, snapshot_sha: str,
                      external: bool) -> None:
    metadata = json.loads((reference.parent / 'metrics.json').read_text())
    if metadata['source_snapshot_sha256'] != snapshot_sha:
        raise ValueError('Frozen sparse predictions use another snapshot')
    if external and metadata['external_observations_before_prediction'] != 0:
        raise ValueError('External reference used external probes')


def paired_model_bootstrap(scores: pd.DataFrame) -> list[dict]:
    """Descriptive model-level intervals for the incremental latency change."""
    rng = np.random.default_rng(42)
    output = []
    for campaign, subset in scores.groupby('campaign'):
        pivot = subset.pivot(index='heldout_model_id', columns='method',
                             values='latency_male').sort_index()
        delta = (pivot.support_smooth_latency -
                 pivot.space_cov_transfer).to_numpy(dtype=float)
        draws = delta[rng.integers(0, len(delta), size=(20000, len(delta)))].mean(axis=1)
        output.append({'campaign': campaign,
                       'delta_support_minus_cov_mean_per_model': float(delta.mean()),
                       'descriptive_model_bootstrap_95_interval':
                           [float(v) for v in np.quantile(draws, [0.025, 0.975])],
                       'per_model_delta': {str(model): float(value)
                                           for model, value in zip(pivot.index, delta)}})
    return output


def run(snapshot: Path, main_prediction: Path, external_prediction: Path,
        output: Path) -> dict:
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    _verify_reference(main_prediction, snapshot_sha, external=False)
    _verify_reference(external_prediction, snapshot_sha, external=True)
    raw = pd.read_parquet(snapshot)
    usable = raw[raw.eligible & raw.device.eq('cuda')].copy()
    main = usable[usable.campaign == CAMPAIGN].copy()
    external = usable[usable.campaign.isin(EXTERNAL_CAMPAIGNS)].copy()
    validate_slates(main)
    validate_slates(external)
    reference_main = pd.read_parquet(main_prediction)
    reference_main = reference_main[reference_main.budget == 8].copy()
    reference_external = pd.read_parquet(external_prediction)
    if (len(reference_main) != len(main) or len(reference_external) != len(external) or
            reference_main.execution_id.duplicated().any() or
            reference_external.execution_id.duplicated().any()):
        raise ValueError('Incomplete or duplicated frozen K8 predictions')
    if (set(reference_main.execution_id) != set(main.execution_id) or
            set(reference_external.execution_id) != set(external.execution_id)):
        raise ValueError('Frozen predictions refer to a different candidate slate')
    diagnostics = diagnose_resource_response(main)
    results, predictions, splits = [], [], []
    selected_hyperparameters = {}
    for held_model in sorted(main.model_id.unique()):
        train_raw = main[main.model_id != held_model]
        test_main = main[main.model_id == held_model]
        test_external = external[external.model_id == held_model]
        train = prepare(train_raw)
        encoder_train = train[train.workload_task == 'inference']
        alpha, blend, inner_cv = select_inner_hyperparameters(encoder_train)
        selected_hyperparameters[held_model] = {'alpha': alpha, 'latency_blend': blend,
                                                'inner_cv_latency_male': inner_cv}
        response = SmoothResponse(alpha).fit(encoder_train)
        support = donor_support(train)
        size = registered_parameter_bytes(test_main)
        for truth, frozen in ((test_main, reference_main),
                              (test_external, reference_external)):
            if truth.empty:
                continue
            fold_reference = frozen[frozen.model_id == held_model]
            static = static_features(truth, size)
            predicted = predict_response(static, fold_reference, response,
                                         support, latency_blend=blend)
            if set(predicted.execution_id) != set(truth.execution_id):
                raise ValueError('Held model prediction and truth mismatch')
            for campaign, campaign_truth in truth.groupby('campaign'):
                campaign_prediction = predicted[predicted.campaign == campaign]
                scores = scenario_metrics(campaign_prediction, campaign_truth,
                                          methods=METHODS)
                for method, score in scores.items():
                    results.append({'heldout_model_id': held_model,
                                    'campaign': campaign, 'method': method,
                                    'probe_budget': 8, **score})
            predictions.append(predicted)
        manifest = reference_main[reference_main.model_id == held_model]
        if manifest.space_probe_groups_json.nunique() != 1:
            raise ValueError('Expected one K8 medoid probe manifest per model')
        probe_groups = json.loads(manifest.space_probe_groups_json.iloc[0])
        if len(probe_groups) != 8 or len(set(probe_groups)) != 8:
            raise ValueError('Expected eight distinct existing probes')
        splits.append({'heldout_model_id': held_model,
                       'training_model_ids_json': json.dumps(sorted(train_raw.model_id.unique())),
                       'probe_groups_json': json.dumps(probe_groups),
                       'donor_exact_condition_count': len(support[0]),
                       'selected_alpha': alpha, 'selected_latency_blend': blend})
        print(f'{held_model}: completed support-gated response', flush=True)
    score_frame = pd.DataFrame(results)
    pred_frame = pd.concat(predictions, ignore_index=True)
    macro = score_frame.groupby(['campaign', 'method'], as_index=False).agg(
        models=('heldout_model_id', 'nunique'),
        groups=('scored_groups', 'sum'),
        latency_male=('latency_male', 'mean'),
        rps_male=('rps_male', 'mean'),
        successful_choices=('successful_choices', 'sum'),
        slo_violations=('slo_violations', 'sum'),
        feasible_groups=('feasible_groups', 'sum'))
    output.mkdir(parents=True, exist_ok=True)
    score_frame.to_csv(output / 'per_model_metrics.csv', index=False)
    pd.DataFrame(splits).to_csv(output / 'split_manifest.csv', index=False)
    pred_frame.to_parquet(output / 'predictions.parquet', index=False)
    report = {
        'scope': 'exploratory same-host single-GPU-type batch benchmark; generated SLOs',
        'source_snapshot_sha256': snapshot_sha,
        'main_prediction_sha256': hashlib.sha256(main_prediction.read_bytes()).hexdigest(),
        'external_prediction_sha256': hashlib.sha256(external_prediction.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'protocol': 'outer held-out model; donor main outcomes only; frozen common K8 medoid probes; no external probes',
        'hyperparameters': 'selected by donor-only four-fold condition CV within each outer model fold',
        'selected_hyperparameters': selected_hyperparameters,
        'support_rule': 'blend smooth latency only if >=2 same-task donor models exist but <2 have the exact condition; otherwise incumbent covariance prediction',
        'same_probe_budget_for_each_method': 8,
        'resource_pair_diagnostics': diagnostics,
        'aggregate': macro.to_dict(orient='records'),
        'paired_latency_bootstrap': paired_model_bootstrap(score_frame),
        'smooth_latency_rows': pred_frame.groupby('campaign').uses_smooth_latency.sum().astype(int).to_dict(),
    }
    (output / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'data/audit_v1/measurements.parquet')
    parser.add_argument('--main-predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_v1/predictions.parquet')
    parser.add_argument('--external-predictions', type=Path,
                        default=ROOT / 'artifacts/cross_model_covariance_external_v1/predictions.parquet')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/physical_response_transfer_v1')
    args = parser.parse_args()
    run(args.snapshot, args.main_predictions, args.external_predictions, args.output)
