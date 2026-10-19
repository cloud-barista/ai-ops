#!/usr/bin/env python3
"""Post-score size-linear plus donor-residual p90 baselines for H100 readiness."""

from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path

import numpy as np

from scripts import evaluate_h100_readiness_eta as original


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_PREDICTIONS = ROOT / "artifacts/h100_readiness_eta_v1/predictions.csv"
ORIGINAL_METRICS = ROOT / "artifacts/h100_readiness_eta_v1/metrics.json"
ADDENDUM = ROOT / "reports/h100_readiness_eta_size_baseline_posthoc_plan.md"
OUTPUT = ROOT / "artifacts/h100_readiness_eta_size_baseline_posthoc"
NO_CACHE = "phase_size_linear_residual_p90"
ORACLE_CACHE = "phase_cache_size_linear_residual_p90"


def fit_size_linear_residual_p90(donor: list[dict], target: list[dict]) -> np.ndarray:
    if len({row["model"] for row in target}) != 1:
        raise ValueError("target must contain one held-out model")
    if target[0]["model"] in {row["model"] for row in donor}:
        raise ValueError("held-out model leaked into donor fit")
    x = np.asarray([row["model_size_b"] for row in donor], dtype=float)
    y = np.asarray([row["actual_remaining_s"] for row in donor], dtype=float)
    design = np.column_stack((np.ones(len(donor)), x))
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    residual_p90 = np.quantile(
        y - design @ coefficients, original.QUANTILE, method="linear"
    )
    target_x = np.asarray([row["model_size_b"] for row in target], dtype=float)
    return np.maximum(coefficients[0] + coefficients[1] * target_x + residual_p90, 0.0)


def key(row: dict) -> tuple:
    return (row["model"], row["condition"], int(row["trial"]), row["stage"])


def load_original_predictions(path: Path) -> dict[tuple, dict]:
    with path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 1200:
        raise ValueError("expected 1200 original prediction rows")
    result = {}
    for row in rows:
        row_key = key(row)
        if row_key in result:
            raise ValueError("duplicate original prediction key")
        result[row_key] = row
    return result


def generate(rows: list[dict], previous: dict[tuple, dict]) -> list[dict]:
    if len(rows) != len(previous):
        raise ValueError("original prediction set differs from raw data")
    output = []
    for heldout in original.MODELS:
        for stage in original.STAGES:
            target = [
                r for r in rows if r["model"] == heldout and r["stage"] == stage
            ]
            donor = [
                r for r in rows if r["model"] != heldout and r["stage"] == stage
            ]
            no_cache = fit_size_linear_residual_p90(donor, target)
            oracle = {}
            for condition in original.CONDITIONS:
                cache_donor = [r for r in donor if r["condition"] == condition]
                cache_target = [r for r in target if r["condition"] == condition]
                oracle[condition] = fit_size_linear_residual_p90(
                    cache_donor, cache_target
                )
            cache_positions = defaultdict(int)
            for index, row in enumerate(target):
                earlier = previous.get(key(row))
                if earlier is None:
                    raise ValueError(f"missing original prediction: {key(row)}")
                for field in ("model_size_b", "elapsed_s", "actual_remaining_s"):
                    if not np.isclose(float(earlier[field]), row[field], atol=1e-10):
                        raise ValueError(f"original prediction value mismatch: {key(row)}")
                condition = row["condition"]
                oracle_index = cache_positions[condition]
                cache_positions[condition] += 1
                output.append(
                    {
                        **row,
                        "phase_quantile": float(earlier["phase_quantile"]),
                        "phase_cache_quantile": float(
                            earlier["phase_cache_quantile"]
                        ),
                        NO_CACHE: float(no_cache[index]),
                        ORACLE_CACHE: float(oracle[condition][oracle_index]),
                    }
                )
    if {key(row) for row in output} != set(previous):
        raise ValueError("row keys changed in post-score join")
    return sorted(
        output,
        key=lambda x: (
            original.MODELS.index(x["model"]),
            original.CONDITIONS.index(x["condition"]),
            x["trial"],
            original.STAGES.index(x["stage"]),
        ),
    )


