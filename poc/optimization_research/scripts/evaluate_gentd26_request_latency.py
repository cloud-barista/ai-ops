"""Chronological request-duration quantiles on Alibaba's GenTD26 trace.

The output is research evidence about request complexity and latency. It does
not estimate a VM count or the effect of a scaling action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data/external/gentd26/lora_request_trace.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/gentd26_request_latency_v1"
NUMERIC = (
    "prompt_length",
    "negative_prompt_length",
    "negative_prompt_missing",
    "num_images_per_prompt",
    "num_inference_steps",
    "num_lora",
    "image_steps",
)
CATEGORICAL = ("predict_type", "checkpoint_model_version_id")
Q = 0.9
TRAIN_END = pd.Timestamp("2024-11-29")
VALID_END = pd.Timestamp("2024-12-04")
VALID_CAL_END = pd.Timestamp("2024-12-01")
WINDOWS = (200, 500, 1000, 2000, None)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_requests(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    required = set(CATEGORICAL) | {
        "gmt_create", "predict_status", "exec_time_seconds",
        "prompt_length", "negative_prompt_length", "num_images_per_prompt",
        "num_inference_steps", "num_lora",
    }
    if not required.issubset(data.columns):
        raise ValueError(f"Missing request columns: {sorted(required - set(data.columns))}")
    data = data.loc[data["predict_status"].eq("SUCCEED")].copy()
    data["t"] = pd.to_datetime(data["gmt_create"], errors="raise")
    data = data.loc[
        np.isfinite(data["exec_time_seconds"])
        & data["exec_time_seconds"].gt(0)
    ].copy()
    data["negative_prompt_missing"] = data["negative_prompt_length"].isna().astype(float)
    data["negative_prompt_length"] = data["negative_prompt_length"].fillna(0)
    data["image_steps"] = (
        data["num_images_per_prompt"] * data["num_inference_steps"]
    )
    return data.sort_values("t", kind="stable").reset_index(drop=True)


def fit_feature_state(train: pd.DataFrame) -> dict:
    return {
        "categories": {
            col: {str(value): i for i, value in enumerate(sorted(train[col].dropna().unique()))}
            for col in CATEGORICAL
        },
        "medians": {col: float(train[col].median()) for col in NUMERIC},
    }


def encode_features(data: pd.DataFrame, state: dict, *, use_model: bool = True) -> np.ndarray:
    encoded = []
    for col in CATEGORICAL:
        if col == "checkpoint_model_version_id" and not use_model:
            values = np.full(len(data), -1, dtype=float)
        else:
            values = data[col].astype("string").map(state["categories"][col]).fillna(-1).to_numpy(float)
        encoded.append(values)
    for col in NUMERIC:
        values = pd.to_numeric(data[col], errors="coerce").fillna(state["medians"][col])
        encoded.append(values.to_numpy(float))
    return np.column_stack(encoded)


def train_quantile(train: pd.DataFrame, state: dict, *, use_model: bool = True):
    model = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=Q,
        categorical_features=[0, 1],
        max_iter=140,
        max_leaf_nodes=15,
        min_samples_leaf=60,
        l2_regularization=10,
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(encode_features(train, state, use_model=use_model), train["exec_time_seconds"])
    return model


def model_only_quantile(train: pd.DataFrame, future: pd.DataFrame, minimum: int = 30) -> np.ndarray:
    groups = train.groupby("checkpoint_model_version_id")["exec_time_seconds"]
    counts = groups.size()
    estimates = groups.quantile(Q)
    known = future["checkpoint_model_version_id"].map(estimates)
    supported = future["checkpoint_model_version_id"].map(counts).ge(minimum)
    return known.where(supported, train["exec_time_seconds"].quantile(Q)).to_numpy(float)


def assumed_feedback_time(frame: pd.DataFrame, feedback_delay_seconds: int) -> pd.Series:
    """A sensitivity assumption: real completion/recording times are absent."""
    if feedback_delay_seconds < 0:
        raise ValueError("feedback delay must be nonnegative")
    return (
        frame["t"]
        + pd.to_timedelta(frame["exec_time_seconds"], unit="s")
        + pd.to_timedelta(feedback_delay_seconds, unit="s")
    )


def training_rows_available_at_cutoff(
    data: pd.DataFrame,
    cutoff: pd.Timestamp,
    feedback_delay_seconds: int,
) -> pd.DataFrame:
    """Training labels assumed available by the frozen model's fit time."""
    created_before = data.loc[data.t.lt(cutoff)]
    available = assumed_feedback_time(created_before, feedback_delay_seconds).le(cutoff)
    return created_before.loc[available].copy()


