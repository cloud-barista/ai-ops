"""Exploratory p90 replay on BurstGPT rows with a positive duration label.

The public trace has many zero `Elapsed time` values with strong date and log
type dependence. Consequently, this script evaluates an observed-label subset,
not the latency distribution of all requests or a scaling policy.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from rolling_quantile_buffer import RollingQuantileBuffer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/burstgpt_v2/BurstGPT_3.csv"
OUTPUT = ROOT / "artifacts/burstgpt_observed_duration_v1"
Q = 0.9
DAY = 86400.0
FIT_DAYS = 30
FIRST_TEST_DAY = 40
BLOCK_DAYS = 10
LAST_DAY = 110
FEEDBACK_DELAY = 3600.0
GLOBAL_WINDOW = 2000
MODEL_WINDOW = 500
MODEL_MINIMUM = 50
MODEL_SHRINKAGE = 200
GUARD_TARGET = 0.92
GUARD_MINIMUM = 100
REQUIRED = ("Timestamp", "Elapsed time", "Model", "Request tokens", "Log Type")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_observed(source: Path) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(source, usecols=list(REQUIRED))
    if tuple(raw.columns) != REQUIRED:
        raise ValueError("Unexpected BurstGPT columns")
    for col in ("Timestamp", "Elapsed time", "Request tokens"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    t = raw["Timestamp"].to_numpy(float)
    if not np.isfinite(t).all() or np.any(np.diff(t) < 0):
        raise ValueError("BurstGPT submission times must be finite and sorted")
    start = float(t.min())
    raw["day"] = np.floor((t - start) / DAY).astype(int)
    duration = raw["Elapsed time"].to_numpy(float)
    tokens = raw["Request tokens"].to_numpy(float)
    valid = (
        np.isfinite(duration) & (duration > 0)
        & np.isfinite(tokens) & (tokens >= 0)
        & raw.Model.notna().to_numpy() & raw["Log Type"].notna().to_numpy()
    )
    grouped = raw.assign(observed_duration=valid).groupby("day", sort=True).agg(
        requests=("Timestamp", "size"), observed_duration=("observed_duration", "sum")
    )
    grouped_kind = raw.assign(observed_duration=valid).groupby(
        ["Model", "Log Type"], sort=True
    ).agg(requests=("Timestamp", "size"), observed_duration=("observed_duration", "sum"))
    audit = {
        "all_rows": int(len(raw)),
        "invalid_by_reason": {
            "elapsed_nonfinite": int(np.sum(~np.isfinite(duration))),
            "elapsed_nonpositive": int(np.sum(np.isfinite(duration) & (duration <= 0))),
            "request_tokens_nonfinite": int(np.sum(~np.isfinite(tokens))),
            "request_tokens_negative": int(np.sum(np.isfinite(tokens) & (tokens < 0))),
            "model_missing": int(raw.Model.isna().sum()),
            "log_type_missing": int(raw["Log Type"].isna().sum()),
        },
        "positive_duration_rows": int(np.sum(np.isfinite(duration) & (duration > 0))),
        "usable_rows": int(valid.sum()),
        "relative_day_min": int(raw.day.min()),
        "relative_day_max": int(raw.day.max()),
        "label_availability_by_day": {
            str(day): {"all": int(row.requests), "usable": int(row.observed_duration)}
            for day, row in grouped.iterrows()
        },
        "label_availability_by_model_log_type": {
            f"{model} | {kind}": {"all": int(row.requests), "usable": int(row.observed_duration)}
            for (model, kind), row in grouped_kind.iterrows()
        },
        "observed_duration_quantiles_seconds": {
            str(q): float(np.quantile(duration[valid], q)) for q in (0.5, 0.9, 0.99)
        },
    }
    observed = raw.loc[valid].copy().reset_index(drop=True)
    observed["t"] = observed["Timestamp"].to_numpy(float) - start
    observed["available_at"] = observed.t + observed["Elapsed time"] + FEEDBACK_DELAY
    return observed, audit


def training_mask(frame: pd.DataFrame, cutoff: float = FIT_DAYS * DAY) -> np.ndarray:
    """A logged duration may train the frozen model only after feedback arrives."""
    return frame.t.lt(cutoff).to_numpy() & frame.available_at.le(cutoff).to_numpy()


def feature_state(train: pd.DataFrame) -> dict:
    return {
        col: {str(value): index for index, value in enumerate(sorted(train[col].unique()))}
        for col in ("Model", "Log Type")
    }


def encode(frame: pd.DataFrame, state: dict) -> np.ndarray:
    model = frame.Model.astype("string").map(state["Model"]).fillna(-1).to_numpy(float)
    kind = frame["Log Type"].astype("string").map(state["Log Type"]).fillna(-1).to_numpy(float)
    tokens = np.log1p(frame["Request tokens"].to_numpy(float))
    return np.column_stack((model, kind, tokens))


def fit_base(train: pd.DataFrame, state: dict) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(
        loss="quantile", quantile=Q, categorical_features=[0, 1],
        max_iter=140, max_leaf_nodes=15, min_samples_leaf=60,
        l2_regularization=10, learning_rate=0.05, random_state=42,
    )
    model.fit(encode(train, state), train["Elapsed time"].to_numpy(float))
    return model


def causal_rolling_predictions(
    timestamp: np.ndarray, duration: np.ndarray, base: np.ndarray,
    model_ids: np.ndarray, delay_seconds: float = FEEDBACK_DELAY,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reveal residuals in assumed completion order before each submission."""
    timestamp = np.asarray(timestamp, float)
    duration = np.asarray(duration, float)
    base = np.asarray(base, float)
    model_ids = np.asarray(model_ids, str)
    if not (len(timestamp) == len(duration) == len(base) == len(model_ids)):
        raise ValueError("input lengths differ")
    if np.any(np.diff(timestamp) < 0) or np.any(duration <= 0) or delay_seconds < 0:
        raise ValueError("time must be sorted, duration positive, and delay nonnegative")
    global_buffer = RollingQuantileBuffer(GLOBAL_WINDOW, Q)
    model_buffers: dict[str, RollingQuantileBuffer] = {}
    completed: list[tuple[float, int]] = []
    global_pred = np.empty(len(timestamp), float)
    model_pred = np.empty(len(timestamp), float)
    model_support = np.empty(len(timestamp), int)
    for i, now in enumerate(timestamp):
        while completed and completed[0][0] <= now:
            _, j = heapq.heappop(completed)
            residual = duration[j] - base[j]
            global_buffer.add(residual)
            key = model_ids[j]
            buffer = model_buffers.setdefault(key, RollingQuantileBuffer(MODEL_WINDOW, Q))
            buffer.add(residual)
        global_offset = global_buffer.value() or 0.0
        support = model_buffers.get(model_ids[i])
        count = len(support) if support is not None else 0
        model_support[i] = count
        if count >= MODEL_MINIMUM:
            weight = count / (count + MODEL_SHRINKAGE)
            local_offset = support.value()
            assert local_offset is not None
            blended = weight * local_offset + (1 - weight) * global_offset
        else:
            blended = global_offset
        global_pred[i] = max(0.0, base[i] + global_offset)
        model_pred[i] = max(0.0, base[i] + blended)
        heapq.heappush(completed, (now + duration[i] + delay_seconds, i))
    return global_pred, model_pred, model_support


