"""Frozen time-shard test of simultaneous *observed* CPU/RAM peak envelopes.

`prepare` never opens shard 450. The later `test` verifies the frozen program,
protocol, model, and Google's official file digests before scoring once.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts import evaluate_google_2011_observed_resource as base


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/google_2011_joint_envelope_protocol.md"
SCRIPT = Path(__file__).resolve()
OUT = ROOT / "data/derived/google_2011_joint_envelope"
OLD_JOB_TABLE = ROOT / "data/derived/google_2011_observed_resource/test_job_scores.csv"
TEST_SHARD = 450
OFFICIAL_TEST = {
    "task_events/part-00450-of-00500.csv.gz": "e6184d294745eab2fc9679c1c73cd2385c2ee2ba5cbace663339c61b8ad1d401",
    "task_usage/part-00450-of-00500.csv.gz": "a41cb509d4825c96dc523dbcaa2d5cee89ba9c8b972a2d060d8c0c8cfdb2e2ef",
}
METHODS = ("request_raw", "hgb_raw", "request_joint", "hgb_joint", "hgb_cpu_only")


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def official_test_hashes() -> None:
    lines = (base.SOURCE / "SHA256SUM").read_text().splitlines()
    listed = {name: digest for digest, name in
              (line.split(" *", 1) for line in lines if " *" in line)}
    if {name: listed.get(name) for name in OFFICIAL_TEST} != OFFICIAL_TEST:
        raise ValueError("Official SHA256SUM does not match the frozen test digests")


def check_test_files() -> dict[str, str]:
    official_test_hashes()
    actual = {name: base.sha256(base.SOURCE / name) for name in OFFICIAL_TEST}
    if actual != OFFICIAL_TEST:
        raise ValueError("Test file checksum differs from the official Google trace")
    return actual


def equal_job_quantile(values: np.ndarray, frame: pd.DataFrame, q: float = 0.9) -> float:
    """Empirical job-equal quantile; permits +inf for impossible CPU-only repair."""
    values = np.asarray(values, dtype=float)
    if len(values) != len(frame) or np.isnan(values).any() or np.isneginf(values).any():
        raise ValueError("Invalid calibration scores")
    weights = base.job_weights(frame)
    order = np.argsort(values, kind="stable")
    rank = np.cumsum(weights[order])
    position = min(int(np.searchsorted(rank, q * rank[-1], side="left")), len(values) - 1)
    return float(values[order[position]])


def apply_margin(prediction: np.ndarray, margin: float) -> np.ndarray:
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("Invalid nonnegative margin")
    result = np.expm1(np.log1p(prediction) + margin)
    if not np.isfinite(result).all() or (result < 0).any():
        raise ValueError("Invalid calibrated prediction")
    return result


def calibrate(frame: pd.DataFrame, cpu: np.ndarray, memory: np.ndarray) -> dict[str, float | None]:
    if len(frame) != len(cpu) or len(frame) != len(memory):
        raise ValueError("Prediction count differs from calibration rows")
    cpu_gap = np.log1p(frame.max_cpu.to_numpy(float)) - np.log1p(cpu)
    memory_gap = np.log1p(frame.max_memory.to_numpy(float)) - np.log1p(memory)
    joint = equal_job_quantile(np.maximum(cpu_gap, memory_gap), frame)
    # If RAM fails, increasing only CPU can never repair that row.
    cpu_only = equal_job_quantile(np.where(memory_gap <= 0, cpu_gap, np.inf), frame)
    return {"joint": max(0.0, joint),
            "cpu_only": max(0.0, cpu_only) if math.isfinite(cpu_only) else None}


def raw_predictions(frame: pd.DataFrame, model: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    request_cpu = frame.cpu_request.to_numpy(float)
    request_memory = frame.memory_request.to_numpy(float)
    hgb_cpu = base.predict(frame, model, "cpu")["hgb"]
    return {"request": (request_cpu, request_memory),
            "hgb": (hgb_cpu, request_memory)}


def all_predictions(frame: pd.DataFrame, model: dict, margins: dict) -> dict:
    raw = raw_predictions(frame, model)
    result = {"request_raw": raw["request"], "hgb_raw": raw["hgb"]}
    for name in ("request", "hgb"):
        cpu, memory = raw[name]
        result[f"{name}_joint"] = (apply_margin(cpu, margins[name]["joint"]),
                                    apply_margin(memory, margins[name]["joint"]))
    cpu_margin = margins["hgb"]["cpu_only"]
    if cpu_margin is not None:
        cpu, memory = raw["hgb"]
        result["hgb_cpu_only"] = (apply_margin(cpu, cpu_margin), memory)
    return result


def job_table(frame: pd.DataFrame, predictions: dict) -> pd.DataFrame:
    cpu = frame.max_cpu.to_numpy(float)
    memory = frame.max_memory.to_numpy(float)
    data = pd.DataFrame({"job": frame.job.to_numpy(), "n": np.ones(len(frame), dtype=int)})
    for name, (pc, pm) in predictions.items():
        data[f"{name}_joint_coverage"] = ((cpu <= pc) & (memory <= pm)).astype(float)
        data[f"{name}_cpu_coverage"] = (cpu <= pc).astype(float)
        data[f"{name}_memory_coverage"] = (memory <= pm).astype(float)
        data[f"{name}_cpu_pinball"] = base.pinball(cpu, pc)
        data[f"{name}_memory_pinball"] = base.pinball(memory, pm)
        data[f"{name}_log_area"] = np.log1p(pc) + np.log1p(pm)
    aggregate = {key: ("sum" if key == "n" else "mean") for key in data if key != "job"}
    return data.groupby("job", sort=True).agg(aggregate).reset_index()


def summarize(table: pd.DataFrame) -> dict:
    result = {}
    for name in METHODS:
        key = f"{name}_joint_coverage"
        if key not in table:
            continue
        result[name] = {}
        for metric in ("joint_coverage", "cpu_coverage", "memory_coverage",
                       "cpu_pinball", "memory_pinball", "log_area"):
            values = table[f"{name}_{metric}"].to_numpy(float)
            result[name][f"job_equal_{metric}"] = float(values.mean())
            result[name][f"row_{metric}"] = float(np.average(values, weights=table.n))
    return result


def paired_bootstrap(table: pd.DataFrame) -> dict:
    rng = np.random.default_rng(20260929)
    draws = rng.integers(0, len(table), size=(2000, len(table)))
    comparison = {}
    for metric in ("joint_coverage", "cpu_pinball", "log_area"):
        diff = (table[f"hgb_joint_{metric}"] - table[f"request_joint_{metric}"]).to_numpy(float)
        comparison[metric] = {
            "hgb_minus_request": float(diff.mean()),
            "job_bootstrap_ci95": [float(x) for x in np.quantile(diff[draws].mean(axis=1), [.025, .975])],
        }
    return comparison


def prepare() -> None:
    if OUT.exists():
        raise FileExistsError("Frozen output directory already exists")
    official_test_hashes()  # Never open shard 450 here.
    source_hashes = base.source_hashes()
    train, train_info = base.extract_shard(50)
    validation, validation_info = base.extract_shard(200)
    prior = json.loads((ROOT / "data/derived/google_2011_observed_resource/freeze_manifest.json").read_text())
    if train_info["used_rows"] != 3134 or validation_info["used_rows"] != 2770:
        raise ValueError("Original extraction counts changed")
    if set(train.job) & set(validation.job):
        raise ValueError("Training and calibration jobs overlap")
    if base.sha256(OLD_JOB_TABLE) != "b01a0efe26b15a848e25c8af67f8ec41f1bd33492fd348a1773adb5516ec2b9f":
        raise ValueError("Prior shard-400 job list changed")
    old_jobs = sorted(pd.read_csv(OLD_JOB_TABLE, usecols=["job"]).job.astype(str).unique().tolist())
    model = base.fit_bundle(train)
    raw = raw_predictions(validation, model)
    margins = {name: calibrate(validation, *raw[name]) for name in ("request", "hgb")}
    if margins["hgb"]["cpu_only"] is None:
        raise ValueError("CPU-only joint calibration is infeasible in shard 200")
    validation_table = job_table(validation, all_predictions(validation, model, margins))
    validation_scores = summarize(validation_table)
    OUT.mkdir(parents=True)
    model_path = OUT / "model.joblib"
    joblib.dump(model, model_path, compress=3)
    freeze = {
        "protocol_sha256": base.sha256(PROTOCOL), "script_sha256": base.sha256(SCRIPT),
        "base_script_sha256": base.sha256(Path(base.__file__)),
        "source_sha256": source_hashes, "official_test_sha256": OFFICIAL_TEST,
        "prior_400_job_table_sha256": base.sha256(OLD_JOB_TABLE),
        "train_jobs": sorted(train.job.astype(str).unique().tolist()),
        "calibration_jobs": sorted(validation.job.astype(str).unique().tolist()),
        "prior_400_jobs": old_jobs, "train_info": train_info,
        "calibration_info": validation_info, "margins": margins,
        "calibration_scores": validation_scores, "model_sha256": base.sha256(model_path),
        "python": platform.python_version(), "numpy": np.__version__,
        "pandas": pd.__version__, "sklearn": sklearn.__version__,
        "known_prior_selected": prior["selected"], "test_labels_read": False,
    }
    _json(OUT / "freeze_manifest.json", freeze)
    print(json.dumps({"margins": margins, "calibration_scores": validation_scores,
                      "freeze_manifest_sha256": base.sha256(OUT / "freeze_manifest.json")}, indent=2))


def test() -> None:
    path = OUT / "freeze_manifest.json"
    freeze = json.loads(path.read_text())
    if (OUT / "test_scores.json").exists():
        raise FileExistsError("Test has already been scored")
    for actual, key in ((base.sha256(PROTOCOL), "protocol_sha256"),
                        (base.sha256(SCRIPT), "script_sha256"),
                        (base.sha256(Path(base.__file__)), "base_script_sha256"),
                        (base.sha256(OUT / "model.joblib"), "model_sha256"),
                        (base.sha256(OLD_JOB_TABLE), "prior_400_job_table_sha256")):
        if actual != freeze[key]:
            raise ValueError(f"Frozen {key} changed before test")
    if base.source_hashes() != freeze["source_sha256"]:
        raise ValueError("Development source bytes changed")
    test_hashes = check_test_files()
    frame, info = base.extract_shard(TEST_SHARD)
    overlap = sorted(set(frame.job.astype(str)) & set(freeze["train_jobs"] +
                                                  freeze["calibration_jobs"] + freeze["prior_400_jobs"]))
    if info["jobs"] < 30 or info["used_rows"] < 1000 or overlap:
        result = {"status": "NO_GO_SUPPORT", "test_info": info, "overlapping_job_ids": overlap,
                  "test_sha256": test_hashes, "freeze_manifest_sha256": base.sha256(path)}
        _json(OUT / "test_scores.json", result)
        print(json.dumps(result, indent=2))
        return
    model = joblib.load(OUT / "model.joblib")
    table = job_table(frame, all_predictions(frame, model, freeze["margins"]))
    scored = summarize(table)
    hgb, request = scored["hgb_joint"], scored["request_joint"]
    gate = {
        "job_equal_joint_coverage_at_least_085": hgb["job_equal_joint_coverage"] >= .85,
        "job_equal_log_area_lower_than_calibrated_request": hgb["job_equal_log_area"] < request["job_equal_log_area"],
        "job_equal_cpu_pinball_lower_than_calibrated_request": hgb["job_equal_cpu_pinball"] < request["job_equal_cpu_pinball"],
    }
    top_job = table.loc[table.n.idxmax(), "job"]
    result = {
        "status": "SCORED", "test_info": info, "test_sha256": test_hashes,
        "scores": scored, "prespecified_gate": gate, "joint_envelope_improvement_confirmed": all(gate.values()),
        "paired_job_bootstrap": paired_bootstrap(table),
        "remove_largest_job": {"job_id": str(top_job),
                               "removed_rows": int(table.loc[table.job == top_job, "n"].iloc[0]),
                               "scores": summarize(table.loc[table.job != top_job])},
        "freeze_manifest_sha256": base.sha256(path),
    }
    table.to_csv(OUT / "test_job_scores.csv", index=False)
    _json(OUT / "test_scores.json", result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "test"))
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    else:
        test()


if __name__ == "__main__":
    main()
