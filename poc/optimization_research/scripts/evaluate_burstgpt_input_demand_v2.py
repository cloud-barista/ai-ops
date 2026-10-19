"""One blocked train-only residual calibration for BurstGPT input demand.

`freeze` hashes files only. `select` scores relative days 225--284 after an
independent review supplies the exact freeze digest. There is no test mode.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_burstgpt_input_demand as v1


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/burstgpt_input_demand_v2_protocol.md"
SCRIPT = Path(__file__).resolve()
TESTS = ROOT / "tests/test_burstgpt_input_demand_v2.py"
OUT = ROOT / "artifacts/burstgpt_input_demand_v2"
FREEZE = OUT / "freeze_manifest.json"
SUPPORT = OUT / "preperiod_block_support.json"
METRICS = OUT / "validation_metrics.json"
MODELS = OUT / "fit_models.joblib"
PREDICTIONS = OUT / "validation_predictions.csv"
FIT_START_H = v1.START_H + 25
CAL_START_H = 255 * 24
VAL_START_H = v1.VAL_START_H
TEST_START_H = v1.TEST_START_H
MODEL_NAME = "v2_calibrated_hgb_30d"
RAW_NAME = "v2_uncalibrated_hgb_30d"
OLD_NAME = "v1_quantile_hgb_40d"
EXPECTED_FIT_N = 694
EXPECTED_CAL_N = 239
EXPECTED_VAL_N = 480
EXPECTED_CAL_RANK = 216
PREDICTION_COLUMNS = (
    "cohort", "relative_hour", "relative_day", "recorded_request_count",
    "recorded_input_tokens", *v1.BASELINES, OLD_NAME, RAW_NAME, MODEL_NAME,
)
V1_FILES = {
    "v1_freeze_sha256": v1.FREEZE,
    "v1_support_sha256": v1.SUPPORT,
    "v1_metrics_sha256": v1.METRICS,
    "v1_models_sha256": v1.MODELS,
    "v1_predictions_sha256": v1.PREDICTIONS,
    "v1_script_sha256": v1.SCRIPT,
}


def _sha256_hex(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _versions() -> dict[str, str]:
    return {"python": sys.version.split()[0], "numpy": np.__version__,
            "sklearn": sklearn.__version__, "joblib": joblib.__version__}


def _ensure_new_outputs(paths: tuple[Path, ...]) -> None:
    if any(path.exists() for path in paths):
        raise FileExistsError("Refuse to overwrite existing BurstGPT v2 artifact")


def freeze() -> dict:
    """Hash all dependencies without parsing any raw request target."""
    _ensure_new_outputs((FREEZE, SUPPORT, METRICS, MODELS, PREDICTIONS))
    if v1.SOURCE.stat().st_size != v1.SOURCE_BYTES or v1.sha256_file(v1.SOURCE) != v1.SOURCE_SHA256:
        raise ValueError("BurstGPT source differs from pinned v1 original")
    frozen = {
        "kind": "khu.burstgpt_input_demand_v2.freeze",
        "status": "train_validation_only_no_test_targets",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source_sha256": v1.SOURCE_SHA256,
        "source_bytes": v1.SOURCE_BYTES,
        "source_audit_sha256": v1.sha256_file(v1.SOURCE_AUDIT),
        "protocol_sha256": v1.sha256_file(PROTOCOL),
        "script_sha256": v1.sha256_file(SCRIPT),
        "test_sha256": v1.sha256_file(TESTS),
        **{key: v1.sha256_file(path) for key, path in V1_FILES.items()},
        "versions": _versions(),
        "reviewed_digest_required_on_cli": True,
        "permitted_next_action": "Independent review, then score only train/calibration and validation before day 285.",
        "prior_exposure": "v1 validation metrics known; two incidental day-334 source rows exposed; v2 validation is exploratory.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with FREEZE.open("x", encoding="utf-8") as stream:
        json.dump(frozen, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return frozen


def verify_freeze(expected_sha256: str) -> dict:
    if not _sha256_hex(expected_sha256):
        raise ValueError("Expected reviewed v2 freeze SHA256 is required")
    if FREEZE.is_symlink() or not FREEZE.is_file() or v1.sha256_file(FREEZE) != expected_sha256:
        raise ValueError("v2 freeze differs from reviewed SHA256")
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (frozen.get("kind") != "khu.burstgpt_input_demand_v2.freeze"
            or frozen.get("status") != "train_validation_only_no_test_targets"
            or frozen.get("source_sha256") != v1.SOURCE_SHA256
            or frozen.get("source_bytes") != v1.SOURCE_BYTES
            or frozen.get("reviewed_digest_required_on_cli") is not True
            or frozen.get("versions") != _versions()):
        raise ValueError("v2 freeze identity, source, or runtime differs")
    files = {"source_audit_sha256": v1.SOURCE_AUDIT,
             "protocol_sha256": PROTOCOL, "script_sha256": SCRIPT,
             "test_sha256": TESTS, **V1_FILES}
    for key, path in files.items():
        if path.is_symlink() or not path.is_file() or frozen.get(key) != v1.sha256_file(path):
            raise ValueError(f"Frozen {key} differs")
    if v1.SOURCE.stat().st_size != v1.SOURCE_BYTES or v1.sha256_file(v1.SOURCE) != v1.SOURCE_SHA256:
        raise ValueError("BurstGPT source differs from pinned v1 original")
    old_freeze = v1.verify_freeze()
    old_metrics = json.loads(v1.METRICS.read_text(encoding="utf-8"))
    old_support = json.loads(v1.SUPPORT.read_text(encoding="utf-8"))
    if (old_freeze.get("script_sha256") != frozen["v1_script_sha256"]
            or old_metrics.get("validation_predictions_sha256") != frozen["v1_predictions_sha256"]
            or old_metrics.get("models_sha256") != frozen["v1_models_sha256"]
            or old_metrics.get("support_sha256") != frozen["v1_support_sha256"]
            or old_metrics.get("freeze_manifest_sha256") != frozen["v1_freeze_sha256"]
            or old_support.get("all_pass") is not True
            or old_metrics.get("test_target_read") is not False):
        raise ValueError("v1 artifacts do not form the approved preperiod chain")
    return frozen


def split_blocks() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fit = np.arange(FIT_START_H, CAL_START_H - 1, dtype=np.int64)
    cal = np.arange(CAL_START_H, VAL_START_H - 1, dtype=np.int64)
    val = np.arange(VAL_START_H, TEST_START_H, dtype=np.int64)
    if (len(fit) != EXPECTED_FIT_N or len(cal) != EXPECTED_CAL_N
            or len(val) != EXPECTED_VAL_N or fit[-1] + 2 > CAL_START_H
            or cal[-1] + 2 > VAL_START_H or CAL_START_H - 1 in fit):
        raise AssertionError("Frozen fit/calibration/validation boundaries changed")
    return fit, cal, val


def block_support(hourly: dict, freeze_sha256: str) -> dict:
    result = {"kind": "burstgpt_input_demand_v2_preperiod_block_support",
              "freeze_manifest_sha256": freeze_sha256,
              "source_sha256": v1.SOURCE_SHA256, "cohorts": {}, "all_pass": True}
    for cohort in v1.COHORTS:
        counts = hourly[cohort]["count"]
        cohort_support = {}
        for name, day_start, day_end, required_days in (
                ("fit", 225, 255, 25), ("calibration", 255, 265, 8)):
            left = (day_start - v1.TRAIN_START_DAY) * 24
            right = (day_end - v1.TRAIN_START_DAY) * 24
            active = counts[left:right] > 0
            days = day_end - day_start
            present_days = int(active.reshape(days, 24).any(axis=1).sum())
            active_hours = int(active.sum())
            passed = present_days >= required_days and active_hours / len(active) >= 0.70
            cohort_support[name] = {
                "relative_days": [day_start, day_end - 1],
                "hours": len(active), "days_with_logged_requests": present_days,
                "hours_with_logged_requests": active_hours,
                "hours_with_logged_requests_fraction": active_hours / len(active),
                "pass": passed,
            }
            result["all_pass"] = result["all_pass"] and passed
        result["cohorts"][cohort] = cohort_support
    return result


def calibration_delta(residuals: np.ndarray, *, expected_n: int = EXPECTED_CAL_N) -> tuple[float, int]:
    scores = np.asarray(residuals, dtype=float)
    if scores.ndim != 1 or len(scores) != expected_n or not np.isfinite(scores).all():
        raise ValueError("Calibration requires the frozen finite residual vector")
    rank = math.ceil((len(scores) + 1) * v1.Q)
    if rank < 1 or rank > len(scores):
        raise ValueError("Calibration rank is outside the residual vector")
    return max(0.0, float(np.partition(scores, rank - 1)[rank - 1])), rank


def _features(data: dict, hours: np.ndarray) -> np.ndarray:
    return np.stack([v1.feature_row(data["volume"], data["count"], h=int(h)) for h in hours])


def fit_and_calibrate(hourly: dict, fit_h: np.ndarray, cal_h: np.ndarray) -> tuple[dict, dict, dict]:
    models, deltas, diagnostics = {}, {}, {}
    for cohort in v1.COHORTS:
        data = hourly[cohort]
        y_fit = data["volume"][fit_h - v1.START_H].astype(float)
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=v1.Q, max_iter=80, max_leaf_nodes=7,
            learning_rate=0.05, min_samples_leaf=20, l2_regularization=1.0,
            random_state=42)
        model.fit(_features(data, fit_h), y_fit)
        q_cal = np.maximum(0, model.predict(_features(data, cal_h)))
        y_cal = data["volume"][cal_h - v1.START_H].astype(float)
        residuals = y_cal - q_cal
        delta, rank = calibration_delta(residuals)
        if rank != EXPECTED_CAL_RANK:
            raise AssertionError("Calibration rank differs from protocol")
        models[cohort], deltas[cohort] = model, delta
        diagnostics[cohort] = {"fit_hours": len(fit_h), "calibration_hours": len(cal_h),
                               "calibration_rank_1_based": rank,
                               "nonnegative_residual_shift": delta,
                               "calibration_raw_coverage": float(np.mean(y_cal <= q_cal)),
                               "calibration_shifted_coverage": float(np.mean(y_cal <= q_cal + delta))}
    return models, deltas, diagnostics


def read_frozen_v1_validation(hourly: dict, validation_h: np.ndarray) -> dict[tuple[str, int], dict]:
    expected_fields = ("cohort", "relative_hour", "relative_day", "recorded_request_count",
                       "recorded_input_tokens", *v1.BASELINES, "quantile_hgb")
    rows = {}
    with v1.PREDICTIONS.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != expected_fields:
            raise ValueError("Frozen v1 validation prediction schema differs")
        for row in reader:
            cohort, hour = row["cohort"], int(row["relative_hour"])
            key = (cohort, hour)
            if (cohort not in v1.COHORTS or hour not in range(VAL_START_H, TEST_START_H)
                    or key in rows or int(row["relative_day"]) != hour // 24):
                raise ValueError("Frozen v1 validation keys differ")
            ix = hour - v1.START_H
            data = hourly[cohort]
            if (int(row["recorded_request_count"]) != int(data["count"][ix])
                    or int(row["recorded_input_tokens"]) != int(data["volume"][ix])):
                raise ValueError("Frozen v1 validation target/count differs from source")
            predictions = {name: float(row[name]) for name in (*v1.BASELINES, "quantile_hgb")}
            if any(not math.isfinite(value) or value < 0 for value in predictions.values()):
                raise ValueError("Frozen v1 validation predictions are invalid")
            rows[key] = {"recorded_request_count": int(row["recorded_request_count"]),
                         "recorded_input_tokens": int(row["recorded_input_tokens"]),
                         **predictions}
    if (len(rows) != len(v1.COHORTS) * len(validation_h)
            or set(rows) != {(cohort, int(h)) for cohort in v1.COHORTS for h in validation_h}):
        raise ValueError("Frozen v1 validation rows are incomplete")
    return rows


def score_validation(hourly: dict, models: dict, deltas: dict,
                     validation_h: np.ndarray, v1_rows: dict,
                     v1_metrics: dict) -> tuple[dict, list[dict]]:
    names = (*v1.BASELINES, OLD_NAME, RAW_NAME, MODEL_NAME)
    by_cohort, by_day, prediction_rows = {}, {}, []
    for cohort in v1.COHORTS:
        data = hourly[cohort]
        y = data["volume"][validation_h - v1.START_H].astype(float)
        q_raw = np.maximum(0, models[cohort].predict(_features(data, validation_h)))
        q_cal = np.maximum(0, q_raw + deltas[cohort])
        if (not np.isfinite(q_raw).all() or not np.isfinite(q_cal).all()
                or np.any(q_raw < 0) or np.any(q_cal < 0)):
            raise ValueError("v2 validation predictions must be finite and nonnegative")
        predictions = {name: np.asarray([v1_rows[(cohort, int(h))][name]
                                         for h in validation_h], float)
                       for name in v1.BASELINES}
        predictions[OLD_NAME] = np.asarray([
            v1_rows[(cohort, int(h))]["quantile_hgb"] for h in validation_h], float)
        predictions[RAW_NAME], predictions[MODEL_NAME] = q_raw, q_cal
        for i, hour in enumerate(validation_h):
            source_row = v1_rows[(cohort, int(hour))]
            prediction_rows.append({
                "cohort": cohort, "relative_hour": int(hour),
                "relative_day": int(hour // 24),
                "recorded_request_count": source_row["recorded_request_count"],
                "recorded_input_tokens": source_row["recorded_input_tokens"],
                **{name: float(predictions[name][i]) for name in names},
            })
        by_cohort[cohort] = {name: {"pinball": v1.pinball(y, predictions[name]),
                                   "coverage": float(np.mean(y <= predictions[name])),
                                   "mean_prediction": float(np.mean(predictions[name]))}
                             for name in names}
        by_day[cohort] = {str(day): {name: v1.pinball(
            y[validation_h // 24 == day], predictions[name][validation_h // 24 == day])
            for name in names} for day in range(v1.VAL_START_DAY, v1.TEST_START_DAY)}
    overall = {name: float(np.mean([by_cohort[c][name]["pinball"] for c in v1.COHORTS]))
               for name in names}
    best = min(overall[name] for name in v1.BASELINES)
    selected = next(name for name in v1.BASELINES if overall[name] <= best + 1e-12)
    if selected != v1_metrics.get("selected_baseline"):
        raise ValueError("Frozen v1 selected baseline differs from v2 recomputation")
    for cohort in v1.COHORTS:
        for name in (*v1.BASELINES, OLD_NAME):
            prior_name = "quantile_hgb" if name == OLD_NAME else name
            prior = v1_metrics["by_cohort"][cohort][prior_name]
            for metric in ("pinball", "coverage", "mean_prediction"):
                if abs(by_cohort[cohort][name][metric] - prior[metric]) > 1e-8:
                    raise ValueError("Frozen v1 validation metric differs from recomputation")
    daily_wins = int(sum(np.mean([by_day[c][str(day)][MODEL_NAME] for c in v1.COHORTS])
                         < np.mean([by_day[c][str(day)][selected] for c in v1.COHORTS])
                         for day in range(v1.VAL_START_DAY, v1.TEST_START_DAY)))
    gates = {
        "relative_pinball_improvement_at_least_5pct": overall[MODEL_NAME] <= 0.95 * overall[selected],
        "both_cohorts_lower_pinball": all(by_cohort[c][MODEL_NAME]["pinball"]
                                          < by_cohort[c][selected]["pinball"] for c in v1.COHORTS),
        "both_cohorts_coverage_at_least_85pct": all(by_cohort[c][MODEL_NAME]["coverage"]
                                                    >= 0.85 for c in v1.COHORTS),
        "wins_at_least_12_of_20_days": daily_wins >= 12,
    }
    gates["all_pass"] = all(gates.values())
    tradeoff = {cohort: {
        "pinball_change_vs_v1_40d": by_cohort[cohort][MODEL_NAME]["pinball"] - by_cohort[cohort][OLD_NAME]["pinball"],
        "coverage_change_vs_v1_40d": by_cohort[cohort][MODEL_NAME]["coverage"] - by_cohort[cohort][OLD_NAME]["coverage"],
        "mean_prediction_change_vs_v1_40d": by_cohort[cohort][MODEL_NAME]["mean_prediction"] - by_cohort[cohort][OLD_NAME]["mean_prediction"],
        "pinball_change_due_to_calibration": by_cohort[cohort][MODEL_NAME]["pinball"] - by_cohort[cohort][RAW_NAME]["pinball"],
        "coverage_change_due_to_calibration": by_cohort[cohort][MODEL_NAME]["coverage"] - by_cohort[cohort][RAW_NAME]["coverage"],
    } for cohort in v1.COHORTS}
    return ({"selected_baseline": selected, "overall_equal_cohort_pinball": overall,
             "by_cohort": by_cohort, "daily_pinball": by_day,
             "daily_wins_against_selected_baseline": daily_wins,
             "tradeoff": tradeoff, "adoption_gates": gates}, prediction_rows)


def select(expected_freeze_sha256: str) -> dict:
    _ensure_new_outputs((SUPPORT, METRICS, MODELS, PREDICTIONS))
    frozen = verify_freeze(expected_freeze_sha256)
    hourly, source_info = v1.load_preperiod()
    support = block_support(hourly, expected_freeze_sha256)
    support.update(source_info)
    if source_info != {"preperiod_source_rows": 3_452_522,
                       "stopped_before_test_target": True}:
        raise ValueError("Original preperiod boundary or row count differs")
    OUT.mkdir(parents=True, exist_ok=True)
    with SUPPORT.open("x", encoding="utf-8") as stream:
        json.dump(support, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    if not support["all_pass"]:
        raise ValueError("Blocked fit/calibration support failed; do not fit or score")
    fit_h, cal_h, val_h = split_blocks()
    models, deltas, diagnostics = fit_and_calibrate(hourly, fit_h, cal_h)
    old_metrics = json.loads(v1.METRICS.read_text(encoding="utf-8"))
    old_rows = read_frozen_v1_validation(hourly, val_h)
    scores, prediction_rows = score_validation(
        hourly, models, deltas, val_h, old_rows, old_metrics)
    with tempfile.TemporaryDirectory(prefix=".burstgpt_v2_", dir=OUT) as name:
        temp = Path(name)
        model_tmp, prediction_tmp, metric_tmp = (temp / path.name for path in (MODELS, PREDICTIONS, METRICS))
        joblib.dump({"models": models, "calibration_deltas": deltas,
                     "fit_hours": [int(fit_h.min()), int(fit_h.max())],
                     "calibration_hours": [int(cal_h.min()), int(cal_h.max())],
                     "feature_names": v1.FEATURE_NAMES,
                     "source_sha256": v1.SOURCE_SHA256,
                     "freeze_manifest_sha256": expected_freeze_sha256}, model_tmp)
        with prediction_tmp.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=PREDICTION_COLUMNS)
            writer.writeheader()
            writer.writerows(prediction_rows)
        result = {
            "kind": "burstgpt_input_demand_v2_train_only_block_calibration_validation",
            "exploratory_validation_after_v1_exposure": True,
            "test_target_read": False,
            "source_sha256": v1.SOURCE_SHA256,
            "freeze_manifest_sha256": expected_freeze_sha256,
            "protocol_sha256": frozen["protocol_sha256"],
            "script_sha256": frozen["script_sha256"],
            "support_sha256": v1.sha256_file(SUPPORT),
            "models_sha256": v1.sha256_file(model_tmp),
            "validation_predictions_sha256": v1.sha256_file(prediction_tmp),
            "v1_validation_predictions_sha256": frozen["v1_predictions_sha256"],
            "fit_hours_per_cohort": len(fit_h),
            "calibration_hours_per_cohort": len(cal_h),
            "validation_hours_per_cohort": len(val_h),
            "calibration": diagnostics,
            **scores,
        }
        with metric_tmp.open("w", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
        _ensure_new_outputs((MODELS, PREDICTIONS, METRICS))
        model_tmp.replace(MODELS)
        prediction_tmp.replace(PREDICTIONS)
        metric_tmp.replace(METRICS)
    return {"metrics": str(METRICS), "selected_baseline": result["selected_baseline"],
            "adoption_gates": result["adoption_gates"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "select"))
    parser.add_argument("--expected-freeze-sha256")
    args = parser.parse_args()
    if args.stage == "freeze":
        if args.expected_freeze_sha256 is not None:
            parser.error("freeze does not take --expected-freeze-sha256")
        frozen = freeze()
        print(json.dumps({"freeze": str(FREEZE), "status": frozen["status"]}, sort_keys=True))
    else:
        if not args.expected_freeze_sha256:
            parser.error("select requires reviewed --expected-freeze-sha256")
        print(json.dumps(select(args.expected_freeze_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