def minimum_margin(labels: np.ndarray, predictions: np.ndarray, target: float) -> float:
    if len(labels) == 0 or len(labels) != len(predictions):
        raise ValueError("nonempty matching labels and predictions required")
    residual = np.sort(np.asarray(labels, float) - np.asarray(predictions, float))
    index = int(np.ceil(target * len(residual))) - 1
    return max(0.0, float(residual[index]))


def fit_guard(
    labels: np.ndarray, predictions: np.ndarray, kinds: np.ndarray,
    model_ids: np.ndarray,
) -> dict:
    """Frozen margin from the prior block's already available outcomes."""
    labels = np.asarray(labels, float)
    current = np.asarray(predictions, float).copy()
    kinds = np.asarray(kinds, str)
    model_ids = np.asarray(model_ids, str)
    if not (len(labels) == len(current) == len(kinds) == len(model_ids)):
        raise ValueError("guard lengths differ")
    global_margin = minimum_margin(labels, current, GUARD_TARGET)
    current += global_margin
    margins: dict[str, dict[str, float]] = {"Log Type": {}, "Model": {}}
    support: dict[str, dict[str, int]] = {"Log Type": {}, "Model": {}}
    for field, keys in (("Log Type", kinds), ("Model", model_ids)):
        for key in np.unique(keys):
            selection = keys == key
            n = int(selection.sum())
            if n < GUARD_MINIMUM:
                continue
            margin = minimum_margin(labels[selection], current[selection], GUARD_TARGET)
            margins[field][key] = margin
            support[field][key] = n
            current[selection] += margin
    return {
        "global_margin_seconds": global_margin,
        "group_margins_seconds": margins,
        "group_support": support,
        "selection_coverage": float(np.mean(labels <= current)),
        "selection_rows": int(len(labels)),
    }


