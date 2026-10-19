"""One-time, pre-frozen transfer test on the *sampled* RAGPulse input trace."""

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

import evaluate_azure_code_input_demand as coding


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/ragpulse_input_demand_transfer_protocol.md"
TEST_FILE = ROOT / "tests/test_ragpulse_input_demand_transfer.py"
SOURCE = ROOT / "data/external/ragpulse_2025/0_trace.jsonl"
OUTPUT = ROOT / "artifacts/ragpulse_input_demand_transfer_v1"
SOURCE_BYTES = 9_961_760
SOURCE_GIT_BLOB_SHA1 = "a058ecc31c56648b20709a8c211f21dd97c7d08b"
EXPECTED_ROWS = 34_987
N_DAYS = 42
SLOTS_PER_DAY = 288
N_SLOTS = N_DAYS * SLOTS_PER_DAY
FIRST_TEST_DAY = 28
LAST_TEST_DAY = 34
MIN_SCALE = coding.MIN_SCALE
Q = coding.Q
FEATURES = tuple(feature for feature in coding.FEATURES
                 if feature not in ("clock_sin", "clock_cos", "weekend"))
RAW_METHODS = coding.METHODS
CALIBRATED = tuple("calibrated_" + method for method in RAW_METHODS)
PRIMARY = CALIBRATED[0]
BASELINES = CALIBRATED[1:]


