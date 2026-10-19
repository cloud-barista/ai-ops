"""Frozen time-split output-token p90 study on the BurstGPT v2.0 trace.

The experiment estimates a request-side token-length signal. It does not
estimate VM/GPU requirements or the effect of a deployment or scaling action.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/burstgpt_v2/BurstGPT_3.csv"
OUTPUT = ROOT / "artifacts/burstgpt_output_tokens_v1"
PROTOCOL = ROOT / "reports/burstgpt_output_tokens_protocol.md"
EXPECTED_SOURCE_SHA256 = "2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f"
SOURCE_COLUMNS = (
    "Timestamp", "Session ID", "Elapsed time", "Model", "Request tokens",
    "Response tokens", "Total tokens", "Log Type",
)
USECOLS = ("Timestamp", "Model", "Request tokens", "Response tokens", "Log Type")
Q = 0.9
FIT_START_DAY = 225
FIT_LAST_AVAILABLE_DAY = 293
VALIDATION_START_DAY = 295
SELECTION_START_DAY = 303
TEST_START_DAY = 315
END_DAY = 334
MIN_SUPPORT = 100
INPUT_BIN_EDGES = (64, 256, 1024, 4096)
METHODS = (
    "static_global", "static_cohort", "rolling_cohort", "rolling_bin",
    "hgb_base", "hgb_adaptive",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def history_window(day: int) -> tuple[int, int]:
    """Seven request days assumed delivered in a two-day batch feedback rule."""
    return day - 8, day - 2


def input_bin(request_tokens: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.asarray(INPUT_BIN_EDGES), request_tokens, side="left").astype(np.int8)


def empirical_p90(values: np.ndarray) -> float:
    if len(values) == 0:
        raise ValueError("Cannot estimate a quantile from an empty group")
    return float(np.quantile(values, Q, method="higher"))


def available_fit_mask(frame: pd.DataFrame) -> np.ndarray:
    """At day 295, the batch-feedback rule withholds request day 294."""
    return frame.day.between(FIT_START_DAY, FIT_LAST_AVAILABLE_DAY).to_numpy()


def load_source(path: Path = SOURCE, *, check_pin: bool = True) -> pd.DataFrame:
    if check_pin and sha256(path) != EXPECTED_SOURCE_SHA256:
        raise ValueError("BurstGPT source differs from the pinned v2.0 asset")
    with path.open(newline="") as handle:
        if tuple(next(csv.reader(handle))) != SOURCE_COLUMNS:
            raise ValueError("Unexpected BurstGPT source columns")
    frame = pd.read_csv(path, usecols=list(USECOLS), dtype={
        "Model": "category", "Log Type": "category",
        "Request tokens": "int64", "Response tokens": "int64",
    })
    if tuple(frame.columns) != USECOLS:
        raise ValueError("Unexpected selected column order")
    if check_pin and len(frame) != 5_344_021:
        raise ValueError("Unexpected BurstGPT row count")
    timestamp = frame.Timestamp.to_numpy(float)
    if not np.isfinite(timestamp).all() or np.any(np.diff(timestamp) < 0):
        raise ValueError("Timestamps must be finite and ordered")
    if frame[["Request tokens", "Response tokens"]].isna().any().any():
        raise ValueError("Missing token values")
    if (frame[["Request tokens", "Response tokens"]] < 0).any().any():
        raise ValueError("Negative token values")
    if frame[["Model", "Log Type"]].isna().any().any():
        raise ValueError("Missing request-side category")
    frame["day"] = np.floor(timestamp / 86400.0).astype(np.int16)
    if check_pin and (int(frame.day.min()), int(frame.day.max())) != (225, 334):
        raise ValueError("Unexpected relative day range")
    frame = frame.loc[frame["Request tokens"] > 0].copy()
    frame["row_id"] = frame.index.to_numpy(np.int64)
    frame["cohort"] = frame.Model.astype(str) + " | " + frame["Log Type"].astype(str)
    frame["input_bin"] = input_bin(frame["Request tokens"].to_numpy(np.int64))
    return frame


def feature_state(fit: pd.DataFrame) -> dict:
    return {
        col: {str(value): index for index, value in enumerate(sorted(fit[col].unique()))}
        for col in ("Model", "Log Type")
    }


def encode(frame: pd.DataFrame, state: dict) -> np.ndarray:
    model = frame.Model.astype("string").map(state["Model"]).fillna(-1).to_numpy(float)
    kind = frame["Log Type"].astype("string").map(state["Log Type"]).fillna(-1).to_numpy(float)
    request = np.log1p(frame["Request tokens"].to_numpy(float))
    return np.column_stack((model, kind, request))


def fit_fixed(frame: pd.DataFrame) -> dict:
    fit = frame.loc[available_fit_mask(frame)]
    if fit.empty:
        raise ValueError("No available fit rows")
    state = feature_state(fit)
    estimator = HistGradientBoostingRegressor(
        loss="quantile", quantile=Q, categorical_features=[0, 1],
        max_iter=140, max_leaf_nodes=15, min_samples_leaf=60,
        l2_regularization=10, learning_rate=0.05,
        early_stopping=False, random_state=42,
    )
    estimator.fit(encode(fit, state), fit["Response tokens"].to_numpy(float))
    cohort_p90 = {
        str(cohort): empirical_p90(group["Response tokens"].to_numpy())
        for cohort, group in fit.groupby("cohort", sort=True)
    }
    return {
        "estimator": estimator,
        "feature_state": state,
        "static_global_p90": empirical_p90(fit["Response tokens"].to_numpy()),
        "static_cohort_p90": cohort_p90,
        "fit_rows": int(len(fit)),
        "fit_days": [FIT_START_DAY, FIT_LAST_AVAILABLE_DAY],
    }


def fixed_predictions(frame: pd.DataFrame, bundle: dict) -> np.ndarray:
    prediction = bundle["estimator"].predict(encode(frame, bundle["feature_state"]))
    return np.maximum(0.0, prediction)


def quantiles_by_group(frame: pd.DataFrame, columns: str | list[str]) -> tuple[dict, dict]:
    quantiles: dict = {}
    counts: dict = {}
    if isinstance(columns, str):
        columns = [columns]
    for key, group in frame.groupby(columns, sort=True, observed=True):
        if len(columns) == 1 and isinstance(key, tuple):
            key = key[0]
        quantiles[key] = empirical_p90(group["Response tokens"].to_numpy())
        counts[key] = int(len(group))
    return quantiles, counts


def rolling_predictions_for_day(
    current: pd.DataFrame,
    eligible: pd.DataFrame,
    out_of_sample: pd.DataFrame,
    day: int,
    bundle: dict,
) -> dict[str, np.ndarray]:
    low, high = history_window(day)
    history = eligible.loc[(eligible.day >= low) & (eligible.day <= high)]
    if history.empty:
        raise ValueError(f"No available history for day {day}")
    cohort_q, cohort_n = quantiles_by_group(history, "cohort")
    bin_q, bin_n = quantiles_by_group(history, ["cohort", "input_bin"])
    static_global = float(bundle["static_global_p90"])
    static_map = bundle["static_cohort_p90"]
    cohort = current.cohort.to_numpy(str)
    bin_ids = current.input_bin.to_numpy(np.int8)
    static_cohort = np.array([static_map.get(c, static_global) for c in cohort], float)
    rolling_cohort = np.array([cohort_q.get(c, static_map.get(c, static_global)) for c in cohort], float)
    rolling_bin = np.array([
        bin_q[(c, int(b))] if bin_n.get((c, int(b)), 0) >= MIN_SUPPORT
        else cohort_q.get(c, static_map.get(c, static_global))
        for c, b in zip(cohort, bin_ids)
    ], float)

    # The base model was trained through day 293. Only day >= 295 residuals
    # enter adaptation, so every residual here is out of sample. Delivery by
    # request day d-2 is an explicit batch assumption, not observed latency.
    feedback = out_of_sample.loc[
        (out_of_sample.day >= max(low, VALIDATION_START_DAY))
        & (out_of_sample.day <= high)
    ]
    if not feedback.empty:
        feedback = feedback.copy()
        feedback["residual"] = feedback["Response tokens"].to_numpy(float) - feedback.base_pred.to_numpy(float)
        global_residual = empirical_p90(feedback.residual.to_numpy(float)) if len(feedback) >= MIN_SUPPORT else 0.0
        residual_q = {
            str(key): empirical_p90(group.residual.to_numpy(float))
            for key, group in feedback.groupby("cohort", sort=True)
            if len(group) >= MIN_SUPPORT
        }
    else:
        global_residual = 0.0
        residual_q = {}
    hgb_base = current.base_pred.to_numpy(float)
    hgb_adaptive = np.maximum(0.0, hgb_base + np.array([
        residual_q.get(c, global_residual) for c in cohort
    ], float))
    return {
        "static_global": np.full(len(current), static_global, float),
        "static_cohort": static_cohort,
        "rolling_cohort": rolling_cohort,
        "rolling_bin": rolling_bin,
        "hgb_base": hgb_base,
        "hgb_adaptive": hgb_adaptive,
    }


def make_predictions(
    eligible: pd.DataFrame,
    out_of_sample: pd.DataFrame,
    days: range,
    bundle: dict,
) -> pd.DataFrame:
    parts = []
    for day in days:
        current = out_of_sample.loc[out_of_sample.day == day].copy()
        if current.empty:
            raise ValueError(f"No eligible rows on day {day}")
        estimates = rolling_predictions_for_day(current, eligible, out_of_sample, day, bundle)
        for name in METHODS:
            current[name] = estimates[name]
        parts.append(current[[
            "row_id", "Timestamp", "day", "Model", "Log Type", "cohort",
            "Request tokens", "input_bin", "Response tokens", *METHODS,
        ]])
    return pd.concat(parts, ignore_index=True)


def score_arrays(y: np.ndarray, pred: np.ndarray) -> dict:
    error = y - pred
    loss = np.where(error >= 0, Q * error, (Q - 1.0) * error)
    return {
        "rows": int(len(y)),
        "pinball_tokens": float(np.mean(loss)),
        "coverage": float(np.mean(y <= pred)),
        "mean_budget_tokens": float(np.mean(pred)),
    }


def summarize(predictions: pd.DataFrame) -> dict:
    summary: dict = {}
    for method in METHODS:
        y = predictions["Response tokens"].to_numpy(float)
        pred = predictions[method].to_numpy(float)
        overall = score_arrays(y, pred)
        days = {
            str(int(day)): score_arrays(part["Response tokens"].to_numpy(float), part[method].to_numpy(float))
            for day, part in predictions.groupby("day", sort=True)
        }
        by_cohort = {
            str(cohort): score_arrays(part["Response tokens"].to_numpy(float), part[method].to_numpy(float))
            for cohort, part in predictions.groupby("cohort", sort=True)
        }
        blocks = {
            str(int(start)): score_arrays(
                part["Response tokens"].to_numpy(float), part[method].to_numpy(float)
            )
            for start, part in predictions.assign(
                block=(predictions.day.to_numpy(int) - int(predictions.day.min())) // 5 * 5
                + int(predictions.day.min())
            ).groupby("block", sort=True)
        }
        overall["equal_day_pinball_tokens"] = float(np.mean([
            row["pinball_tokens"] for row in days.values()
        ]))
        overall["by_day"] = days
        overall["by_cohort"] = by_cohort
        overall["by_5_day_block"] = blocks
        summary[method] = overall
    return summary


def select_method(validation_scores: dict) -> str:
    return min(
        METHODS,
        key=lambda name: (
            validation_scores[name]["equal_day_pinball_tokens"],
            validation_scores[name]["pinball_tokens"],
            METHODS.index(name),
        ),
    )


def _check_unwritten(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(f"Existing frozen artifact; explicit amendment needed: {existing}")


def validation(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "model.joblib"
    pred_path = output / "validation_predictions.parquet"
    metrics_path = output / "validation_metrics.json"
    manifest_path = output / "selection_manifest.json"
    _check_unwritten([model_path, pred_path, metrics_path, manifest_path])
    eligible = load_source(source)
    all_eligible_rows = int(len(eligible))
    eligible = eligible.loc[eligible.day < TEST_START_DAY].copy()
    bundle = fit_fixed(eligible)
    joblib.dump(bundle, model_path)
    out_of_sample = eligible.loc[eligible.day >= VALIDATION_START_DAY].copy()
    out_of_sample["base_pred"] = fixed_predictions(out_of_sample, bundle)
    predictions = make_predictions(eligible, out_of_sample, range(SELECTION_START_DAY, TEST_START_DAY), bundle)
    predictions.to_parquet(pred_path, index=False)
    scores = summarize(predictions)
    selected = select_method(scores)
    metrics = {
        "status": "validation",
        "source_rows": 5_344_021,
        "eligible_rows": all_eligible_rows,
        "eligible_pretest_rows": int(len(eligible)),
        "fit_rows": bundle["fit_rows"],
        "nominal_fit_days": [225, 294],
        "available_fit_days": bundle["fit_days"],
        "validation_days_scored": [SELECTION_START_DAY, TEST_START_DAY - 1],
        "validation_rows_scored": int(len(predictions)),
        "selected_method": selected,
        "methods": scores,
        "software": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__},
    }
    write_json(metrics_path, metrics)
    manifest = {
        "status": "selection_frozen_before_test",
        "source_sha256": sha256(source),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model_path),
        "validation_predictions_sha256": sha256(pred_path),
        "validation_metrics_sha256": sha256(metrics_path),
        "selected_method": selected,
        "candidates": list(METHODS),
        "selection_days": [SELECTION_START_DAY, TEST_START_DAY - 1],
        "test_days": [TEST_START_DAY, END_DAY],
        "criterion": "minimum mean of daily p90 pinball, then row mean, then candidate order",
        "feedback_rule": "at day d start, output logs from request days d-8 through d-2 assumed delivered",
    }
    write_json(manifest_path, manifest)
    return metrics


def frozen_test(source: Path = SOURCE, output: Path = OUTPUT) -> dict:
    model_path = output / "model.joblib"
    val_pred_path = output / "validation_predictions.parquet"
    val_metrics_path = output / "validation_metrics.json"
    manifest_path = output / "selection_manifest.json"
    pred_path = output / "test_predictions.parquet"
    metrics_path = output / "test_metrics.json"
    _check_unwritten([pred_path, metrics_path])
    if not all(path.exists() for path in [model_path, val_pred_path, val_metrics_path, manifest_path]):
        raise FileNotFoundError("Run validation and freeze selection before test")
    manifest = json.loads(manifest_path.read_text())
    checks = {
        "source_sha256": sha256(source),
        "protocol_sha256": sha256(PROTOCOL),
        "script_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model_path),
        "validation_predictions_sha256": sha256(val_pred_path),
        "validation_metrics_sha256": sha256(val_metrics_path),
    }
    for key, actual in checks.items():
        if manifest.get(key) != actual:
            raise ValueError(f"Frozen selection mismatch: {key}")
    if manifest.get("status") != "selection_frozen_before_test":
        raise ValueError("Selection manifest is not frozen")
    bundle = joblib.load(model_path)
    eligible = load_source(source)
    out_of_sample = eligible.loc[eligible.day >= VALIDATION_START_DAY].copy()
    out_of_sample["base_pred"] = fixed_predictions(out_of_sample, bundle)
    predictions = make_predictions(eligible, out_of_sample, range(TEST_START_DAY, END_DAY + 1), bundle)
    predictions.to_parquet(pred_path, index=False)
    scores = summarize(predictions)
    metrics = {
        "status": "test",
        "selected_method": manifest["selected_method"],
        "test_days": [TEST_START_DAY, END_DAY],
        "test_rows": int(len(predictions)),
        "manifest_sha256": sha256(manifest_path),
        "source_sha256": checks["source_sha256"],
        "protocol_sha256": checks["protocol_sha256"],
        "script_sha256": checks["script_sha256"],
        "model_sha256": checks["model_sha256"],
        "predictions_sha256": sha256(pred_path),
        "methods": scores,
    }
    write_json(metrics_path, metrics)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "test"))
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = validation(args.source, args.output) if args.phase == "validate" else frozen_test(args.source, args.output)
    print(json.dumps({
        "phase": args.phase,
        "rows": result.get("validation_rows_scored", result.get("test_rows")),
        "selected_method": result["selected_method"],
        "equal_day_pinball_tokens": result["methods"][result["selected_method"]]["equal_day_pinball_tokens"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
