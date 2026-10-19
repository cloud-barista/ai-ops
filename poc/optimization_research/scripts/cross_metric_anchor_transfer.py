"""Exploratory two-output residual transfer for sparse GPU shape profiling.

Only donor-model out-of-fold residuals build the prior. Target-model outcomes
enter the posterior at the eight explicitly selected GPU1/CPU2 anchors.
Previous model/campaign outcomes are never treated as target observations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cross_model_covariance import (
    KEY_COLUMNS, NOISE_FRACTION, RHO, _coordinates, _keyed_anchor,
    _predict_response, posterior_residual, predict_sparse, residual_covariance,
)
from cross_model_covariance_external import _base_kernel, continuous_prior
from conditional_response import ANCHOR, BASE_FEATURES
from sparse_anchor_transfer import (
    _anchor_rows, registered_parameter_bytes, static_features, validate_slates,
)


OUTPUT_COLUMNS = ("residual_latency", "residual_rps")


def _output_moment(donor_oof: pd.DataFrame) -> np.ndarray:
    residuals = donor_oof[list(OUTPUT_COLUMNS)].to_numpy(dtype=float)
    if residuals.ndim != 2 or residuals.shape[1] != 2 or len(residuals) < 2:
        raise ValueError("at_least_two_donor_residual_rows_required")
    if not np.isfinite(residuals).all():
        raise ValueError("nonfinite_donor_residual")
    moment = residuals.T @ residuals / len(residuals)
    moment[np.diag_indices(2)] = np.maximum(np.diag(moment), 0.02 ** 2)
    if np.linalg.eigvalsh(moment).min() < -1e-10:
        raise ValueError("non_psd_donor_output_moment")
    return moment


def _aligned_surfaces(ordered: pd.DataFrame, donor_oof: pd.DataFrame,
                      *, interpolate: bool) -> tuple[np.ndarray | None, list[str]]:
    donor = donor_oof.copy()
    donor["condition_key"] = list(map(tuple, donor[list(KEY_COLUMNS)].to_numpy()))
    same_task = donor[donor.workload_task == ordered.workload_task.iloc[0]]
    groups = {name: part.set_index("condition_key")
              for name, part in same_task.groupby("model_id", sort=True)}
    if len(groups) < 2:
        return None, []
    target_keys = list(ordered.index)
    if not interpolate:
        selected = {name: part for name, part in groups.items()
                    if part.index.is_unique and set(target_keys).issubset(part.index)}
        if len(selected) < 2:
            return None, []
        return np.stack([part.loc[target_keys, list(OUTPUT_COLUMNS)].to_numpy(dtype=float)
                         for part in selected.values()]), list(selected)

    if any(not part.index.is_unique for part in groups.values()):
        raise ValueError("duplicate_donor_condition")
    common = set.intersection(*(set(part.index) for part in groups.values()))
    if not common:
        return None, []
    reference_keys = sorted(common)
    reference = next(iter(groups.values())).loc[reference_keys]
    x_reference = _coordinates(reference)
    x_target = _coordinates(ordered)
    k_rr = _base_kernel(x_reference, x_reference)
    k_tr = _base_kernel(x_target, x_reference)
    interpolation = np.linalg.solve(k_rr + 0.1 * np.eye(len(reference_keys)),
                                    k_tr.T).T
    surfaces = np.stack([
        interpolation @ part.loc[reference_keys, list(OUTPUT_COLUMNS)].to_numpy(dtype=float)
        for part in groups.values()
    ])
    return surfaces, list(groups)


def joint_residual_prior(target_anchors: pd.DataFrame, donor_oof: pd.DataFrame,
                         *, interpolate: bool = False,
                         rho: float = RHO) -> tuple[np.ndarray, np.ndarray, dict]:
    """Build coupled prior and its exact independent-output ablation.

    Flattening uses (condition index, output index) in C order. The independent
    ablation is required to equal the previously published two-output prior
    on each diagonal block. No target performance result is read here.
    """
    if not 0 <= rho <= 1:
        raise ValueError("invalid_smooth_weight")
    if interpolate and rho != RHO:
        raise ValueError("interpolated_prior_requires_frozen_smooth_weight")
    ordered = _keyed_anchor(target_anchors)
    n = len(ordered)
    moment = _output_moment(donor_oof)
    coords = _coordinates(ordered)
    kernel = _base_kernel(coords, coords)
    smooth = np.kron(kernel, moment)
    surfaces, names = _aligned_surfaces(ordered, donor_oof,
                                         interpolate=interpolate)
    if surfaces is None:
        # Preserve the old no-aligned-donor fallback exactly, including Qwen.
        joint = np.array(smooth, copy=True)
        joint[0::2, 1::2] = 0.0
        joint[1::2, 0::2] = 0.0
        mode = "independent_kernel_fallback"
    else:
        flat = surfaces.reshape(len(surfaces), n * 2)
        empirical = flat.T @ flat / len(flat)
        joint = (1.0 - rho) * empirical + rho * smooth
        mode = "interpolated_joint_prior" if interpolate else "aligned_joint_prior"
    independent = np.array(joint, copy=True)
    independent[0::2, 1::2] = 0.0
    independent[1::2, 0::2] = 0.0

    old, old_info = (continuous_prior(ordered, donor_oof) if interpolate else
                     residual_covariance(ordered, donor_oof, rho=rho))
    for output in range(2):
        if not np.allclose(independent[output::2, output::2], old[:, :, output],
                           rtol=1e-11, atol=1e-11):
            raise AssertionError("independent_prior_does_not_match_existing_method")
    if not np.isfinite(joint).all() or not np.allclose(joint, joint.T, atol=1e-10):
        raise ValueError("invalid_joint_prior")
    smallest = float(np.linalg.eigvalsh(joint)[0])
    if smallest < -1e-7 * max(1.0, float(np.diag(joint).max())):
        raise ValueError("joint_prior_not_psd")
    return joint, independent, {
        "mode": mode, "donor_models": names,
        "legacy_mode": old_info["mode"],
        "output_second_moment": moment.tolist(),
        "minimum_eigenvalue": smallest,
    }


def joint_posterior_residual(prior: np.ndarray, observed_indices: np.ndarray,
                             observed_residual: np.ndarray,
                             noise_fraction: float = NOISE_FRACTION) -> np.ndarray:
    """Condition both output residuals together, preserving exact anchor values."""
    covariance = np.asarray(prior, dtype=float)
    values = np.asarray(observed_residual, dtype=float)
    selected = np.asarray(observed_indices, dtype=int)
    if covariance.ndim != 2 or covariance.shape[0] != covariance.shape[1] or covariance.shape[0] % 2:
        raise ValueError("invalid_joint_prior_shape")
    n = covariance.shape[0] // 2
    if (selected.ndim != 1 or not len(selected) or values.shape != (len(selected), 2)
            or len(set(selected)) != len(selected) or (selected < 0).any()
            or (selected >= n).any() or noise_fraction <= 0
            or not np.isfinite(covariance).all() or not np.isfinite(values).all()):
        raise ValueError("invalid_joint_observations")
    output_variance = [max(float(np.diag(covariance[o::2, o::2]).mean()), 1e-8)
                       for o in range(2)]
    flat_selected = (selected[:, None] * 2 + np.arange(2)).reshape(-1)
    noise = noise_fraction * np.tile(output_variance, len(selected))
    posterior = covariance[:, flat_selected] @ np.linalg.solve(
        covariance[np.ix_(flat_selected, flat_selected)] + np.diag(noise),
        values.reshape(-1),
    )
    result = posterior.reshape(n, 2)
    result[selected] = values
    return result


def assert_independent_posterior_parity(independent: np.ndarray,
                                        legacy: np.ndarray,
                                        selected: np.ndarray,
                                        values: np.ndarray) -> None:
    """Fail before scoring if the proposed ablation changes the old method."""
    expected = posterior_residual(legacy, selected, values)
    actual = joint_posterior_residual(independent, selected, values)
    if not np.allclose(actual, expected, rtol=1e-10, atol=1e-10):
        raise AssertionError("independent_posterior_does_not_match_existing_method")


def predict_with_joint_prior(test_raw: pd.DataFrame, probes: list[str],
                             joint: np.ndarray, independent: np.ndarray,
                             legacy: np.ndarray, absolute, conditional,
                             *, parameter_bytes: float | None = None) -> pd.DataFrame:
    """Append joint and ablation predictions to the unchanged old baselines."""
    validate_slates(test_raw)
    anchors = _keyed_anchor(test_raw)
    groups = anchors.workload_group.tolist()
    index = {group: i for i, group in enumerate(groups)}
    if len(probes) != 8 or len(set(probes)) != 8 or not set(probes).issubset(index):
        raise ValueError("exactly_eight_distinct_anchor_probes_required")
    n = len(groups)
    if joint.shape != (2 * n, 2 * n) or independent.shape != joint.shape or legacy.shape != (n, n, 2):
        raise ValueError("prior_and_workload_grid_disagree")
    known_size = (registered_parameter_bytes(test_raw)
                  if parameter_bytes is None else float(parameter_bytes))
    static = static_features(test_raw, known_size)
    baseline = np.asarray(absolute.predict(static[list(BASE_FEATURES)]), dtype=float)
    anchor_mask = ((static.replica_count == ANCHOR[0]) &
                   (static.cpu_core_limit == ANCHOR[1])).to_numpy()
    baseline_anchor = pd.DataFrame(
        baseline[anchor_mask], index=static.loc[anchor_mask, "workload_group"],
        columns=["latency", "rps"],
    ).loc[groups]
    measured = _anchor_rows(test_raw).loc[probes]
    selected = np.array([index[group] for group in probes], dtype=int)
    values = np.column_stack((
        np.log(measured.latency_p95_ms.to_numpy(dtype=float)),
        np.log((measured.examples_per_second / measured.batch_size).to_numpy(dtype=float)),
    )) - baseline_anchor.loc[probes].to_numpy()
    assert_independent_posterior_parity(independent, legacy, selected, values)

    result = predict_sparse(test_raw, probes, probes, legacy, absolute, conditional,
                            parameter_bytes=known_size)
    group_indices = np.array([index[group] for group in static.workload_group])
    measured_rows = anchor_mask & np.isin(group_indices, selected)
    for method, prior in (("joint_cross_metric", joint),
                          ("joint_no_cross", independent)):
        residual = joint_posterior_residual(prior, selected, values)
        inferred = baseline_anchor + residual
        prediction = _predict_response(static, inferred, conditional)
        prediction[measured_rows] = inferred.loc[
            static.loc[measured_rows, "workload_group"]].to_numpy()
        result[f"{method}_latency_log"] = prediction[:, 0]
        result[f"{method}_rps_log"] = prediction[:, 1]
    if not np.allclose(result.joint_no_cross_latency_log,
                       result.space_cov_transfer_latency_log,
                       rtol=1e-10, atol=1e-10):
        raise AssertionError("ablation_latency_response_changed")
    if not np.allclose(result.joint_no_cross_rps_log,
                       result.space_cov_transfer_rps_log,
                       rtol=1e-10, atol=1e-10):
        raise AssertionError("ablation_throughput_response_changed")
    return result