def causal_rolling_offsets(
    history: pd.DataFrame,
    evaluation: pd.DataFrame,
    window: int | None,
    feedback_delay_seconds: int = 0,
) -> np.ndarray:
    """Quantile of residuals with assumed availability before creation time.

    `history` and `evaluation` require columns t, exec_time_seconds, base_p90.
    True completion timestamps are absent. The assumed availability time is
    creation + logged duration + additional feedback delay. This is only a
    sensitivity assumption, never proof of operational label availability.
    """
    if window is not None and window < 1:
        raise ValueError("window must be positive")
    combined = pd.concat([history, evaluation], ignore_index=True)
    completed = assumed_feedback_time(combined, feedback_delay_seconds)
    order = np.argsort(completed.to_numpy(), kind="stable")
    completed_ns = completed.to_numpy()[order]
    residuals = (
        combined["exec_time_seconds"] - combined["base_p90"]
    ).to_numpy(float)[order]
    request_ns = evaluation["t"].to_numpy()
    prior_counts = np.searchsorted(completed_ns, request_ns, side="right")
    offsets = np.zeros(len(evaluation), dtype=float)
    for i, count in enumerate(prior_counts):
        start = 0 if window is None else max(0, count - window)
        if count > start:
            offsets[i] = float(np.quantile(residuals[start:count], Q))
    return offsets


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "n": int(len(y)),
        "pinball_seconds": float(mean_pinball_loss(y, p, alpha=Q)),
        "coverage": float(np.mean(y <= p)),
        "mean_predicted_seconds": float(np.mean(p)),
        "mean_target_seconds": float(np.mean(y)),
        "mean_shortfall_seconds": float(np.mean(np.maximum(y - p, 0))),
    }


def rolling_comparison(
    valid: pd.DataFrame, test: pd.DataFrame, prediction_column: str,
    feedback_delay_seconds: int = 3600,
) -> tuple[list[dict], int | None, np.ndarray]:
    """Select a past-observation window on validation, then replay test."""
    cols = ["t", "exec_time_seconds", prediction_column]
    valid_frame = valid[cols].rename(columns={prediction_column: "base_p90"})
    test_frame = test[cols].rename(columns={prediction_column: "base_p90"})
    init = valid_frame.loc[valid_frame.t.lt(VALID_CAL_END)]
    validation = valid_frame.loc[valid_frame.t.ge(VALID_CAL_END)]
    # Window selection itself must use only outcomes assumed available at the
    # test boundary. This excludes late validation requests whose labels would
    # arrive during the test period.
    selection_available = assumed_feedback_time(validation, feedback_delay_seconds).le(VALID_END).to_numpy()
    if selection_available.sum() < 100:
        raise ValueError("Not enough available validation outcomes for window selection")
    candidates = []
    for window in WINDOWS:
        offsets = causal_rolling_offsets(init, validation, window, feedback_delay_seconds)
        pred = np.maximum(0, validation.base_p90.to_numpy() + offsets)
        candidates.append({
            "window": window,
            **metrics(validation.exec_time_seconds.to_numpy()[selection_available], pred[selection_available]),
        })
    chosen = min(candidates, key=lambda item: item["pinball_seconds"])["window"]
    # Requests from late validation enter the replay when their assumed
    # feedback becomes available, even if that is after the test boundary.
    offsets = causal_rolling_offsets(valid_frame, test_frame, chosen, feedback_delay_seconds)
    return candidates, chosen, np.maximum(0, test_frame.base_p90.to_numpy() + offsets)


