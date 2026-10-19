"""Rolling-origin audit of GenTD26 request-duration p90 calibration.

Each origin refits its base predictor using labels assumed available before the
fit cutoff. The following calibration, policy-selection, and test windows are
strictly chronological. True completion times are absent from this trace:
availability is assumed to be creation + logged execution time + one hour.

This is a retrospective stability audit, not an independent prospective test
or an evaluation of VM scaling actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from evaluate_gentd26_request_latency import (
    DEFAULT_DATA,
    Q,
    WINDOWS,
    encode_features,
    fit_feature_state,
    load_requests,
    metrics,
    model_only_quantile,
    train_quantile,
)
from evaluate_gentd26_subgroup import (
    _evaluate_candidates,
    blend_offsets,
    causal_global_offsets,
    causal_group_offsets,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/gentd26_walkforward_v1"
ORIGINS = tuple(pd.Timestamp(day) for day in (
    "2024-11-29", "2024-12-01", "2024-12-03", "2024-12-05", "2024-12-07"
))
WINDOW = pd.Timedelta(days=2)
FEEDBACK_DELAY_SECONDS = 3600
METHODS = (
    "base", "static", "global_rolling", "selected_group",
    "model_only", "model_only_rolling", "empirical_rolling",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assumed_availability(frame: pd.DataFrame, delay_seconds: int) -> pd.Series:
    if delay_seconds < 0:
        raise ValueError("feedback delay must be nonnegative")
    return frame.t + pd.to_timedelta(
        frame.exec_time_seconds + delay_seconds, unit="s"
    )


def split_origin(
    data: pd.DataFrame, test_start: pd.Timestamp,
    delay_seconds: int = FEEDBACK_DELAY_SECONDS,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Make chronological fit, calibration, selection and disjoint test slices."""
    test_start = pd.Timestamp(test_start)
    fit_end = test_start - 2 * WINDOW
    selection_start = test_start - WINDOW
    test_end = test_start + WINDOW
    t = data.t
    fit_pool = data.loc[t.lt(fit_end)].copy()
    train = fit_pool.loc[assumed_availability(fit_pool, delay_seconds).le(fit_end)].copy()
    frames = {
        "fit": train,
        "calibration": data.loc[t.ge(fit_end) & t.lt(selection_start)].copy(),
        "selection": data.loc[t.ge(selection_start) & t.lt(test_start)].copy(),
        "test": data.loc[t.ge(test_start) & t.lt(test_end)].copy(),
    }
    boundaries = {
        "fit_end_exclusive": str(fit_end),
        "calibration_end_exclusive": str(selection_start),
        "selection_end_exclusive_test_start": str(test_start),
        "test_end_exclusive": str(test_end),
    }
    if min(map(len, frames.values())) < 100:
        raise ValueError(f"Too few requests in origin {test_start}: "
                         f"{ {name: len(frame) for name, frame in frames.items()} }")
    assert frames["fit"].t.max() < fit_end
    assert assumed_availability(frames["fit"], delay_seconds).max() <= fit_end
    assert frames["calibration"].t.max() < frames["selection"].t.min()
    assert frames["selection"].t.max() < frames["test"].t.min()
    return frames, boundaries


def select_rolling_window(
    calibration: pd.DataFrame, selection: pd.DataFrame,
    delay_seconds: int,
) -> tuple[int | None, list[dict]]:
    """Choose window using only the policy-selection block."""
    y = selection.exec_time_seconds.to_numpy(float)
    base = selection.base_p90.to_numpy(float)
    candidates = []
    for window in WINDOWS:
        offset = causal_global_offsets(
            calibration, selection, window, delay_seconds
        )
        candidates.append({
            "window": window,
            **metrics(y, np.maximum(0, base + offset)),
        })
    winner = min(candidates, key=lambda row: row["pinball_seconds"])
    return winner["window"], candidates


def predict_rolling(
    history: pd.DataFrame, test: pd.DataFrame,
    window: int | None, delay_seconds: int,
) -> np.ndarray:
    offset = causal_global_offsets(history, test, window, delay_seconds)
    return np.maximum(0, test.base_p90.to_numpy(float) + offset)


def static_offset(
    history: pd.DataFrame, cutoff: pd.Timestamp, delay_seconds: int,
) -> float:
    """Frozen residual p90 from feedback assumed available at test start."""
    available = history.loc[
        assumed_availability(history, delay_seconds).le(cutoff)
    ]
    if available.empty:
        return 0.0
    return float(np.quantile(
        available.exec_time_seconds.to_numpy(float)
        - available.base_p90.to_numpy(float), Q,
    ))


def selectable_feedback(
    frame: pd.DataFrame, cutoff: pd.Timestamp, delay_seconds: int,
) -> pd.DataFrame:
    """Only outcomes known at the decision time may score a policy choice."""
    return frame.loc[assumed_availability(frame, delay_seconds).le(cutoff)].copy()


