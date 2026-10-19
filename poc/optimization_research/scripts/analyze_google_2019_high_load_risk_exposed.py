"""Exploratory high-load classification on four *already exposed* Borg cells.

This is a development audit, not an independent trial. It never reads cell-f
or cell-h. Labels are future 1-hour JOB arrival averages; every feature and
alert cutoff at decision bin t uses information available before t begins.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/google_2019_high_load_risk_exposed_v1/results.json"
SERIES = {
    "a": ("google_2019_cell_a_sentinel_v2", "7678e205e07891ab7698ff7315c29eda7d9eb2c99fc52e84af54d87acccb9f12"),
    "b": ("google_2019_cell_b_adaptive_v1", "8dcdf90d5487e1eafd1ea4f5f0b6d1ca2002d83da069ce6553e98dbd07a9c439"),
    "g": ("google_2019_cell_g_seasonal_v1", "b3b10498a1a0d96b7012ce2e7414a048943d55f78706b312aecf543e140e7d18"),
    "e": ("google_2019_cell_e_selector_v1", "63b7636f9b27a9217aee54ab173ca68c8fb2986ec966277d65e80bd7f3092e97"),
}
BURN_IN = 300
CALIBRATION_END = 864
HORIZON = 12
ALERT_LOOKBACK = 576
ALERT_QUANTILE = .9
FEATURES = (
    "lag1_over_threshold", "lag2_over_threshold", "mean3_over_threshold",
    "mean12_over_threshold", "std12_over_threshold", "max12_over_threshold",
    "mean288_over_threshold", "std288_over_threshold", "lag288_over_threshold",
    "y_lag1_minus_lag289_over_threshold", "time_of_day_sine", "time_of_day_cosine",
)
MODEL_PARAMS = {
    "max_iter": 100,
    "max_leaf_nodes": 15,
    "max_depth": 3,
    "min_samples_leaf": 50,
    "learning_rate": .05,
    "l2_regularization": 1,
    "random_state": 42,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_series(cell: str) -> np.ndarray:
    folder, expected = SERIES[cell]
    path = ROOT / "artifacts" / folder / "series.json"
    if sha256(path) != expected:
        raise ValueError(f"{cell} exposed series SHA-256 changed")
    data = json.loads(path.read_text())
    counts = data.get("counts")
    if (not isinstance(counts, list) or len(counts) <= CALIBRATION_END + HORIZON
            or any(type(value) is not int or value < 0 for value in counts)
            or data.get("last_full_bin") - data.get("first_full_bin") + 1 != len(counts)):
        raise ValueError(f"{cell} complete-bin series malformed")
    return np.asarray(counts, dtype=float)


def burn_in_threshold(counts: np.ndarray) -> float:
    """The first 300 closed bins contain exactly 289 12-bin means."""
    if len(counts) < BURN_IN:
        raise ValueError("insufficient burn-in")
    means = np.convolve(counts[:BURN_IN], np.ones(HORIZON) / HORIZON, mode="valid")
    return float(np.quantile(means, .9, method="higher"))


def one_feature(counts: np.ndarray, t: int, threshold: float) -> tuple[np.ndarray, dict[str, float]]:
    """Produce a feature row using only counts[:t]."""
    if t < BURN_IN or t > len(counts) or threshold <= 0:
        raise ValueError("invalid feature time or threshold")
    recent = counts[t - 12:t]
    day = counts[t - 288:t]
    features = np.asarray([
        counts[t - 1] / threshold,
        counts[t - 2] / threshold,
        float(np.mean(recent[-3:])) / threshold,
        float(np.mean(recent)) / threshold,
        float(np.std(recent)) / threshold,
        float(np.max(recent)) / threshold,
        float(np.mean(day)) / threshold,
        float(np.std(day)) / threshold,
        counts[t - 288] / threshold,
        (counts[t - 1] - counts[t - 289]) / threshold,
        math.sin(2 * math.pi * t / 288),
        math.cos(2 * math.pi * t / 288),
    ], dtype=float)
    baselines = {
        "mean12": float(np.mean(recent)) / threshold,
        "max12": float(np.max(recent)) / threshold,
        "daily_future_hour": float(np.mean(counts[t - 288:t - 276])) / threshold,
    }
    return features, baselines


def make_examples(counts: np.ndarray) -> dict:
    """Forecast t..t+11, whose label becomes known at the start of t+12."""
    threshold = burn_in_threshold(counts)
    if threshold <= 0 or len(counts) <= CALIBRATION_END + HORIZON:
        raise ValueError("insufficient positive threshold or evaluation bins")
    offsets = np.arange(BURN_IN, len(counts) - HORIZON + 1, dtype=int)
    features = []
    baselines = {key: [] for key in ("mean12", "max12", "daily_future_hour")}
    labels = []
    for t in offsets:
        row, scores = one_feature(counts, int(t), threshold)
        features.append(row)
        for key in baselines:
            baselines[key].append(scores[key])
        labels.append(int(np.mean(counts[t:t + HORIZON]) >= threshold))
    return {
        "threshold": threshold,
        "offsets": offsets,
        "feature_matrix": np.asarray(features),
        "labels": np.asarray(labels, dtype=int),
        "baseline_scores": {key: np.asarray(values) for key, values in baselines.items()},
    }


def causal_alerts(scores: np.ndarray, first_score_index: int = CALIBRATION_END - BURN_IN) -> np.ndarray:
    """At index i, rank score[i] against only prior up-to-576 scores."""
    alarms = np.zeros(len(scores), dtype=bool)
    for i in range(first_score_index, len(scores)):
        past = scores[max(0, i - ALERT_LOOKBACK):i]
        if len(past) == 0 or not np.isfinite(past).all() or not np.isfinite(scores[i]):
            raise ValueError("missing historical prediction score")
        k = math.ceil((len(past) - 1) * ALERT_QUANTILE)
        cutoff = np.partition(past, k)[k]
        alarms[i] = scores[i] >= cutoff
    return alarms


def score_method(labels: np.ndarray, scores: np.ndarray) -> dict:
    first = CALIBRATION_END - BURN_IN
    actual = labels[first:]
    predicted = scores[first:]
    if len(actual) == 0 or actual.sum() == 0:
        raise ValueError("no evaluation positives")
    alarms = causal_alerts(scores)[first:]
    true_positive = int(np.sum(alarms & (actual == 1)))
    alert_count = int(alarms.sum())
    return {
        "average_precision": float(average_precision_score(actual, predicted)),
        "test_bins": int(len(actual)),
        "positive_bins": int(actual.sum()),
        "alert_bins": alert_count,
        "true_positive_bins": true_positive,
        "alert_rate": float(np.mean(alarms)),
        "precision": float(true_positive / alert_count) if alert_count else None,
        "recall": float(true_positive / actual.sum()),
    }


def analyze() -> dict:
    examples = {cell: make_examples(load_series(cell)) for cell in SERIES}
    result = {
        "status": "exposed-development-only; not independent validation or an AI-App action outcome",
        "source_series_sha256": {cell: expected for cell, (_, expected) in SERIES.items()},
        "label": "mean(y[t:t+12]) >= q90_higher(first 300 bins' 289 closed 12-bin means)",
        "label_maturity": "At decision t only y[:t] is available; the label is complete at the start of bin t+12",
        "feature_names": FEATURES,
        "model": {"name": "HistGradientBoostingClassifier", "params": MODEL_PARAMS,
                  "sklearn_version": sklearn.__version__},
        "alert": "score[t] >= higher-q90 of prior max 576 scores; test starts t=864",
        "cells": {},
    }
    for heldout, own in examples.items():
        train_features = np.concatenate([data["feature_matrix"] for cell, data in examples.items()
                                         if cell != heldout])
        train_labels = np.concatenate([data["labels"] for cell, data in examples.items()
                                       if cell != heldout])
        model = HistGradientBoostingClassifier(**MODEL_PARAMS)
        model.fit(train_features, train_labels)
        risk = model.predict_proba(own["feature_matrix"])[:, 1]
        labels = own["labels"]
        first = CALIBRATION_END - BURN_IN
        metrics = {key: score_method(labels, values)
                   for key, values in {"hgb": risk, **own["baseline_scores"]}.items()}
        result["cells"][heldout] = {
            "threshold_jobs_per_5min_as_future_hour_mean": own["threshold"],
            "full_bins": int(len(own["offsets"]) + HORIZON + BURN_IN - 1),
            "train_cells": [cell for cell in SERIES if cell != heldout],
            "training_label_count": int(len(train_labels)),
            "calibration_offsets": [BURN_IN, CALIBRATION_END - 1],
            "calibration_positive_bins": int(labels[:first].sum()),
            "evaluation_offsets": [CALIBRATION_END, int(own["offsets"][-1])],
            "metrics": metrics,
        }
    return result


def main() -> None:
    result = analyze()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()
