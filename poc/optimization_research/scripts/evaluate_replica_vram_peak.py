"""Exploratory same-host GPU2 per-device reserved-memory peak forecast.

GPU1 measurement is available before GPU2 starts. The target is the larger
per-process PyTorch reserved peak of the two GPU2 children, never minimum
VRAM, OOM feasibility, or an executed VM scaling outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/audit_v1/measurements.parquet"
PROTOCOL = ROOT / "reports/replica_vram_peak_protocol.md"
OUTPUT = ROOT / "artifacts/replica_vram_peak_v1"
SNAPSHOT_SHA256 = "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"
PROTOCOL_SHA256 = "cd8bb79aa9366b62b376feec1af34d2c1c3c716402a851e7e193f0a738f7faa5"
CAMPAIGNS = (
    "khu-hf-local-exhaustive",
    "khu-hf-local-novel-matrix",
    "khu-hf-local-space-filling",
)
EXPECTED_PAIRS = dict(zip(CAMPAIGNS, (2300, 72, 176)))
PAIR_KEY = ("campaign", "host", "model_id", "model_revision", "workload_group", "cpu_core_limit")
STATIC_EQUAL = ("batch_size", "max_length", "request_concurrency", "repeats",
                "dataset", "cpu_threads", "workload_task", "input_encoding")
FEATURE_COLUMNS = (
    "log1p_gpu1_peak_mib", "log1p_gpu1_parameter_mib", "log_batch_size",
    "log_max_length", "log1p_request_concurrency", "log_repeats",
    "log_cpu_core_limit",
)
METHODS = ("unchanged_gpu1", "donor_q90_delta", "hgb_q90_delta")
HGB_PARAMS = dict(loss="quantile", quantile=0.9, max_iter=80,
                  max_leaf_nodes=8, min_samples_leaf=40,
                  l2_regularization=10.0, learning_rate=0.05,
                  early_stopping=False, random_state=42)
MIB = float(1024 ** 2)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def positive_int(value: object, name: str) -> int:
    if (isinstance(value, bool) or
            not isinstance(value, (int, float, np.integer, np.floating)) or
            not np.isfinite(value) or value <= 0 or float(value) != int(value)):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _record_peak_bytes(record: dict, name: str) -> int:
    value = record.get("gpu_memory_reserved_bytes")
    return positive_int(value, f"{name}.gpu_memory_reserved_bytes")


def _pair_to_row(first: dict, second: dict) -> dict:
    for column in STATIC_EQUAL:
        if first[column] != second[column]:
            raise ValueError(f"GPU1/GPU2 static input mismatch: {column}")
    if first["host"] != "icns" or second["host"] != "icns":
        raise ValueError("Only the icns host is supported")
    if first["device"] != "cuda" or second["device"] != "cuda":
        raise ValueError("Expected CUDA physical measurements")
    if first["cpu_threads"] != first["cpu_core_limit"]:
        raise ValueError("CPU thread/core mismatch")
    if json.loads(first["gpu_indices_json"]) != [0] or json.loads(second["gpu_indices_json"]) != [0, 1]:
        raise ValueError("Unexpected physical GPU indices")
    if pd.Timestamp(first["finished_at"]) >= pd.Timestamp(second["started_at"]):
        raise ValueError("GPU1 measurement must finish before GPU2 begins")

    gpu1_result = json.loads(first["result_json"])
    gpu2_result = json.loads(second["result_json"])
    if not isinstance(gpu1_result, dict) or not isinstance(gpu2_result, dict):
        raise ValueError("Expected JSON result objects")
    gpu1_bytes = _record_peak_bytes(gpu1_result, "gpu1")
    if gpu1_bytes != positive_int(first["gpu_memory_reserved_bytes"], "gpu1 column"):
        raise ValueError("GPU1 result/column memory mismatch")
    parameter_bytes = positive_int(gpu1_result.get("model_parameter_bytes"),
                                   "gpu1.model_parameter_bytes")
    replicas = gpu2_result.get("replica_results")
    if not isinstance(replicas, list) or len(replicas) != 2 or any(not isinstance(r, dict) for r in replicas):
        raise ValueError("GPU2 must have exactly two per-process results")
    indices = [r.get("gpu_index") for r in replicas]
    if any(isinstance(i, bool) or not isinstance(i, int) for i in indices) or set(indices) != {0, 1}:
        raise ValueError("GPU2 per-process GPU indices must be {0,1}")
    peaks = [_record_peak_bytes(r, "gpu2 replica") for r in replicas]
    gpu2_sum = positive_int(second["gpu_memory_reserved_bytes"], "gpu2 column")
    if sum(peaks) != gpu2_sum or gpu2_result.get("gpu_memory_reserved_bytes") != gpu2_sum:
        raise ValueError("GPU2 top-level memory must equal per-process sum")
    for column in ("batch_size", "max_length", "request_concurrency", "repeats", "cpu_core_limit"):
        positive_int(first[column], column)
    return {
        **{column: first[column] for column in PAIR_KEY},
        "workload_task": first["workload_task"],
        "gpu1_execution_id": first["execution_id"],
        "gpu2_execution_id": second["execution_id"],
        "gpu1_peak_mib": gpu1_bytes / MIB,
        "gpu1_parameter_mib": parameter_bytes / MIB,
        "gpu2_max_peak_mib": max(peaks) / MIB,
        "gpu2_min_peak_mib": min(peaks) / MIB,
        **{column: first[column] for column in
           ("batch_size", "max_length", "request_concurrency", "repeats")},
    }


def extract_pairs(snapshot: pd.DataFrame, campaign: str, expected: int | None = None) -> pd.DataFrame:
    """Build physical GPU1/GPU2 pairs; reject duplicates or incomplete groups."""
    frame = snapshot.loc[(snapshot.campaign == campaign) & snapshot.eligible &
                         (snapshot.success == 1) & (snapshot.device == "cuda") &
                         snapshot.replica_count.isin([1, 2])]
    if frame.empty:
        raise ValueError(f"No eligible CUDA rows in {campaign}")
    groups: dict[tuple, dict[int, dict]] = {}
    for row in frame.to_dict("records"):
        key = tuple(row[column] for column in PAIR_KEY)
        group = groups.setdefault(key, {})
        replica = positive_int(row["replica_count"], "replica_count")
        if replica in group:
            raise ValueError(f"Duplicate candidate for {key}, replicas={replica}")
        group[replica] = row
    pairs = []
    for key in sorted(groups):
        group = groups[key]
        if set(group) != {1, 2}:
            raise ValueError(f"Incomplete GPU1/GPU2 pair: {key}")
        pairs.append(_pair_to_row(group[1], group[2]))
    result = pd.DataFrame(pairs)
    if expected is not None and len(result) != expected:
        raise ValueError(f"Expected {expected} pairs in {campaign}, got {len(result)}")
    if result.duplicated(list(PAIR_KEY)).any():
        raise ValueError("Pair keys are not unique")
    return result


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Only GPU1 observations and pre-run profile fields enter the estimator."""
    raw = frame[["gpu1_peak_mib", "gpu1_parameter_mib", "batch_size", "max_length",
                 "request_concurrency", "repeats", "cpu_core_limit"]].astype(float)
    if not np.isfinite(raw.to_numpy()).all() or (raw <= 0).any().any():
        raise ValueError("Predictor inputs must be finite and positive")
    features = pd.DataFrame(index=frame.index)
    features["log1p_gpu1_peak_mib"] = np.log1p(raw.gpu1_peak_mib)
    features["log1p_gpu1_parameter_mib"] = np.log1p(raw.gpu1_parameter_mib)
    features["log_batch_size"] = np.log(raw.batch_size)
    features["log_max_length"] = np.log(raw.max_length)
    features["log1p_request_concurrency"] = np.log1p(raw.request_concurrency)
    features["log_repeats"] = np.log(raw.repeats)
    features["log_cpu_core_limit"] = np.log(raw.cpu_core_limit)
    return features[list(FEATURE_COLUMNS)]