def _with_base(frame: pd.DataFrame, prediction: np.ndarray) -> pd.DataFrame:
    # Reuse the causal calibration implementation for all three base types.
    result = frame[["t", "exec_time_seconds"]].copy()
    result["base_p90"] = prediction
    return result


def evaluate_fold(
    frames: dict[str, pd.DataFrame], boundaries: dict[str, str],
    delay_seconds: int,
) -> tuple[dict, pd.DataFrame]:
    train, calibration, selection, test = (
        frames[name].copy() for name in
        ("fit", "calibration", "selection", "test")
    )
    state = fit_feature_state(train)
    model = train_quantile(train, state)
    for frame in (calibration, selection, test):
        frame["base_p90"] = model.predict(encode_features(frame, state))
        frame["model_only_p90"] = model_only_quantile(train, frame)
        frame["zero_p90"] = 0.0

    test_start = pd.Timestamp(boundaries["selection_end_exclusive_test_start"])
    selection_scored = selectable_feedback(selection, test_start, delay_seconds)
    if len(selection_scored) < 100:
        raise ValueError("Too few selection outcomes available at test start")

    global_window, global_selection = select_rolling_window(
        calibration, selection_scored, delay_seconds
    )
    group_candidates, _ = _evaluate_candidates(
        calibration, selection_scored, global_window,
        np.ones(len(selection_scored), dtype=bool), delay_seconds,
    )
    group_winner = min(group_candidates, key=lambda row: row["pinball_seconds"])

    history = pd.concat([calibration, selection], ignore_index=True)
    global_offset = causal_global_offsets(
        history, test, global_window, delay_seconds
    )
    y = test.exec_time_seconds.to_numpy(float)
    base = test.base_p90.to_numpy(float)
    predictions = {
        "base": base,
        "static": np.maximum(0, base + static_offset(
            history, test_start, delay_seconds,
        )),
        "global_rolling": np.maximum(0, base + global_offset),
        "model_only": test.model_only_p90.to_numpy(float),
    }
    if group_winner["group"] == "global":
        predictions["selected_group"] = predictions["global_rolling"].copy()
        support = np.ones(len(test), dtype=bool)
        support_count = np.zeros(len(test), dtype=int)
    else:
        local_offset, support_count = causal_group_offsets(
            history, test, group_winner["group"],
            int(group_winner["window"]), delay_seconds,
        )
        blended, support = blend_offsets(
            global_offset, local_offset, support_count,
            int(group_winner["minimum"]), int(group_winner["shrinkage"]),
        )
        predictions["selected_group"] = np.maximum(0, base + blended)

    baseline_choices = {}
    for name, column in (("model_only", "model_only_p90"),
                         ("empirical", "zero_p90")):
        cal = _with_base(calibration, calibration[column].to_numpy(float))
        sel = _with_base(selection_scored, selection_scored[column].to_numpy(float))
        sel_history = _with_base(selection, selection[column].to_numpy(float))
        tst = _with_base(test, test[column].to_numpy(float))
        window, candidates = select_rolling_window(cal, sel, delay_seconds)
        baseline_choices[name] = {"window": window, "selection": candidates}
        predictions[f"{name}_rolling"] = predict_rolling(
            pd.concat([cal, sel_history], ignore_index=True), tst,
            window, delay_seconds,
        )

    result = {
        "boundaries": boundaries,
        "rows": {name: len(frame) for name, frame in frames.items()},
        "selection_scored_rows_available_at_test_start": len(selection_scored),
        "global_window": global_window,
        "global_selection": global_selection,
        "group_selection": group_candidates,
        "selected_group": {key: group_winner[key] for key in (
            "group", "window", "minimum", "shrinkage", "pinball_seconds",
            "coverage", "supported_fraction",
        )},
        "selected_group_test_supported_fraction": float(support.mean()),
        "baseline_choices": baseline_choices,
        "test": {name: metrics(y, predictions[name]) for name in METHODS},
    }
    pred_frame = test[["t", "predict_type", "checkpoint_model_version_id",
                       "exec_time_seconds"]].copy()
    pred_frame["origin"] = boundaries["selection_end_exclusive_test_start"]
    for name in METHODS:
        pred_frame[f"{name}_p90"] = predictions[name]
    pred_frame["selected_group_support_count"] = support_count
    return result, pred_frame


def _by_group(frame: pd.DataFrame, group: str) -> dict:
    result = {}
    for key, rows in frame.groupby(group, dropna=False):
        if len(rows) < 20:
            continue
        y = rows.exec_time_seconds.to_numpy(float)
        result[str(key)] = {
            name: metrics(y, rows[f"{name}_p90"].to_numpy(float))
            for name in ("global_rolling", "selected_group")
        }
    return dict(sorted(result.items(), key=lambda item: -item[1]["global_rolling"]["n"]))