def git_blob_sha1(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_external_source() -> str:
    if SOURCE.stat().st_size != SOURCE_BYTES or git_blob_sha1(SOURCE) != SOURCE_GIT_BLOB_SHA1:
        raise ValueError("RAGPulse bytes or official Git blob SHA-1 changed")
    return coding.file_sha256(SOURCE)


def aggregate_jsonl(path: Path, n_slots: int = N_SLOTS) -> tuple[np.ndarray, np.ndarray, int]:
    tokens = np.zeros(n_slots, dtype=np.int64)
    counts = np.zeros(n_slots, dtype=np.int64)
    previous_timestamp = -1.0
    rows = 0
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                stamp = record["timestamp"]
                value = record["input_length"]
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"invalid JSON or missing field at line {line_no}") from exc
            if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                    or not math.isfinite(stamp) or stamp < 0
                    or stamp >= n_slots * 300 or stamp < previous_timestamp):
                raise ValueError(f"invalid/out-of-order timestamp at line {line_no}")
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ValueError(f"invalid input_length at line {line_no}")
            slot = int(stamp // 300)
            if tokens[slot] > np.iinfo(np.int64).max - value:
                raise ValueError("token sum overflow")
            tokens[slot] += value
            counts[slot] += 1
            previous_timestamp = float(stamp)
            rows += 1
    if rows != EXPECTED_ROWS or int(counts.sum()) != rows:
        raise ValueError("unexpected official request count")
    return tokens, counts, rows


def design(y: np.ndarray) -> pd.DataFrame:
    """Features at slot t depend only on completed slots strictly before t."""
    if (len(y) < SLOTS_PER_DAY * 2 or np.asarray(y).ndim != 1
            or not np.isfinite(y).all() or (y < 0).any()):
        raise ValueError("invalid fixed-grid token sums")
    observed = pd.Series(np.asarray(y, dtype=float))
    prior = observed.shift(1)
    scale = prior.rolling(12, min_periods=12).mean()
    ratio = observed / scale.where(scale >= MIN_SCALE)
    past_hour_q90 = prior.rolling(12, min_periods=12).apply(
        lambda values: float(np.quantile(values, Q)), raw=True)
    recent_six = prior.rolling(6, min_periods=6).mean()
    previous_six = observed.shift(7).rolling(6, min_periods=6).mean()
    return pd.DataFrame({
        "y": observed,
        "scale": scale,
        "ratio": ratio,
        "lag1_over_scale": prior / scale,
        "lag2_over_scale": observed.shift(2) / scale,
        "lag3_over_scale": observed.shift(3) / scale,
        "lag12_over_scale": observed.shift(12) / scale,
        "previous_day_ratio": ratio.shift(SLOTS_PER_DAY),
        "past_hour_max_over_scale": prior.rolling(12, min_periods=12).max() / scale,
        "past_hour_q90_over_scale": past_hour_q90 / scale,
        "recent_six_over_previous_six": recent_six / previous_six.where(previous_six > 0),
        "past_hour_empirical_q90": past_hour_q90,
    }).replace([np.inf, -np.inf], np.nan)


def fit_coding_model() -> dict:
    coding.verify_source()
    y, counts, rows = coding.aggregate_source(coding.SOURCE)
    if rows != coding.EXPECTED_ROWS or (counts == 0).any():
        raise ValueError("Coding training source support changed")
    frame = design(y)
    fit = frame.iloc[coding.TRAIN]
    if (len(fit) != 852 or fit["ratio"].isna().any()
            or (fit["scale"] < MIN_SCALE).any()):
        raise ValueError("Coding training support changed")
    scales = fit["scale"].to_numpy(float)
    model = HistGradientBoostingRegressor(**coding.PARAMS)
    model.fit(fit[list(FEATURES)].to_numpy(float),
              fit["ratio"].to_numpy(float), sample_weight=scales/scales.mean())
    ratios = fit["ratio"].to_numpy(float)
    global_q90 = coding.weighted_quantile(ratios, scales, Q)
    residual = fit["ratio"] - fit["previous_day_ratio"]
    valid = residual.notna().to_numpy()
    if valid.sum() != 564:
        raise ValueError("Coding residual support changed")
    residual_q90 = coding.weighted_quantile(residual.to_numpy(float)[valid],
                                             scales[valid], Q)
    return {"model": model, "feature_names": FEATURES,
            "global_ratio_q90": global_q90,
            "previous_day_ratio_residual_q90": residual_q90,
            "parameters": coding.PARAMS}


def predict_raw(bundle: dict, frame: pd.DataFrame) -> dict[str, np.ndarray]:
    if tuple(bundle["feature_names"]) != FEATURES:
        raise ValueError("incompatible model features")
    scale = frame["scale"].to_numpy(float)
    previous = frame["previous_day_ratio"].to_numpy(float)
    has_previous = np.isfinite(previous)
    fallback = np.where(has_previous, previous, bundle["global_ratio_q90"])
    model_ratio = bundle["model"].predict(frame[list(FEATURES)].to_numpy(float))
    return {
        RAW_METHODS[0]: np.maximum(0, model_ratio) * scale,
        RAW_METHODS[1]: bundle["global_ratio_q90"] * scale,
        RAW_METHODS[2]: frame["past_hour_empirical_q90"].to_numpy(float),
        RAW_METHODS[3]: np.maximum(0, fallback) * scale,
        RAW_METHODS[4]: np.where(has_previous,
                                 np.maximum(0, fallback + bundle["previous_day_ratio_residual_q90"]),
                                 bundle["global_ratio_q90"]) * scale,
    }


def daily_support(frame: pd.DataFrame) -> dict:
    per_day = {}
    for day in range(FIRST_TEST_DAY - 1, LAST_TEST_DAY + 1):
        selected = frame.iloc[day*SLOTS_PER_DAY:(day+1)*SLOTS_PER_DAY]
        if len(selected) != SLOTS_PER_DAY:
            raise ValueError("incomplete fixed day")
        eligible = int((selected["scale"] >= MIN_SCALE).sum())
        per_day[str(day)] = {"eligible": eligible,
                             "ineligible": SLOTS_PER_DAY - eligible}
    threshold = math.ceil(.95*SLOTS_PER_DAY)
    return {"per_day": per_day, "threshold_per_day": threshold,
            "all_passed": all(entry["eligible"] >= threshold
                              for entry in per_day.values())}


def daily_multiplier(y: np.ndarray, raw: np.ndarray, scale: np.ndarray,
                     day: int) -> float:
    first, end = day*SLOTS_PER_DAY, (day+1)*SLOTS_PER_DAY
    mask = scale[first:end] >= MIN_SCALE
    prior_prediction = np.maximum(raw[first:end][mask], 1.)
    prior_y = y[first:end][mask]
    if (not len(prior_y) or not np.isfinite(prior_prediction).all()
            or not np.isfinite(prior_y).all()):
        raise ValueError("invalid previous-day calibration support")
    return coding.weighted_quantile(prior_y/prior_prediction,
                                     prior_prediction, Q)


def metrics(y: np.ndarray, predicted: np.ndarray) -> dict:
    if (not len(y) or not np.isfinite(predicted).all()
            or (predicted < 0).any()):
        raise ValueError("invalid prediction")
    return {"bins": int(len(y)),
            "absolute_token_pinball": float(np.mean(coding.pinball(y, predicted))),
            "coverage": float(np.mean(y <= predicted)),
            "mean_observed_tokens": float(np.mean(y)),
            "mean_predicted_tokens": float(np.mean(predicted))}


def score(frame: pd.DataFrame, raw: dict[str, np.ndarray]) -> dict:
    y = frame["y"].to_numpy(float)
    scale = frame["scale"].to_numpy(float)
    result = {}
    for day in range(FIRST_TEST_DAY, LAST_TEST_DAY + 1):
        first, end = day*SLOTS_PER_DAY, (day+1)*SLOTS_PER_DAY
        mask = scale[first:end] >= MIN_SCALE
        actual = y[first:end][mask]
        entry = {"eligible_bins": int(mask.sum()), "methods": {},
                 "previous_day_multiplier": {}}
        for name, predicted in raw.items():
            subset = predicted[first:end][mask]
            alpha = daily_multiplier(y, predicted, scale, day-1)
            entry["previous_day_multiplier"][name] = alpha
            entry["methods"][name] = metrics(actual, subset)
            entry["methods"]["calibrated_"+name] = metrics(
                actual, alpha * np.maximum(subset, 1.))
        result[str(day)] = entry
    methods = RAW_METHODS + CALIBRATED
    average = {method: float(np.mean([
        entry["methods"][method]["absolute_token_pinball"]
        for entry in result.values()])) for method in methods}
    wins = sum(entry["methods"][PRIMARY]["absolute_token_pinball"] < min(
        entry["methods"][baseline]["absolute_token_pinball"]
        for baseline in BASELINES) for entry in result.values())
    checks = {f"5pct_below_{baseline}": average[PRIMARY] <= .95*average[baseline]
              for baseline in BASELINES}
    checks["at_least_6_of_7_daily_wins"] = wins >= 6
    checks["primary_coverage_each_day_0_85_to_0_95"] = all(
        .85 <= entry["methods"][PRIMARY]["coverage"] <= .95
        for entry in result.values())
    return {"per_day": result, "equal_day_mean_absolute_token_pinball": average,
            "gate": {"all_passed": bool(all(checks.values())),
                     "checks": checks, "daily_wins": int(wins)}}


def environment() -> dict:
    return {"script_sha256": coding.file_sha256(Path(__file__)),
            "test_sha256": coding.file_sha256(TEST_FILE),
            "protocol_sha256": coding.file_sha256(PROTOCOL),
            "python": os.sys.version.split()[0], "numpy": np.__version__,
            "pandas": pd.__version__, "sklearn": sklearn.__version__}


def freeze() -> dict:
    if SOURCE.exists():
        raise ValueError("RAGPulse body already exists before model freeze")
    if OUTPUT.exists():
        raise FileExistsError("RAGPulse transfer freeze already exists")
    if not TEST_FILE.is_file() or not PROTOCOL.is_file():
        raise FileNotFoundError("protocol or tests missing")
    bundle = fit_coding_model()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    model_path = OUTPUT / "weekend_free_coding_model.joblib"
    temp = model_path.with_name(f".{model_path.name}.{os.getpid()}.tmp")
    try:
        joblib.dump(bundle, temp, compress=3)
        os.replace(temp, model_path)
    finally:
        temp.unlink(missing_ok=True)
    record = {"status": "FROZEN_BEFORE_RAGPULSE_BODY",
              "source_commit": "7da286becf0f049b2bcb1e5a11d9ba8eb638eff4",
              "source_git_blob_sha1": SOURCE_GIT_BLOB_SHA1,
              "source_bytes": SOURCE_BYTES, "expected_rows": EXPECTED_ROWS,
              "coding_source_sha256": coding.SOURCE_SHA256,
              "coding_training_bins": 852, "features": FEATURES,
              "model_parameters": coding.PARAMS,
              "model_sha256": coding.file_sha256(model_path),
              "environment": environment(),
              "test_days": list(range(FIRST_TEST_DAY, LAST_TEST_DAY+1)),
              "calibration_day_for_first_test_day": FIRST_TEST_DAY-1,
              "support_threshold_per_day": math.ceil(.95*SLOTS_PER_DAY),
              "known_prior_exposure": "Azure Coding and Conversation outcomes; RAGPulse source body unseen"}
    coding.atomic_json(OUTPUT / "freeze.json", record)
    return record


def evaluate_once() -> dict:
    frozen_path = OUTPUT / "freeze.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    model_path = OUTPUT / "weekend_free_coding_model.joblib"
    if (frozen["status"] != "FROZEN_BEFORE_RAGPULSE_BODY"
            or frozen["source_git_blob_sha1"] != SOURCE_GIT_BLOB_SHA1
            or frozen["model_sha256"] != coding.file_sha256(model_path)
            or frozen["environment"] != environment()):
        raise ValueError("RAGPulse transfer freeze changed")
    if (OUTPUT / "evaluation_started.json").exists():
        raise FileExistsError("RAGPulse transfer score already started")
    source_sha256 = verify_external_source()
    y, counts, rows = aggregate_jsonl(SOURCE)
    if len(y) != N_SLOTS or rows != EXPECTED_ROWS or int(counts.sum()) != rows:
        raise ValueError("RAGPulse fixed grid invalid")
    frame = design(y)
    support = daily_support(frame)
    if not support["all_passed"]:
        result = {"status": "SUPPORT_NO_GO_NO_SCORE",
                  "source_sha256": source_sha256,
                  "source_git_blob_sha1": SOURCE_GIT_BLOB_SHA1,
                  "freeze_sha256": coding.file_sha256(frozen_path),
                  "request_rows": rows, "support": support}
        coding.atomic_json(OUTPUT / "support_no_go.json", result)
        return result
    bundle = joblib.load(model_path)
    raw = predict_raw(bundle, frame)
    scale = frame["scale"].to_numpy(float)
    for day in range(FIRST_TEST_DAY, LAST_TEST_DAY + 1):
        for forecast in raw.values():
            daily_multiplier(y.astype(float), forecast, scale, day-1)
    coding.atomic_json(OUTPUT / "evaluation_started.json", {
        "status": "ONE_TIME_RAGPULSE_SCORE_STARTED",
        "freeze_sha256": coding.file_sha256(frozen_path),
        "source_sha256": source_sha256,
        "support": support})
    assessment = score(frame, raw)
    result = {"status": "INDEPENDENT_TRANSFER_EXPLORATORY_GO"
              if assessment["gate"]["all_passed"] else
              "INDEPENDENT_TRANSFER_NO_GO",
              "scope": "recorded RAGPulse sample next-five-minute input token sums",
              "source_sha256": source_sha256,
              "source_git_blob_sha1": SOURCE_GIT_BLOB_SHA1,
              "freeze_sha256": coding.file_sha256(frozen_path),
              "model_sha256": coding.file_sha256(model_path),
              "code_environment": environment(),
              "request_rows": rows, "support": support,
              "assessment": assessment,
              "target_outcomes_seen_before_freeze": False,
              "method_hypothesis_informed_by_prior_Conversation_failure": True}
    coding.atomic_json(OUTPUT / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "evaluate_once"))
    args = parser.parse_args()
    result = freeze() if args.command == "freeze" else evaluate_once()
    print(json.dumps({"status": result["status"],
                      "support": result.get("support"),
                      "gate": result.get("assessment", {}).get("gate")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