def fit_fold(donor: pd.DataFrame, held_model_id: str) -> tuple[float, HistGradientBoostingRegressor]:
    if donor.empty or held_model_id in set(donor.model_id):
        raise ValueError("Held model included in donor training or donor empty")
    if set(donor.workload_task) != {"inference"} or donor.model_id.nunique() != 7:
        raise ValueError("Expected seven encoder donor models")
    residual = donor.gpu2_max_peak_mib.to_numpy(dtype=float) - donor.gpu1_peak_mib.to_numpy(dtype=float)
    q90 = float(np.quantile(residual, 0.9, method="higher"))
    model = HistGradientBoostingRegressor(**HGB_PARAMS)
    model.fit(feature_matrix(donor), residual)
    return q90, model


def predict_fold(frame: pd.DataFrame, q90: float,
                 model: HistGradientBoostingRegressor) -> pd.DataFrame:
    if frame.empty or set(frame.workload_task) != {"inference"} or frame.model_id.nunique() != 1:
        raise ValueError("Expected one supported encoder target model")
    peak = frame.gpu1_peak_mib.to_numpy(dtype=float)
    predictions = {
        "unchanged_gpu1": peak,
        "donor_q90_delta": np.maximum(1.0, peak + q90),
        "hgb_q90_delta": np.maximum(1.0, peak + model.predict(feature_matrix(frame))),
    }
    for name, values in predictions.items():
        if len(values) != len(frame) or not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError(f"Invalid {name} prediction")
    out = frame[list(PAIR_KEY) + ["workload_task", "gpu1_execution_id", "gpu2_execution_id",
                                  "gpu1_peak_mib", "gpu2_max_peak_mib", "gpu2_min_peak_mib"]].copy()
    for name in METHODS:
        out[f"pred_{name}_mib"] = predictions[name]
    return out