def evaluate(data_path: Path, output: Path, feedback_delay_seconds: int = 3600) -> dict:
    data = load_requests(data_path)
    train_candidates = data.loc[data.t.lt(TRAIN_END)]
    train = training_rows_available_at_cutoff(data, TRAIN_END, feedback_delay_seconds)
    valid = data.loc[data.t.ge(TRAIN_END) & data.t.lt(VALID_END)].copy()
    test = data.loc[data.t.ge(VALID_END)].copy()
    if min(len(train), len(valid), len(test)) < 100:
        raise ValueError("Not enough requests in chronological splits")
    state = fit_feature_state(train)
    model = train_quantile(train, state)
    no_model = train_quantile(train, state, use_model=False)
    for frame in (valid, test):
        frame["base_p90"] = model.predict(encode_features(frame, state))
        frame["no_model_p90"] = no_model.predict(encode_features(frame, state, use_model=False))
        frame["model_only_p90"] = model_only_quantile(train, frame)
        frame["zero_p90"] = 0.0
    candidates, choice, test["rolling_p90"] = rolling_comparison(
        valid, test, "base_p90", feedback_delay_seconds
    )
    model_candidates, model_choice, test["model_only_rolling_p90"] = rolling_comparison(
        valid, test, "model_only_p90", feedback_delay_seconds
    )
    empirical_candidates, empirical_choice, test["empirical_rolling_p90"] = rolling_comparison(
        valid, test, "zero_p90", feedback_delay_seconds
    )

    # Only validation labels assumed available at test start can set a fixed correction.
    valid_completed = assumed_feedback_time(valid, feedback_delay_seconds)
    known_valid = valid.loc[valid_completed.le(VALID_END)].copy()
    static_offset = float(np.quantile(known_valid.exec_time_seconds - known_valid.base_p90, Q))
    test["static_p90"] = np.maximum(0, test.base_p90 + static_offset)
    test["rolling_offset"] = test.rolling_p90 - test.base_p90
    y = test.exec_time_seconds.to_numpy()
    result = {
        "dataset": "Alibaba GenTD26 lora_request_trace.csv",
        "source_sha256": sha256(data_path),
        "script_sha256": sha256(Path(__file__)),
        "sklearn_version": sklearn.__version__,
        "rows": {
            "train": len(train),
            "train_created_before_cutoff": len(train_candidates),
            "train_excluded_unavailable": len(train_candidates) - len(train),
            "validation": len(valid),
            "test": len(test),
        },
        "split": {"train_end_exclusive": str(TRAIN_END), "test_start": str(VALID_END)},
        "target": "successful request exec_time_seconds",
        "quantile": Q,
        "assumed_feedback_delay_seconds": feedback_delay_seconds,
        "valid_rolling_selection": candidates,
        "chosen_window": choice,
        "baseline_rolling_selection": {
            "model_only": {"candidates": model_candidates, "chosen_window": model_choice},
            "empirical": {"candidates": empirical_candidates, "chosen_window": empirical_choice},
        },
        "static_offset_seconds": static_offset,
        "test": {
            name: metrics(y, test[column].to_numpy())
            for name, column in (
                ("model_only", "model_only_p90"),
                ("no_model", "no_model_p90"),
                ("base", "base_p90"),
                ("static", "static_p90"),
                ("rolling", "rolling_p90"),
                ("model_only_rolling", "model_only_rolling_p90"),
                ("empirical_rolling", "empirical_rolling_p90"),
            )
        },
        "test_by_day": {
            str(day): metrics(frame.exec_time_seconds.to_numpy(), frame.rolling_p90.to_numpy())
            for day, frame in test.groupby(test.t.dt.date)
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    pred_cols = [
        "t", "checkpoint_model_version_id", "predict_type", "exec_time_seconds",
        "num_images_per_prompt", "num_inference_steps", "num_lora",
        "model_only_p90", "no_model_p90", "base_p90", "static_p90",
        "rolling_offset", "rolling_p90", "model_only_rolling_p90", "empirical_rolling_p90",
    ]
    test[pred_cols].to_parquet(output / "test_predictions.parquet", index=False)
    joblib.dump({"quantile_model": model, "feature_state": state}, output / "model.joblib")
    (output / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--feedback-delay-seconds", type=int, default=3600)
    args = parser.parse_args()
    result = evaluate(args.data, args.output, args.feedback_delay_seconds)
    print(json.dumps(result, indent=2, ensure_ascii=False))
