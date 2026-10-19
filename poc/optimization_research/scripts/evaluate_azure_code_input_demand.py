"""Frozen relative next-5-minute input-token demand on Azure Coding.

This consumes only TIMESTAMP and ContextTokens from the local Coding CSV.
Conversation data and output-token labels are outside this program.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/azure_llm_2024/AzureLLMInferenceTrace_code_1week.csv"
PROTOCOL = ROOT / "reports/azure_code_input_demand_protocol.md"
TEST_FILE = ROOT / "tests/test_azure_code_input_demand.py"
OUTPUT = ROOT / "artifacts/azure_code_input_demand_v1"
SOURCE_SHA256 = "71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f"
START = pd.Timestamp("2024-05-10T00:00:00Z")
SLOT_NS = 300_000_000_000
N_SLOTS = 7 * 288
EXPECTED_ROWS = 16_803_695
MIN_SCALE = 1_000.0
Q = .9
TRAIN = slice(12, 3 * 288)
VALIDATION = slice(3 * 288, 4 * 288)
ASSESSMENT = slice(4 * 288, 7 * 288)
PARAMS = dict(loss="quantile", quantile=Q, max_iter=150,
              learning_rate=.05, max_leaf_nodes=7, min_samples_leaf=20,
              l2_regularization=1., early_stopping=False, random_state=42)
FEATURES = (
    "lag1_over_scale", "lag2_over_scale", "lag3_over_scale",
    "lag12_over_scale", "previous_day_ratio",
    "past_hour_max_over_scale", "past_hour_q90_over_scale",
    "recent_six_over_previous_six", "clock_sin", "clock_cos", "weekend",
)
METHODS = ("relative_q90_hgb", "train_weighted_ratio_q90",
           "past_hour_empirical_q90", "previous_day_ratio",
           "previous_day_ratio_plus_train_residual_q90")
BASELINES = METHODS[1:]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def code_environment() -> dict:
    return {"script_sha256": file_sha256(Path(__file__)),
            "test_sha256": file_sha256(TEST_FILE),
            "python": os.sys.version.split()[0], "numpy": np.__version__,
            "pandas": pd.__version__, "sklearn": sklearn.__version__}


def verify_source() -> None:
    if SOURCE.stat().st_size != 691_989_454 or file_sha256(SOURCE) != SOURCE_SHA256:
        raise ValueError("Azure Coding source size or SHA-256 changed")


def aggregate_source(path: Path, start: pd.Timestamp = START,
                     n_slots: int = N_SLOTS) -> tuple[np.ndarray, np.ndarray, int]:
    """Aggregate the two allowed columns in bounded chunks, without row output."""
    counts = np.zeros(n_slots, dtype=np.int64)
    tokens = np.zeros(n_slots, dtype=np.int64)
    rows = 0
    last_time_ns = None
    with pd.read_csv(path, usecols=["TIMESTAMP", "ContextTokens"],
                     dtype={"TIMESTAMP": "string", "ContextTokens": "int64"},
                     chunksize=750_000) as reader:
        for chunk in reader:
            times = pd.to_datetime(chunk["TIMESTAMP"], utc=True, errors="raise")
            time_ns = times.astype("int64").to_numpy()
            values = chunk["ContextTokens"].to_numpy(np.int64)
            if (not len(time_ns) or (np.diff(time_ns) < 0).any()
                    or (last_time_ns is not None and time_ns[0] < last_time_ns)
                    or (values <= 0).any()):
                raise ValueError("invalid, unsorted, or nonpositive Azure request rows")
            slot = (time_ns - start.value) // SLOT_NS
            if (slot < 0).any() or (slot >= n_slots).any():
                raise ValueError("Azure request outside fixed seven days")
            counts += np.bincount(slot, minlength=n_slots).astype(np.int64)
            tokens += np.bincount(slot, weights=values,
                                  minlength=n_slots).astype(np.int64)
            rows += len(values)
            last_time_ns = int(time_ns[-1])
    if rows == 0 or int(counts.sum()) != rows:
        raise ValueError("empty or count-inconsistent Azure file")
    return tokens, counts, rows


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float = Q) -> float:
    """Left quantile: first value whose cumulative positive weight reaches q."""
    value = np.asarray(values, dtype=float)
    weight = np.asarray(weights, dtype=float)
    if (len(value) != len(weight) or not len(value) or not np.isfinite(value).all()
            or not np.isfinite(weight).all() or (weight <= 0).any()
            or not 0 < q < 1):
        raise ValueError("invalid weighted-quantile input")
    order = np.argsort(value, kind="mergesort")
    sorted_weight = weight[order]
    crossing = int(np.searchsorted(np.cumsum(sorted_weight),
                                   q * sorted_weight.sum(), side="left"))
    return float(value[order[min(crossing, len(value) - 1)]])


def design_from_aggregates(y: np.ndarray,
                           start: pd.Timestamp = START) -> pd.DataFrame:
    if len(y) != N_SLOTS or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("invalid fixed-grid token totals")
    observed = pd.Series(np.asarray(y, dtype=float))
    prior = observed.shift(1)
    scale = prior.rolling(12, min_periods=12).mean()
    ratio = observed / scale.where(scale >= MIN_SCALE)
    previous_day_ratio = ratio.shift(288)
    past_hour_q90 = prior.rolling(12, min_periods=12).apply(
        lambda values: float(np.quantile(values, Q)), raw=True)
    recent_six = prior.rolling(6, min_periods=6).mean()
    previous_six = observed.shift(7).rolling(6, min_periods=6).mean()
    clock = np.arange(N_SLOTS) % 288
    weekend = ((start.dayofweek + np.arange(N_SLOTS) // 288) % 7 >= 5).astype(float)
    frame = pd.DataFrame({
        "y": observed, "scale": scale, "ratio": ratio,
        "past_hour_empirical_q90": past_hour_q90,
        "lag1_over_scale": prior / scale,
        "lag2_over_scale": observed.shift(2) / scale,
        "lag3_over_scale": observed.shift(3) / scale,
        "lag12_over_scale": observed.shift(12) / scale,
        "previous_day_ratio": previous_day_ratio,
        "past_hour_max_over_scale": prior.rolling(12, min_periods=12).max() / scale,
        "past_hour_q90_over_scale": past_hour_q90 / scale,
        "recent_six_over_previous_six": recent_six / previous_six.where(previous_six > 0),
        "clock_sin": np.sin(2 * np.pi * clock / 288),
        "clock_cos": np.cos(2 * np.pi * clock / 288),
        "weekend": weekend,
        "day": np.arange(N_SLOTS) // 288,
    })
    return frame.replace([np.inf, -np.inf], np.nan)


def fit_and_predict(frame: pd.DataFrame) -> tuple[dict, dict[str, np.ndarray]]:
    train = frame.iloc[TRAIN]
    if (len(train) != 852 or train["ratio"].isna().any()
            or (train["scale"] < MIN_SCALE).any()):
        raise ValueError("training support or ratio denominator failed")
    scale_train = train["scale"].to_numpy(float)
    weights = scale_train / scale_train.mean()
    model = HistGradientBoostingRegressor(**PARAMS)
    model.fit(train[list(FEATURES)].to_numpy(float),
              train["ratio"].to_numpy(float), sample_weight=weights)
    global_ratio_q90 = weighted_quantile(train["ratio"].to_numpy(float), scale_train)
    residual = train["ratio"] - train["previous_day_ratio"]
    residual_ok = residual.notna().to_numpy()
    if int(residual_ok.sum()) != 564:
        raise ValueError("training previous-day residual support failed")
    residual_q90 = weighted_quantile(residual.to_numpy(float)[residual_ok],
                                     scale_train[residual_ok])
    fitted = {"model": model, "global_ratio_q90": global_ratio_q90,
              "previous_day_ratio_residual_q90": residual_q90,
              "train_mean_scale": float(scale_train.mean()),
              "training_weights_sum": float(weights.sum()),
              "feature_names": FEATURES, "parameters": PARAMS}
    return fitted, predict_from_bundle(fitted, frame)


def predict_from_bundle(fitted: dict, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """Apply the frozen Coding predictor and its four baselines unchanged."""
    if len(frame) != N_SLOTS or tuple(fitted["feature_names"]) != FEATURES:
        raise ValueError("incompatible fixed-grid prediction input")
    all_scale = frame["scale"].to_numpy(float)
    observed_previous = frame["previous_day_ratio"].to_numpy(float)
    has_previous = np.isfinite(observed_previous)
    previous = np.where(has_previous, observed_previous,
                        fitted["global_ratio_q90"])
    raw_model = fitted["model"].predict(frame[list(FEATURES)].to_numpy(float))
    predictions = {
        "relative_q90_hgb": np.maximum(0., raw_model) * all_scale,
        "train_weighted_ratio_q90": (
            np.full(N_SLOTS, fitted["global_ratio_q90"]) * all_scale),
        "past_hour_empirical_q90": frame["past_hour_empirical_q90"].to_numpy(float),
        "previous_day_ratio": np.maximum(0., previous) * all_scale,
        "previous_day_ratio_plus_train_residual_q90": (
            np.where(has_previous,
                     np.maximum(0., previous +
                                fitted["previous_day_ratio_residual_q90"]),
                     fitted["global_ratio_q90"])
            * all_scale),
    }
    return predictions


def pinball(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    difference = y - p
    return np.maximum(Q * difference, (Q - 1.) * difference)


def metric(y: np.ndarray, predicted: np.ndarray, scale: np.ndarray) -> dict:
    if (not len(y) or not np.isfinite(predicted).all() or
            not np.isfinite(scale).all() or (scale < MIN_SCALE).any()):
        raise ValueError("invalid evaluated forecast or support")
    return {"bins": int(len(y)),
            "absolute_token_pinball": float(np.mean(pinball(y, predicted))),
            "relative_ratio_pinball": float(np.mean(pinball(y / scale, predicted / scale))),
            "coverage": float(np.mean(y <= predicted)),
            "underprediction_fraction": float(np.mean(y > predicted)),
            "mean_predicted_tokens": float(np.mean(predicted)),
            "mean_observed_tokens": float(np.mean(y))}


def score_period(frame: pd.DataFrame, predictions: dict[str, np.ndarray],
                 first_day: int, last_day: int,
                 start: pd.Timestamp = START) -> dict:
    per_day = {}
    for day in range(first_day, last_day + 1):
        left, right = day * 288, (day + 1) * 288
        subset = frame.iloc[left:right]
        mask = (subset["scale"] >= MIN_SCALE).to_numpy()
        if len(mask) != 288:
            raise ValueError("incomplete day")
        name = (start + pd.Timedelta(days=day)).strftime("%Y-%m-%d")
        per_day[name] = {
            "eligible_bins": int(mask.sum()),
            "ineligible_bins": int((~mask).sum()),
            "methods": {method: metric(subset["y"].to_numpy(float)[mask],
                                       value[left:right][mask],
                                       subset["scale"].to_numpy(float)[mask])
                        for method, value in predictions.items()},
        }
    equal_day_loss = {method: float(np.mean([
        day["methods"][method]["absolute_token_pinball"]
        for day in per_day.values()])) for method in METHODS}
    return {"per_day": per_day,
            "equal_day_mean_absolute_token_pinball": equal_day_loss,
            "support_fraction_each_day_at_least_0_95": all(
                day["eligible_bins"] >= math.ceil(.95 * 288)
                for day in per_day.values())}


def assessment_gate(assessment: dict) -> dict:
    scores = assessment["equal_day_mean_absolute_token_pinball"]
    proposed = scores["relative_q90_hgb"]
    checks = {
        f"mean_pinball_5pct_below_{baseline}": proposed <= .95 * scores[baseline]
        for baseline in BASELINES
    }
    wins = sum(
        day["methods"]["relative_q90_hgb"]["absolute_token_pinball"]
        < min(day["methods"][baseline]["absolute_token_pinball"]
              for baseline in BASELINES)
        for day in assessment["per_day"].values())
    checks["at_least_two_of_three_daily_wins"] = wins >= 2
    checks["coverage_each_day_0_85_to_0_95"] = all(
        .85 <= day["methods"]["relative_q90_hgb"]["coverage"] <= .95
        for day in assessment["per_day"].values())
    checks["support_each_day_at_least_0_95"] = assessment[
        "support_fraction_each_day_at_least_0_95"]
    return {"all_passed": bool(all(checks.values())), "checks": checks,
            "daily_wins": int(wins),
            "failed": [name for name, passed in checks.items() if not passed]}


def coding_support(frame: pd.DataFrame, counts: np.ndarray, nrows: int) -> dict:
    """Check source completeness and fixed training/evaluation eligibility."""
    if (len(frame) != N_SLOTS or len(counts) != N_SLOTS
            or nrows != EXPECTED_ROWS or int(counts.sum()) != nrows
            or (counts == 0).any()):
        raise ValueError("Coding source rows or fixed five-minute grid incomplete")
    train_eligible = int((frame.iloc[TRAIN]["scale"] >= MIN_SCALE).sum())
    daily = {}
    for day in range(3, 7):
        name = (START + pd.Timedelta(days=day)).strftime("%Y-%m-%d")
        eligible = int((frame.iloc[day * 288:(day + 1) * 288]["scale"]
                        >= MIN_SCALE).sum())
        daily[name] = {"eligible": eligible, "ineligible": 288 - eligible}
    return {"training_eligible": train_eligible,
            "expected_training_eligible": 852,
            "evaluation_days": daily,
            "all_passed": (train_eligible == 852 and
                           all(record["eligible"] >= math.ceil(.95 * 288)
                               for record in daily.values()))}


def freeze() -> dict:
    verify_source()
    if not TEST_FILE.exists():
        raise FileNotFoundError(TEST_FILE)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    record = {"status": "FROZEN_BEFORE_CODING_MODEL_SCORE",
              "source_sha256": SOURCE_SHA256,
              "protocol_sha256": file_sha256(PROTOCOL),
              "code_environment": code_environment(),
              "target": "next-five-minute observed Coding subset ContextTokens sum",
              "training_bins": 852, "validation_bins": 288,
              "retrospective_assessment_bins": 864,
              "min_previous_hour_mean_tokens": MIN_SCALE,
              "features": FEATURES, "methods": METHODS, "model_parameters": PARAMS,
              "known_exposure": "all Coding dates aggregated before protocol; older output-token test used May 14-16"}
    atomic_json(OUTPUT / "freeze.json", record)
    return record


def evaluate_once() -> dict:
    frozen_path = OUTPUT / "freeze.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if (frozen["status"] != "FROZEN_BEFORE_CODING_MODEL_SCORE"
            or frozen["source_sha256"] != SOURCE_SHA256
            or frozen["protocol_sha256"] != file_sha256(PROTOCOL)
            or frozen["code_environment"] != code_environment()):
        raise ValueError("Coding demand freeze or environment changed")
    if (OUTPUT / "evaluation_started.json").exists():
        raise FileExistsError("Coding demand one-time score already started")
    verify_source()
    y, counts, nrows = aggregate_source(SOURCE)
    frame = design_from_aggregates(y)
    support = coding_support(frame, counts, nrows)
    if not support["all_passed"]:
        failure = {"status": "CODING_SUPPORT_NO_GO_NO_MODEL_SCORE",
                   "freeze_sha256": file_sha256(frozen_path),
                   "source_sha256": SOURCE_SHA256,
                   "support": support}
        atomic_json(OUTPUT / "support_failure.json", failure)
        return failure
    atomic_json(OUTPUT / "evaluation_started.json", {
        "status": "ONE_TIME_CODING_SCORE_STARTED",
        "freeze_sha256": file_sha256(frozen_path)})
    fitted, forecasts = fit_and_predict(frame)
    model_path = OUTPUT / "relative_q90_model.joblib"
    temporary = model_path.with_name(f".{model_path.name}.{os.getpid()}.tmp")
    try:
        joblib.dump(fitted, temporary, compress=3)
        os.replace(temporary, model_path)
    finally:
        temporary.unlink(missing_ok=True)
    validation = score_period(frame, forecasts, 3, 3)
    assessment = score_period(frame, forecasts, 4, 6)
    gate = assessment_gate(assessment)
    result = {
        "status": "RETROSPECTIVE_EXPLORATORY_GO" if gate["all_passed"] else
                  "RETROSPECTIVE_EXPLORATORY_NO_GO",
        "scope": "observed public Coding subset input-token sums; no SLO or VM action",
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": file_sha256(PROTOCOL),
        "script_sha256": file_sha256(Path(__file__)),
        "test_sha256": file_sha256(TEST_FILE),
        "freeze_sha256": file_sha256(frozen_path),
        "model_sha256": file_sha256(model_path),
        "code_environment": code_environment(),
        "raw_request_rows": nrows, "complete_five_minute_bins": N_SLOTS,
        "training_eligible_bins": len(frame.iloc[TRAIN]),
        "training_previous_day_residual_bins": 564,
        "pre_score_support": support,
        "training_global_ratio_q90": fitted["global_ratio_q90"],
        "training_previous_day_ratio_residual_q90": fitted[
            "previous_day_ratio_residual_q90"],
        "training_mean_scale": fitted["train_mean_scale"],
        "validation": validation,
        "retrospective_assessment": assessment,
        "assessment_gate": gate,
        "blind_or_independent_test": False,
        "Conversation_source_accessed": False,
    }
    atomic_json(OUTPUT / "results.json", result)
    atomic_json(OUTPUT / "manifest.json", {
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": result["protocol_sha256"],
        "script_sha256": result["script_sha256"],
        "test_sha256": result["test_sha256"],
        "freeze_sha256": result["freeze_sha256"],
        "model_sha256": result["model_sha256"],
        "results_sha256": file_sha256(OUTPUT / "results.json"),
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "evaluate_once"))
    args = parser.parse_args()
    result = freeze() if args.command == "freeze" else evaluate_once()
    print(json.dumps({"status": result["status"],
                      "gate": result.get("assessment_gate")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
