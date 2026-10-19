"""Causal subgroup residual calibration for the GenTD26 request p90 study.

This is a retrospective request-duration experiment, not a scale-action policy.
The frozen base model comes from evaluate_gentd26_request_latency.py. True
completion timestamps are absent, so a request's correction uses only outcomes
whose *assumed* availability precedes its creation timestamp. The default
availability assumption adds one hour to creation time plus logged duration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from evaluate_gentd26_request_latency import (
    DEFAULT_DATA,
    Q,
    TRAIN_END,
    VALID_CAL_END,
    VALID_END,
    WINDOWS,
    assumed_feedback_time,
    causal_rolling_offsets,
    encode_features,
    load_requests,
    metrics,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_MODEL = ROOT / "artifacts/gentd26_request_latency_v1/model.joblib"
BASE_DIR_BY_LAG = {
    0: ROOT / "artifacts/gentd26_request_latency_delay0",
    3600: ROOT / "artifacts/gentd26_request_latency_v1",
}
DEFAULT_OUTPUT = ROOT / "artifacts/gentd26_subgroup_v1"
DEFAULT_FEEDBACK_LAG_SECONDS = 3600
LOCAL_WINDOWS = (50, 100, 200, 500)
MIN_COUNTS = (20, 50, 100)
SHRINKAGES = (0, 50, 200)
GROUPS = ("predict_type", "checkpoint_model_version_id")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _availability_pools(
    frame: pd.DataFrame, group_column: str, feedback_lag_seconds: float = 0,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Map each group to (assumed feedback-availability times, residuals)."""
    available = frame["t"] + pd.to_timedelta(
        frame["exec_time_seconds"] + feedback_lag_seconds, unit="s"
    )
    values = frame["exec_time_seconds"].to_numpy(float) - frame["base_p90"].to_numpy(float)
    keys = frame[group_column].astype("string").fillna("<MISSING>").to_numpy(str)
    result = {}
    for key in np.unique(keys):
        selected = np.flatnonzero(keys == key)
        order = np.argsort(available.to_numpy()[selected], kind="stable")
        result[str(key)] = (available.to_numpy()[selected][order], values[selected][order])
    return result


