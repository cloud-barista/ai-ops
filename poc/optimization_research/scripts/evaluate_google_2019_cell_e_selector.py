"""Pre-frozen two-expert selection on a held-out Borg cell-e arrival series.

The first three complete days select between recent-mean and the previously
trained seasonal residual expert. All scored bins follow that selection period.
Neither expert nor its selection rule is fit on the cell-e test interval.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import prepare_google_2019_cell_e as source


OUTPUT = ROOT / "artifacts/google_2019_cell_e_selector_v1"
FREEZE = OUTPUT / "freeze.json"
SERIES = OUTPUT / "series.json"
RESULTS = OUTPUT / "results.json"
PREDICTIONS = OUTPUT / "predictions.csv"
PROTOCOL = ROOT / "reports/google_2019_cell_e_selector_protocol_v1.md"
A_SERIES = ROOT / "artifacts/google_2019_cell_a_sentinel_v2/series.json"
B_SERIES = ROOT / "artifacts/google_2019_cell_b_adaptive_v1/series.json"
G_SERIES = ROOT / "artifacts/google_2019_cell_g_seasonal_v1/series.json"
G_FREEZE = ROOT / "artifacts/google_2019_cell_g_seasonal_v1/freeze.json"
G_RESULTS = ROOT / "artifacts/google_2019_cell_g_seasonal_v1/results.json"
DEV_SCRIPT = ROOT / "scripts/analyze_google_2019_burnin_gates.py"
DEV_RESULTS = ROOT / "artifacts/google_2019_burnin_gate_development_v1/results.json"
A_VERIFIED = ROOT / "data/external/google_cluster_2019_a/verified_collection_events_manifest.json"
B_VERIFIED = ROOT / "data/external/google_cluster_2019_b/verified_collection_events_manifest.json"
G_VERIFIED = ROOT / "data/external/google_cluster_2019_g/verified_collection_events_manifest.json"
EXPECTED_G_FREEZE_SHA = "5294f5f58cb0d9e363bc0b75bd52cee40abcf9a1871e36ef649243f4af1522e2"
EXPECTED_G_RESULTS_SHA = "0900e675bfb87e6ed200bcecdc1dee83b1b0b4687d92df28994a40b98a7b11e0"
EXPECTED_DEV_SCRIPT_SHA = "17382833832b1fe633f1c00619562a38524523330049b06bbc43d19eff3538fa"
EXPECTED_DEV_RESULTS_SHA = "b28c19e7788d95cc28f87c0a79843272d137839e03291712e84d534d8c1d36aa"
BURN_IN = 300
SELECT_END = 864
MIN_FULL_BINS = 1728
SELECT_MARGIN = .95
METHODS = ("persistence", "mean_12", "daily_lag", "seasonal_residual", "selected")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def validated_counts(series: dict) -> np.ndarray:
    counts = series.get("counts")
    first = series.get("first_full_bin")
    last = series.get("last_full_bin")
    if (not isinstance(counts, list) or len(counts) < MIN_FULL_BINS
            or type(first) is not int or type(last) is not int or first < 1
            or last - first + 1 != len(counts)
            or any(type(value) is not int or value < 0 for value in counts)):
        raise ValueError("incomplete or malformed complete-bin series")
    return np.asarray(counts, dtype=float)


def verify_development_sources() -> float:
    if sha256(G_FREEZE) != EXPECTED_G_FREEZE_SHA:
        raise ValueError("previously trained seasonal coefficient freeze changed")
    if (sha256(G_RESULTS) != EXPECTED_G_RESULTS_SHA or
            sha256(DEV_SCRIPT) != EXPECTED_DEV_SCRIPT_SHA or
            sha256(DEV_RESULTS) != EXPECTED_DEV_RESULTS_SHA):
        raise ValueError("exposed development evidence changed")
    previous = json.loads(G_FREEZE.read_text())
    if previous.get("alpha") != .85:
        raise ValueError("previously trained seasonal coefficient differs")
    if previous.get("cell_a_clean_series_sha256") != sha256(A_SERIES) or \
            previous.get("cell_b_series_sha256") != sha256(B_SERIES):
        raise ValueError("previous cell-a/b training inputs changed")
    results = json.loads(G_RESULTS.read_text())
    if results.get("freeze_sha256") != EXPECTED_G_FREEZE_SHA or \
            results.get("series_sha256") != sha256(G_SERIES):
        raise ValueError("exposed cell-g result and series differ")
    for path, verified, version in ((A_SERIES, A_VERIFIED, 2),
                                    (B_SERIES, B_VERIFIED, 1),
                                    (G_SERIES, G_VERIFIED, 1)):
        series = json.loads(path.read_text())
        if series.get("version") != version or \
                series.get("source_manifest_sha256") != sha256(verified) or \
                series.get("audit", {}).get("non_timed_pretrace_rows", 0) <= 0:
            raise ValueError(f"development series provenance differs: {path.name}")
        validated_counts(series)
    return float(previous["alpha"])


def forecast_arrays(series: dict, alpha: float) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    if not math.isfinite(alpha) or alpha != .85:
        raise ValueError("seasonal coefficient differs from frozen development model")
    y = validated_counts(series)
    offsets = np.arange(BURN_IN, len(y), dtype=int)
    recent = np.asarray([float(np.mean(y[t - 12:t])) for t in offsets])
    delta = np.asarray([float(np.mean(y[t - 2:t] - y[t - 290:t - 288]))
                        for t in offsets])
    predictions = {
        "persistence": y[offsets - 1],
        "mean_12": recent,
        "daily_lag": y[offsets - 288],
        "seasonal_residual": np.maximum(0.0, y[offsets - 288] + alpha * delta),
    }
    return y, offsets, predictions


def select_expert(y: np.ndarray, offsets: np.ndarray,
                  predictions: dict[str, np.ndarray]) -> tuple[str, dict]:
    calibration = offsets < SELECT_END
    if int(np.sum(calibration)) != SELECT_END - BURN_IN:
        raise ValueError("initial selection interval is incomplete")
    actual = y[offsets[calibration]]
    recent_mae = float(np.mean(np.abs(actual - predictions["mean_12"][calibration])))
    seasonal_mae = float(np.mean(np.abs(actual - predictions["seasonal_residual"][calibration])))
    choice = ("seasonal_residual" if seasonal_mae < recent_mae
              and seasonal_mae <= SELECT_MARGIN * recent_mae else "mean_12")
    return choice, {"start_offset": BURN_IN, "end_offset_exclusive": SELECT_END,
                    "bins": int(np.sum(calibration)), "mean_12_mae": recent_mae,
                    "seasonal_residual_mae": seasonal_mae,
                    "seasonal_selection_margin": SELECT_MARGIN,
                    "selected_expert": choice}


def freeze_payload() -> dict:
    _objects, metadata_sha = source.metadata_preflight()
    alpha = verify_development_sources()
    return {
        "source_metadata_sha256": metadata_sha,
        "source_schema_sha256": sha256(source.SCHEMA),
        "prepare_script_sha256": sha256(Path(source.__file__)),
        "script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(PROTOCOL),
        "cell_a_clean_series_sha256": sha256(A_SERIES),
        "cell_b_series_sha256": sha256(B_SERIES),
        "cell_g_series_sha256": sha256(G_SERIES),
        "cell_a_verified_manifest_sha256": sha256(A_VERIFIED),
        "cell_b_verified_manifest_sha256": sha256(B_VERIFIED),
        "cell_g_verified_manifest_sha256": sha256(G_VERIFIED),
        "cell_g_coefficient_freeze_sha256": sha256(G_FREEZE),
        "cell_g_exposed_results_sha256": sha256(G_RESULTS),
        "exposed_selector_development_script_sha256": sha256(DEV_SCRIPT),
        "exposed_selector_development_results_sha256": sha256(DEV_RESULTS),
        "numpy_version": np.__version__,
        "alpha": alpha,
        "burn_in_bins": BURN_IN,
        "selection_end_offset_exclusive": SELECT_END,
        "seasonal_selection_margin": SELECT_MARGIN,
        "expected_e_objects": 1,
    }


def freeze() -> None:
    payload = freeze_payload()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if FREEZE.exists():
        if json.loads(FREEZE.read_text()) != payload:
            raise ValueError("existing cell-e freeze differs from current inputs")
        print(f"Verified existing freeze {FREEZE}, sha256={sha256(FREEZE)}")
        return
    if source.VERIFIED.exists() or any(source.SOURCE.glob("collection_events-*.json.gz")):
        raise ValueError("cell-e event content exists before pre-content freeze")
    write_json(FREEZE, payload)
    print(f"Wrote pre-content freeze {FREEZE}, sha256={sha256(FREEZE)}, alpha={payload['alpha']}")


def verify_freeze() -> dict:
    if not FREEZE.exists():
        raise ValueError("cell-e pre-content freeze missing")
    frozen = json.loads(FREEZE.read_text())
    if frozen != freeze_payload():
        raise ValueError("cell-e freeze differs from current data, code, or protocol")
    return frozen


def metrics(actual: np.ndarray, prediction: np.ndarray, threshold: float) -> dict:
    absolute = np.abs(actual - prediction)
    positive = actual >= threshold
    alert = prediction >= threshold
    total = float(np.sum(actual))
    return {
        "bins": int(actual.size),
        "mae": float(np.mean(absolute)),
        "rmse": float(np.sqrt(np.mean((actual - prediction) ** 2))),
        "wape": float(np.sum(absolute) / total) if total > 0 else None,
        "p90_absolute_error": float(np.quantile(absolute, .9, method="higher")),
        "high_load_true_bins": int(np.sum(positive)),
        "high_load_alert_bins": int(np.sum(alert)),
        "high_load_alert_rate": float(np.mean(alert)),
        "high_load_recall": float(np.mean(alert[positive])) if np.any(positive) else None,
        "high_load_precision": float(np.mean(positive[alert])) if np.any(alert) else None,
        "mean_prediction": float(np.mean(prediction)),
    }


def score_series(series: dict, alpha: float) -> tuple[dict, list[dict]]:
    y, offsets, predictions = forecast_arrays(series, alpha)
    choice, selection = select_expert(y, offsets, predictions)
    test = offsets >= SELECT_END
    target_offsets = offsets[test]
    actual = y[target_offsets]
    predictions["selected"] = predictions[choice]
    if any(not np.all(np.isfinite(predictions[name])) for name in METHODS):
        raise ValueError("nonfinite forecast")
    test_predictions = {name: predictions[name][test] for name in METHODS}
    threshold = float(np.quantile(y[:BURN_IN], .9, method="higher"))
    scored = {name: metrics(actual, test_predictions[name], threshold) for name in METHODS}
    best_name = min(("mean_12", "seasonal_residual"),
                    key=lambda name: (scored[name]["mae"], name != "mean_12"))
    best = scored[best_name]
    main = scored["selected"]
    gate = {
        "positive_high_load_threshold": threshold > 0,
        "at_least_100_high_load_test_bins": best["high_load_true_bins"] >= 100,
        "mae_not_worse_than_recent_mean": main["mae"] <= scored["mean_12"]["mae"],
        "mae_within_2pct_of_best_expert": main["mae"] <= 1.02 * best["mae"],
        "p90_within_5pct_of_best_expert": main["p90_absolute_error"] <=
        1.05 * best["p90_absolute_error"],
        "high_load_recall_within_1pp": (
            main["high_load_recall"] is not None and best["high_load_recall"] is not None
            and main["high_load_recall"] >= best["high_load_recall"] - .01),
        "high_load_recall_at_least_20pct": (
            main["high_load_recall"] is not None and main["high_load_recall"] >= .20),
        "high_load_alert_rate_within_5pp": (
            main["high_load_alert_rate"] <= best["high_load_alert_rate"] + .05),
    }
    rows = [{"bin_id": int(series["first_full_bin"] + t),
             "trace_start_us": int((series["first_full_bin"] + t) * source.FIVE_MIN_US),
             "actual": int(actual[index]),
             **{name: float(test_predictions[name][index]) for name in METHODS}}
            for index, t in enumerate(target_offsets)]
    days = []
    for day in sorted({row["bin_id"] // 288 for row in rows}):
        subset = [row for row in rows if row["bin_id"] // 288 == day]
        days.append({"trace_day_index": day, "bins": len(subset),
                     "selected_mae": float(np.mean([abs(row["actual"] - row["selected"])
                                                    for row in subset])),
                     "mean_12_mae": float(np.mean([abs(row["actual"] - row["mean_12"])
                                                   for row in subset])),
                     "seasonal_residual_mae": float(np.mean([
                         abs(row["actual"] - row["seasonal_residual"]) for row in subset]))})
    return {
        "scope": "unseen Borg cell-e JOB arrival pressure, no AI-App action or user SLO labels",
        "target": series["target"],
        "source_audit": series["audit"],
        "full_bins": len(y),
        "burn_in_bins": BURN_IN,
        "selection": selection,
        "test_bins": len(rows),
        "alpha_trained_on_exposed_cell_a_b": alpha,
        "best_fixed_expert_in_hindsight": best_name,
        "high_load_threshold_burn_in_p90": threshold,
        "metrics": scored,
        "daily": days,
        "research_gate": {**gate, "passed": all(gate.values())},
    }, rows


def evaluate() -> None:
    frozen = verify_freeze()
    paths, verified_sha = source.verify_inputs()
    if len(paths) != 1:
        raise ValueError("cell-e requires exactly one verified event object")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_e_arrival_", dir=OUTPUT) as directory:
        series = source.extract_event_rows(paths, Path(directory))
    series["source_manifest_sha256"] = verified_sha
    result, rows = score_series(series, frozen["alpha"])
    write_json(SERIES, series)
    result["freeze_sha256"] = sha256(FREEZE)
    result["verified_source_manifest_sha256"] = verified_sha
    result["series_sha256"] = sha256(SERIES)
    write_json(RESULTS, result)
    with PREDICTIONS.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("bin_id", "trace_start_us", "actual", *METHODS))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"test_bins": len(rows), "selected_expert": result["selection"]["selected_expert"],
                      "best_fixed_expert": result["best_fixed_expert_in_hindsight"],
                      "gate": result["research_gate"],
                      "mae": {name: result["metrics"][name]["mae"] for name in METHODS},
                      "source_audit": series["audit"]}, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "evaluate"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    else:
        evaluate()


if __name__ == "__main__":
    main()