def pinball90(observed: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    error = observed - predicted
    return np.maximum(0.9 * error, -0.1 * error)


def score_predictions(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Workload-group means, then equal-model macro scores."""
    rows = []
    for method in METHODS:
        frame = predictions.copy()
        actual = frame.gpu2_max_peak_mib.to_numpy(dtype=float)
        estimate = frame[f"pred_{method}_mib"].to_numpy(dtype=float)
        frame["pinball90_mib"] = pinball90(actual, estimate)
        frame["covered"] = (actual <= estimate).astype(float)
        frame["slack_mib"] = estimate - actual
        frame["shortfall_mib"] = np.maximum(actual - estimate, 0.0)
        frame["delta_from_gpu1_mib"] = estimate - frame.gpu1_peak_mib.to_numpy(dtype=float)
        by_group = frame.groupby(["campaign", "model_id", "workload_group"], as_index=False).agg(
            core_cells=("covered", "size"), pinball90_mib=("pinball90_mib", "mean"),
            coverage=("covered", "mean"), slack_mib=("slack_mib", "mean"),
            shortfall_mib=("shortfall_mib", "max"),
            delta_from_gpu1_mib=("delta_from_gpu1_mib", "mean"))
        by_group["method"] = method
        rows.append(by_group)
    groups = pd.concat(rows, ignore_index=True)
    per_model = groups.groupby(["campaign", "model_id", "method"], as_index=False).agg(
        workload_groups=("workload_group", "size"), core_cells=("core_cells", "sum"),
        pinball90_mib=("pinball90_mib", "mean"), coverage=("coverage", "mean"),
        mean_slack_mib=("slack_mib", "mean"),
        mean_delta_from_gpu1_mib=("delta_from_gpu1_mib", "mean"),
        max_shortfall_mib=("shortfall_mib", "max"))
    macro = per_model.groupby(["campaign", "method"], as_index=False).agg(
        models=("model_id", "size"), workload_groups=("workload_groups", "sum"),
        core_cells=("core_cells", "sum"), pinball90_mib=("pinball90_mib", "mean"),
        coverage=("coverage", "mean"), mean_slack_mib=("mean_slack_mib", "mean"),
        mean_delta_from_gpu1_mib=("mean_delta_from_gpu1_mib", "mean"),
        max_shortfall_mib=("max_shortfall_mib", "max"))
    return groups, per_model, macro


def evaluate(snapshot: pd.DataFrame, output: Path) -> dict:
    panels = {campaign: extract_pairs(snapshot, campaign, EXPECTED_PAIRS[campaign])
              for campaign in CAMPAIGNS}
    main = panels[CAMPAIGNS[0]]
    encoder = sorted(main.loc[main.workload_task == "inference", "model_id"].unique())
    unsupported = main.loc[main.workload_task != "inference"]
    if len(encoder) != 8 or len(unsupported) != 252 or unsupported.model_id.nunique() != 1:
        raise ValueError("Expected eight encoder models and one unsupported Qwen model")
    output.mkdir(parents=True, exist_ok=True)
    folds = []
    predictions = []
    for held in encoder:
        donor = main.loc[(main.workload_task == "inference") & (main.model_id != held)]
        q90, model = fit_fold(donor, held)
        slug = held.replace("/", "__")
        model_path = output / f"hgb_excluding_{slug}.joblib"
        joblib.dump(model, model_path)
        folds.append({"held_model_id": held, "donor_model_ids": sorted(donor.model_id.unique()),
                      "donor_pairs": len(donor), "donor_workload_groups": donor.workload_group.nunique(),
                      "donor_q90_delta_mib": q90,
                      "hgb_artifact": model_path.name, "hgb_sha256": sha256(model_path)})
        for campaign, panel in panels.items():
            target = panel.loc[panel.model_id == held]
            if not target.empty:
                predictions.append(predict_fold(target, q90, model))
    prediction_frame = pd.concat(predictions, ignore_index=True)
    prediction_frame.to_csv(output / "predictions.csv", index=False)
    groups, per_model, macro = score_predictions(prediction_frame)
    groups.to_csv(output / "per_workload.csv", index=False)
    per_model.to_csv(output / "per_model.csv", index=False)
    macro.to_csv(output / "macro.csv", index=False)
    (output / "fold_manifest.json").write_text(json.dumps(folds, indent=2) + "\n")

    primary = macro.loc[macro.campaign == CAMPAIGNS[0]].set_index("method")
    primary_baseline = min(primary.loc["unchanged_gpu1", "pinball90_mib"],
                           primary.loc["donor_q90_delta", "pinball90_mib"])
    primary_hgb = primary.loc["hgb_q90_delta"]
    primary_model = per_model.loc[per_model.campaign == CAMPAIGNS[0]].pivot(
        index="model_id", columns="method", values=["pinball90_mib", "coverage"])
    improved_models = int(((primary_model["pinball90_mib"]["hgb_q90_delta"] <
                           primary_model["pinball90_mib"][["unchanged_gpu1", "donor_q90_delta"]].min(axis=1))).sum())
    primary_checks = {
        "pinball_at_least_5pct_below_best_baseline": bool(primary_hgb.pinball90_mib <= 0.95 * primary_baseline),
        "macro_coverage_at_least_90pct": bool(primary_hgb.coverage >= 0.90),
        "each_model_coverage_at_least_85pct": bool((primary_model["coverage"]["hgb_q90_delta"] >= 0.85).all()),
        "at_least_six_models_pinball_better_than_best_model_baseline": improved_models >= 6,
        "mean_ceiling_at_least_10mib_lower_than_identity": bool(
            primary_hgb.mean_delta_from_gpu1_mib <= -10.0),
    }
    external_checks = {}
    for campaign in CAMPAIGNS[1:]:
        part = macro.loc[macro.campaign == campaign].set_index("method")
        benchmark = min(part.loc["unchanged_gpu1", "pinball90_mib"],
                        part.loc["donor_q90_delta", "pinball90_mib"])
        external_checks[campaign] = {
            "macro_coverage_at_least_85pct": bool(part.loc["hgb_q90_delta", "coverage"] >= 0.85),
            "pinball_no_more_than_105pct_best_baseline": bool(
                part.loc["hgb_q90_delta", "pinball90_mib"] <= 1.05 * benchmark),
        }
    qwen = unsupported.copy()
    qwen_actual = qwen.gpu2_max_peak_mib.to_numpy(dtype=float)
    qwen_base = qwen.gpu1_peak_mib.to_numpy(dtype=float)
    metrics = {
        "status": "exploratory_existing_sqlite_only",
        "scope": "observed_same_host_gpu2_per_device_pytorch_reserved_peak_not_minimum_vram_or_oom",
        "sha256": {"snapshot": sha256(SNAPSHOT), "protocol": sha256(PROTOCOL),
                   "script": sha256(Path(__file__)), "predictions": sha256(output / "predictions.csv"),
                   "fold_manifest": sha256(output / "fold_manifest.json")},
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "sklearn": sklearn.__version__},
        "hgb_params": HGB_PARAMS,
        "pair_counts": {campaign: len(panel) for campaign, panel in panels.items()},
        "supported_encoder_models": encoder,
        "qwen_abstention": {"model_id": unsupported.model_id.iloc[0], "pairs": len(unsupported),
                            "reason": "no_same_task_donor_model",
                            "identity_coverage": float(np.mean(qwen_actual <= qwen_base)),
                            "identity_pinball90_mib": float(pinball90(qwen_actual, qwen_base).mean()),
                            "identity_max_shortfall_mib": float(np.maximum(qwen_actual-qwen_base, 0).max())},
        "macro_scores": macro.to_dict("records"),
        "gate": {"primary": primary_checks, "external": external_checks,
                 "pass": bool(all(primary_checks.values()) and
                              all(all(checks.values()) for checks in external_checks.values()))},
    }
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if sha256(SNAPSHOT) != SNAPSHOT_SHA256 or sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("Snapshot or pre-score protocol hash changed")
    metrics = evaluate(pd.read_parquet(SNAPSHOT), args.output)
    print(json.dumps({"pair_counts": metrics["pair_counts"], "gate": metrics["gate"],
                      "macro_scores": metrics["macro_scores"]}, indent=2))


if __name__ == "__main__":
    main()