def apply_guard(predictions: np.ndarray, kinds: np.ndarray,
                model_ids: np.ndarray, policy: dict) -> np.ndarray:
    result = np.asarray(predictions, float).copy() + policy["global_margin_seconds"]
    for field, keys in (("Log Type", np.asarray(kinds, str)),
                        ("Model", np.asarray(model_ids, str))):
        for key, margin in policy["group_margins_seconds"][field].items():
            result[keys == key] += margin
    return result


def score(labels: np.ndarray, predictions: np.ndarray) -> dict:
    labels = np.asarray(labels, float)
    predictions = np.asarray(predictions, float)
    error = labels - predictions
    return {
        "n": int(len(labels)),
        "pinball_seconds": float(np.mean(np.maximum(Q * error, (Q - 1) * error))),
        "coverage": float(np.mean(labels <= predictions)),
        "mean_predicted_seconds": float(np.mean(predictions)),
        "mean_target_seconds": float(np.mean(labels)),
    }


def score_daily_paired(frame: pd.DataFrame, left: str, right: str) -> dict:
    """Keep day-level comparisons separate from request-weighted headline scores."""
    daily = []
    for day, part in frame.groupby("day", sort=True):
        y = part["Elapsed time"].to_numpy(float)
        l = part[left].to_numpy(float)
        r = part[right].to_numpy(float)
        l_loss = np.maximum(Q * (y - l), (Q - 1) * (y - l))
        r_loss = np.maximum(Q * (y - r), (Q - 1) * (y - r))
        daily.append({"day": int(day), "n": int(len(part)),
                      "left_minus_right_pinball_seconds": float(np.mean(l_loss - r_loss))})
    return {"days": daily,
            "mean_daily_difference_seconds": float(np.mean(
                [row["left_minus_right_pinball_seconds"] for row in daily])),
            "days_left_better": int(sum(
                row["left_minus_right_pinball_seconds"] < 0 for row in daily))}


def delay_sensitivity(replay: pd.DataFrame, base: np.ndarray, y: np.ndarray,
                      t: np.ndarray, models: np.ndarray, kinds: np.ndarray,
                      delay_seconds: float) -> dict:
    """Hold the fit model fixed; vary online feedback and block calibration delay."""
    global_pred, model_pred, _ = causal_rolling_predictions(
        t, y, base, models, delay_seconds=delay_seconds
    )
    test = replay.day.ge(FIRST_TEST_DAY).to_numpy() & replay.day.lt(LAST_DAY).to_numpy()
    guarded = np.full(len(replay), np.nan)
    by_block = {}
    for block_start in range(FIRST_TEST_DAY, LAST_DAY, BLOCK_DAYS):
        cutoff = block_start * DAY
        selection = (
            replay.day.ge(block_start - BLOCK_DAYS).to_numpy()
            & replay.day.lt(block_start).to_numpy()
            & (t + y + delay_seconds <= cutoff)
        )
        target = replay.day.ge(block_start).to_numpy() & replay.day.lt(block_start + BLOCK_DAYS).to_numpy()
        policy = fit_guard(y[selection], model_pred[selection], kinds[selection], models[selection])
        guarded[target] = apply_guard(model_pred[target], kinds[target], models[target], policy)
        by_block[str(block_start)] = {
            "global": score(y[target], global_pred[target]),
            "model": score(y[target], model_pred[target]),
            "guard92": score(y[target], guarded[target]),
        }
    return {"delay_seconds": delay_seconds,
            "fit_model": "frozen at original 1-hour assumption",
            "all_observed_test": {
                "global": score(y[test], global_pred[test]),
                "model": score(y[test], model_pred[test]),
                "guard92": score(y[test], guarded[test]),
            },
            "by_block": by_block}


