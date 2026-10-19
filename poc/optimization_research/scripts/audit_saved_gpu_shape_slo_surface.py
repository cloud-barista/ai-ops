"""Exact retrospective SLO-sensitivity surface for pinned GPU-shape predictions.

The saved estimator is hashed but never loaded or refitted. Every rectangle in
the generated-SLO factor domain has a constant eight-candidate decision.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from audit_saved_gpu_shape_decisions import (
    CONFIGS, FACTORS, GROUP_KEY, INPUTS, METHODS, PRIMARY, ROOT,
    sha256_file, validated_gpu_rows, verify_pins,
)


PROTOCOL = ROOT / "reports/saved_gpu_shape_slo_surface_protocol.md"
PROTOCOL_SHA256 = "da46551644ae79ccb86213c04eaadca7e384dcb574bfb4e8c0c8688807166b1e"
PRIOR_DIR = ROOT / "artifacts/saved_gpu_shape_decision_audit_v1"
LOWER_FACTOR = 0.75
UPPER_FACTOR = 1.25
AUDITED_METHODS = (*METHODS, "fixed_gpu1_cpu2_median")
AREA_FIELDS = (
    "area_selected", "area_success", "area_false_approval",
    "area_feasible_abstain", "area_feasible_wrong_approval",
    "area_correct_min_shape", "area_oracle_feasible", "area_partial_feasible",
    "area_zero_feasible", "area_all_eight_feasible", "area_partial_selected",
    "area_partial_success", "area_partial_wrong_approval",
    "area_partial_abstain", "area_partial_correct_min_shape",
)
PRIOR_BOOLEAN_FIELDS = (
    "oracle_feasible", "selected", "true_success", "false_approval",
    "missed_feasible", "correct_min_shape",
)


def _slate_arrays(slate: pd.DataFrame) -> dict[str, np.ndarray | float]:
    """Use the validated, resource-ordered eight-row group only."""
    if len(slate) != 8 or tuple(map(tuple, slate[
            ["replica_count", "cpu_core_limit"]].itertuples(index=False, name=None))) != CONFIGS:
        raise ValueError("incomplete_or_unordered_eight_candidate_slate")
    batch = slate.batch_size.to_numpy(dtype=float)
    if not np.isfinite(batch).all() or np.any(batch <= 0):
        raise ValueError("nonpositive_or_nonfinite_surface_input")
    arrays: dict[str, np.ndarray | float] = {
        "latency_target": float(slate.latency_slo_ms.iloc[0]),
        "rps_target": float(slate.target_throughput_rps.iloc[0]),
        "actual_latency": slate.latency_p95_ms.to_numpy(dtype=float),
        "actual_rps": slate.examples_per_second.to_numpy(dtype=float) / batch,
        "stored_latency": slate.predicted_latency_p95_ms.to_numpy(dtype=float),
        "stored_rps": slate.predicted_examples_per_second.to_numpy(dtype=float) / batch,
        "median_latency": slate.baseline_latency_p95_ms.to_numpy(dtype=float),
        "median_rps": slate.baseline_examples_per_second.to_numpy(dtype=float) / batch,
    }
    for value in arrays.values():
        check = np.asarray(value, dtype=float)
        if not np.isfinite(check).all() or np.any(check <= 0):
            raise ValueError("nonpositive_or_nonfinite_surface_input")
    return arrays


def _bounded_breaks(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    interior = values[(values > LOWER_FACTOR) & (values < UPPER_FACTOR)]
    result = np.unique(np.concatenate(([LOWER_FACTOR, UPPER_FACTOR], interior)))
    if result[0] != LOWER_FACTOR or result[-1] != UPPER_FACTOR:
        raise AssertionError("surface_boundaries_changed")
    return result


def boundary_grid(arrays: dict[str, np.ndarray | float]) -> tuple[np.ndarray, np.ndarray]:
    """Include every truth and predictor threshold so decisions are constant."""
    latency = np.concatenate([
        np.asarray(arrays[key], dtype=float) / float(arrays["latency_target"])
        for key in ("actual_latency", "stored_latency", "median_latency")
    ])
    rps = np.concatenate([
        np.asarray(arrays[key], dtype=float) / float(arrays["rps_target"])
        for key in ("actual_rps", "stored_rps", "median_rps")
    ])
    return _bounded_breaks(latency), _bounded_breaks(rps)


def _candidate_pass(latency: np.ndarray, rps: np.ndarray,
                    target_latency: np.ndarray, target_rps: np.ndarray) -> np.ndarray:
    return ((latency[None, None, :] <= target_latency[:, None, None])
            & (rps[None, None, :] >= target_rps[None, :, None]))


def decision_masks(arrays: dict[str, np.ndarray | float], method: str,
                   latency_factors: np.ndarray, rps_factors: np.ndarray) -> dict[str, np.ndarray]:
    """Vectorized inclusive SLO rule, with GPU then CPU resource order."""
    if method not in AUDITED_METHODS:
        raise ValueError("unknown_surface_method")
    x = np.atleast_1d(np.asarray(latency_factors, dtype=float))
    y = np.atleast_1d(np.asarray(rps_factors, dtype=float))
    if (x.ndim != 1 or y.ndim != 1 or not len(x) or not len(y)
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or np.any(x <= 0) or np.any(y <= 0)):
        raise ValueError("invalid_slo_factors")
    latency_target = x * float(arrays["latency_target"])
    rps_target = y * float(arrays["rps_target"])
    actual = _candidate_pass(np.asarray(arrays["actual_latency"], dtype=float),
                             np.asarray(arrays["actual_rps"], dtype=float),
                             latency_target, rps_target)
    passing_count = actual.sum(axis=2)
    oracle_feasible = passing_count > 0
    oracle_index = actual.argmax(axis=2)
    if method == "fixed_gpu1_cpu2_median":
        predicted = _candidate_pass(
            np.asarray(arrays["median_latency"], dtype=float),
            np.asarray(arrays["median_rps"], dtype=float),
            latency_target, rps_target)
        selected = predicted[:, :, 0]
        chosen_index = np.zeros(oracle_feasible.shape, dtype=int)
    else:
        prefix = "stored" if method == "stored_model" else "median"
        predicted = _candidate_pass(
            np.asarray(arrays[f"{prefix}_latency"], dtype=float),
            np.asarray(arrays[f"{prefix}_rps"], dtype=float),
            latency_target, rps_target)
        selected = predicted.any(axis=2)
        chosen_index = predicted.argmax(axis=2)
    chosen_is_true = np.take_along_axis(
        actual, chosen_index[:, :, None], axis=2)[:, :, 0]
    success = selected & chosen_is_true
    partial = (passing_count > 0) & (passing_count < 8)
    correct_min = success & (chosen_index == oracle_index)
    return {
        "selected": selected,
        "success": success,
        "false_approval": selected & ~success,
        "feasible_abstain": oracle_feasible & ~selected,
        "feasible_wrong_approval": oracle_feasible & selected & ~success,
        "correct_min_shape": correct_min,
        "oracle_feasible": oracle_feasible,
        "partial_feasible": partial,
        "zero_feasible": passing_count == 0,
        "all_eight_feasible": passing_count == 8,
        "partial_selected": partial & selected,
        "partial_success": partial & success,
        "partial_wrong_approval": partial & selected & ~success,
        "partial_abstain": partial & ~selected,
        "partial_correct_min_shape": partial & correct_min,
        "passing_count": passing_count,
        "chosen_index": chosen_index,
        "oracle_index": oracle_index,
    }


def _point_record(arrays: dict[str, np.ndarray | float], method: str,
                  latency_factor: float, rps_factor: float) -> dict:
    masks = decision_masks(arrays, method, np.array([latency_factor]),
                           np.array([rps_factor]))
    extract = lambda name: masks[name][0, 0]
    chosen = int(extract("chosen_index"))
    oracle = int(extract("oracle_index"))
    is_selected = bool(extract("selected"))
    oracle_feasible = bool(extract("oracle_feasible"))
    count = int(extract("passing_count"))
    return {
        "oracle_feasible_configs": count,
        "difficulty": "zero" if count == 0 else "all_eight" if count == 8 else "one_to_seven",
        "oracle_feasible": oracle_feasible,
        "oracle_gpu": CONFIGS[oracle][0] if oracle_feasible else None,
        "oracle_cores": CONFIGS[oracle][1] if oracle_feasible else None,
        "selected": is_selected,
        "selected_gpu": CONFIGS[chosen][0] if is_selected else None,
        "selected_cores": CONFIGS[chosen][1] if is_selected else None,
        "true_success": bool(extract("success")),
        "false_approval": bool(extract("false_approval")),
        "missed_feasible": bool(oracle_feasible and not extract("success")),
        "correct_min_shape": bool(extract("correct_min_shape")),
    }


def verify_prior_rows(scored: pd.DataFrame, prior_rows: pd.DataFrame) -> int:
    """Replay all original and nine-factor saved decisions with the new scorer."""
    key = [*GROUP_KEY, "method", "scenario"]
    if len(prior_rows) != 2660 or prior_rows.duplicated(key).any():
        raise ValueError("prior_decision_rows_count_or_key_mismatch")
    _check_prior_bool_types(prior_rows)
    lookup = prior_rows.set_index(key, verify_integrity=True)
    checked = 0
    for group_key, slate in scored.groupby(GROUP_KEY, sort=True):
        arrays = _slate_arrays(slate)
        for method in METHODS:
            scenarios = [("original", 1.0, 1.0)]
            scenarios.extend((f"sensitivity_l{x:.2f}_r{y:.2f}", x, y)
                             for x in FACTORS for y in FACTORS)
            for scenario, x, y in scenarios:
                target = lookup.loc[(*group_key, method, scenario)]
                fresh = _point_record(arrays, method, x, y)
                for field, expected in fresh.items():
                    observed = target[field]
                    if expected is None:
                        if not pd.isna(observed):
                            raise ValueError(f"prior_nine_point_mismatch:{field}")
                    elif isinstance(expected, bool):
                        if type(observed) not in (bool, np.bool_) or observed != expected:
                            raise ValueError(f"prior_nine_point_mismatch:{field}")
                    elif observed != expected:
                        raise ValueError(f"prior_nine_point_mismatch:{field}")
                checked += 1
    if checked != len(prior_rows):
        raise ValueError("prior_nine_point_coverage_mismatch")
    return checked


def _check_prior_bool_types(prior_rows: pd.DataFrame) -> None:
    """A nonempty string such as 'False' must never become truthy by coercion."""
    if any(field not in prior_rows or not pd.api.types.is_bool_dtype(prior_rows[field])
           for field in PRIOR_BOOLEAN_FIELDS):
        raise ValueError("prior_boolean_column_not_boolean")


def score_surface(slate: pd.DataFrame, method: str) -> dict:
    arrays = _slate_arrays(slate)
    x_breaks, y_breaks = boundary_grid(arrays)
    x_mid = np.sqrt(x_breaks[:-1] * x_breaks[1:])
    y_mid = np.sqrt(y_breaks[:-1] * y_breaks[1:])
    weights = np.outer(np.log(x_breaks[1:] / x_breaks[:-1]),
                       np.log(y_breaks[1:] / y_breaks[:-1]))
    full_area = math.log(UPPER_FACTOR / LOWER_FACTOR) ** 2
    weights /= full_area
    if not np.isclose(weights.sum(), 1.0, rtol=0, atol=1e-12):
        raise AssertionError("surface_weights_not_one")
    masks = decision_masks(arrays, method, x_mid, y_mid)
    record = {column: slate[column].iloc[0] for column in GROUP_KEY}
    record.update(method=method, rectangle_count=int(weights.size),
                  latency_boundary_count=len(x_breaks),
                  rps_boundary_count=len(y_breaks))
    for field in AREA_FIELDS:
        flag = field.removeprefix("area_")
        record[field] = float(np.sum(weights * masks[flag]))
    if not np.isclose(record["area_zero_feasible"] + record["area_partial_feasible"]
                      + record["area_all_eight_feasible"], 1.0, atol=1e-12):
        raise AssertionError("truth_area_partition_failed")
    if not np.isclose(record["area_selected"], record["area_success"]
                      + record["area_false_approval"], atol=1e-12):
        raise AssertionError("selected_area_partition_failed")
    if not np.isclose(record["area_oracle_feasible"], record["area_success"]
                      + record["area_feasible_abstain"]
                      + record["area_feasible_wrong_approval"], atol=1e-12):
        raise AssertionError("feasible_area_partition_failed")
    if not np.isclose(record["area_partial_feasible"], record["area_partial_success"]
                      + record["area_partial_wrong_approval"]
                      + record["area_partial_abstain"], atol=1e-12):
        raise AssertionError("partial_area_partition_failed")
    return record


def _aggregate(part: pd.DataFrame) -> dict:
    result = {"workload_groups": len(part), "models": int(part.model_id.nunique())}
    result.update({field: float(part[field].mean()) for field in AREA_FIELDS})
    selected = result["area_selected"]
    possible = result["area_oracle_feasible"]
    result["false_approval_area_given_selected"] = (
        result["area_false_approval"] / selected if selected else None)
    result["successful_area_given_feasible"] = (
        result["area_success"] / possible if possible else None)
    partial = result["area_partial_feasible"]
    partial_selected = result["area_partial_selected"]
    result["successful_area_given_partial_feasible"] = (
        result["area_partial_success"] / partial if partial else None)
    result["abstained_area_given_partial_feasible"] = (
        result["area_partial_abstain"] / partial if partial else None)
    result["wrong_approval_area_given_partial_selected"] = (
        result["area_partial_wrong_approval"] / partial_selected
        if partial_selected else None)
    return result


def summarize(rows: pd.DataFrame, prior_rows: pd.DataFrame) -> dict:
    by_campaign = []
    by_model = []
    for (campaign, method), part in rows.groupby(["campaign", "method"], sort=True):
        by_campaign.append({"campaign": campaign, "method": method, **_aggregate(part)})
    for (campaign, model, method), part in rows.groupby(
            ["campaign", "model_id", "method"], sort=True):
        by_model.append({"campaign": campaign, "model_id": model,
                         "method": method, **_aggregate(part)})
    original = prior_rows[(prior_rows.method == "stored_model")
                          & (prior_rows.scenario == "original")]
    support = []
    for campaign, part in original.groupby("campaign", sort=True):
        count = int(part.difficulty.eq("one_to_seven").sum())
        threshold = 20 if campaign == PRIMARY else 10
        support.append({"campaign": campaign, "workload_groups": len(part),
                        "original_partially_feasible_workload_groups": count,
                        "minimum_for_quantitative_discussion": threshold,
                        "support_gate_passed": count >= threshold,
                        "interpretation": ("retrospective_descriptive_only" if count >= threshold
                                           else "support_limited_reference_only")})
    return {"campaign": by_campaign, "campaign_model": by_model,
            "original_target_support": support}


def run(output: Path) -> dict:
    pins = verify_pins()
    if sha256_file(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("surface_protocol_sha256_mismatch")
    prior_metrics_path = PRIOR_DIR / "metrics.json"
    prior_rows_path = PRIOR_DIR / "decision_rows.csv"
    prior_metrics = json.loads(prior_metrics_path.read_text(encoding="utf-8"))
    if (prior_metrics["input_sha256"] != pins
            or prior_metrics["decision_rows_sha256"] != sha256_file(prior_rows_path)
            or prior_metrics["script_sha256"] != sha256_file(
                ROOT / "scripts/audit_saved_gpu_shape_decisions.py")):
        raise ValueError("prior_audit_provenance_mismatch")
    prior_rows = pd.read_csv(prior_rows_path)
    snapshot = pd.read_parquet(INPUTS["snapshot"][0])
    predictions = pd.read_parquet(INPUTS["predictions"][0])
    manifest = pd.read_csv(INPUTS["manifest"][0])
    scored = validated_gpu_rows(snapshot, predictions, manifest)
    replayed = verify_prior_rows(scored, prior_rows)
    records = [score_surface(slate, method)
               for _, slate in scored.groupby(GROUP_KEY, sort=True)
               for method in AUDITED_METHODS]
    rows = pd.DataFrame(records)
    if len(rows) != 133 * len(AUDITED_METHODS):
        raise AssertionError("unexpected_surface_workload_count")
    summary = summarize(rows, prior_rows)
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "per_workload_surface.csv"
    rows.to_csv(rows_path, index=False, float_format="%.17g")
    report = {
        "scope": "retrospective same-host static batch benchmark SLO sensitivity; not independent validation or VM scaling evidence",
        "factor_bounds": [LOWER_FACTOR, UPPER_FACTOR],
        "measure": "uniform area in natural-log latency-factor by natural-log requests-per-second-factor",
        "input_sha256": pins,
        "surface_protocol_sha256": PROTOCOL_SHA256,
        "surface_script_sha256": sha256_file(Path(__file__)),
        "prior_decision_rows_sha256": sha256_file(prior_rows_path),
        "prior_rows_replayed": replayed,
        "per_workload_surface_sha256": sha256_file(rows_path),
        "throughput_unit": "requests/s = examples_per_second / batch_size",
        "resource_order": "fewest GPU replicas, then fewest CPU affinity cores",
        "group_weight": "equal unique workload groups within each campaign; rectangle area is not an independent sample",
        "summary": summary,
    }
    (output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/saved_gpu_shape_slo_surface_v1")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"prior_rows_replayed": result["prior_rows_replayed"],
                      "original_target_support": result["summary"]["original_target_support"],
                      "campaign": result["summary"]["campaign"]},
                     ensure_ascii=False, indent=2))
