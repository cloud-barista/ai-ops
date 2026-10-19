"""Exploratory nested, donor-only choice of load-curve shrinkage weight."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_scherm_b200_load_curve as base


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/scherm_b200_load_curve_shrinkage_protocol.md"
OUT = ROOT / "artifacts/scherm_b200_load_curve_shrinkage_v1"
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blended(frame: pd.DataFrame, metric: str, weight: float,
             *, ridge_prefix: str = "ridge") -> np.ndarray:
    donor = frame[f"donor_median_{metric}"].to_numpy(dtype=float)
    ridge = frame[f"{ridge_prefix}_{metric}"].to_numpy(dtype=float)
    return np.exp((1 - weight) * np.log(donor) + weight * np.log(ridge))


def score_weight(frame: pd.DataFrame, weight: float,
                 *, ridge_prefix: str = "ridge") -> dict:
    models = sorted(frame.held_out_model.unique())
    per_model = {}
    for model in models:
        mask = frame.held_out_model.eq(model)
        part = frame.loc[mask]
        per_model[model] = {}
        for metric in base.METRICS:
            actual = part[f"actual_{metric}"].to_numpy(dtype=float)
            pred = _blended(part, metric, weight, ridge_prefix=ridge_prefix)
            donor = part[f"donor_median_{metric}"].to_numpy(dtype=float)
            per_model[model][metric] = {
                "candidate": float(np.abs(np.log(pred / actual)).mean()),
                "donor": float(np.abs(np.log(donor / actual)).mean()),
            }
    macro = {
        metric: {
            name: float(np.mean([per_model[model][metric][name] for model in models]))
            for name in ("candidate", "donor")
        } for metric in base.METRICS
    }
    both_wins = sum(all(per_model[model][metric]["candidate"]
                        < per_model[model][metric]["donor"] for metric in base.METRICS)
                    for model in models)
    worst_ratio = 0.0
    for model in models:
        for metric in base.METRICS:
            pair = per_model[model][metric]
            ratio = pair["candidate"] / pair["donor"] if pair["donor"] else (
                1.0 if pair["candidate"] == 0 else float("inf"))
            worst_ratio = max(worst_ratio, ratio)
    return {
        "weight": weight,
        "macro_male": macro,
        "both_metric_model_wins": both_wins,
        "worst_model_metric_loss_ratio": worst_ratio,
        "per_model_male": per_model,
    }


def choose_inner_weight(inner: pd.DataFrame) -> tuple[float, list[dict]]:
    if inner.held_out_model.nunique() != 6:
        raise ValueError("Expected six independent donor models in inner selection")
    scores = [score_weight(inner, w) for w in WEIGHTS]
    eligible = []
    for item in scores[1:]:
        macro = item["macro_male"]
        if (all(macro[m]["candidate"] <= 0.95 * macro[m]["donor"] for m in base.METRICS)
                and item["both_metric_model_wins"] >= 4
                and item["worst_model_metric_loss_ratio"] <= 1.2):
            eligible.append(item)
    if not eligible:
        return 0.0, scores
    chosen = min(eligible, key=lambda item: (
        sum(item["macro_male"][m]["candidate"] / item["macro_male"][m]["donor"]
            for m in base.METRICS), item["weight"],
    ))
    return float(chosen["weight"]), scores


def run(selected: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    outer_predictions, _ = base.evaluate(selected)
    frames = []
    weights = {}
    inner_scores = {}
    for held in sorted(selected.model.unique()):
        # The inner fit never receives any row of the outer held-out model.
        donor_source = selected.loc[~selected.model.eq(held)].copy()
        inner_predictions, _ = base.evaluate(donor_source)
        weight, scores = choose_inner_weight(inner_predictions)
        weights[held] = weight
        inner_scores[held] = scores
        part = outer_predictions.loc[outer_predictions.held_out_model.eq(held)].copy()
        part["selected_weight"] = weight
        for metric in base.METRICS:
            part[f"shrinkage_{metric}"] = _blended(part, metric, weight)
        frames.append(part)
    predictions = pd.concat(frames, ignore_index=True).sort_values(
        ["held_out_model", "concurrency"]).reset_index(drop=True)
    # The score function sees labels only after all inner choices have been made.
    overall = score_weight(predictions, 1.0, ridge_prefix="shrinkage")
    macro = overall["macro_male"]
    improvement = {metric: 1 - macro[metric]["candidate"] / macro[metric]["donor"]
                   for metric in base.METRICS}
    gates = {
        "both_metric_macro_improvement_at_least_5pct": all(x >= 0.05 for x in improvement.values()),
        "both_metric_model_wins_at_least_5_of_7": overall["both_metric_model_wins"] >= 5,
        "worst_model_metric_loss_ratio_at_most_1_2": overall["worst_model_metric_loss_ratio"] <= 1.2,
    }
    summary = {
        "status": "EXPLORATORY_GO" if all(gates.values()) else "EXPLORATORY_NO_GO",
        "models": sorted(selected.model.unique()),
        "target_cells": len(predictions),
        "weights_by_outer_model": weights,
        "inner_candidate_scores": inner_scores,
        "outer_macro_male": macro,
        "outer_per_model_male": overall["per_model_male"],
        "outer_improvement_fraction": improvement,
        "outer_both_metric_model_wins": overall["both_metric_model_wins"],
        "outer_worst_model_metric_loss_ratio": overall["worst_model_metric_loss_ratio"],
        "gates": gates,
    }
    return predictions, summary


def main() -> None:
    selected = base.load_selected()
    predictions, summary = run(selected)
    summary.update({
        "source_sha256": base.SOURCE_SHA256,
        "base_script_sha256": sha256(Path(base.__file__)),
        "script_sha256": sha256(Path(__file__)),
        "protocol_sha256": sha256(PROTOCOL),
    })
    OUT.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(OUT / "predictions.csv", index=False, float_format="%.12g")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: summary[key] for key in (
        "status", "weights_by_outer_model", "outer_macro_male", "outer_improvement_fraction",
        "outer_both_metric_model_wins", "outer_worst_model_metric_loss_ratio", "gates",
    )}, indent=2))


if __name__ == "__main__":
    main()