def evaluate(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    data, audit = load_observed(source)
    fit = data.loc[training_mask(data)].copy()
    if len(fit) < 10000:
        raise ValueError("insufficient available fit labels")
    state = feature_state(fit)
    base_model = fit_base(fit, state)
    replay = data.loc[data.day.ge(FIT_DAYS)].copy().reset_index(drop=True)
    replay["base_p90"] = np.maximum(0, base_model.predict(encode(replay, state)))
    base = replay.base_p90.to_numpy(float)
    y = replay["Elapsed time"].to_numpy(float)
    t = replay.t.to_numpy(float)
    models = replay.Model.to_numpy(str)
    kinds = replay["Log Type"].to_numpy(str)
    global_pred, model_pred, support = causal_rolling_predictions(t, y, base, models)
    replay["global_p90"] = global_pred
    replay["model_p90"] = model_pred
    replay["model_feedback_support"] = support
    replay["guard92_p90"] = np.nan
    policies = []
    for block_start in range(FIRST_TEST_DAY, LAST_DAY, BLOCK_DAYS):
        cutoff = block_start * DAY
        selection = (
            replay.day.ge(block_start - BLOCK_DAYS).to_numpy()
            & replay.day.lt(block_start).to_numpy()
            & replay.available_at.le(cutoff).to_numpy()
        )
        test = replay.day.ge(block_start).to_numpy() & replay.day.lt(block_start + BLOCK_DAYS).to_numpy()
        if selection.sum() < 1000 or test.sum() < 1000:
            raise ValueError(f"insufficient observed labels in block {block_start}")
        policy = fit_guard(y[selection], model_pred[selection], kinds[selection], models[selection])
        replay.loc[test, "guard92_p90"] = apply_guard(
            model_pred[test], kinds[test], models[test], policy
        )
        policies.append({"test_start_day": block_start, "test_end_day_exclusive": block_start + BLOCK_DAYS,
                         "selection_available_count": int(selection.sum()), "policy": policy})
    test = replay.loc[replay.day.ge(FIRST_TEST_DAY) & replay.day.lt(LAST_DAY)].copy()
    if test.guard92_p90.isna().any():
        raise AssertionError("every test row needs a frozen guard")
    methods = {"base": "base_p90", "global": "global_p90",
               "model": "model_p90", "guard92": "guard92_p90"}
    metrics = {
        "all_observed_test": {
            name: score(test["Elapsed time"], test[column]) for name, column in methods.items()
        },
        "by_block": {}, "by_model": {}, "by_log_type": {},
    }
    for block_start in range(FIRST_TEST_DAY, LAST_DAY, BLOCK_DAYS):
        part = test.loc[test.day.ge(block_start) & test.day.lt(block_start + BLOCK_DAYS)]
        metrics["by_block"][str(block_start)] = {
            "all_source_rows": sum(audit["label_availability_by_day"][str(d)]["all"]
                                   for d in range(block_start, block_start + BLOCK_DAYS)),
            "observed_test_rows": int(len(part)),
            "methods": {name: score(part["Elapsed time"], part[column])
                        for name, column in methods.items()},
        }
    for field, target in (("Model", "by_model"), ("Log Type", "by_log_type")):
        for key, part in test.groupby(field, sort=True):
            metrics[target][str(key)] = {
                "n": int(len(part)),
                "methods": {name: score(part["Elapsed time"], part[column])
                            for name, column in methods.items()},
            }
    metrics["daily_paired_model_vs_global"] = score_daily_paired(test, "model_p90", "global_p90")
    metrics["daily_paired_guard92_vs_global"] = score_daily_paired(test, "guard92_p90", "global_p90")
    sensitivity = {
        str(delay): delay_sensitivity(replay, base, y, t, models, kinds, delay)
        for delay in (0.0, 6 * 3600.0)
    }
    result = {
        "dataset": "BurstGPT v2.0 BurstGPT_3.csv",
        "source_sha256": sha256(source),
        "script_sha256": sha256(Path(__file__)),
        "buffer_script_sha256": sha256(ROOT / "scripts/rolling_quantile_buffer.py"),
        "sklearn_version": sklearn.__version__,
        "target": "positive reported Elapsed time only; not the full request population",
        "assumed_feedback_delay_seconds": FEEDBACK_DELAY,
        "split": {"fit_days": [0, FIT_DAYS], "guard_start_day": FIRST_TEST_DAY,
                  "test_blocks_days": BLOCK_DAYS, "last_day_exclusive": LAST_DAY},
        "fit_rows_available": int(len(fit)),
        "fit_rows_if_delay_changed_without_retraining": {
            str(delay): int(np.sum(
                data.t.lt(FIT_DAYS * DAY).to_numpy()
                & (data.t.to_numpy(float) + data["Elapsed time"].to_numpy(float) + delay
                   <= FIT_DAYS * DAY)
            )) for delay in (0.0, 6 * 3600.0)
        },
        "raw_audit": audit,
        "policies": policies,
        "metrics": metrics,
        "feedback_delay_sensitivity": sensitivity,
        "primary_external_validation_valid": False,
        "reason_primary_invalid": "Elapsed time <= 0 for 59.4% of source requests with strong date/log-type dependence",
    }
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": base_model, "feature_state": state}, output / "model.joblib")
    test[["Timestamp", "day", "Model", "Log Type", "Request tokens", "Elapsed time",
          "available_at", "model_feedback_support"] + list(methods.values())].to_parquet(
              output / "observed_test_predictions.parquet", index=False
          )
    (output / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    outcome = evaluate(args.source, args.output)
    print(json.dumps({"fit_rows": outcome["fit_rows_available"],
                      "test": outcome["metrics"]["all_observed_test"],
                      "primary_external_validation_valid": False}, indent=2))
