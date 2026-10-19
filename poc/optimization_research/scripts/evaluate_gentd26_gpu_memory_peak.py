"""Forecast the next 32 recorded GenTD26 pod GPU-memory samples' maximum.

The target is observed container-level memory under the existing deployment.
It is not a minimum feasible VRAM label or a scale-action outcome.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/gentd26"
OUTPUT = ROOT / "artifacts/gentd26_gpu_memory_peak_v1"
PROTOCOL = ROOT / "reports/gentd26_gpu_memory_peak_protocol.md"
PROTOCOL_SHA256 = "213a9183c86d712f74f291d0e1420d73e6ff112a88b2bbb935629d8bba07d030"
SOURCE_SHA256 = {
    "pod_gpu_memory_used_bytes_anon.csv": "0edc1ad35284b6b7236bdd2e69795052bedc302d7b295a1a32c5bb9ff4579596",
    "pod_gpu_duty_cycle_anon.csv": "b3a91d9bd9141156e66da6b8d34520602246ee043ac60b0d2172e689a3919f99",
}
T0 = 1662858720
FIT_END = T0 + 8 * 3600
VAL_END = T0 + 12 * 3600
SECOND_HALF = T0 + 17 * 3600
PERIOD = 57
STRIDE = 5
PAST = 64
LAG = 6
FUTURE = 32
GIB = float(2**30)
Q = 0.9
BASELINE_NAMES = ("recent_peak", "global_residual_q90", "container_completed_q90", "past4h_peak")
MEMORY_FEATURES = (
    "memory_last", "memory_mean_6", "memory_mean_16", "memory_mean_32", "memory_mean_64",
    "memory_max_6", "memory_max_16", "memory_max_32", "memory_max_64",
    "memory_std_16", "memory_std_64", "memory_min_64", "memory_q90_64",
    "memory_zero_fraction_64", "memory_change_16",
)
DUTY_FEATURES = (
    "duty_mean_6", "duty_mean_16", "duty_mean_32", "duty_mean_64",
    "duty_max_6", "duty_max_16", "duty_max_32", "duty_max_64",
    "duty_missing_fraction_6", "duty_missing_fraction_16",
    "duty_missing_fraction_32", "duty_missing_fraction_64", "duty_zero_fraction_64",
)
MODEL_KWARGS = dict(
    loss="quantile", quantile=Q, max_iter=120, max_leaf_nodes=15,
    min_samples_leaf=80, learning_rate=0.05, l2_regularization=10,
    random_state=42,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_frozen_inputs(data: Path) -> None:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Frozen protocol changed")
    for filename, expected in SOURCE_SHA256.items():
        if sha256(data / filename) != expected:
            raise ValueError(f"Source changed: {filename}")


def held_out(container_ip: str) -> bool:
    code = int.from_bytes(hashlib.sha256(container_ip.encode()).digest()[:8], "big")
    return code % 5 == 0


def load_measurements(data: Path) -> pd.DataFrame:
    """Exact same-key join; missing duty stays NaN, including around genuine zeros."""
    memory = pd.read_csv(data / "pod_gpu_memory_used_bytes_anon.csv")
    duty = pd.read_csv(data / "pod_gpu_duty_cycle_anon.csv")
    required = {"timestamp_anon", "value", "container_ip"}
    for name, frame in (("memory", memory), ("duty", duty)):
        if not required.issubset(frame):
            raise ValueError(f"{name}: missing required columns")
        if frame.container_ip.isna().any() or frame.duplicated(["container_ip", "timestamp_anon"]).any():
            raise ValueError(f"{name}: missing or duplicate container/time key")
        if not np.isfinite(frame.timestamp_anon.to_numpy(float)).all():
            raise ValueError(f"{name}: invalid timestamps")
        if not np.isfinite(frame.value.to_numpy(float)).all() or frame.value.lt(0).any():
            raise ValueError(f"{name}: invalid measurements")
    if float(memory.timestamp_anon.min()) != T0:
        raise ValueError("Trace start changed")
    if duty.value.gt(100).any():
        raise ValueError("Duty exceeds its documented percentage range")
    memory = memory.rename(columns={
        "timestamp_anon": "time", "value": "memory_bytes", "container_ip": "cid",
    })
    duty = duty.rename(columns={
        "timestamp_anon": "time", "value": "duty_percent", "container_ip": "cid",
    })
    joined = memory.merge(
        duty[["cid", "time", "duty_percent"]], on=["cid", "time"],
        how="left", validate="one_to_one",
    )
    return joined.sort_values(["cid", "time"], kind="stable").reset_index(drop=True)


def split_of(decision_time: float, future_end_time: float) -> str:
    if future_end_time <= FIT_END:
        return "fit"
    if decision_time >= FIT_END and future_end_time <= VAL_END:
        return "validation"
    if decision_time >= VAL_END:
        return "test"
    return "boundary"


def _observed_stats(values: np.ndarray) -> tuple[float, float]:
    seen = values[np.isfinite(values)]
    if len(seen) == 0:
        return np.nan, np.nan
    return float(np.mean(seen)), float(np.max(seen))


def history_features(memory_gib: np.ndarray, duty: np.ndarray) -> dict[str, float]:
    """Only the 64 readings ending 342 seconds before decision are accepted."""
    if len(memory_gib) != PAST or len(duty) != PAST:
        raise ValueError("Wrong history length")
    if not np.isfinite(memory_gib).all() or (memory_gib < 0).any():
        raise ValueError("Invalid memory history")
    feature: dict[str, float] = {"memory_last": float(memory_gib[-1])}
    for count in (6, 16, 32, 64):
        part = memory_gib[-count:]
        feature[f"memory_mean_{count}"] = float(np.mean(part))
        feature[f"memory_max_{count}"] = float(np.max(part))
        duty_part = duty[-count:]
        feature[f"duty_mean_{count}"], feature[f"duty_max_{count}"] = _observed_stats(duty_part)
        feature[f"duty_missing_fraction_{count}"] = float(np.mean(~np.isfinite(duty_part)))
    feature.update({
        "memory_std_16": float(np.std(memory_gib[-16:])),
        "memory_std_64": float(np.std(memory_gib)),
        "memory_min_64": float(np.min(memory_gib)),
        "memory_q90_64": float(np.quantile(memory_gib, Q)),
        "memory_zero_fraction_64": float(np.mean(memory_gib == 0)),
        "memory_change_16": float(memory_gib[-1] - memory_gib[-17]),
        "duty_zero_fraction_64": float(np.mean(duty == 0)),
    })
    return feature


def build_episodes(data: Path, hide_heldout_and_test_labels: bool) -> pd.DataFrame:
    """Use future timestamps for cohort quality, never future values as features."""
    measurements = load_measurements(data)
    rows: list[dict] = []
    for cid, group in measurements.groupby("cid", sort=True):
        times = group.time.to_numpy(float)
        memory = group.memory_bytes.to_numpy(float) / GIB
        duty = group.duty_percent.to_numpy(float)
        first_time = float(times[0])
        holdout = held_out(cid)
        for index in range(PAST + LAG - 1, len(group) - FUTURE, STRIDE):
            segment = times[index - (PAST + LAG - 1):index + FUTURE + 1]
            if len(segment) != PAST + LAG + FUTURE or not np.all(np.diff(segment) == PERIOD):
                continue
            decision_time = float(times[index])
            input_index = index - LAG
            future_end = float(times[index + FUTURE])
            split = split_of(decision_time, future_end)
            past_memory = memory[input_index - PAST + 1:input_index + 1]
            past_duty = duty[input_index - PAST + 1:input_index + 1]
            feature = history_features(past_memory, past_duty)
            if any(name not in feature for name in MEMORY_FEATURES + DUTY_FEATURES):
                raise AssertionError("Feature schema incomplete")
            raw4_start = int(np.searchsorted(times, times[input_index] - 4 * 3600, side="left"))
            raw4 = memory[raw4_start:input_index + 1]
            reveal = not hide_heldout_and_test_labels or (not holdout and future_end <= VAL_END)
            future = memory[index + 1:index + FUTURE + 1] if reveal else None
            rows.append({
                "cid": cid, "decision_time": decision_time,
                "last_input_time": float(times[input_index]),
                "future_end_time": future_end, "first_seen_time": first_time,
                "split": split, "heldout": holdout,
                "recent_peak": float(np.max(past_memory)),
                "past4h_peak": float(np.max(raw4)) if len(raw4) >= 60 else float(np.max(past_memory)),
                "y": float(np.max(future)) if future is not None else np.nan,
                "future_has_zero": bool(np.any(future == 0)) if future is not None else False,
                **feature,
            })
    if not rows:
        raise ValueError("No eligible episodes")
    frame = pd.DataFrame(rows).sort_values(["cid", "decision_time"], kind="stable").reset_index(drop=True)
    if (frame.last_input_time > frame.decision_time - 300).any():
        raise AssertionError("Telemetry lag violated")
    if frame.duplicated(["cid", "decision_time"]).any():
        raise AssertionError("Repeated decision")
    return frame


def pinball_each(y: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    difference = y - prediction
    return np.maximum(Q * difference, (Q - 1.0) * difference)


def quality_score(y: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    if len(y) == 0 or len(y) != len(prediction) or not np.isfinite(y).all() or not np.isfinite(prediction).all():
        raise ValueError("Invalid score rows")
    if (prediction < 0).any():
        raise ValueError("Negative peak prediction")
    return {
        "n": int(len(y)),
        "pinball_gib": float(np.mean(pinball_each(y, prediction))),
        "coverage": float(np.mean(y <= prediction)),
        "mean_excess_gib": float(np.mean(np.maximum(prediction - y, 0))),
        "mean_prediction_gib": float(np.mean(prediction)),
        "mean_observed_gib": float(np.mean(y)),
    }


def online_container_residuals(frame: pd.DataFrame, global_residual_q90: float) -> np.ndarray:
    """At decision t, use only same-ID residual labels whose horizon ended by t."""
    result = np.empty(len(frame), dtype=float)
    for _, group in frame.groupby("cid", sort=False):
        indices = group.index.to_numpy()
        times = group.decision_time.to_numpy(float)
        end_times = group.future_end_time.to_numpy(float)
        residual = (group.y - group.recent_peak).to_numpy(float)
        completed: deque[tuple[float, float]] = deque()
        available = 0
        for offset, row_index in enumerate(indices):
            current = times[offset]
            while available < len(group) and end_times[available] <= current:
                if np.isfinite(residual[available]):
                    completed.append((end_times[available], residual[available]))
                available += 1
            while completed and completed[0][0] < current - 4 * 3600:
                completed.popleft()
            values = [value for _, value in completed]
            if len(values) < 5:
                result[row_index] = global_residual_q90
            else:
                own = float(np.quantile(values, Q))
                weight = len(values) / (len(values) + 20)
                result[row_index] = weight * own + (1 - weight) * global_residual_q90
    return result


def baseline_predictions(frame: pd.DataFrame, global_residual_q90: float) -> dict[str, np.ndarray]:
    recent = frame.recent_peak.to_numpy(float)
    local = online_container_residuals(frame, global_residual_q90)
    return {
        "recent_peak": recent.copy(),
        "global_residual_q90": np.maximum(0, recent + global_residual_q90),
        "container_completed_q90": np.maximum(0, recent + local),
        "past4h_peak": frame.past4h_peak.to_numpy(float),
    }


def _cohort(frame: pd.DataFrame, split: str, heldout: bool) -> np.ndarray:
    return (frame.split.eq(split) & frame.heldout.eq(heldout)).to_numpy(bool)


def _metadata_counts(frame: pd.DataFrame) -> dict:
    result = {}
    for split in ("fit", "validation", "test", "boundary"):
        for held in (False, True):
            selected = frame.loc[_cohort(frame, split, held)]
            result[f"{split}_{'heldout' if held else 'development'}"] = {
                "rows": int(len(selected)), "containers": int(selected.cid.nunique()),
                "duty_at_least_half_observed": float(np.mean(selected.duty_missing_fraction_64 <= 0.5))
                if len(selected) else None,
            }
    return result


def _model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(**MODEL_KWARGS)


def _model_predictions(model: HistGradientBoostingRegressor, frame: pd.DataFrame,
                       features: tuple[str, ...]) -> np.ndarray:
    residual = model.predict(frame.loc[:, list(features)])
    return np.maximum(0, frame.recent_peak.to_numpy(float) + residual)


def select(data: Path, output: Path) -> dict:
    validate_frozen_inputs(data)
    if (output / "selection_manifest.json").exists():
        raise FileExistsError("Selection manifest already exists; preserve the frozen experiment")
    frame = build_episodes(data, hide_heldout_and_test_labels=True)
    fit_mask = _cohort(frame, "fit", False)
    val_mask = _cohort(frame, "validation", False)
    if fit_mask.sum() < 1000 or val_mask.sum() < 1000:
        raise ValueError("Insufficient fit/validation rows")
    if frame.loc[frame.heldout | frame.split.eq("test"), "y"].notna().any():
        raise AssertionError("Held-out or test targets visible during selection")
    fit = frame.loc[fit_mask]
    validation = frame.loc[val_mask]
    global_q90 = float(np.quantile((fit.y - fit.recent_peak).to_numpy(float), Q))
    baselines = baseline_predictions(frame, global_q90)
    val_baseline_scores = {
        name: quality_score(validation.y.to_numpy(float), predictions[val_mask])
        for name, predictions in baselines.items()
    }
    chosen_baseline = min(BASELINE_NAMES, key=lambda name: val_baseline_scores[name]["pinball_gib"])
    memory_model = _model().fit(fit.loc[:, list(MEMORY_FEATURES)], (fit.y - fit.recent_peak).to_numpy(float))
    duty_model = _model().fit(fit.loc[:, list(MEMORY_FEATURES + DUTY_FEATURES)], (fit.y - fit.recent_peak).to_numpy(float))
    memory_prediction = _model_predictions(memory_model, validation, MEMORY_FEATURES)
    duty_prediction = _model_predictions(duty_model, validation, MEMORY_FEATURES + DUTY_FEATURES)
    val_model_scores = {
        "memory_only": quality_score(validation.y.to_numpy(float), memory_prediction),
        "memory_plus_duty": quality_score(validation.y.to_numpy(float), duty_prediction),
    }
    selected_model = (
        "memory_plus_duty" if val_model_scores["memory_plus_duty"]["pinball_gib"]
        <= 0.98 * val_model_scores["memory_only"]["pinball_gib"] else "memory_only"
    )
    output.mkdir(parents=True, exist_ok=True)
    model_paths = {
        "memory_only": output / "memory_only_model.joblib",
        "memory_plus_duty": output / "memory_plus_duty_model.joblib",
    }
    joblib.dump(memory_model, model_paths["memory_only"])
    joblib.dump(duty_model, model_paths["memory_plus_duty"])
    val_predictions = validation[["cid", "decision_time", "last_input_time", "future_end_time", "y", "recent_peak"]].copy()
    for name, predictions in baselines.items():
        val_predictions[name] = predictions[val_mask]
    val_predictions["memory_only"] = memory_prediction
    val_predictions["memory_plus_duty"] = duty_prediction
    val_path = output / "validation_predictions.parquet"
    val_predictions.to_parquet(val_path, index=False)
    dry_run = {
        "source_sha256": SOURCE_SHA256,
        "protocol_sha256": PROTOCOL_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "cohorts": _metadata_counts(frame),
        "contains_heldout_or_test_targets": False,
    }
    dry_path = output / "dry_run.json"
    dry_path.write_text(json.dumps(dry_run, indent=2) + "\n")
    manifest = {
        "protocol": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": PROTOCOL_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_sha256": SOURCE_SHA256,
        "dry_run_sha256": sha256(dry_path),
        "validation_predictions_sha256": sha256(val_path),
        "sklearn_version": sklearn.__version__,
        "t0": T0, "fit_end": FIT_END, "validation_end": VAL_END,
        "memory_features": MEMORY_FEATURES,
        "duty_features": DUTY_FEATURES,
        "model_kwargs": MODEL_KWARGS,
        "fit_rows": int(fit_mask.sum()),
        "validation_rows": int(val_mask.sum()),
        "global_residual_q90_gib": global_q90,
        "validation_baselines": val_baseline_scores,
        "chosen_baseline": chosen_baseline,
        "validation_models": val_model_scores,
        "selected_model": selected_model,
        "model_sha256": {name: sha256(path) for name, path in model_paths.items()},
        "contains_heldout_or_test_targets": False,
    }
    (output / "selection_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _container_macro(frame: pd.DataFrame, prediction: np.ndarray) -> float:
    scored = pd.DataFrame({
        "cid": frame.cid.to_numpy(),
        "loss": pinball_each(frame.y.to_numpy(float), prediction),
    })
    return float(scored.groupby("cid").loss.mean().mean())


def _bootstrap_containers(frame: pd.DataFrame, learned: np.ndarray,
                          baseline: np.ndarray) -> dict:
    y = frame.y.to_numpy(float)
    delta = pinball_each(y, learned) - pinball_each(y, baseline)
    groups = [np.flatnonzero(frame.cid.to_numpy() == cid) for cid in frame.cid.unique()]
    generator = np.random.default_rng(43)
    draws = np.empty(2000)
    for repeat in range(len(draws)):
        picked = generator.integers(0, len(groups), len(groups))
        positions = np.concatenate([groups[index] for index in picked])
        draws[repeat] = float(np.mean(delta[positions]))
    return {
        "point_difference_gib": float(np.mean(delta)),
        "interval_95_within_day_gib": np.quantile(draws, [0.025, 0.975]).tolist(),
        "containers": len(groups), "repeats": len(draws), "seed": 43,
    }


def _score_slice(frame: pd.DataFrame, preds: dict[str, np.ndarray], mask: np.ndarray) -> dict:
    y = frame.loc[mask, "y"].to_numpy(float)
    return {name: quality_score(y, values[mask]) for name, values in preds.items()}


def score_test(data: Path, output: Path) -> dict:
    manifest_path = output / "selection_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    validate_frozen_inputs(data)
    if sha256(Path(__file__)) != manifest["script_sha256"]:
        raise ValueError("Scoring code changed after selection")
    if manifest["source_sha256"] != SOURCE_SHA256 or manifest["protocol_sha256"] != PROTOCOL_SHA256:
        raise ValueError("Frozen source/protocol changed")
    if sha256(output / "dry_run.json") != manifest["dry_run_sha256"]:
        raise ValueError("Dry-run changed after selection")
    if sha256(output / "validation_predictions.parquet") != manifest["validation_predictions_sha256"]:
        raise ValueError("Validation predictions changed after selection")
    for name, expected in manifest["model_sha256"].items():
        if sha256(output / f"{name}_model.joblib") != expected:
            raise ValueError(f"Model changed after selection: {name}")
    if (output / "test_metrics.json").exists():
        raise FileExistsError("Test already scored; preserve the first result")
    frame = build_episodes(data, hide_heldout_and_test_labels=False)
    dry_run = json.loads((output / "dry_run.json").read_text())
    if _metadata_counts(frame) != dry_run["cohorts"]:
        raise ValueError("Cohort metadata changed after selection")
    baseline = baseline_predictions(frame, float(manifest["global_residual_q90_gib"]))
    memory_model = joblib.load(output / "memory_only_model.joblib")
    duty_model = joblib.load(output / "memory_plus_duty_model.joblib")
    learned = {
        "memory_only": _model_predictions(memory_model, frame, MEMORY_FEATURES),
        "memory_plus_duty": _model_predictions(duty_model, frame, MEMORY_FEATURES + DUTY_FEATURES),
    }
    predictions = {**baseline, **learned}
    primary_mask = _cohort(frame, "test", True)
    secondary_mask = _cohort(frame, "test", False)
    primary = frame.loc[primary_mask].reset_index(drop=True)
    if not len(primary):
        raise ValueError("No primary test rows")
    primary_predictions = {name: values[primary_mask] for name, values in predictions.items()}
    selected = manifest["selected_model"]
    chosen_baseline = manifest["chosen_baseline"]
    primary_scores = _score_slice(primary, primary_predictions, np.ones(len(primary), dtype=bool))
    secondary = frame.loc[secondary_mask].reset_index(drop=True)
    secondary_predictions = {name: values[secondary_mask] for name, values in predictions.items()}
    secondary_scores = _score_slice(secondary, secondary_predictions, np.ones(len(secondary), dtype=bool))
    half_masks = {
        "first": primary.decision_time.lt(SECOND_HALF).to_numpy(bool),
        "second": primary.decision_time.ge(SECOND_HALF).to_numpy(bool),
    }
    halves = {name: _score_slice(primary, primary_predictions, mask) for name, mask in half_masks.items()}
    per_container = pd.DataFrame([
        {
            "cid": cid, "rows": len(group),
            "selected_pinball_gib": float(np.mean(pinball_each(
                group.y.to_numpy(float), primary_predictions[selected][group.index.to_numpy()]))),
            "baseline_pinball_gib": float(np.mean(pinball_each(
                group.y.to_numpy(float), primary_predictions[chosen_baseline][group.index.to_numpy()]))),
        }
        for cid, group in primary.groupby("cid", sort=True)
    ])
    per_container.to_csv(output / "primary_per_container.csv", index=False)
    bootstrap = _bootstrap_containers(
        primary, primary_predictions[selected], primary_predictions[chosen_baseline]
    )
    spike = primary.y.to_numpy(float) > primary.recent_peak.to_numpy(float) + 2.0
    subgroup_masks = {
        "last_input_zero": primary.memory_last.eq(0).to_numpy(bool),
        "future_contains_zero": primary.future_has_zero.to_numpy(bool),
        "duty_more_than_half_missing": primary.duty_missing_fraction_64.gt(0.5).to_numpy(bool),
        "new_container_after_fit": primary.first_seen_time.ge(FIT_END).to_numpy(bool),
        "future_peak_above_recent_plus_2gib": spike,
    }
    subgroups = {}
    for name, mask in subgroup_masks.items():
        subgroups[name] = {
            "rows": int(mask.sum()), "containers": int(primary.loc[mask, "cid"].nunique()),
            "selected": quality_score(primary.loc[mask, "y"].to_numpy(float), primary_predictions[selected][mask])
            if mask.any() else None,
            "baseline": quality_score(primary.loc[mask, "y"].to_numpy(float), primary_predictions[chosen_baseline][mask])
            if mask.any() else None,
        }
    baseline_loss = primary_scores[chosen_baseline]["pinball_gib"]
    selected_loss = primary_scores[selected]["pinball_gib"]
    duty_incremental = (
        selected != "memory_plus_duty"
        or selected_loss <= 0.98 * primary_scores["memory_only"]["pinball_gib"]
    )
    gates = {
        "primary_rows_at_least_2000": len(primary) >= 2000,
        "primary_containers_at_least_20": primary.cid.nunique() >= 20,
        "spikes_at_least_50": int(spike.sum()) >= 50,
        "pinball_improves_baseline_5pct": selected_loss <= 0.95 * baseline_loss,
        "container_macro_improves": _container_macro(primary, primary_predictions[selected])
        < _container_macro(primary, primary_predictions[chosen_baseline]),
        "both_halves_pinball_improve": all(
            halves[half][selected]["pinball_gib"] < halves[half][chosen_baseline]["pinball_gib"]
            for half in ("first", "second")
        ),
        "overall_coverage_at_least_88pct": primary_scores[selected]["coverage"] >= 0.88,
        "each_half_coverage_at_least_85pct": all(
            halves[half][selected]["coverage"] >= 0.85 for half in ("first", "second")
        ),
        "container_bootstrap_upper_below_zero": bootstrap["interval_95_within_day_gib"][1] < 0,
        "duty_incremental_if_selected": duty_incremental,
    }
    recorded = frame.loc[frame.split.eq("test")].copy()
    for name, values in predictions.items():
        recorded[name + "_p90_gib"] = values[frame.split.eq("test").to_numpy(bool)]
    prediction_path = output / "test_predictions.parquet"
    recorded.to_parquet(prediction_path, index=False)
    metrics = {
        "selection_manifest_sha256": sha256(manifest_path),
        "protocol_sha256": PROTOCOL_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_sha256": SOURCE_SHA256,
        "target": "next 32 observed 57-second container GPU-memory samples' maximum, published bytes / 2^30",
        "chosen_baseline": chosen_baseline,
        "selected_model": selected,
        "primary_scores": primary_scores,
        "secondary_scores": secondary_scores,
        "primary_halves": halves,
        "primary_container_macro_pinball_gib": {
            name: _container_macro(primary, values) for name, values in primary_predictions.items()
        },
        "primary_container_bootstrap_selected_minus_baseline": bootstrap,
        "primary_subgroups": subgroups,
        "gates": gates,
        "adopt_for_research_decision": bool(all(gates.values())),
        "test_predictions_sha256": sha256(prediction_path),
        "primary_per_container_sha256": sha256(output / "primary_per_container.csv"),
        "limitations": [
            "One anonymized serving day with known feasibility-peeked time trend; test is not fully blind",
            "Telemetry availability delay of 342 seconds is an assumption, not logged receipt time",
            "GPU capacity/allocation, OOM, VM action, and user SLO are absent",
            "Overlapping 30-minute outcome windows make adjacent decisions dependent",
        ],
    }
    (output / "test_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("select", "score"), required=True)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = select(arguments.data, arguments.output) if arguments.stage == "select" else score_test(arguments.data, arguments.output)
    if arguments.stage == "select":
        print(json.dumps({
            "fit_rows": result["fit_rows"], "validation_rows": result["validation_rows"],
            "chosen_baseline": result["chosen_baseline"], "selected_model": result["selected_model"],
            "validation_baselines": result["validation_baselines"],
            "validation_models": result["validation_models"],
        }, indent=2))
    else:
        print(json.dumps({
            "chosen_baseline": result["chosen_baseline"], "selected_model": result["selected_model"],
            "primary_scores": result["primary_scores"], "gates": result["gates"],
            "adopt_for_research_decision": result["adopt_for_research_decision"],
        }, indent=2))