def causal_group_offsets(
    history: pd.DataFrame,
    evaluation: pd.DataFrame,
    group_column: str,
    window: int,
    feedback_lag_seconds: float = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """p90 residual from the last `window` assumed-available group outcomes.

    The local support count is returned so callers can fall back to the global
    correction for a new or sparse group. The evaluation frame is included in
    the pool, but each label enters only at its assumed availability time.
    """
    if window < 1:
        raise ValueError("window must be positive")
    if feedback_lag_seconds < 0:
        raise ValueError("feedback lag must be nonnegative")
    if group_column not in GROUPS:
        raise ValueError(f"Unsupported group: {group_column}")
    combined = pd.concat([history, evaluation], ignore_index=True)
    pools = _availability_pools(combined, group_column, feedback_lag_seconds)
    keys = evaluation[group_column].astype("string").fillna("<MISSING>").to_numpy(str)
    requests = evaluation["t"].to_numpy()
    offsets = np.zeros(len(evaluation), dtype=float)
    counts = np.zeros(len(evaluation), dtype=int)
    for key in np.unique(keys):
        indices = np.flatnonzero(keys == key)
        available, residuals = pools[str(key)]
        prior = np.searchsorted(available, requests[indices], side="right")
        for index, count in zip(indices, prior):
            start = max(0, int(count) - window)
            counts[index] = int(count) - start
            if count > start:
                offsets[index] = float(np.quantile(residuals[start:count], Q))
    return offsets, counts


def causal_global_offsets(
    history: pd.DataFrame,
    evaluation: pd.DataFrame,
    window: int | None,
    feedback_lag_seconds: float = 0,
) -> np.ndarray:
    """Global rolling residual gated by assumed feedback-availability time."""
    if feedback_lag_seconds < 0:
        raise ValueError("feedback lag must be nonnegative")
    if feedback_lag_seconds == 0:
        return causal_rolling_offsets(history, evaluation, window)
    if window is not None and window < 1:
        raise ValueError("window must be positive")
    combined = pd.concat([history, evaluation], ignore_index=True)
    available = combined.t + pd.to_timedelta(
        combined.exec_time_seconds + feedback_lag_seconds, unit="s"
    )
    order = np.argsort(available.to_numpy(), kind="stable")
    available_times = available.to_numpy()[order]
    residuals = (combined.exec_time_seconds - combined.base_p90).to_numpy(float)[order]
    counts = np.searchsorted(available_times, evaluation.t.to_numpy(), side="right")
    offsets = np.zeros(len(evaluation), dtype=float)
    for i, count in enumerate(counts):
        start = 0 if window is None else max(0, int(count) - window)
        if count > start:
            offsets[i] = float(np.quantile(residuals[start:count], Q))
    return offsets


def blend_offsets(
    global_offsets: np.ndarray,
    local_offsets: np.ndarray,
    counts: np.ndarray,
    minimum: int,
    shrinkage: int,
) -> tuple[np.ndarray, np.ndarray]:
    if minimum < 1 or shrinkage < 0:
        raise ValueError("minimum must be positive and shrinkage nonnegative")
    support = counts >= minimum
    weight = np.zeros(len(counts), dtype=float)
    weight[support] = counts[support] / (counts[support] + shrinkage)
    return global_offsets + weight * (local_offsets - global_offsets), support


def _frames(data_path: Path, model_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = load_requests(data_path)
    valid = data.loc[data.t.ge(TRAIN_END) & data.t.lt(VALID_END)].copy()
    test = data.loc[data.t.ge(VALID_END)].copy()
    frozen = joblib.load(model_path)
    for frame in (valid, test):
        frame["base_p90"] = frozen["quantile_model"].predict(
            encode_features(frame, frozen["feature_state"])
        )
    return valid, test


def _evaluate_candidates(
    history: pd.DataFrame,
    evaluation: pd.DataFrame,
    global_window: int | None,
    selection_available: np.ndarray,
    feedback_lag_seconds: float = 0,
) -> tuple[list[dict], np.ndarray]:
    global_offset = causal_global_offsets(
        history, evaluation, global_window, feedback_lag_seconds
    )
    y = evaluation.exec_time_seconds.to_numpy(float)
    base = evaluation.base_p90.to_numpy(float)
    candidates = [{
        "group": "global", "window": global_window,
        "minimum": None, "shrinkage": None, "supported_fraction": 1.0,
        **metrics(y[selection_available], np.maximum(0, base + global_offset)[selection_available]),
    }]
    for group in GROUPS:
        for window in LOCAL_WINDOWS:
            local_offset, counts = causal_group_offsets(
                history, evaluation, group, window, feedback_lag_seconds
            )
            for minimum in MIN_COUNTS:
                for shrinkage in SHRINKAGES:
                    offset, supported = blend_offsets(
                        global_offset, local_offset, counts, minimum, shrinkage
                    )
                    candidates.append({
                        "group": group, "window": window,
                        "minimum": minimum, "shrinkage": shrinkage,
                        "supported_fraction": float(supported[selection_available].mean()),
                        **metrics(y[selection_available], np.maximum(0, base + offset)[selection_available]),
                    })
    return candidates, global_offset


def select_global_window(
    history: pd.DataFrame,
    evaluation: pd.DataFrame,
    selection_available: np.ndarray,
    feedback_lag_seconds: float,
) -> tuple[int | None, list[dict]]:
    """Repeat the original global-window choice at the same feedback delay."""
    rows = []
    base = evaluation.base_p90.to_numpy(float)
    y = evaluation.exec_time_seconds.to_numpy(float)
    for window in WINDOWS:
        offset = causal_global_offsets(history, evaluation, window, feedback_lag_seconds)
        rows.append({
            "window": window,
            **metrics(y[selection_available], np.maximum(0, base + offset)[selection_available]),
        })
    return min(rows, key=lambda row: row["pinball_seconds"])["window"], rows


def _subgroup_metrics(frame: pd.DataFrame, predictions: np.ndarray, group: str) -> dict:
    result = {}
    for key, subgroup in frame.groupby(group, dropna=False):
        if len(subgroup) < 20:
            continue
        indices = frame.index.get_indexer(subgroup.index)
        result[str(key)] = metrics(
            subgroup.exec_time_seconds.to_numpy(float), predictions[indices]
        )
    return dict(sorted(result.items(), key=lambda item: -item[1]["n"]))


def evaluate(
    data_path: Path, model_path: Path, output: Path,
    feedback_lag_seconds: float = DEFAULT_FEEDBACK_LAG_SECONDS,
) -> dict:
    if feedback_lag_seconds < 0:
        raise ValueError("feedback lag must be nonnegative")
    valid, test = _frames(data_path, model_path)
    if min(len(valid), len(test)) < 100:
        raise ValueError("Not enough requests for chronological evaluation")
    calibration = valid.loc[valid.t.lt(VALID_CAL_END)].copy()
    selection = valid.loc[valid.t.ge(VALID_CAL_END)].copy()
    # Selection labels must be assumed available before the test starts.
    # Keep all validation requests in the causal pool; score only this mask.
    selection_available = assumed_feedback_time(
        selection, feedback_lag_seconds
    ).le(VALID_END).to_numpy()
    if selection_available.sum() < 100:
        raise ValueError("Not enough available validation outcomes for selection")
    global_window, global_candidates = select_global_window(
        calibration, selection, selection_available, feedback_lag_seconds
    )
    candidates, _ = _evaluate_candidates(
        calibration, selection, global_window, selection_available,
        feedback_lag_seconds,
    )
    winner = min(candidates, key=lambda row: row["pinball_seconds"])
    # All validation requests are eligible to enter the pool, including those
    # whose assumed feedback arrives during test. The causal gate decides when.
    validation_history = valid
    global_offset = causal_global_offsets(
        validation_history, test, global_window, feedback_lag_seconds
    )
    global_p90 = np.maximum(0, test.base_p90.to_numpy(float) + global_offset)
    if winner["group"] == "global":
        selected_p90 = global_p90.copy()
        support = np.ones(len(test), dtype=bool)
        support_count = np.zeros(len(test), dtype=int)
    else:
        local_offset, support_count = causal_group_offsets(
            validation_history, test, winner["group"], int(winner["window"]),
            feedback_lag_seconds,
        )
        offset, support = blend_offsets(
            global_offset, local_offset, support_count,
            int(winner["minimum"]), int(winner["shrinkage"])
        )
        selected_p90 = np.maximum(0, test.base_p90.to_numpy(float) + offset)
    # Persist each simple group alternative too. They are diagnostic, not
    # post-test selected policies.
    group_predictions = {}
    group_metrics = {}
    for group in GROUPS:
        local_offset, count = causal_group_offsets(
            validation_history, test, group, 200, feedback_lag_seconds
        )
        offset, supported = blend_offsets(global_offset, local_offset, count, 50, 50)
        pred = np.maximum(0, test.base_p90.to_numpy(float) + offset)
        group_predictions[group] = pred
        group_metrics[group] = {
            "all": metrics(test.exec_time_seconds.to_numpy(float), pred),
            "supported_fraction": float(supported.mean()),
            "by_predict_type": _subgroup_metrics(test, pred, "predict_type"),
            "by_model": _subgroup_metrics(test, pred, "checkpoint_model_version_id"),
        }
    reference_dir = BASE_DIR_BY_LAG.get(feedback_lag_seconds)
    reference_meta = json.loads((reference_dir / "metrics.json").read_text()) if reference_dir else None
    reference_delay = (
        float(reference_meta["assumed_feedback_delay_seconds"])
        if reference_meta is not None else None
    )
    reference_difference = None
    if reference_meta is not None:
        if feedback_lag_seconds != reference_delay:
            raise ValueError("Reference delay does not match experiment delay")
        if sha256(model_path) != sha256(reference_dir / "model.joblib"):
            raise ValueError("Frozen model differs from delay-matched reference")
        reference = pd.read_parquet(reference_dir / "test_predictions.parquet")
        if len(reference) != len(test) or not np.array_equal(reference.t.to_numpy(), test.t.to_numpy()):
            raise ValueError("Frozen reference predictions do not match test rows")
        if reference_meta["chosen_window"] != global_window:
            raise ValueError("Validation-selected global window changed")
        reference_difference = float(np.max(np.abs(reference.rolling_p90.to_numpy() - global_p90)))
    if reference_difference is not None and reference_difference > 1e-6:
        raise ValueError(f"Global baseline changed: max difference {reference_difference}")
    result = {
        "dataset": "Alibaba GenTD26 lora_request_trace.csv",
        "source_sha256": sha256(data_path),
        "base_model_sha256": sha256(model_path),
        "script_sha256": sha256(Path(__file__)),
        "reference_max_abs_difference_seconds": reference_difference,
        "feedback_lag_seconds": feedback_lag_seconds,
        "reference_feedback_lag_seconds": reference_delay,
        "rows": {
            "validation_calibration": len(calibration),
            "validation_selection_total": len(selection),
            "validation_selection_available": int(selection_available.sum()),
            "test": len(test),
        },
        "split": {"calibration_end_exclusive": str(VALID_CAL_END), "test_start": str(VALID_END)},
        "target": "successful request exec_time_seconds",
        "quantile": Q,
        "global_window": global_window,
        "global_validation_candidates": global_candidates,
        "validation_candidates": candidates,
        "selected": {key: winner[key] for key in ("group", "window", "minimum", "shrinkage", "pinball_seconds", "coverage")},
        "test": {
            "global": metrics(test.exec_time_seconds.to_numpy(float), global_p90),
            "selected": metrics(test.exec_time_seconds.to_numpy(float), selected_p90),
            "selected_supported_fraction": float(support.mean()),
            "global_by_predict_type": _subgroup_metrics(test, global_p90, "predict_type"),
            "selected_by_predict_type": _subgroup_metrics(test, selected_p90, "predict_type"),
            "global_by_model": _subgroup_metrics(test, global_p90, "checkpoint_model_version_id"),
            "selected_by_model": _subgroup_metrics(test, selected_p90, "checkpoint_model_version_id"),
            "fixed_group_alternatives": group_metrics,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    predictions = test[["t", "predict_type", "checkpoint_model_version_id", "exec_time_seconds", "base_p90"]].copy()
    predictions["global_p90"] = global_p90
    predictions["selected_p90"] = selected_p90
    predictions["selected_local_support_count"] = support_count
    for group, pred in group_predictions.items():
        predictions[f"fixed_{group}_p90"] = pred
    predictions.to_parquet(output / "test_predictions.parquet", index=False)
    (output / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", type=Path,
                        help="Frozen base model (defaults to the matching delay artifact)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedback-lag-seconds", type=float,
                        default=DEFAULT_FEEDBACK_LAG_SECONDS,
                        help="Additional delay before an outcome is considered available")
    args = parser.parse_args()
    if args.model is None:
        reference_dir = BASE_DIR_BY_LAG.get(args.feedback_lag_seconds)
        if reference_dir is None:
            parser.error("Supply --model for a feedback lag other than 0 or 3600 seconds")
        model_path = reference_dir / "model.joblib"
    else:
        model_path = args.model
    result = evaluate(args.data, model_path, args.output, args.feedback_lag_seconds)
    print(json.dumps({"selected": result["selected"], "test": result["test"]["selected"]}, indent=2))
