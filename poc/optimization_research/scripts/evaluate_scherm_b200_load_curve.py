"""Exploratory model-held-out B200 TP2 load-curve forecasting."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/external/scherm_onprem_2026/serving_ALL.csv"
PROTOCOL = ROOT / "reports/scherm_b200_load_curve_protocol.md"
OUT = ROOT / "artifacts/scherm_b200_load_curve_v1"
SOURCE_SHA256 = "d7f677cf0c4ccfd8c702c8458c4e6c3d324e90292bf13e5f09e065431e8d5f9e"
ANCHORS = (1, 4, 8)
TARGETS = (16, 32, 64, 128, 256, 512)
METRICS = ("output_tok_s_median", "ttft_ms_p95")
METHODS = ("keep_8", "linear_or_keep", "donor_median", "low_slope", "ridge")
BASELINES = METHODS[:-1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_selected() -> pd.DataFrame:
    if sha256(SOURCE) != SOURCE_SHA256:
        raise ValueError("Pinned Scherm source hash changed")
    frame = pd.read_csv(SOURCE)
    if len(frame) != 528 or len(frame.columns) != 15:
        raise ValueError("Pinned source dimensions changed")
    tp = pd.to_numeric(frame["shard"].str.extract(r"_tp(\d+)_", expand=False), errors="coerce")
    mask = (
        frame["gpu"].eq("NVIDIA B200")
        & frame["model"].str.fullmatch(r"Qwen/Qwen2\.5-(?:Coder-)?(?:7|14|32|72)B-Instruct-AWQ")
        & tp.eq(2)
    )
    selected = frame.loc[mask, ["model", "concurrency", "n_ok", *METRICS, "shard"]].copy()
    if len(selected) != 64 or selected["model"].nunique() != 7:
        raise ValueError("Expected 64 rows and 7 models at B200 TP2")
    if selected.duplicated(["model", "concurrency"]).any():
        raise ValueError("Duplicate model/concurrency cell")
    if not selected["n_ok"].eq(10).all():
        raise ValueError("Expected ten successful repeats in every TP2 cell")
    if not np.isfinite(selected[list(METRICS)].to_numpy(dtype=float)).all() or not (selected[list(METRICS)] > 0).all().all():
        raise ValueError("All target metrics must be positive and finite")
    for model, group in selected.groupby("model"):
        if not set((*ANCHORS, *TARGETS)).issubset(set(group["concurrency"])):
            raise ValueError(f"Incomplete anchor/target grid for {model}")
    return selected.sort_values(["model", "concurrency"]).reset_index(drop=True)


def model_size(model: str) -> float:
    match = re.search(r"-(7|14|32|72)B-Instruct-AWQ$", model)
    if match is None:
        raise ValueError(f"Unknown model size: {model}")
    return float(match.group(1))


def features(model: str, anchors: dict[int, dict[str, float]], concurrency: int) -> np.ndarray:
    t = np.log(concurrency / 8)
    a = anchors
    out41 = np.log(a[4][METRICS[0]] / a[1][METRICS[0]])
    out84 = np.log(a[8][METRICS[0]] / a[4][METRICS[0]])
    tt41 = np.log(a[4][METRICS[1]] / a[1][METRICS[1]])
    tt84 = np.log(a[8][METRICS[1]] / a[4][METRICS[1]])
    slopes = (out41, out84, tt41, tt84)
    return np.asarray([
        t, t * t,
        np.log(a[8][METRICS[0]]), np.log(a[8][METRICS[1]]),
        *slopes, np.log(model_size(model)), float("Coder-" in model),
        *(t * value for value in slopes),
    ], dtype=float)


def evaluate(selected: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Use held-out models' 1/4/8 inputs; never use their >=16 labels to fit them."""
    by_model: dict[str, dict[int, dict[str, float]]] = {}
    for model, group in selected.groupby("model"):
        by_model[model] = {
            int(row.concurrency): {metric: float(getattr(row, metric)) for metric in METRICS}
            for row in group.itertuples(index=False)
        }
    models = sorted(by_model)
    rows = []
    for held in models:
        donor = [model for model in models if model != held]
        anchors = {model: {c: by_model[model][c] for c in ANCHORS} for model in models}
        x_train = np.stack([features(model, anchors[model], c) for model in donor for c in TARGETS])
        x_test = np.stack([features(held, anchors[held], c) for c in TARGETS])
        predictions: dict[str, dict[str, np.ndarray]] = {metric: {} for metric in METRICS}
        for metric in METRICS:
            y_train = np.asarray([
                np.log(by_model[model][c][metric] / anchors[model][8][metric])
                for model in donor for c in TARGETS
            ])
            regressor = make_pipeline(StandardScaler(), Ridge(alpha=10))
            regressor.fit(x_train, y_train)
            ridge_ratio = np.exp(np.clip(regressor.predict(x_test), -4, 4))
            donor_ratio = np.asarray([
                np.exp(np.median([
                    np.log(by_model[model][c][metric] / anchors[model][8][metric])
                    for model in donor
                ])) for c in TARGETS
            ])
            c_ratio = np.asarray(TARGETS, dtype=float) / 8
            if metric == METRICS[0]:
                low_slope = np.clip(
                    np.log(anchors[held][8][metric] / anchors[held][4][metric]) / np.log(2),
                    0, 1,
                )
                linear_ratio = c_ratio
                slope_ratio = c_ratio ** low_slope
            else:
                low_slope = max(0.0, np.log(anchors[held][8][metric] / anchors[held][4][metric]) / np.log(2))
                linear_ratio = np.ones(len(TARGETS))
                slope_ratio = np.clip(c_ratio ** low_slope, 0.25, 4)
            predictions[metric] = {
                "keep_8": np.ones(len(TARGETS)),
                "linear_or_keep": linear_ratio,
                "donor_median": donor_ratio,
                "low_slope": slope_ratio,
                "ridge": ridge_ratio,
            }
        for i, c in enumerate(TARGETS):
            row = {"held_out_model": held, "concurrency": c}
            for metric in METRICS:
                row[f"actual_{metric}"] = by_model[held][c][metric]
                row[f"anchor8_{metric}"] = anchors[held][8][metric]
                for method in METHODS:
                    row[f"{method}_{metric}"] = anchors[held][8][metric] * predictions[metric][method][i]
            rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["held_out_model", "concurrency"]).reset_index(drop=True)
    per_model = {}
    macro = {method: {} for method in METHODS}
    for model, group in frame.groupby("held_out_model"):
        per_model[model] = {}
        for method in METHODS:
            per_model[model][method] = {
                metric: float(np.abs(np.log(group[f"{method}_{metric}"] / group[f"actual_{metric}"])).mean())
                for metric in METRICS
            }
    for method in METHODS:
        for metric in METRICS:
            macro[method][metric] = float(np.mean([per_model[model][method][metric] for model in models]))
    best = {metric: min(BASELINES, key=lambda method: macro[method][metric]) for metric in METRICS}
    both_wins = 0
    max_ratio = 0.0
    for model in models:
        wins = []
        for metric in METRICS:
            reference = per_model[model][best[metric]][metric]
            candidate = per_model[model]["ridge"][metric]
            wins.append(candidate < reference)
            ratio = candidate / reference if reference else (1.0 if candidate == 0 else float("inf"))
            max_ratio = max(max_ratio, ratio)
        both_wins += all(wins)
    improvements = {
        metric: 1 - macro["ridge"][metric] / macro[best[metric]][metric]
        for metric in METRICS
    }
    checks = {
        "both_metric_macro_improvement_at_least_5pct": all(value >= 0.05 for value in improvements.values()),
        "both_metric_model_wins_at_least_5_of_7": both_wins >= 5,
        "worst_model_metric_loss_ratio_at_most_1_2": max_ratio <= 1.2,
    }
    result = {
        "status": "EXPLORATORY_GO" if all(checks.values()) else "EXPLORATORY_NO_GO",
        "models": models,
        "prediction_cells": len(frame),
        "anchor_concurrencies": ANCHORS,
        "target_concurrencies": TARGETS,
        "macro_male": macro,
        "per_model_male": per_model,
        "best_single_baseline": best,
        "ridge_improvement_fraction": improvements,
        "ridge_both_metric_model_wins": both_wins,
        "ridge_worst_model_metric_loss_ratio": max_ratio,
        "gates": checks,
    }
    return frame, result


def main() -> None:
    selected = load_selected()
    predictions, summary = evaluate(selected)
    summary.update({
        "source_sha256": SOURCE_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(PROTOCOL),
    })
    OUT.mkdir(parents=True, exist_ok=True)
    selected.to_csv(OUT / "selected_cells.csv", index=False, float_format="%.12g")
    predictions.to_csv(OUT / "predictions.csv", index=False, float_format="%.12g")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: summary[key] for key in (
        "status", "prediction_cells", "macro_male", "best_single_baseline",
        "ridge_improvement_fraction", "ridge_both_metric_model_wins",
        "ridge_worst_model_metric_loss_ratio", "gates",
    )}, indent=2))


if __name__ == "__main__":
    main()
