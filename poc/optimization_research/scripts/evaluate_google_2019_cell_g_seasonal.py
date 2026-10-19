"""Freeze and test a cell-balanced seasonal residual forecast on unseen cell-g.

Cell-a/cell-b are exposed development cells. This script refuses to create a
new freeze after any cell-g event object or verified manifest already exists.
The one trained coefficient and research gate must be fixed before download.
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
from scripts import prepare_google_2019_cell_g as source


OUTPUT = ROOT / "artifacts/google_2019_cell_g_seasonal_v1"
FREEZE = OUTPUT / "freeze.json"
SERIES = OUTPUT / "series.json"
RESULTS = OUTPUT / "results.json"
PREDICTIONS = OUTPUT / "predictions.csv"
PROTOCOL = ROOT / "reports/google_2019_cell_g_seasonal_protocol_v1.md"
A_SERIES = ROOT / "artifacts/google_2019_cell_a_sentinel_v2/series.json"
B_SERIES = ROOT / "artifacts/google_2019_cell_b_adaptive_v1/series.json"
A_VERIFIED = ROOT / "data/external/google_cluster_2019_a/verified_collection_events_manifest.json"
B_VERIFIED = ROOT / "data/external/google_cluster_2019_b/verified_collection_events_manifest.json"
B_FREEZE = ROOT / "artifacts/google_2019_cell_b_adaptive_v1/freeze.json"
WINDOW = 2
BURN_IN = 300
MIN_FULL_BINS = 1164
ALPHAS = tuple(index / 20 for index in range(21))
METHODS = ("persistence", "mean_12", "daily_lag", "seasonal_residual")


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
            or type(first) is not int or type(last) is not int
            or first < 1 or last - first + 1 != len(counts)
            or any(type(value) is not int or value < 0 for value in counts)):
        raise ValueError("incomplete or malformed complete-bin series")
    return np.asarray(counts, dtype=float)


def design(series: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return target, daily lag, recent mean, and previous-day delta using past only."""
    y = validated_counts(series)
    offsets = np.arange(BURN_IN, len(y), dtype=int)
    target = y[offsets]
    daily = y[offsets - 288]
    recent = np.asarray([float(np.mean(y[t - 12:t])) for t in offsets])
    residual = np.asarray([
        float(np.mean(y[t - WINDOW:t] - y[t - 288 - WINDOW:t - 288]))
        for t in offsets
    ])
    return target, daily, recent, residual


def verify_development_sources() -> tuple[dict, dict]:
    if sha256(B_FREEZE) != "5b98c60c2bc2b414404c5cd71de4be8897895017c550c48e4421320f4ce19618":
        raise ValueError("cell-b frozen source changed")
    a = json.loads(A_SERIES.read_text())
    b = json.loads(B_SERIES.read_text())
    if a.get("version") != 2 or b.get("version") != 1:
        raise ValueError("development series versions changed")
    if a.get("source_manifest_sha256") != sha256(A_VERIFIED):
        raise ValueError("clean cell-a source manifest mismatch")
    if b.get("source_manifest_sha256") != sha256(B_VERIFIED):
        raise ValueError("cell-b source manifest mismatch")
    for series in (a, b):
        validated_counts(series)
    if a.get("audit", {}).get("non_timed_pretrace_rows", 0) <= 0:
        raise ValueError("clean cell-a does not document pretrace exclusion")
    if b.get("audit", {}).get("non_timed_pretrace_rows", 0) <= 0:
        raise ValueError("cell-b does not document pretrace exclusion")
    return a, b


def train_alpha(a: dict, b: dict) -> tuple[float, dict]:
    """One coefficient minimizes mean of cell-normalized MAE ratios."""
    examples = []
    for label, series in (("cell_a_clean", a), ("cell_b", b)):
        actual, daily, _recent, residual = design(series)
        daily_mae = float(np.mean(np.abs(actual - daily)))
        if daily_mae <= 0:
            raise ValueError(f"{label} daily-lag MAE is zero")
        examples.append((label, actual, daily, residual, daily_mae))
    rows = []
    for alpha in ALPHAS:
        cell = {}
        for label, actual, daily, residual, daily_mae in examples:
            prediction = np.maximum(0.0, daily + alpha * residual)
            cell[label] = float(np.mean(np.abs(actual - prediction)) / daily_mae)
        rows.append({"alpha": alpha, "cell_mae_ratio": cell,
                     "equal_cell_mean_mae_ratio": float(np.mean(list(cell.values())))})
    best = min(rows, key=lambda row: (row["equal_cell_mean_mae_ratio"], row["alpha"]))
    return best["alpha"], {"window_bins": WINDOW, "burn_in_bins": BURN_IN,
                           "alpha_grid": list(ALPHAS), "grid_scores": rows,
                           "selected": best}


