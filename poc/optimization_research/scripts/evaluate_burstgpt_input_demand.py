"""Frozen BurstGPT Conversation-log demand experiment: train/validation only.

There is deliberately no test-scoring mode. ``freeze`` hashes inputs and code
without parsing rows; ``select`` stops before relative day 285 and writes only
train/validation support, scores, and train-fitted models.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/burstgpt_v2/BurstGPT_3.csv"
SOURCE_AUDIT = ROOT / "data/external/burstgpt_v2/audit.json"
PROTOCOL = ROOT / "reports/burstgpt_input_demand_protocol.md"
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_burstgpt_input_demand.py"
OUT = ROOT / "artifacts/burstgpt_input_demand_v1"
FREEZE = OUT / "freeze_manifest.json"
SUPPORT = OUT / "preperiod_support.json"
METRICS = OUT / "validation_metrics.json"
MODELS = OUT / "train_models.joblib"
PREDICTIONS = OUT / "validation_predictions.csv"

SOURCE_SHA256 = "2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f"
SOURCE_BYTES = 231_682_327
HEADER = ("Timestamp", "Session ID", "Elapsed time", "Model", "Request tokens",
          "Response tokens", "Total tokens", "Log Type")
COHORTS = ("ChatGPT", "GPT-4")
TRAIN_START_DAY, VAL_START_DAY, TEST_START_DAY, TEST_END_DAY = 225, 265, 285, 335
START_H = TRAIN_START_DAY * 24
VAL_START_H = VAL_START_DAY * 24
TEST_START_H = TEST_START_DAY * 24
Q = 0.9
BASELINES = ("train_fixed_p90", "last_available_residual_p90",
             "same_hour_yesterday_residual_p90", "trailing_24h_p90")
FEATURE_NAMES = (
    "log_volume_lag2", "log_volume_lag3", "log_volume_lag4", "log_volume_lag24",
    "log_count_lag2", "log_count_lag3", "log_count_lag4", "log_count_lag24",
    "log_trailing24_volume_mean", "log_trailing24_volume_p90",
    "trailing24_nonempty_fraction", "hour_phase_sin", "hour_phase_cos",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def freeze() -> dict:
    if any(path.exists() for path in (FREEZE, SUPPORT, METRICS, MODELS, PREDICTIONS)):
        raise FileExistsError("BurstGPT input-demand v1 artifacts already exist")
    if SOURCE.stat().st_size != SOURCE_BYTES or sha256_file(SOURCE) != SOURCE_SHA256:
        raise ValueError("Official BurstGPT_3.csv bytes or SHA256 differ")
    frozen = {
        "kind": "khu.burstgpt_input_demand_v1.freeze",
        "status": "train_validation_only_no_test_targets",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source_sha256": SOURCE_SHA256,
        "source_bytes": SOURCE_BYTES,
        "source_audit_sha256": sha256_file(SOURCE_AUDIT),
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(SCRIPT),
        "test_sha256": sha256_file(TESTS),
        "python_version": sys.version.split()[0],
        "numpy_version": np.__version__,
        "sklearn_version": sklearn.__version__,
        "joblib_version": joblib.__version__,
        "permitted_next_action": "Audit and score only relative days 225-284; do not parse test target.",
    }
    _write_json_new(FREEZE, frozen)
    return frozen


def verify_freeze() -> dict:
    if FREEZE.is_symlink() or not FREEZE.is_file():
        raise ValueError("Frozen train/validation manifest is absent or a symlink")
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (frozen.get("kind") != "khu.burstgpt_input_demand_v1.freeze"
            or frozen.get("status") != "train_validation_only_no_test_targets"
            or frozen.get("source_bytes") != SOURCE_BYTES
            or frozen.get("source_sha256") != SOURCE_SHA256):
        raise ValueError("Wrong BurstGPT input-demand freeze kind or source")
    for key, path in (("source_audit_sha256", SOURCE_AUDIT),
                      ("protocol_sha256", PROTOCOL), ("script_sha256", SCRIPT),
                      ("test_sha256", TESTS)):
        if frozen.get(key) != sha256_file(path):
            raise ValueError(f"Frozen {key} differs")
    if SOURCE.stat().st_size != SOURCE_BYTES or sha256_file(SOURCE) != SOURCE_SHA256:
        raise ValueError("Frozen source bytes or SHA256 differ")
    for key, actual in (("python_version", sys.version.split()[0]),
                        ("numpy_version", np.__version__),
                        ("sklearn_version", sklearn.__version__),
                        ("joblib_version", joblib.__version__)):
        if frozen.get(key) != actual:
            raise ValueError(f"Frozen runtime {key} differs")
    return frozen


def empty_hourly(start_h: int, end_h: int) -> dict[str, dict[str, np.ndarray]]:
    n = end_h - start_h
    if n <= 0:
        raise ValueError("Hour interval must be nonempty")
    return {cohort: {"count": np.zeros(n, np.int64),
                     "volume": np.zeros(n, np.int64),
                     "zero_token_requests": np.zeros(n, np.int64)}
            for cohort in COHORTS}


def add_event(hourly: dict, *, start_h: int, timestamp_s: float,
              model: str, log_type: str, request_tokens: int) -> None:
    """An unlogged hour stays count=0; a logged zero-token event has count>0."""
    if not math.isfinite(timestamp_s) or timestamp_s < 0 or request_tokens < 0:
        raise ValueError("Invalid event timestamp or input-token count")
    if log_type != "Conversation log" or model not in COHORTS:
        return
    h = math.floor(timestamp_s / 3600) - start_h
    if not 0 <= h < len(hourly[model]["count"]):
        raise ValueError("Event is outside the requested pre-period")
    hourly[model]["count"][h] += 1
    hourly[model]["volume"][h] += request_tokens
    hourly[model]["zero_token_requests"][h] += int(request_tokens == 0)


def load_preperiod(source: Path = SOURCE,
                   *, expected_pre_rows: int = 3_452_522) -> tuple[dict, dict]:
    """Never parse the input-token field of a row on or after test day 285."""
    hourly = empty_hourly(START_H, TEST_START_H)
    source_rows = 0
    prev_timestamp = -math.inf
    reached_test_boundary = False
    with source.open("r", encoding="utf-8", newline="") as stream:
        header = next(stream).rstrip("\r\n").split(",")
        if tuple(header) != HEADER:
            raise ValueError("BurstGPT CSV header changed")
        for line in stream:
            # The first field alone determines whether any other field may be parsed.
            timestamp_s = float(line.split(",", 1)[0])
            if not math.isfinite(timestamp_s) or timestamp_s < prev_timestamp:
                raise ValueError("BurstGPT pre-period timestamp is invalid or unsorted")
            if timestamp_s >= TEST_START_DAY * 86_400:
                reached_test_boundary = True
                break
            prev_timestamp = timestamp_s
            if timestamp_s < TRAIN_START_DAY * 86_400:
                raise ValueError("Source begins before the frozen train interval")
            row = next(csv.reader((line,)))
            if len(row) != len(HEADER):
                raise ValueError("Malformed pre-period CSV row")
            source_rows += 1
            add_event(hourly, start_h=START_H, timestamp_s=timestamp_s,
                      model=row[3], request_tokens=int(row[4]), log_type=row[7])
    if not reached_test_boundary or source_rows != expected_pre_rows:
        raise ValueError("Pre-period source boundary or frozen row count differs")
    return hourly, {"preperiod_source_rows": source_rows,
                    "stopped_before_test_target": reached_test_boundary}


def feature_row(volume: np.ndarray, count: np.ndarray, *, h: int,
                start_h: int = START_H) -> np.ndarray:
    idx = h - start_h
    if idx < 25 or idx >= len(volume) or len(volume) != len(count):
        raise ValueError("Decision lacks the frozen 24-hour past or source hour")
    recent_v = volume[idx - 25:idx - 1]  # h-25 .. h-2, exactly 24 hours
    recent_n = count[idx - 25:idx - 1]
    if len(recent_v) != 24:
        raise AssertionError("Past window is not 24 completed, published hours")
    values = [np.log1p(volume[idx - lag]) for lag in (2, 3, 4, 24)]
    values.extend(np.log1p(count[idx - lag]) for lag in (2, 3, 4, 24))
    values.extend((np.log1p(float(recent_v.mean())),
                   np.log1p(float(np.quantile(recent_v, Q, method="higher"))),
                   float(np.mean(recent_n > 0)),
                   math.sin(2 * math.pi * (h % 24) / 24),
                   math.cos(2 * math.pi * (h % 24) / 24)))
    return np.asarray(values, dtype=float)


def split_hours() -> tuple[np.ndarray, np.ndarray]:
    # The label for h is published at h+2; the final train hour h=6359
    # cannot be used for fitting at validation start h=6360.
    train = np.arange(START_H + 25, VAL_START_H - 1, dtype=np.int64)
    validation = np.arange(VAL_START_H, TEST_START_H, dtype=np.int64)
    assert len(train) == 934 and train.max() + 2 <= VAL_START_H
    assert len(validation) == 480 and validation.min() == VAL_START_H
    return train, validation


def quantile_higher(values: np.ndarray) -> float:
    return float(np.quantile(values, Q, method="higher"))


def train_baseline_constants(volume: np.ndarray, train_h: np.ndarray,
                             *, start_h: int = START_H) -> dict[str, float]:
    idx = train_h - start_h
    y = volume[idx].astype(float)
    return {
        "train_fixed_p90": quantile_higher(y),
        "last_available_residual_p90": quantile_higher(y - volume[idx - 2]),
        "same_hour_yesterday_residual_p90": quantile_higher(y - volume[idx - 24]),
    }


def baseline_predictions(volume: np.ndarray, hours: np.ndarray,
                         constants: dict[str, float],
                         *, start_h: int = START_H) -> dict[str, np.ndarray]:
    idx = hours - start_h
    if np.any(idx < 25) or np.any(idx >= len(volume)):
        raise ValueError("Baseline hours leave the loaded pre-period")
    rolling = np.asarray([quantile_higher(volume[i - 25:i - 1]) for i in idx])
    return {
        "train_fixed_p90": np.full(len(idx), constants["train_fixed_p90"]),
        "last_available_residual_p90": np.maximum(
            0, volume[idx - 2] + constants["last_available_residual_p90"]),
        "same_hour_yesterday_residual_p90": np.maximum(
            0, volume[idx - 24] + constants["same_hour_yesterday_residual_p90"]),
        "trailing_24h_p90": rolling,
    }


def pinball(y: np.ndarray, prediction: np.ndarray) -> float:
    if len(y) != len(prediction) or not len(y):
        raise ValueError("Pinball arrays must align and be nonempty")
    error = np.asarray(y, float) - np.asarray(prediction, float)
    return float(np.mean(np.maximum(Q * error, (Q - 1) * error)))


def preperiod_support(hourly: dict, source_info: dict) -> dict:
    support = {"kind": "burstgpt_input_demand_v1_preperiod_support",
               "source_sha256": SOURCE_SHA256,
               "freeze_manifest_sha256": sha256_file(FREEZE),
               **source_info, "cohorts": {}, "all_pass": True}
    for cohort in COHORTS:
        data = hourly[cohort]
        parts = {}
        for name, left, right, min_days, min_frac in (
                ("train", TRAIN_START_DAY, VAL_START_DAY, 30, 0.70),
                ("validation", VAL_START_DAY, TEST_START_DAY, 15, 0.70)):
            sl = slice((left - TRAIN_START_DAY) * 24, (right - TRAIN_START_DAY) * 24)
            counts = data["count"][sl]
            active = counts > 0
            days = len(counts) // 24
            item = {
                "relative_days": [left, right - 1],
                "hours": len(counts),
                "logged_requests": int(counts.sum()),
                "zero_input_token_requests": int(data["zero_token_requests"][sl].sum()),
                "hours_with_logged_requests": int(active.sum()),
                "days_with_logged_requests": int(active.reshape(days, 24).any(axis=1).sum()),
            }
            item["hours_with_logged_requests_fraction"] = item["hours_with_logged_requests"] / len(counts)
            item["pass"] = (item["days_with_logged_requests"] >= min_days
                            and item["hours_with_logged_requests_fraction"] >= min_frac)
            parts[name] = item
            support["all_pass"] = support["all_pass"] and item["pass"]
        support["cohorts"][cohort] = parts
    return support


def fit_train_only(hourly: dict, train_h: np.ndarray) -> tuple[dict, dict]:
    models, constants = {}, {}
    for cohort in COHORTS:
        data = hourly[cohort]
        x = np.stack([feature_row(data["volume"], data["count"], h=int(h))
                      for h in train_h])
        y = data["volume"][train_h - START_H].astype(float)
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=Q, max_iter=80, max_leaf_nodes=7,
            learning_rate=0.05, min_samples_leaf=20, l2_regularization=1.0,
            random_state=42)
        model.fit(x, y)
        models[cohort] = model
        constants[cohort] = train_baseline_constants(data["volume"], train_h)
    return models, constants


def score_validation(hourly: dict, models: dict, constants: dict,
                     validation_h: np.ndarray) -> tuple[dict, list[dict]]:
    by_cohort = {}
    by_day = {}
    prediction_rows = []
    for cohort in COHORTS:
        data = hourly[cohort]
        volume, count = data["volume"], data["count"]
        x = np.stack([feature_row(volume, count, h=int(h)) for h in validation_h])
        y = volume[validation_h - START_H].astype(float)
        predictions = baseline_predictions(volume, validation_h, constants[cohort])
        predictions["quantile_hgb"] = np.maximum(0, models[cohort].predict(x))
        for i, hour in enumerate(validation_h):
            prediction_rows.append({
                "cohort": cohort, "relative_hour": int(hour),
                "relative_day": int(hour // 24),
                "recorded_request_count": int(count[hour - START_H]),
                "recorded_input_tokens": int(y[i]),
                **{name: float(pred[i]) for name, pred in predictions.items()},
            })
        by_cohort[cohort] = {
            name: {"pinball": pinball(y, pred),
                   "coverage": float(np.mean(y <= pred)),
                   "mean_prediction": float(np.mean(pred))}
            for name, pred in predictions.items()
        }
        by_day[cohort] = {
            str(day): {name: pinball(y[validation_h // 24 == day],
                                    pred[validation_h // 24 == day])
                       for name, pred in predictions.items()}
            for day in range(VAL_START_DAY, TEST_START_DAY)
        }
    overall = {name: float(np.mean([by_cohort[c][name]["pinball"]
                                    for c in COHORTS]))
               for name in (*BASELINES, "quantile_hgb")}
    best = min(overall[name] for name in BASELINES)
    selected = next(name for name in BASELINES if overall[name] <= best + 1e-12)
    daily_wins = int(sum(
        np.mean([by_day[c][str(day)]["quantile_hgb"] for c in COHORTS])
        < np.mean([by_day[c][str(day)][selected] for c in COHORTS])
        for day in range(VAL_START_DAY, TEST_START_DAY)))
    gates = {
        "relative_pinball_improvement_at_least_5pct": overall["quantile_hgb"] <= 0.95 * overall[selected],
        "both_cohorts_lower_pinball": all(by_cohort[c]["quantile_hgb"]["pinball"]
                                          < by_cohort[c][selected]["pinball"] for c in COHORTS),
        "both_cohorts_coverage_at_least_85pct": all(by_cohort[c]["quantile_hgb"]["coverage"]
                                                    >= 0.85 for c in COHORTS),
        "wins_at_least_12_of_20_days": daily_wins >= 12,
    }
    gates["all_pass"] = all(gates.values())
    return ({"selected_baseline": selected, "overall_equal_cohort_pinball": overall,
             "by_cohort": by_cohort, "daily_pinball": by_day,
             "daily_wins_against_selected_baseline": daily_wins,
             "adoption_gates": gates}, prediction_rows)


def select() -> dict:
    if any(path.exists() for path in (SUPPORT, METRICS, MODELS, PREDICTIONS)):
        raise FileExistsError("Refuse to overwrite existing pre-period artifacts")
    frozen = verify_freeze()
    hourly, source_info = load_preperiod()
    support = preperiod_support(hourly, source_info)
    _write_json_new(SUPPORT, support)
    if not support["all_pass"]:
        raise ValueError("Pre-period support gate failed; do not fit or score")
    train_h, validation_h = split_hours()
    models, constants = fit_train_only(hourly, train_h)
    scores, prediction_rows = score_validation(hourly, models, constants, validation_h)
    if MODELS.exists():
        raise FileExistsError("Refuse to overwrite train-fitted model")
    joblib.dump({"models": models, "baseline_train_constants": constants,
                 "feature_names": FEATURE_NAMES,
                 "train_relative_hours": [int(train_h.min()), int(train_h.max())],
                 "source_sha256": SOURCE_SHA256,
                 "freeze_manifest_sha256": sha256_file(FREEZE)}, MODELS)
    with PREDICTIONS.open("x", encoding="utf-8", newline="") as stream:
        columns = ("cohort", "relative_hour", "relative_day", "recorded_request_count",
                   "recorded_input_tokens", *BASELINES, "quantile_hgb")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(prediction_rows)
    result = {
        "kind": "burstgpt_input_demand_v1_train_validation_only",
        "test_target_read": False,
        "source_sha256": SOURCE_SHA256,
        "freeze_manifest_sha256": sha256_file(FREEZE),
        "protocol_sha256": frozen["protocol_sha256"],
        "script_sha256": frozen["script_sha256"],
        "support_sha256": sha256_file(SUPPORT),
        "models_sha256": sha256_file(MODELS),
        "validation_predictions_sha256": sha256_file(PREDICTIONS),
        "train_fit_hours_per_cohort": len(train_h),
        "validation_hours_per_cohort": len(validation_h),
        "train_last_target_hour": int(train_h.max()),
        "validation_first_decision_hour": int(validation_h.min()),
        **scores,
    }
    _write_json_new(METRICS, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "select"))
    args = parser.parse_args()
    if args.stage == "freeze":
        value = freeze()
        print(json.dumps({"freeze": str(FREEZE), "status": value["status"]}, sort_keys=True))
    else:
        value = select()
        print(json.dumps({"metrics": str(METRICS),
                          "selected_baseline": value["selected_baseline"],
                          "adoption_gates": value["adoption_gates"]}, sort_keys=True))


if __name__ == "__main__":
    main()