def score_set(rows: list[dict]) -> dict:
    return {
        method: original.score(rows, method)
        for method in ("phase_quantile", NO_CACHE, "phase_cache_quantile", ORACLE_CACHE)
    }


def summarize(rows: list[dict]) -> dict:
    overall = score_set(rows)
    by_model = {
        m: score_set([r for r in rows if r["model"] == m])
        for m in original.MODELS
    }
    by_stage = {
        stage: score_set([r for r in rows if r["stage"] == stage])
        for stage in original.STAGES
    }
    by_condition = {
        c: score_set([r for r in rows if r["condition"] == c])
        for c in original.CONDITIONS
    }
    by_stage_condition = {
        stage: {
            c: score_set(
                [r for r in rows if r["stage"] == stage and r["condition"] == c]
            )
            for c in original.CONDITIONS
        }
        for stage in original.STAGES
    }
    comparisons = {}
    for learned, baseline in (
        ("phase_quantile", NO_CACHE),
        ("phase_cache_quantile", ORACLE_CACHE),
    ):
        a, b = overall[learned], overall[baseline]
        wins = sum(
            by_model[m][learned]["pinball_s"] < by_model[m][baseline]["pinball_s"]
            for m in original.MODELS
        )
        comparisons[learned] = {
            "posthoc_baseline": baseline,
            "pinball_relative_improvement": 1 - a["pinball_s"] / b["pinball_s"],
            "under_gt5_rate_difference": (
                a["under_gt5_rate"] - b["under_gt5_rate"]
            ),
            "model_wins": wins,
            "meets_original_numeric_rule_posthoc_only": (
                a["pinball_s"] <= 0.95 * b["pinball_s"]
                and a["under_gt5_rate"] <= b["under_gt5_rate"]
                and wins >= 3
            ),
        }
    return {
        "overall": overall,
        "by_model": by_model,
        "by_stage": by_stage,
        "by_condition": by_condition,
        "by_stage_condition": by_stage_condition,
        "comparisons": comparisons,
    }


def main() -> None:
    records = original.read_trials(original.DEFAULT_INPUT)
    raw_rows = original.build_stage_rows(records)
    previous = load_original_predictions(ORIGINAL_PREDICTIONS)
    rows = generate(raw_rows, previous)
    scores = summarize(rows)
    old = json.loads(ORIGINAL_METRICS.read_text())
    for method in ("phase_quantile", "phase_cache_quantile"):
        if not np.isclose(
            scores["overall"][method]["pinball_s"],
            old["scores"]["overall"][method]["pinball_s"],
            atol=1e-12,
        ):
            raise ValueError("original score was not reproduced")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    pred_path, metric_path = OUTPUT / "predictions.csv", OUTPUT / "metrics.json"
    if pred_path.exists() or metric_path.exists():
        raise FileExistsError("post-score artifacts already exist")
    with pred_path.open("x", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metrics = {
        "status": "post_score_baseline_not_independent_validation",
        "source_sha256": original.sha256_file(original.DEFAULT_INPUT),
        "original_predictions_sha256": original.sha256_file(ORIGINAL_PREDICTIONS),
        "original_metrics_sha256": original.sha256_file(ORIGINAL_METRICS),
        "plan_sha256": original.sha256_file(ADDENDUM),
        "script_sha256": original.sha256_file(Path(__file__)),
        "predictions_sha256": original.sha256_file(pred_path),
        "row_count": len(rows),
        "scores": scores,
    }
    with metric_path.open("x") as destination:
        json.dump(metrics, destination, indent=2, sort_keys=True)
        destination.write("\n")
    print(metric_path)


if __name__ == "__main__":
    main()