def freeze_payload() -> dict:
    _entries, metadata_sha = source.metadata_preflight()
    a, b = verify_development_sources()
    alpha, learning = train_alpha(a, b)
    return {
        "source_metadata_sha256": metadata_sha,
        "source_schema_sha256": sha256(source.SCHEMA),
        "prepare_script_sha256": sha256(Path(source.__file__)),
        "script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(PROTOCOL),
        "cell_a_clean_series_sha256": sha256(A_SERIES),
        "cell_b_series_sha256": sha256(B_SERIES),
        "cell_a_verified_manifest_sha256": sha256(A_VERIFIED),
        "cell_b_verified_manifest_sha256": sha256(B_VERIFIED),
        "cell_b_freeze_sha256": sha256(B_FREEZE),
        "numpy_version": np.__version__,
        "alpha": alpha,
        "learning": learning,
        "expected_g_objects": 1,
    }


def freeze() -> None:
    payload = freeze_payload()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if FREEZE.exists():
        if json.loads(FREEZE.read_text()) != payload:
            raise ValueError("existing cell-g freeze differs from current inputs")
        print(f"Verified existing freeze {FREEZE}, sha256={sha256(FREEZE)}")
        return
    if source.VERIFIED.exists() or any(source.SOURCE.glob("collection_events-*.json.gz")):
        raise ValueError("cell-g event content exists before pre-content freeze")
    write_json(FREEZE, payload)
    print(f"Wrote pre-content freeze {FREEZE}, sha256={sha256(FREEZE)}, alpha={payload['alpha']}")


def verify_freeze() -> dict:
    if not FREEZE.exists():
        raise ValueError("cell-g pre-content freeze missing")
    frozen = json.loads(FREEZE.read_text())
    if frozen != freeze_payload():
        raise ValueError("cell-g freeze differs from current data, code, or protocol")
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
    if alpha not in ALPHAS or not math.isfinite(alpha):
        raise ValueError("alpha is outside frozen training grid")
    actual, daily, recent, residual = design(series)
    y = validated_counts(series)
    offsets = np.arange(BURN_IN, len(y), dtype=int)
    predictions = {
        "persistence": y[offsets - 1],
        "mean_12": recent,
        "daily_lag": daily,
        "seasonal_residual": np.maximum(0.0, daily + alpha * residual),
    }
    if any(not np.all(np.isfinite(predictions[name])) for name in METHODS):
        raise ValueError("nonfinite forecast")
    threshold = float(np.quantile(y[:BURN_IN], .9, method="higher"))
    scored = {name: metrics(actual, predictions[name], threshold) for name in METHODS}
    main, baseline = scored["seasonal_residual"], scored["daily_lag"]
    positive = scored["daily_lag"]["high_load_true_bins"]
    gate = {
        "positive_high_load_threshold": threshold > 0,
        "at_least_100_high_load_test_bins": positive >= 100,
        "mae_at_least_5pct_better_than_daily_lag": main["mae"] <= .95 * baseline["mae"],
        "p90_absolute_error_not_worse": main["p90_absolute_error"] <= baseline["p90_absolute_error"],
        "high_load_recall_within_1pp": (
            main["high_load_recall"] is not None and baseline["high_load_recall"] is not None
            and main["high_load_recall"] >= baseline["high_load_recall"] - .01),
        "high_load_alert_rate_within_5pp": (
            main["high_load_alert_rate"] <= baseline["high_load_alert_rate"] + .05),
    }
    rows = [{"bin_id": int(series["first_full_bin"] + t),
             "trace_start_us": int((series["first_full_bin"] + t) * source.FIVE_MIN_US),
             "actual": int(actual[index]),
             **{name: float(predictions[name][index]) for name in METHODS}}
            for index, t in enumerate(offsets)]
    days = []
    for day in sorted({row["bin_id"] // 288 for row in rows}):
        subset = [row for row in rows if row["bin_id"] // 288 == day]
        days.append({"trace_day_index": day, "bins": len(subset),
                     "daily_lag_mae": float(np.mean([abs(row["actual"] - row["daily_lag"])
                                                      for row in subset])),
                     "seasonal_residual_mae": float(np.mean([
                         abs(row["actual"] - row["seasonal_residual"]) for row in subset]))})
    return {
        "scope": "unseen Borg cell-g JOB arrival pressure, no AI-App action or user SLO labels",
        "target": series["target"],
        "source_audit": series["audit"],
        "full_bins": len(y),
        "burn_in_bins": BURN_IN,
        "test_bins": len(rows),
        "alpha_trained_on_exposed_cells": alpha,
        "high_load_threshold_burn_in_p90": threshold,
        "metrics": scored,
        "daily": days,
        "research_gate": {**gate, "passed": all(gate.values())},
    }, rows


def evaluate() -> None:
    frozen = verify_freeze()
    paths, verified_sha = source.verify_inputs()
    if len(paths) != 1:
        raise ValueError("cell-g requires exactly one verified event object")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_g_arrival_", dir=OUTPUT) as directory:
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
    print(json.dumps({"test_bins": len(rows), "alpha": frozen["alpha"],
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
