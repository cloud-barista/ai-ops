"""Frozen, validation-only BurstGPT hourly response-token demand experiment.

``freeze`` hashes the source without parsing outcomes. ``select`` stops at the
first test-day timestamp, so this script cannot score day 285 onward.
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
PROTOCOL = ROOT / "reports/burstgpt_output_demand_protocol.md"
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_burstgpt_output_demand.py"
OUT = ROOT / "artifacts/burstgpt_output_demand_v1"
FREEZE = OUT / "freeze_manifest.json"
SUPPORT = OUT / "preperiod_support.json"
MODELS = OUT / "train_models.joblib"
PREDICTIONS = OUT / "validation_predictions.csv"
METRICS = OUT / "validation_metrics.json"

SOURCE_SHA256 = "2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f"
SOURCE_BYTES = 231_682_327
HEADER = ("Timestamp", "Session ID", "Elapsed time", "Model", "Request tokens",
          "Response tokens", "Total tokens", "Log Type")
COHORTS = ("ChatGPT", "GPT-4")
TRAIN_START_DAY, VAL_START_DAY, TEST_START_DAY = 225, 265, 285
START_H, VAL_START_H, TEST_START_H = (day * 24 for day in
                                      (TRAIN_START_DAY, VAL_START_DAY, TEST_START_DAY))
Q = 0.9
BASELINES = ("train_fixed_p90", "last_available_residual_p90",
             "same_hour_yesterday_residual_p90", "trailing_24h_p90")
FEATURE_NAMES = (
    "log_output_lag2", "log_output_lag3", "log_output_lag4", "log_output_lag24",
    "log_input_lag2", "log_input_lag3", "log_input_lag4", "log_input_lag24",
    "log_count_lag2", "log_count_lag3", "log_count_lag4", "log_count_lag24",
    "log_trailing24_output_mean", "log_trailing24_output_p90",
    "log_trailing24_input_mean", "trailing24_nonempty_fraction",
    "hour_phase_sin", "hour_phase_cos",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def freeze() -> dict:
    if any(p.exists() for p in (FREEZE, SUPPORT, MODELS, PREDICTIONS, METRICS)):
        raise FileExistsError("Output-demand v1 artifacts already exist")
    if SOURCE.stat().st_size != SOURCE_BYTES or sha256_file(SOURCE) != SOURCE_SHA256:
        raise ValueError("Official BurstGPT_3.csv bytes or SHA256 differ")
    frozen = {
        "kind": "khu.burstgpt_output_demand_v1.freeze",
        "status": "train_validation_only_no_test_targets_exploratory",
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
        "permitted_next_action": "Score only days 225-284; do not parse test target.",
    }
    write_json_new(FREEZE, frozen)
    return frozen


def verify_freeze() -> dict:
    if FREEZE.is_symlink() or not FREEZE.is_file():
        raise ValueError("Frozen output-demand manifest is absent or a symlink")
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (frozen.get("kind") != "khu.burstgpt_output_demand_v1.freeze"
            or frozen.get("status") != "train_validation_only_no_test_targets_exploratory"
            or frozen.get("source_bytes") != SOURCE_BYTES
            or frozen.get("source_sha256") != SOURCE_SHA256):
        raise ValueError("Wrong output-demand freeze kind or source")
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
    if end_h <= start_h:
        raise ValueError("Hour interval must be nonempty")
    n = end_h - start_h
    return {cohort: {key: np.zeros(n, dtype=np.int64)
                     for key in ("count", "input", "output", "zero_output_requests")}
            for cohort in COHORTS}


def add_event(hourly: dict, *, start_h: int, timestamp_s: float, model: str,
              log_type: str, request_tokens: int, response_tokens: int) -> None:
    if (not math.isfinite(timestamp_s) or timestamp_s < 0
            or request_tokens < 0 or response_tokens < 0):
        raise ValueError("Invalid event timestamp or token count")
    if log_type != "Conversation log" or model not in COHORTS:
        return
    idx = math.floor(timestamp_s / 3600) - start_h
    if not 0 <= idx < len(hourly[model]["count"]):
        raise ValueError("Event is outside the loaded pre-period")
    data = hourly[model]
    data["count"][idx] += 1
    data["input"][idx] += request_tokens
    data["output"][idx] += response_tokens
    data["zero_output_requests"][idx] += int(response_tokens == 0)


def _scan_preperiod(source: Path, *, expected_pre_rows: int,
                    parse_outcomes: bool) -> tuple[dict, dict]:
    """In the support pass, tokenize rows but never inspect token outcomes."""
    hourly = empty_hourly(START_H, TEST_START_H)
    source_rows = 0
    prev_timestamp = -math.inf
    reached_test_boundary = False
    with source.open("r", encoding="utf-8", newline="") as stream:
        header = next(stream).rstrip("\r\n").split(",")
        if tuple(header) != HEADER:
            raise ValueError("BurstGPT CSV header changed")
        for line in stream:
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
            if row[7] == "Conversation log" and row[3] in COHORTS:
                if parse_outcomes:
                    add_event(hourly, start_h=START_H, timestamp_s=timestamp_s,
                              model=row[3], log_type=row[7],
                              request_tokens=int(row[4]), response_tokens=int(row[5]))
                else:
                    idx = math.floor(timestamp_s / 3600) - START_H
                    hourly[row[3]]["count"][idx] += 1
    if not reached_test_boundary or source_rows != expected_pre_rows:
        raise ValueError("Pre-period source boundary or frozen row count differs")
    return hourly, {"preperiod_source_rows": source_rows,
                    "stopped_before_test_target": reached_test_boundary}


def load_preperiod_support(source: Path = SOURCE,
                           *, expected_pre_rows: int = 3_452_522) -> tuple[dict, dict]:
    """Read only pre-test timestamps, cohort identity, and event counts."""
    return _scan_preperiod(source, expected_pre_rows=expected_pre_rows,
                           parse_outcomes=False)


def load_preperiod(source: Path = SOURCE,
                   *, expected_pre_rows: int = 3_452_522) -> tuple[dict, dict]:
    """Read target totals only after the separate support gate has passed."""
    return _scan_preperiod(source, expected_pre_rows=expected_pre_rows,
                           parse_outcomes=True)


def feature_row(output: np.ndarray, input_: np.ndarray, count: np.ndarray,
                *, h: int, start_h: int = START_H) -> np.ndarray:
    idx = h - start_h
    if (idx < 25 or idx >= len(output)
            or len(output) != len(input_) or len(output) != len(count)):
        raise ValueError("Decision lacks 24 published hours or source hour")
    past_output = output[idx - 25:idx - 1]  # h-25 .. h-2
    past_input = input_[idx - 25:idx - 1]
    past_count = count[idx - 25:idx - 1]
    values = [np.log1p(array[idx - lag])
              for array in (output, input_, count) for lag in (2, 3, 4, 24)]
    values.extend((np.log1p(float(past_output.mean())),
                   np.log1p(float(np.quantile(past_output, Q, method="higher"))),
                   np.log1p(float(past_input.mean())),
                   float(np.mean(past_count > 0)),
                   math.sin(2 * math.pi * (h % 24) / 24),
                   math.cos(2 * math.pi * (h % 24) / 24)))
    result = np.asarray(values, dtype=float)
    assert len(result) == len(FEATURE_NAMES)
    return result


def split_hours() -> tuple[np.ndarray, np.ndarray]:
    train = np.arange(START_H + 25, VAL_START_H - 1, dtype=np.int64)
    validation = np.arange(VAL_START_H, TEST_START_H, dtype=np.int64)
    assert len(train) == 934 and train.max() + 2 <= VAL_START_H
    assert len(validation) == 480
    return train, validation


def q90(values: np.ndarray) -> float:
    return float(np.quantile(values, Q, method="higher"))


def train_baseline_constants(output: np.ndarray, train_h: np.ndarray,
                             *, start_h: int = START_H) -> dict[str, float]:
    idx = train_h - start_h
    y = output[idx].astype(float)
    return {
        "train_fixed_p90": q90(y),
        "last_available_residual_p90": q90(y - output[idx - 2]),
        "same_hour_yesterday_residual_p90": q90(y - output[idx - 24]),
    }


def baseline_predictions(output: np.ndarray, hours: np.ndarray,
                         constants: dict[str, float],
                         *, start_h: int = START_H) -> dict[str, np.ndarray]:
    idx = hours - start_h
    if np.any(idx < 25) or np.any(idx >= len(output)):
        raise ValueError("Baseline hours leave the loaded pre-period")
    trailing = np.asarray([q90(output[i - 25:i - 1]) for i in idx])
    return {
        "train_fixed_p90": np.full(len(idx), constants["train_fixed_p90"]),
        "last_available_residual_p90": np.maximum(
            0, output[idx - 2] + constants["last_available_residual_p90"]),
        "same_hour_yesterday_residual_p90": np.maximum(
            0, output[idx - 24] + constants["same_hour_yesterday_residual_p90"]),
        "trailing_24h_p90": trailing,
    }


def pinball(y: np.ndarray, predicted: np.ndarray) -> float:
    if len(y) != len(predicted) or not len(y):
        raise ValueError("Pinball arrays must align and be nonempty")
    error = np.asarray(y, float) - np.asarray(predicted, float)
    return float(np.mean(np.maximum(Q * error, (Q - 1) * error)))


def preperiod_support(hourly: dict, source_info: dict) -> dict:
    support = {"kind": "burstgpt_output_demand_v1_preperiod_support",
               "source_sha256": SOURCE_SHA256,
               "freeze_manifest_sha256": sha256_file(FREEZE),
               **source_info, "cohorts": {}, "all_pass": True}
    for cohort in COHORTS:
        parts = {}
        data = hourly[cohort]
        for name, left, right, min_days in (
                ("train", TRAIN_START_DAY, VAL_START_DAY, 30),
                ("validation", VAL_START_DAY, TEST_START_DAY, 15)):
            sl = slice((left - TRAIN_START_DAY) * 24, (right - TRAIN_START_DAY) * 24)
            counts = data["count"][sl]
            active = counts > 0
            days = len(counts) // 24
            item = {
                "relative_days": [left, right - 1],
                "hours": len(counts),
                "logged_requests": int(counts.sum()),
                "hours_with_logged_requests": int(active.sum()),
                "days_with_logged_requests": int(active.reshape(days, 24).any(axis=1).sum()),
            }
            item["hours_with_logged_requests_fraction"] = (
                item["hours_with_logged_requests"] / len(counts))
            item["pass"] = (item["days_with_logged_requests"] >= min_days
                            and item["hours_with_logged_requests_fraction"] >= 0.70)
            parts[name] = item
            support["all_pass"] = support["all_pass"] and item["pass"]
        support["cohorts"][cohort] = parts
    return support


def fit_train_only(hourly: dict, train_h: np.ndarray) -> tuple[dict, dict]:
    models, constants = {}, {}
    for cohort in COHORTS:
        data = hourly[cohort]
        x = np.stack([feature_row(data["output"], data["input"], data["count"], h=int(h))
                      for h in train_h])
        y = data["output"][train_h - START_H].astype(float)
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=Q, max_iter=80, max_leaf_nodes=7,
            learning_rate=0.05, min_samples_leaf=20, l2_regularization=1.0,
            early_stopping=False, random_state=42)
        model.fit(x, y)
        models[cohort] = model
        constants[cohort] = train_baseline_constants(data["output"], train_h)
    return models, constants


def score_validation(hourly: dict, models: dict, constants: dict,
                     validation_h: np.ndarray) -> tuple[dict, list[dict]]:
    by_cohort, by_day, rows = {}, {}, []
    for cohort in COHORTS:
        data = hourly[cohort]
        output, input_, count = data["output"], data["input"], data["count"]
        x = np.stack([feature_row(output, input_, count, h=int(h)) for h in validation_h])
        y = output[validation_h - START_H].astype(float)
        predicted = baseline_predictions(output, validation_h, constants[cohort])
        predicted["quantile_hgb"] = np.maximum(0, models[cohort].predict(x))
        for i, h in enumerate(validation_h):
            rows.append({
                "cohort": cohort, "relative_hour": int(h), "relative_day": int(h // 24),
                "recorded_request_count": int(count[h - START_H]),
                "recorded_input_tokens": int(input_[h - START_H]),
                "recorded_output_tokens": int(y[i]),
                **{name: float(p[i]) for name, p in predicted.items()},
            })
        by_cohort[cohort] = {
            name: {"pinball": pinball(y, p), "coverage": float(np.mean(y <= p)),
                   "mean_prediction": float(np.mean(p))}
            for name, p in predicted.items()
        }
        by_day[cohort] = {
            str(day): {name: {
                "pinball": pinball(y[validation_h // 24 == day],
                                   p[validation_h // 24 == day]),
                "coverage": float(np.mean(y[validation_h // 24 == day]
                                          <= p[validation_h // 24 == day])),
            } for name, p in predicted.items()}
            for day in range(VAL_START_DAY, TEST_START_DAY)
        }
    overall = {name: float(np.mean([by_cohort[c][name]["pinball"] for c in COHORTS]))
               for name in (*BASELINES, "quantile_hgb")}
    best = min(overall[name] for name in BASELINES)
    selected = next(name for name in BASELINES if overall[name] <= best + 1e-12)
    daily_wins = int(sum(
        np.mean([by_day[c][str(day)]["quantile_hgb"]["pinball"] for c in COHORTS])
        < np.mean([by_day[c][str(day)][selected]["pinball"] for c in COHORTS])
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
             "by_cohort": by_cohort, "daily_metrics": by_day,
             "daily_wins_against_selected_baseline": daily_wins,
             "adoption_gates": gates}, rows)


def select() -> dict:
    if any(p.exists() for p in (SUPPORT, MODELS, PREDICTIONS, METRICS)):
        raise FileExistsError("Refuse to overwrite pre-period output-demand artifacts")
    frozen = verify_freeze()
    support_hourly, source_info = load_preperiod_support()
    support = preperiod_support(support_hourly, source_info)
    write_json_new(SUPPORT, support)
    if not support["all_pass"]:
        raise ValueError("Pre-period support failed; do not fit or score")
    hourly, outcome_info = load_preperiod()
    if outcome_info != source_info or any(
            not np.array_equal(hourly[c]["count"], support_hourly[c]["count"])
            for c in COHORTS):
        raise ValueError("Outcome pass differs from frozen support pass")
    train_h, validation_h = split_hours()
    models, constants = fit_train_only(hourly, train_h)
    scores, rows = score_validation(hourly, models, constants, validation_h)
    joblib.dump({"models": models, "baseline_train_constants": constants,
                 "feature_names": FEATURE_NAMES,
                 "train_relative_hours": [int(train_h.min()), int(train_h.max())],
                 "source_sha256": SOURCE_SHA256,
                 "freeze_manifest_sha256": sha256_file(FREEZE)}, MODELS)
    with PREDICTIONS.open("x", encoding="utf-8", newline="") as stream:
        columns = ("cohort", "relative_hour", "relative_day", "recorded_request_count",
                   "recorded_input_tokens", "recorded_output_tokens",
                   *BASELINES, "quantile_hgb")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "kind": "burstgpt_output_demand_v1_train_validation_only",
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
        "zero_output_token_requests": {
            c: {
                "train": int(hourly[c]["zero_output_requests"][:VAL_START_H - START_H].sum()),
                "validation": int(hourly[c]["zero_output_requests"][
                    VAL_START_H - START_H:TEST_START_H - START_H].sum()),
            } for c in COHORTS
        },
        **scores,
    }
    write_json_new(METRICS, result)
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
