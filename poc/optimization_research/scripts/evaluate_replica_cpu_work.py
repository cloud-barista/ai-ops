"""Frozen exploratory GPU1-to-GPU2 child CPU-work forecast.

The label is the sum of two measured child-process CPU times for one static
profile. It is neither a minimum CPU shape nor a scale-action outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/audit_v1/measurements.parquet"
PROTOCOL = ROOT / "reports/replica_cpu_work_protocol_v1.md"
OUTPUT = ROOT / "artifacts/replica_cpu_work_v1"
FREEZE = OUTPUT / "freeze.json"
EXPECTED_SNAPSHOT_SHA256 = "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"
CAMPAIGNS = {
    "khu-hf-local-exhaustive": 2300,
    "khu-hf-local-novel-matrix": 72,
    "khu-hf-local-space-filling": 176,
}
MAIN = "khu-hf-local-exhaustive"
PAIR_KEY = ("campaign", "host", "model_id", "model_revision", "workload_group", "cpu_core_limit")
SAME_PROFILE = ("batch_size", "max_length", "request_concurrency", "repeats",
                "dataset", "cpu_threads", "workload_task", "input_encoding")
FEATURES = ("log_gpu1_cpu_seconds", "log1p_gpu1_load_seconds",
            "log1p_gpu1_compute_seconds", "log1p_parameter_mib", "log_batch_size",
            "log_max_length", "log1p_request_concurrency", "log_repeats",
            "log_cpu_core_limit")
METHODS = ("twice_gpu1", "donor_ratio", "ridge_ratio", "hgb_ratio")
HGB_KWARGS = dict(loss="squared_error", max_iter=100, learning_rate=0.05,
                  max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=10.0,
                  early_stopping=False, random_state=42)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def positive_int(value: object, name: str) -> int:
    number = positive_number(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def close_recorded(left: object, right: object, name: str) -> None:
    first = positive_number(left, name + " first")
    second = positive_number(right, name + " second")
    if not math.isclose(first, second, abs_tol=2e-6, rel_tol=1e-9):
        raise ValueError(f"{name} differs between recorded fields")


def pair_to_record(gpu1: dict, gpu2: dict) -> dict:
    for name in PAIR_KEY:
        if gpu1[name] != gpu2[name]:
            raise ValueError(f"GPU1/GPU2 pair key mismatch: {name}")
    for name in SAME_PROFILE:
        if gpu1[name] != gpu2[name]:
            raise ValueError(f"GPU1/GPU2 static profile mismatch: {name}")
    if gpu1["host"] != "icns" or gpu1["device"] != "cuda" or gpu2["device"] != "cuda":
        raise ValueError("Unsupported host/device")
    if positive_int(gpu1["cpu_threads"], "cpu_threads") != positive_int(gpu1["cpu_core_limit"], "cpu_core_limit"):
        raise ValueError("CPU thread/core mismatch")
    if json.loads(gpu1["gpu_indices_json"]) != [0] or json.loads(gpu2["gpu_indices_json"]) != [0, 1]:
        raise ValueError("Unexpected physical GPU indices")
    if pd.Timestamp(gpu1["finished_at"]) >= pd.Timestamp(gpu2["started_at"]):
        raise ValueError("GPU1 measurement must finish before GPU2 begins")
    one = json.loads(gpu1["result_json"])
    two = json.loads(gpu2["result_json"])
    if not isinstance(one, dict) or not isinstance(two, dict):
        raise ValueError("Expected JSON result objects")
    close_recorded(gpu1["cpu_seconds"], one.get("cpu_seconds"), "GPU1 CPU seconds")
    close_recorded(gpu2["cpu_seconds"], two.get("cpu_seconds"), "GPU2 CPU seconds")
    children = two.get("replica_results")
    if not isinstance(children, list) or len(children) != 2 or any(not isinstance(item, dict) for item in children):
        raise ValueError("GPU2 must have exactly two child results")
    indices = [item.get("gpu_index") for item in children]
    if any(type(index) is not int for index in indices) or set(indices) != {0, 1}:
        raise ValueError("GPU2 child physical GPU indices must be {0,1}")
    child_cpu = [positive_number(item.get("cpu_seconds"), "child cpu_seconds") for item in children]
    total_child_cpu = sum(child_cpu)
    close_recorded(total_child_cpu, gpu2["cpu_seconds"], "GPU2 child sum")
    gpu1_examples = positive_int(one.get("inference_examples"), "GPU1 inference_examples")
    for child in children:
        if positive_int(child.get("inference_examples"), "child inference_examples") != gpu1_examples:
            raise ValueError("GPU2 child did not execute the full GPU1 profile")
        if (pd.Timestamp(child["started_at"]) < pd.Timestamp(gpu2["started_at"]) or
                pd.Timestamp(child["finished_at"]) > pd.Timestamp(gpu2["finished_at"]) or
                pd.Timestamp(child["started_at"]) >= pd.Timestamp(child["finished_at"])):
            raise ValueError("GPU2 child timestamps lie outside execution envelope")
    for name in ("batch_size", "max_length", "request_concurrency", "repeats", "cpu_core_limit"):
        positive_int(gpu1[name], name)
    return {
        **{name: gpu1[name] for name in PAIR_KEY},
        "workload_task": gpu1["workload_task"],
        "gpu1_execution_id": gpu1["execution_id"],
        "gpu2_execution_id": gpu2["execution_id"],
        "gpu1_cpu_seconds": positive_number(gpu1["cpu_seconds"], "gpu1_cpu_seconds"),
        "gpu1_load_seconds": positive_number(gpu1["load_seconds"], "gpu1_load_seconds"),
        "gpu1_compute_seconds": positive_number(gpu1["compute_seconds"], "gpu1_compute_seconds"),
        "gpu1_parameter_mib": positive_number(one.get("model_parameter_bytes"), "model_parameter_bytes") / 2**20,
        "gpu2_total_cpu_seconds": total_child_cpu,
        "gpu2_max_child_cpu_seconds": max(child_cpu),
        **{name: gpu1[name] for name in
           ("batch_size", "max_length", "request_concurrency", "repeats")},
    }


def extract_pairs(snapshot: pd.DataFrame, campaign: str, expected: int) -> pd.DataFrame:
    rows = snapshot.loc[(snapshot.campaign == campaign) & snapshot.eligible &
                        (snapshot.success == 1) & (snapshot.host == "icns") &
                        (snapshot.device == "cuda") & snapshot.replica_count.isin([1, 2])]
    groups: dict[tuple, dict[int, dict]] = {}
    for row in rows.to_dict("records"):
        key = tuple(row[name] for name in PAIR_KEY)
        replica = positive_int(row["replica_count"], "replica_count")
        group = groups.setdefault(key, {})
        if replica in group:
            raise ValueError(f"Duplicate replica measurement: {key}, {replica}")
        group[replica] = row
    pairs = []
    for key in sorted(groups):
        if set(groups[key]) != {1, 2}:
            raise ValueError(f"Incomplete GPU1/GPU2 pair: {key}")
        pairs.append(pair_to_record(groups[key][1], groups[key][2]))
    if len(pairs) != expected:
        raise ValueError(f"Expected {expected} pairs for {campaign}, found {len(pairs)}")
    return pd.DataFrame(pairs)


def load_pairs() -> dict[str, pd.DataFrame]:
    if sha256(SNAPSHOT) != EXPECTED_SNAPSHOT_SHA256:
        raise ValueError("Physical extraction SHA-256 changed")
    snapshot = pd.read_parquet(SNAPSHOT)
    pairs = {campaign: extract_pairs(snapshot, campaign, count)
             for campaign, count in CAMPAIGNS.items()}
    main = pairs[MAIN]
    encoder = main.loc[main.workload_task == "inference"]
    qwen = main.loc[main.workload_task == "causal_lm_forward"]
    if encoder.model_id.nunique() != 8 or len(encoder) != 2048 or len(qwen) != 252 or qwen.model_id.nunique() != 1:
        raise ValueError("Unexpected main model/task coverage")
    for campaign, expected in (("khu-hf-local-novel-matrix", 64),
                               ("khu-hf-local-space-filling", 160)):
        other = pairs[campaign]
        if len(other.loc[other.workload_task == "inference"]) != expected:
            raise ValueError(f"Unexpected encoder coverage in {campaign}")
        if set(other.loc[other.workload_task == "inference", "model_id"]) != set(encoder.model_id):
            raise ValueError(f"Different encoder model set in {campaign}")
    return pairs


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("No feature rows")
    raw_names = ("gpu1_cpu_seconds", "gpu1_load_seconds", "gpu1_compute_seconds",
                 "gpu1_parameter_mib", "batch_size", "max_length", "request_concurrency",
                 "repeats", "cpu_core_limit")
    raw = frame[list(raw_names)].astype(float)
    if not np.isfinite(raw.to_numpy()).all() or (raw <= 0).any().any():
        raise ValueError("Predictor features must be positive and finite")
    values = np.column_stack((np.log(raw.gpu1_cpu_seconds), np.log1p(raw.gpu1_load_seconds),
                              np.log1p(raw.gpu1_compute_seconds), np.log1p(raw.gpu1_parameter_mib),
                              np.log(raw.batch_size), np.log(raw.max_length),
                              np.log1p(raw.request_concurrency), np.log(raw.repeats),
                              np.log(raw.cpu_core_limit)))
    return pd.DataFrame(values, columns=list(FEATURES), index=frame.index)


def fit_fold(donor: pd.DataFrame, held_model_id: str) -> tuple[float, object, object]:
    if donor.model_id.nunique() != 7 or held_model_id in set(donor.model_id) or set(donor.workload_task) != {"inference"}:
        raise ValueError("Donor must contain the other seven encoder models only")
    ratio = np.log(donor.gpu2_total_cpu_seconds.to_numpy(dtype=float) /
                   donor.gpu1_cpu_seconds.to_numpy(dtype=float))
    if not np.isfinite(ratio).all():
        raise ValueError("Invalid donor CPU ratio")
    donor_log_median = float(np.median(ratio))
    features = feature_matrix(donor)
    ridge = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    ridge.fit(features, ratio)
    hgb = HistGradientBoostingRegressor(**HGB_KWARGS)
    hgb.fit(features, ratio)
    return donor_log_median, ridge, hgb


def predict_fold(frame: pd.DataFrame, donor_log_median: float,
                 ridge: object, hgb: object) -> pd.DataFrame:
    if frame.empty or frame.model_id.nunique() != 1 or set(frame.workload_task) != {"inference"}:
        raise ValueError("Prediction requires one held encoder model")
    gpu1 = frame.gpu1_cpu_seconds.to_numpy(dtype=float)
    features = feature_matrix(frame)
    predictions = {
        "twice_gpu1": gpu1 * 2.0,
        "donor_ratio": gpu1 * math.exp(donor_log_median),
        "ridge_ratio": gpu1 * np.exp(ridge.predict(features)),
        "hgb_ratio": gpu1 * np.exp(hgb.predict(features)),
    }
    out = frame[list(PAIR_KEY) + ["workload_task", "gpu1_execution_id", "gpu2_execution_id",
                                  "gpu1_cpu_seconds", "gpu2_total_cpu_seconds",
                                  "gpu2_max_child_cpu_seconds"]].copy()
    for name, estimate in predictions.items():
        if not np.isfinite(estimate).all() or (estimate <= 0).any():
            raise ValueError(f"Invalid {name} prediction")
        out[f"pred_{name}_cpu_seconds"] = estimate
    return out


def score_campaign(predictions: pd.DataFrame) -> dict:
    model_rows = []
    for (model_id, method), group in (
        pd.concat([
            predictions.assign(method=method,
                               prediction=predictions[f"pred_{method}_cpu_seconds"])
            for method in METHODS
        ]).groupby(["model_id", "method"], sort=True)
    ):
        actual = group.gpu2_total_cpu_seconds.to_numpy(dtype=float)
        predicted = group.prediction.to_numpy(dtype=float)
        abs_log = np.abs(np.log(predicted / actual))
        group = group.assign(abs_log=abs_log)
        per_task = group.groupby("workload_group").abs_log.mean()
        model_rows.append({
            "model_id": model_id,
            "method": method,
            "pairs": len(group),
            "workload_groups": group.workload_group.nunique(),
            "mean_abs_log_error": float(per_task.mean()),
            "median_relative_error": float(np.median(predicted / actual - 1)),
            "p90_abs_log_error": float(np.quantile(abs_log, .9, method="higher")),
            "underprediction_rate": float(np.mean(predicted < actual)),
            "cpu_seconds_mae": float(np.mean(np.abs(predicted - actual))),
        })
    macro = {}
    for method in METHODS:
        rows = [row for row in model_rows if row["method"] == method]
        macro[method] = {
            "models": len(rows),
            "pairs": sum(row["pairs"] for row in rows),
            "mean_abs_log_error": float(np.mean([row["mean_abs_log_error"] for row in rows])),
            "underprediction_rate": float(np.mean([row["underprediction_rate"] for row in rows])),
            "cpu_seconds_mae": float(np.mean([row["cpu_seconds_mae"] for row in rows])),
        }
    return {"macro": macro, "per_model": model_rows}


def gates(scores: dict[str, dict]) -> dict:
    main = scores[MAIN]
    controls = METHODS[:-1]
    learned = main["macro"]["hgb_ratio"]["mean_abs_log_error"]
    best_control = min(main["macro"][method]["mean_abs_log_error"] for method in controls)
    by_model = {}
    for row in main["per_model"]:
        by_model.setdefault(row["model_id"], {})[row["method"]] = row["mean_abs_log_error"]
    improved_models = sum(values["hgb_ratio"] < min(values[method] for method in controls)
                          for values in by_model.values())
    worst_relative = max(values["hgb_ratio"] / min(values[method] for method in controls)
                         for values in by_model.values())
    external = {}
    for campaign in CAMPAIGNS:
        if campaign == MAIN:
            continue
        macro = scores[campaign]["macro"]
        reference = min(macro[method]["mean_abs_log_error"] for method in controls)
        external[campaign] = macro["hgb_ratio"]["mean_abs_log_error"] <= 1.05 * reference
    checks = {
        "main_mae_5pct_below_best_control": learned <= 0.95 * best_control,
        "at_least_6_of_8_models_improve": improved_models >= 6,
        "no_model_more_than_20pct_worse": worst_relative <= 1.20,
        **{f"{campaign}_within_105pct_best_control": passed for campaign, passed in external.items()},
    }
    return {"accepted": all(checks.values()), "checks": checks,
            "main_best_control_mean_abs_log_error": best_control,
            "improved_model_count": improved_models,
            "worst_model_relative_error_to_best_control": worst_relative}


def provenance() -> dict:
    return {"snapshot_sha256": sha256(SNAPSHOT), "protocol_sha256": sha256(PROTOCOL),
            "script_sha256": sha256(Path(__file__)), "sklearn_version": sklearn.__version__,
            "pandas_version": pd.__version__, "python_version": platform.python_version()}


def freeze() -> None:
    pairs = load_pairs()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    content = {**provenance(), "campaign_pair_counts": {name: len(frame) for name, frame in pairs.items()},
               "encoder_model_count": 8, "target": "sum_gpu2_child_process_cpu_seconds"}
    if content["snapshot_sha256"] != EXPECTED_SNAPSHOT_SHA256:
        raise ValueError("Snapshot hash changed")
    if FREEZE.exists():
        if json.loads(FREEZE.read_text()) != content:
            raise ValueError("Existing freeze disagrees with current source/code/protocol")
        print(f"Verified existing freeze {FREEZE}")
        return
    FREEZE.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
    print(f"Wrote pre-score freeze {FREEZE}, sha256={sha256(FREEZE)}")


def evaluate() -> None:
    if not FREEZE.exists():
        raise ValueError("Freeze must be created before model scoring")
    frozen = json.loads(FREEZE.read_text())
    if any(frozen.get(key) != value for key, value in provenance().items()):
        raise ValueError("Source, protocol, code, or runtime differs from freeze")
    pairs = load_pairs()
    if frozen["campaign_pair_counts"] != {name: len(frame) for name, frame in pairs.items()}:
        raise ValueError("Paired source counts differ from freeze")
    main = pairs[MAIN].loc[pairs[MAIN].workload_task == "inference"]
    predictions = []
    for held_model_id in sorted(main.model_id.unique()):
        donor = main.loc[main.model_id != held_model_id]
        median, ridge, hgb = fit_fold(donor, held_model_id)
        for campaign, frame in pairs.items():
            target = frame.loc[(frame.workload_task == "inference") &
                               (frame.model_id == held_model_id)]
            predictions.append(predict_fold(target, median, ridge, hgb))
    predicted = pd.concat(predictions, ignore_index=True)
    scores = {campaign: score_campaign(predicted.loc[predicted.campaign == campaign])
              for campaign in CAMPAIGNS}
    result = {
        "scope": "same-host static GPU1 profile to GPU2 child CPU-work; no VM action labels",
        "freeze_sha256": sha256(FREEZE), "provenance": provenance(),
        "scores": scores, "gate": gates(scores),
        "qwen_abstain_pairs": int(sum((frame.workload_task == "causal_lm_forward").sum()
                                     for frame in pairs.values())),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    predicted.to_csv(OUTPUT / "predictions.csv", index=False)
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"gate": result["gate"], "main_macro": scores[MAIN]["macro"]},
                     indent=2, sort_keys=True))


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
