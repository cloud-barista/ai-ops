"""Exploratory causal expert selection on three *already exposed* Borg cells.

No parameter is trained here. The seasonal residual coefficient (0.85) was
frozen in the cell-g study. This follow-up is development analysis and cannot
be reported as a new independent cell-g test or as an AI-App scaling policy.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/google_2019_burnin_gate_development_v1/results.json"
SERIES = {
    "a": (ROOT / "artifacts/google_2019_cell_a_sentinel_v2/series.json",
          "7678e205e07891ab7698ff7315c29eda7d9eb2c99fc52e84af54d87acccb9f12"),
    "b": (ROOT / "artifacts/google_2019_cell_b_adaptive_v1/series.json",
          "8dcdf90d5487e1eafd1ea4f5f0b6d1ca2002d83da069ce6553e98dbd07a9c439"),
    "g": (ROOT / "artifacts/google_2019_cell_g_seasonal_v1/series.json",
          "b3b10498a1a0d96b7012ce2e7414a048943d55f78706b312aecf543e140e7d18"),
}
G_PREDICTIONS = ROOT / "artifacts/google_2019_cell_g_seasonal_v1/predictions.csv"
G_PREDICTIONS_SHA256 = "6fe69c9a6a551af04f241f0d40382b412928dfc3839cd01ade62c833c57d1fa9"
ALPHA = .85
START = 300
FIRST_CUTOFF = 3 * 288
COMMON_CUTOFF = 7 * 288
ROLLING_WINDOW = 2 * 288
EXPERTS = ("mean12", "seasonal", "daily")  # stable conservative tie order


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_series(path: Path, expected_sha: str) -> tuple[dict, np.ndarray]:
    if sha256(path) != expected_sha:
        raise ValueError(f"exposed series checksum changed: {path.name}")
    series = json.loads(path.read_text())
    raw = series.get("counts")
    if (not isinstance(raw, list) or len(raw) <= COMMON_CUTOFF
            or any(type(value) is not int or value < 0 for value in raw)
            or series.get("last_full_bin") - series.get("first_full_bin") + 1 != len(raw)):
        raise ValueError("malformed complete-bin series")
    return series, np.asarray(raw, dtype=float)


def forecasts(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Return (offsets, actual, forecasts), with each forecast using counts[:t]."""
    offsets = np.arange(START, len(counts), dtype=int)
    actual = counts[offsets]
    mean12 = np.asarray([float(np.mean(counts[t - 12:t])) for t in offsets])
    daily = counts[offsets - 288]
    residual = np.asarray([
        float(np.mean(counts[t - 2:t] - counts[t - 290:t - 288]))
        for t in offsets
    ])
    seasonal = np.maximum(0.0, daily + ALPHA * residual)
    return offsets, actual, {"mean12": mean12, "seasonal": seasonal, "daily": daily}


def metrics(actual: np.ndarray, predicted: np.ndarray, threshold: float) -> dict:
    if len(actual) == 0 or len(actual) != len(predicted):
        raise ValueError("empty or mismatched evaluation slice")
    absolute = np.abs(actual - predicted)
    positive = actual >= threshold
    alert = predicted >= threshold
    return {
        "bins": int(len(actual)),
        "mae": float(np.mean(absolute)),
        "p90_absolute_error": float(np.quantile(absolute, .9, method="higher")),
        "high_load_true_bins": int(positive.sum()),
        "high_load_alert_bins": int(alert.sum()),
        "high_load_true_positive_bins": int(np.sum(positive & alert)),
        "high_load_recall": float(np.mean(alert[positive])) if positive.any() else None,
        "high_load_alert_rate": float(np.mean(alert)),
    }


def select_fixed(offsets: np.ndarray, actual: np.ndarray,
                 predicted: dict[str, np.ndarray], cutoff: int) -> dict:
    """Choose by prior MAE, requiring seasonal to win by at least 5%."""
    prior = offsets < cutoff
    if not prior.any() or offsets[0] >= cutoff:
        raise ValueError("no past forecast errors for fixed selector")
    mean_mae = float(np.mean(np.abs(actual[prior] - predicted["mean12"][prior])))
    seasonal_mae = float(np.mean(np.abs(actual[prior] - predicted["seasonal"][prior])))
    choice = ("seasonal" if seasonal_mae < mean_mae
              and seasonal_mae <= .95 * mean_mae else "mean12")
    plain_choice = "seasonal" if seasonal_mae < mean_mae else "mean12"
    return {"selection_cutoff_offset": cutoff,
            "selection_offsets": [int(offsets[prior][0]), int(offsets[prior][-1])],
            "selection_bins": int(prior.sum()),
            "selection_mae": {"mean12": mean_mae, "seasonal": seasonal_mae},
            "chosen": choice,
            "plain_min_mae_chosen": plain_choice}


def rolling_choices(offsets: np.ndarray, actual: np.ndarray,
                    predicted: dict[str, np.ndarray], three_way: bool) -> tuple[list[str], list[int]]:
    """At each t, select from strictly earlier observed forecast errors."""
    errors = {key: np.abs(actual - value) for key, value in predicted.items()}
    cumulative = {key: np.r_[0.0, np.cumsum(value)] for key, value in errors.items()}
    choices = []
    changes = []
    for offset in range(FIRST_CUTOFF, len(actual) + START):
        index = offset - START
        first_index = max(0, index - ROLLING_WINDOW)
        size = index - first_index
        if size <= 0:
            raise AssertionError("rolling selector reached future or empty history")
        prior_mae = {key: float((values[index] - values[first_index]) / size)
                     for key, values in cumulative.items()}
        if three_way:
            chosen = min(EXPERTS, key=lambda key: prior_mae[key])
        else:
            chosen = ("seasonal" if prior_mae["seasonal"] < prior_mae["mean12"]
                      and prior_mae["seasonal"] <= .95 * prior_mae["mean12"]
                      else "mean12")
        if choices and chosen != choices[-1]:
            changes.append(offset)
        choices.append(chosen)
    return choices, changes