def paired_day_bootstrap(frame: pd.DataFrame, repeats: int = 20000) -> dict:
    """Descriptive paired uncertainty, resampling the ten observed days."""
    y = frame.exec_time_seconds.to_numpy(float)
    global_pred = frame.global_rolling_p90.to_numpy(float)
    group_pred = frame.selected_group_p90.to_numpy(float)
    pinball = lambda p: np.maximum(Q * (y - p), (Q - 1) * (y - p))
    temporary = pd.DataFrame({
        "day": frame.t.dt.date.to_numpy(),
        "loss_delta": pinball(group_pred) - pinball(global_pred),
        "coverage_delta": (y <= group_pred).astype(float) - (y <= global_pred).astype(float),
    })
    daily = temporary.groupby("day").agg(
        n=("loss_delta", "size"),
        loss_sum=("loss_delta", "sum"),
        coverage_sum=("coverage_delta", "sum"),
    )
    rng = np.random.default_rng(20260928)
    indices = rng.integers(0, len(daily), size=(repeats, len(daily)))
    totals = daily.n.to_numpy()[indices].sum(axis=1)
    loss = daily.loss_sum.to_numpy()[indices].sum(axis=1) / totals
    coverage = daily.coverage_sum.to_numpy()[indices].sum(axis=1) / totals
    return {
        "unit": "calendar day",
        "days": len(daily),
        "repeats": repeats,
        "seed": 20260928,
        "group_minus_global_pinball_seconds": float(temporary.loss_delta.mean()),
        "pinball_95pct_interval_seconds": np.quantile(loss, [.025, .975]).tolist(),
        "group_minus_global_coverage": float(temporary.coverage_delta.mean()),
        "coverage_95pct_interval": np.quantile(coverage, [.025, .975]).tolist(),
        "interpretation": "exploratory; does not account for method development or model-selection uncertainty",
    }


def evaluate(
    data_path: Path = DEFAULT_DATA, output: Path = DEFAULT_OUTPUT,
    delay_seconds: int = FEEDBACK_DELAY_SECONDS,
    origins: tuple[pd.Timestamp, ...] = ORIGINS,
) -> dict:
    if delay_seconds < 0:
        raise ValueError("feedback delay must be nonnegative")
    if any(b - a < WINDOW for a, b in zip(origins, origins[1:])):
        raise ValueError("test windows must not overlap")
    data = load_requests(data_path)
    rows = []
    predictions = []
    for origin in origins:
        frames, boundaries = split_origin(data, origin, delay_seconds)
        fold, pred = evaluate_fold(frames, boundaries, delay_seconds)
        fit_pool = data.loc[data.t.lt(pd.Timestamp(boundaries["fit_end_exclusive"]))]
        fold["discarded_unavailable_fit_rows"] = len(fit_pool) - len(frames["fit"])
        rows.append(fold)
        predictions.append(pred)
    all_predictions = pd.concat(predictions, ignore_index=True)
    if all_predictions.groupby("t").origin.nunique().max() > 1:
        raise AssertionError("test windows must be disjoint")
    y = all_predictions.exec_time_seconds.to_numpy(float)
    result = {
        "dataset": "Alibaba GenTD26 lora_request_trace.csv",
        "source_sha256": sha256(data_path),
        "script_sha256": sha256(Path(__file__)),
        "imported_helper_script_sha256": {
            "evaluate_gentd26_request_latency.py": sha256(
                ROOT / "scripts/evaluate_gentd26_request_latency.py"
            ),
            "evaluate_gentd26_subgroup.py": sha256(
                ROOT / "scripts/evaluate_gentd26_subgroup.py"
            ),
        },
        "sklearn_version": sklearn.__version__,
        "target": "successful request exec_time_seconds",
        "quantile": Q,
        "assumed_availability": "gmt_create + exec_time_seconds + additional delay",
        "assumed_additional_feedback_delay_seconds": delay_seconds,
        "design": "expanding fit; 2-day calibration, selection, and disjoint test; 5 rolling origins",
        "origins": rows,
        "pooled_test": {
            name: metrics(y, all_predictions[f"{name}_p90"].to_numpy(float))
            for name in METHODS
        },
        "pooled_by_predict_type": _by_group(all_predictions, "predict_type"),
        "pooled_by_model": _by_group(all_predictions, "checkpoint_model_version_id"),
        "paired_day_bootstrap": paired_day_bootstrap(all_predictions),
        "test_days": {
            str(day): {
                name: metrics(frame.exec_time_seconds.to_numpy(float),
                              frame[f"{name}_p90"].to_numpy(float))
                for name in ("global_rolling", "selected_group")
            }
            for day, frame in all_predictions.groupby(all_predictions.t.dt.date)
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    all_predictions.to_parquet(output / "test_predictions.parquet", index=False)
    (output / "metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedback-delay-seconds", type=int,
                        default=FEEDBACK_DELAY_SECONDS)
    args = parser.parse_args()
    result = evaluate(args.data, args.output, args.feedback_delay_seconds)
    print(json.dumps({
        "origins": [{
            "test_start": row["boundaries"]["selection_end_exclusive_test_start"],
            "selected_group": row["selected_group"],
            "global": row["test"]["global_rolling"],
            "selected": row["test"]["selected_group"],
        } for row in result["origins"]],
        "pooled_test": result["pooled_test"],
    }, indent=2))
