#!/usr/bin/env python3
"""Offline, model-held-out p90 remaining time to vLLM /health readiness."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import re
import sys

import numpy as np
import scipy
import sklearn
from sklearn.linear_model import QuantileRegressor
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/external/h100_readiness_eta/final.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts/h100_readiness_eta_v1"
PROTOCOL = ROOT / "reports/h100_readiness_eta_protocol.md"
EXPECTED_SHA256 = "e70ff5175249a090ce3c2ece3e17251c27fb9e2429d03526b4d70e854d61c07b"
MODELS = ("Qwen/Qwen3-0.6B", "Qwen/Qwen3-1.7B", "Qwen/Qwen3-4B", "Qwen/Qwen3-8B")
CONDITIONS = ("C0", "C1", "C2", "C3", "C4")
STAGES = (
    "pull_start",
    "run_start",
    "weights_loaded",
    "compile_done",
    "graph_capture_done",
    "engine_ready",
)
METHODS = (
    "phase_p90",
    "phase_cache_p90",
    "phase_quantile",
    "phase_cache_quantile",
)
QUANTILE = 0.9
ALPHA = 0.01


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("all timestamps must use UTC Z form")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp is not UTC")
    return parsed


def nominal_size_b(model: str) -> float:
    if model not in MODELS:
        raise ValueError(f"unexpected model: {model}")
    match = re.fullmatch(r"Qwen/Qwen3-(0\.6|1\.7|4|8)B", model)
    if match is None:
        raise ValueError(f"invalid model size: {model}")
    return float(match.group(1))


def event_time(record: dict, stage: str) -> datetime:
    if stage in ("pull_start", "run_start"):
        return parse_utc(record["t"][stage])
    return parse_utc(record["vllm"]["markers"][stage]["abs"])


def read_trials(path: Path) -> list[dict]:
    actual = sha256_file(path)
    if actual != EXPECTED_SHA256:
        raise ValueError(f"source SHA-256 mismatch: {actual}")
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if len(records) != 200:
        raise ValueError("expected exactly 200 source trials")
    cells = Counter()
    keys = set()
    for record in records:
        if record.get("schema") != "trackb.coldstart.v1" or record.get("ok") is not True:
            raise ValueError("invalid or unsuccessful trial")
        model, condition, trial = record["model"], record["condition"], record["trial"]
        nominal_size_b(model)
        if condition not in CONDITIONS or type(trial) is not int:
            raise ValueError("unexpected condition or trial identifier")
        key = (model, condition, trial)
        if key in keys:
            raise ValueError(f"duplicate trial: {key}")
        keys.add(key)
        cells[(model, condition)] += 1
        moments = [event_time(record, name) for name in STAGES]
        ready = parse_utc(record["t"]["health_ok"])
        first_token = parse_utc(record["t"]["first_token"])
        if moments != sorted(moments) or not (moments[-1] <= ready <= first_token):
            raise ValueError(f"out-of-order stage/ready/token timestamps: {key}")
        if not all(np.isfinite((ready - moment).total_seconds()) for moment in moments):
            raise ValueError(f"nonfinite remaining time: {key}")
    if set(cells) != {(m, c) for m in MODELS for c in CONDITIONS}:
        raise ValueError("missing model/cache cells")
    if any(count != 10 for count in cells.values()):
        raise ValueError("expected ten trials per model/cache cell")
    return records


def build_stage_rows(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        ready = parse_utc(record["t"]["health_ok"])
        start = event_time(record, "pull_start")
        for stage in STAGES:
            moment = event_time(record, stage)
            rows.append(
                {
                    "model": record["model"],
                    "model_size_b": nominal_size_b(record["model"]),
                    "condition": record["condition"],
                    "trial": record["trial"],
                    "stage": stage,
                    "elapsed_s": (moment - start).total_seconds(),
                    "actual_remaining_s": (ready - moment).total_seconds(),
                }
            )
    return sorted(
        rows,
        key=lambda x: (
            MODELS.index(x["model"]),
            CONDITIONS.index(x["condition"]),
            x["trial"],
            STAGES.index(x["stage"]),
        ),
    )


def feature_matrix(rows: list[dict], use_cache: bool) -> np.ndarray:
    """Only features available by the named stage, with optional oracle cache state."""
    numeric = np.asarray(
        [[row["model_size_b"], row["elapsed_s"]] for row in rows], dtype=float
    )
    if not use_cache:
        return numeric
    cache = np.asarray(
        [[float(row["condition"] == c) for c in CONDITIONS[1:]] for row in rows],
        dtype=float,
    )
    return np.column_stack((numeric, cache))


def quantile_predict(
    donor: list[dict], target: list[dict], *, use_cache: bool
) -> np.ndarray:
    source_x, target_x = feature_matrix(donor, use_cache), feature_matrix(target, use_cache)
    scaler = StandardScaler()
    source_x[:, :2] = scaler.fit_transform(source_x[:, :2])
    target_x[:, :2] = scaler.transform(target_x[:, :2])
    y = np.asarray([row["actual_remaining_s"] for row in donor], dtype=float)
    model = QuantileRegressor(quantile=QUANTILE, alpha=ALPHA, solver="highs")
    model.fit(source_x, y)
    return np.maximum(model.predict(target_x), 0.0)


def predict_heldout(rows: list[dict]) -> list[dict]:
    predictions = []
    for heldout in MODELS:
        for stage in STAGES:
            donor = [r for r in rows if r["model"] != heldout and r["stage"] == stage]
            target = [r for r in rows if r["model"] == heldout and r["stage"] == stage]
            if len(donor) != 150 or len(target) != 50:
                raise ValueError(f"unexpected model-held-out split: {heldout}, {stage}")
            overall_p90 = float(
                np.quantile(
                    [r["actual_remaining_s"] for r in donor],
                    QUANTILE,
                    method="linear",
                )
            )
            cache_p90 = {
                condition: float(
                    np.quantile(
                        [
                            r["actual_remaining_s"]
                            for r in donor
                            if r["condition"] == condition
                        ],
                        QUANTILE,
                        method="linear",
                    )
                )
                for condition in CONDITIONS
            }
            no_cache = quantile_predict(donor, target, use_cache=False)
            oracle_cache = quantile_predict(donor, target, use_cache=True)
            for index, row in enumerate(target):
                predictions.append(
                    {
                        **row,
                        "phase_p90": overall_p90,
                        "phase_cache_p90": cache_p90[row["condition"]],
                        "phase_quantile": float(no_cache[index]),
                        "phase_cache_quantile": float(oracle_cache[index]),
                    }
                )
    return sorted(
        predictions,
        key=lambda x: (
            MODELS.index(x["model"]),
            CONDITIONS.index(x["condition"]),
            x["trial"],
            STAGES.index(x["stage"]),
        ),
    )


def score(rows: list[dict], method: str) -> dict:
    actual = np.asarray([row["actual_remaining_s"] for row in rows], dtype=float)
    pred = np.asarray([row[method] for row in rows], dtype=float)
    error = actual - pred
    excess = np.maximum(error, 0.0)
    pinball = np.maximum(QUANTILE * error, (QUANTILE - 1) * error)
    return {
        "n": len(rows),
        "pinball_s": float(np.mean(pinball)),
        "coverage": float(np.mean(error <= 0.0)),
        "mean_under_s": float(np.mean(excess)),
        "max_under_s": float(np.max(excess)),
        "under_gt5_rate": float(np.mean(error > 5.0)),
        "under_gt10_rate": float(np.mean(error > 10.0)),
    }


def scores_for(rows: list[dict]) -> dict:
    return {method: score(rows, method) for method in METHODS}


def summarize(rows: list[dict]) -> dict:
    by_model = {
        model: scores_for([r for r in rows if r["model"] == model]) for model in MODELS
    }
    by_stage = {
        stage: scores_for([r for r in rows if r["stage"] == stage]) for stage in STAGES
    }
    by_condition = {
        condition: scores_for([r for r in rows if r["condition"] == condition])
        for condition in CONDITIONS
    }
    by_model_stage = {
        model: {
            stage: scores_for(
                [r for r in rows if r["model"] == model and r["stage"] == stage]
            )
            for stage in STAGES
        }
        for model in MODELS
    }
    by_stage_condition = {
        stage: {
            condition: scores_for(
                [r for r in rows if r["stage"] == stage and r["condition"] == condition]
            )
            for condition in CONDITIONS
        }
        for stage in STAGES
    }
    overall = scores_for(rows)
    gates = {}
    for name, base in (
        ("phase_quantile", "phase_p90"),
        ("phase_cache_quantile", "phase_cache_p90"),
    ):
        wins = sum(
            by_model[model][name]["pinball_s"] < by_model[model][base]["pinball_s"]
            for model in MODELS
        )
        candidate, baseline = overall[name], overall[base]
        gates[name] = {
            "baseline": base,
            "pinball_relative_improvement": (
                1.0 - candidate["pinball_s"] / baseline["pinball_s"]
            ),
            "under_gt5_rate_difference": (
                candidate["under_gt5_rate"] - baseline["under_gt5_rate"]
            ),
            "model_wins": wins,
            "passes": (
                candidate["pinball_s"] <= baseline["pinball_s"] * 0.95
                and candidate["under_gt5_rate"] <= baseline["under_gt5_rate"]
                and wins >= 3
            ),
        }
    return {
        "overall": overall,
        "by_model": by_model,
        "by_stage": by_stage,
        "by_condition": by_condition,
        "by_model_stage": by_model_stage,
        "by_stage_condition": by_stage_condition,
        "gates": gates,
    }


def write_results(
    output: Path, input_path: Path, records: list[dict], predictions: list[dict]
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    pred_path = output / "predictions.csv"
    metrics_path = output / "metrics.json"
    if pred_path.exists() or metrics_path.exists():
        raise FileExistsError("score artifacts already exist; use a new output directory")
    source_count = dict(sorted(Counter(r["source"] for r in records).items()))
    results = {
        "source_sha256": sha256_file(input_path),
        "protocol_sha256": sha256_file(PROTOCOL),
        "script_sha256": sha256_file(Path(__file__)),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "sklearn": sklearn.__version__,
        "source_sweeps": source_count,
        "models": list(MODELS),
        "conditions": list(CONDITIONS),
        "stages": list(STAGES),
        "quantile": QUANTILE,
        "alpha": ALPHA,
        "trial_count": len(records),
        "stage_row_count": len(predictions),
        "stage_event_available_at_observed": False,
        "cache_state_operationally_observed": False,
        "scores": summarize(predictions),
    }
    with pred_path.open("x", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(predictions[0]))
        writer.writeheader()
        writer.writerows(predictions)
    results["predictions_sha256"] = sha256_file(pred_path)
    with metrics_path.open("x") as destination:
        json.dump(results, destination, ensure_ascii=False, indent=2, sort_keys=True)
        destination.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = read_trials(args.input)
    rows = build_stage_rows(records)
    predictions = predict_heldout(rows)
    write_results(args.output, args.input, records, predictions)
    print(args.output / "metrics.json")


if __name__ == "__main__":
    main()
