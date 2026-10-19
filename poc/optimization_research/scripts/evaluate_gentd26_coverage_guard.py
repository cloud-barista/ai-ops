"""Exploratory coverage guard for the five GenTD26 walk-forward request folds.

Only labels assumed available before each test origin tune the guard. The
trace has no actual completion timestamp or scale-action counterfactuals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate_gentd26_request_latency import (
    DEFAULT_DATA, encode_features, fit_feature_state, load_requests, metrics,
    train_quantile,
)
from evaluate_gentd26_subgroup import (
    _evaluate_candidates, blend_offsets, causal_global_offsets,
    causal_group_offsets,
)
from evaluate_gentd26_walkforward import (
    FEEDBACK_DELAY_SECONDS, ORIGINS, ROOT, selectable_feedback,
    select_rolling_window, sha256, split_origin,
)


DEFAULT_OUTPUT = ROOT / "artifacts/gentd26_coverage_guard_v1"
BLENDS = (0.0, 0.25, 0.5, 0.75, 1.0)
TARGETS = (0.90, 0.92)
MIN_GROUP_SUPPORT = 100
GROUPS = ("predict_type", "checkpoint_model_version_id")


def minimum_margin(y: np.ndarray, prediction: np.ndarray, target: float) -> float:
    """Smallest nonnegative additive margin attaining empirical target coverage."""
    if not 0 < target <= 1:
        raise ValueError("target must be in (0, 1]")
    if len(y) != len(prediction) or not len(y):
        raise ValueError("nonempty, matching label and prediction arrays required")
    residual = np.sort(np.asarray(y, float) - np.asarray(prediction, float))
    kth = int(np.ceil(target * len(residual))) - 1
    return float(max(0.0, residual[kth]))


def _keys(frame: pd.DataFrame, column: str) -> np.ndarray:
    return frame[column].astype("string").fillna("<MISSING>").to_numpy(str)


def predict_guard(
    frame: pd.DataFrame, global_prediction: np.ndarray,
    group_prediction: np.ndarray, policy: dict,
) -> np.ndarray:
    result = (
        (1 - policy["blend"]) * np.asarray(global_prediction, float)
        + policy["blend"] * np.asarray(group_prediction, float)
        + policy["global_margin_seconds"]
    )
    for column in GROUPS:
        keys = _keys(frame, column)
        for key, margin in policy["group_margins_seconds"][column].items():
            result[keys == key] += margin
    return result


def fit_guard(
    selection: pd.DataFrame, global_prediction: np.ndarray,
    group_prediction: np.ndarray, blend: float, target: float,
    min_group_support: int = MIN_GROUP_SUPPORT,
) -> dict:
    if not 0 <= blend <= 1 or min_group_support < 1:
        raise ValueError("invalid blend or support threshold")
    y = selection.exec_time_seconds.to_numpy(float)
    current = (1 - blend) * np.asarray(global_prediction, float) + blend * np.asarray(group_prediction, float)
    if len(current) != len(selection):
        raise ValueError("prediction length mismatch")
    global_margin = minimum_margin(y, current, target)
    current = current + global_margin
    group_margins = {column: {} for column in GROUPS}
    group_support = {column: {} for column in GROUPS}
    for column in GROUPS:
        keys = _keys(selection, column)
        for key in sorted(set(keys)):
            selected = keys == key
            count = int(selected.sum())
            if count < min_group_support:
                continue
            margin = minimum_margin(y[selected], current[selected], target)
            group_margins[column][str(key)] = margin
            group_support[column][str(key)] = count
            current[selected] += margin
    policy = {
        "blend": blend,
        "target_selection_coverage": target,
        "global_margin_seconds": global_margin,
        "group_margins_seconds": group_margins,
        "group_support": group_support,
        "selection": metrics(y, current),
        "selection_supported_coverage": {
            column: {
                key: float(np.mean(y[_keys(selection, column) == key]
                                   <= current[_keys(selection, column) == key]))
                for key in group_support[column]
            }
            for column in GROUPS
        },
    }
    return policy


def selected_group_prediction(
    history: pd.DataFrame, evaluation: pd.DataFrame,
    global_offset: np.ndarray, winner: dict, delay_seconds: int,
) -> np.ndarray:
    base = evaluation.base_p90.to_numpy(float)
    if winner["group"] == "global":
        return np.maximum(0, base + global_offset)
    local, counts = causal_group_offsets(
        history, evaluation, winner["group"], int(winner["window"]),
        delay_seconds,
    )
    blended, _ = blend_offsets(
        global_offset, local, counts, int(winner["minimum"]),
        int(winner["shrinkage"]),
    )
    return np.maximum(0, base + blended)


def _subgroup_metrics(frame: pd.DataFrame, column: str, key: str) -> dict:
    selected = _keys(frame, column) == key
    rows = frame.loc[selected]
    return {
        name: metrics(rows.exec_time_seconds.to_numpy(float), rows[f"{name}_p90"].to_numpy(float))
        for name in ("global_rolling", "selected_group", "guard90", "guard92")
    }


def evaluate(
    data_path: Path = DEFAULT_DATA, output: Path = DEFAULT_OUTPUT,
    delay_seconds: int = FEEDBACK_DELAY_SECONDS,
    origins: tuple[pd.Timestamp, ...] = ORIGINS,
) -> dict:
    if delay_seconds < 0:
        raise ValueError("feedback delay must be nonnegative")
    if any(b - a < pd.Timedelta(days=2) for a, b in zip(origins, origins[1:])):
        raise ValueError("test windows must not overlap")
    data = load_requests(data_path)
    folds = []
    predictions = []
    for origin in origins:
        frames, bounds = split_origin(data, origin, delay_seconds)
        fit, calibration, selection, test = (
            frames[name].copy() for name in ("fit", "calibration", "selection", "test")
        )
        state = fit_feature_state(fit)
        model = train_quantile(fit, state)
        for frame in (calibration, selection, test):
            frame["base_p90"] = model.predict(encode_features(frame, state))
        test_start = pd.Timestamp(bounds["selection_end_exclusive_test_start"])
        selection_scored = selectable_feedback(selection, test_start, delay_seconds)
        if len(selection_scored) < 100:
            raise ValueError("too few available selection labels")
        global_window, _ = select_rolling_window(
            calibration, selection_scored, delay_seconds
        )
        candidates, _ = _evaluate_candidates(
            calibration, selection_scored, global_window,
            np.ones(len(selection_scored), bool), delay_seconds,
        )
        winner = min(candidates, key=lambda row: row["pinball_seconds"])
        selection_global_offset = causal_global_offsets(
            calibration, selection_scored, global_window, delay_seconds,
        )
        selection_global = np.maximum(
            0, selection_scored.base_p90.to_numpy(float) + selection_global_offset
        )
        selection_group = selected_group_prediction(
            calibration, selection_scored, selection_global_offset, winner,
            delay_seconds,
        )
        policies = {}
        for target in TARGETS:
            eligible = [
                fit_guard(selection_scored, selection_global, selection_group, blend, target)
                for blend in BLENDS
            ]
            policy = min(eligible, key=lambda row: row["selection"]["pinball_seconds"])
            policy["all_blend_selection"] = [
                {"blend": row["blend"], **row["selection"]} for row in eligible
            ]
            policies[f"guard{int(target * 100)}"] = policy
        history = pd.concat([calibration, selection], ignore_index=True)
        test_global_offset = causal_global_offsets(
            history, test, global_window, delay_seconds,
        )
        test_global = np.maximum(0, test.base_p90.to_numpy(float) + test_global_offset)
        test_group = selected_group_prediction(
            history, test, test_global_offset, winner, delay_seconds,
        )
        fold_pred = test[["t", "predict_type", "checkpoint_model_version_id", "exec_time_seconds"]].copy()
        fold_pred["origin"] = str(origin)
        fold_pred["global_rolling_p90"] = test_global
        fold_pred["selected_group_p90"] = test_group
        for name, policy in policies.items():
            fold_pred[f"{name}_p90"] = predict_guard(
                test, test_global, test_group, policy,
            )
        y = test.exec_time_seconds.to_numpy(float)
        folds.append({
            "boundaries": bounds,
            "rows": {name: len(frame) for name, frame in frames.items()},
            "selection_scored_rows": len(selection_scored),
            "global_window": global_window,
            "selected_group": {key: winner[key] for key in (
                "group", "window", "minimum", "shrinkage", "pinball_seconds", "coverage",
            )},
            "guard_policies": policies,
            "test": {
                name: metrics(y, fold_pred[f"{name}_p90"].to_numpy(float))
                for name in ("global_rolling", "selected_group", "guard90", "guard92")
            },
        })
        predictions.append(fold_pred)
    pooled = pd.concat(predictions, ignore_index=True)
    if pooled.groupby("t").origin.nunique().max() > 1:
        raise AssertionError("overlapping tests")
    y = pooled.exec_time_seconds.to_numpy(float)
    result = {
        "source": "Alibaba GenTD26 lora_request_trace.csv",
        "source_sha256": sha256(data_path),
        "script_sha256": sha256(Path(__file__)),
        "helper_sha256": {
            name: sha256(ROOT / "scripts" / name) for name in (
                "evaluate_gentd26_request_latency.py",
                "evaluate_gentd26_subgroup.py",
                "evaluate_gentd26_walkforward.py",
            )
        },
        "assumed_feedback_delay_seconds": delay_seconds,
        "guard_design": "validation-only nonnegative margins for global, then supported request type, then supported model ID; select blend by validation pinball",
        "selection_targets": list(TARGETS),
        "minimum_group_selection_support": MIN_GROUP_SUPPORT,
        "blends": list(BLENDS),
        "folds": folds,
        "pooled_test": {
            name: metrics(y, pooled[f"{name}_p90"].to_numpy(float))
            for name in ("global_rolling", "selected_group", "guard90", "guard92")
        },
        "pooled_IMG_2_IMG": _subgroup_metrics(pooled, "predict_type", "IMG_2_IMG"),
        "pooled_M0003": _subgroup_metrics(pooled, "checkpoint_model_version_id", "M0003"),
    }
    output.mkdir(parents=True, exist_ok=True)
    pooled.to_parquet(output / "test_predictions.parquet", index=False)
    (output / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedback-delay-seconds", type=int, default=FEEDBACK_DELAY_SECONDS)
    args = parser.parse_args()
    result = evaluate(args.data, args.output, args.feedback_delay_seconds)
    print(json.dumps({
        "pooled_test": result["pooled_test"],
        "pooled_IMG_2_IMG": result["pooled_IMG_2_IMG"],
        "pooled_M0003": result["pooled_M0003"],
    }, indent=2))