def selected_prediction(offsets: np.ndarray, predicted: dict[str, np.ndarray],
                        choices: list[str]) -> np.ndarray:
    future = offsets >= FIRST_CUTOFF
    indices = np.flatnonzero(future)
    if len(indices) != len(choices):
        raise ValueError("selector choice count differs from evaluation period")
    return np.asarray([predicted[chosen][index]
                       for index, chosen in zip(indices, choices, strict=True)], dtype=float)


def check_g_frozen_predictions(series: dict, offsets: np.ndarray, actual: np.ndarray,
                               predicted: dict[str, np.ndarray]) -> None:
    """Check that the follow-up reproduces every published cell-g input forecast."""
    if sha256(G_PREDICTIONS) != G_PREDICTIONS_SHA256:
        raise ValueError("cell-g frozen prediction CSV changed")
    with G_PREDICTIONS.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(offsets):
        raise ValueError("cell-g frozen prediction count changed")
    for index, row in enumerate(rows):
        if (int(row["bin_id"]) != series["first_full_bin"] + int(offsets[index])
                or int(row["actual"]) != actual[index]):
            raise ValueError("cell-g frozen label or offset changed")
        for new_key, old_key in (("mean12", "mean_12"), ("daily", "daily_lag"),
                                 ("seasonal", "seasonal_residual")):
            if not np.isclose(predicted[new_key][index], float(row[old_key]),
                              rtol=1e-12, atol=1e-9):
                raise ValueError(f"cell-g {new_key} prediction mismatch")


def analyze_cell(cell: str, series: dict, counts: np.ndarray) -> dict:
    offsets, actual, predicted = forecasts(counts)
    if cell == "g":
        check_g_frozen_predictions(series, offsets, actual, predicted)
    threshold = float(np.quantile(counts[:START], .9, method="higher"))
    future = offsets >= FIRST_CUTOFF
    fixed = select_fixed(offsets, actual, predicted, FIRST_CUTOFF)
    windows = []
    common = offsets >= COMMON_CUTOFF
    for days in range(3, 8):
        cutoff = days * 288
        selected = select_fixed(offsets, actual, predicted, cutoff)
        name = selected["chosen"]
        own = offsets >= cutoff
        selected["selection_days"] = days
        selected["post_selection_metrics"] = metrics(actual[own], predicted[name][own], threshold)
        selected["common_post_day7_metrics"] = metrics(actual[common], predicted[name][common], threshold)
        windows.append(selected)
    rolling = {}
    for label, three_way in (("two_way_5pct", False), ("three_way_min_mae", True)):
        choices, switches = rolling_choices(offsets, actual, predicted, three_way)
        rolling[label] = {
            "metrics": metrics(actual[future], selected_prediction(offsets, predicted, choices), threshold),
            "initial_choice": choices[0],
            "selection_counts": dict(sorted(Counter(choices).items())),
            "switch_count": len(switches),
            "switch_offsets": switches,
        }
    return {
        "series_sha256": SERIES[cell][1],
        "first_full_bin": series["first_full_bin"],
        "full_bins": len(counts),
        "high_load_threshold_from_first_300_bins": threshold,
        "first_three_day_fixed_selection": fixed,
        "fixed_window_stability": windows,
        "post_day3": {
            "evaluation_offsets": [FIRST_CUTOFF, len(counts) - 1],
            "expert_metrics": {key: metrics(actual[future], value[future], threshold)
                               for key, value in predicted.items()},
            "fixed_selector_metrics": metrics(actual[future],
                                              predicted[fixed["chosen"]][future], threshold),
            "rolling": rolling,
        },
    }


def main() -> None:
    results = {
        "scope": "Exploratory post-hoc analysis of already exposed generic Borg JOB series; not independent validation or AI-App scaling outcome",
        "causal_rule": "Each target t uses only y[:t]; first selector uses forecast errors 300..863; rolling selectors use at most prior 576 eligible forecast errors, initially 564",
        "seasonal_residual_alpha": ALPHA,
        "five_minute_bins_per_day": 288,
        "forecast_error_start_offset": START,
        "post_three_day_evaluation_start_offset": FIRST_CUTOFF,
        "rolling_error_window_bins": ROLLING_WINDOW,
        "fixed_seasonal_margin_fraction": .05,
        "cells": {},
    }
    for cell, (path, expected_sha) in SERIES.items():
        series, counts = read_series(path, expected_sha)
        results["cells"][cell] = analyze_cell(cell, series, counts)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"output": str(OUTPUT), "sha256": sha256(OUTPUT),
                      "cells": {cell: {"fixed": row["first_three_day_fixed_selection"]["chosen"],
                                       "rolling2_mae": row["post_day3"]["rolling"]["two_way_5pct"]["metrics"]["mae"],
                                       "rolling3_mae": row["post_day3"]["rolling"]["three_way_min_mae"]["metrics"]["mae"]}
                                for cell, row in results["cells"].items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
