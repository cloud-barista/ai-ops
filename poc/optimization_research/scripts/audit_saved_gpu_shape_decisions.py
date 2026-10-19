"""Posthoc decision audit of frozen performance_v1 predictions.

This script never loads or refits the stored estimator. It checks the pinned
input files, joins saved predictions to physical measurements, and applies
the same deterministic resource-order decision to both saved predictors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/saved_gpu_shape_decision_audit_protocol.md"
PROTOCOL_SHA256 = "767d2725388d94fa52d8691d6c9330bb80e373c0abdb0ca4444632d03569fe1e"
INPUTS = {
    "snapshot": (ROOT / "data/audit_v1/measurements.parquet",
                 "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906"),
    "model": (ROOT / "artifacts/performance_v1/performance_model.joblib",
              "7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161"),
    "predictions": (ROOT / "artifacts/performance_v1/predictions.parquet",
                    "a1d8db36ce44203eb0b371670b158a13052d51186919d403a96d995692660395"),
    "manifest": (ROOT / "artifacts/performance_v1/split_manifest.csv",
                 "817ccd79e180e0373db085ac254698b26c258b060aea633bcc73c621f86648f8"),
}
PRIMARY = "khu-hf-local-exhaustive"
EXTERNAL = ("khu-hf-local-novel-matrix", "khu-hf-local-space-filling")
CONFIGS = tuple((gpu, cores) for gpu in (1, 2) for cores in (2, 4, 8, 16))
FACTORS = (0.75, 1.0, 1.25)
METHODS = {
    "stored_model": ("predicted_latency_p95_ms", "predicted_examples_per_second"),
    "model_device_median": ("baseline_latency_p95_ms", "baseline_examples_per_second"),
}
JOIN_KEY = ["host", "execution_id"]
GROUP_KEY = ["campaign", "host", "model_id", "workload_group"]
SAVED_EQUAL = ["campaign", "model_id", "device", "workload_group",
               "latency_p95_ms", "examples_per_second"]
MEASUREMENT_COLUMNS = JOIN_KEY + SAVED_EQUAL + [
    "eligible", "replica_count", "cpu_core_limit", "batch_size",
    "latency_slo_ms", "target_throughput_rps"]
PREDICTION_COLUMNS = JOIN_KEY + SAVED_EQUAL + ["evaluation"] + [
    column for pair in METHODS.values() for column in pair]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pins() -> dict[str, str]:
    observed = {}
    for label, (path, expected) in INPUTS.items():
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"{label} SHA-256 mismatch: {actual}")
        observed[label] = actual
    actual_protocol = sha256_file(PROTOCOL)
    if actual_protocol != PROTOCOL_SHA256:
        raise ValueError(f"frozen protocol SHA-256 mismatch: {actual_protocol}")
    observed["protocol"] = actual_protocol
    return observed


def _required(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _key_set(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return set(map(tuple, frame[JOIN_KEY].itertuples(index=False, name=None)))


def validated_gpu_rows(snapshot: pd.DataFrame, predictions: pd.DataFrame,
                       manifest: pd.DataFrame) -> pd.DataFrame:
    """Join pinned GPU test/external rows and reject any silent coverage loss."""
    _required(snapshot, MEASUREMENT_COLUMNS, "snapshot")
    _required(predictions, PREDICTION_COLUMNS, "predictions")
    _required(manifest, JOIN_KEY + ["split"], "manifest")
    measured = snapshot[MEASUREMENT_COLUMNS].copy()
    saved = predictions[PREDICTION_COLUMNS].copy()
    saved = saved[saved.device.eq("cuda")].copy()
    if saved.empty or measured.duplicated(JOIN_KEY).any() or saved.duplicated(JOIN_KEY).any():
        raise ValueError("empty GPU predictions or duplicate host/execution_id")
    if saved[JOIN_KEY + SAVED_EQUAL + ["evaluation"]].isna().any().any():
        raise ValueError("missing saved identifier, outcome, or evaluation")
    joined = saved.merge(measured, on=JOIN_KEY, how="left", validate="one_to_one",
                         suffixes=("_saved", "_measured"), indicator=True)
    if not joined._merge.eq("both").all():
        raise ValueError("saved prediction has no exact physical measurement")
    joined = joined.drop(columns="_merge")
    for column in SAVED_EQUAL:
        a, b = joined[f"{column}_saved"], joined[f"{column}_measured"]
        if a.isna().any() or b.isna().any() or not (a == b).all():
            raise ValueError(f"saved and measured {column} disagree")
        joined[column] = b
    joined = joined.drop(columns=[f"{c}_{suffix}" for c in SAVED_EQUAL
                                  for suffix in ("saved", "measured")])
    if not joined.eligible.eq(True).all() or not joined.device.eq("cuda").all():
        raise ValueError("GPU rows must be eligible CUDA measurements")
    expected_evaluation = np.where(joined.campaign.eq(PRIMARY), "test", joined.campaign)
    if (not joined.campaign.isin((PRIMARY, *EXTERNAL)).all() or
            not np.array_equal(joined.evaluation.to_numpy(), expected_evaluation)):
        raise ValueError("saved evaluation/campaign is outside frozen test scope")

    if manifest.duplicated(JOIN_KEY).any() or manifest[JOIN_KEY + ["split"]].isna().any().any():
        raise ValueError("invalid or duplicate split manifest key")
    primary_saved = joined[joined.campaign.eq(PRIMARY)].merge(
        manifest[JOIN_KEY + ["split"]], on=JOIN_KEY, how="left", validate="one_to_one")
    if not primary_saved["split"].eq("test").all():
        raise ValueError("primary saved predictions include non-test rows")
    manifest_test = manifest[manifest["split"].eq("test")].merge(
        measured[JOIN_KEY + ["device", "campaign", "eligible"]], on=JOIN_KEY,
        how="left", validate="one_to_one")
    expected_test = manifest_test[manifest_test.device.eq("cuda") &
                                  manifest_test.campaign.eq(PRIMARY) &
                                  manifest_test.eligible.eq(True)]
    if _key_set(expected_test) != _key_set(primary_saved):
        raise ValueError("primary saved GPU test coverage differs from split manifest")
    if not joined["workload_group"].notna().all():
        raise ValueError("missing workload group")

    positive = ["latency_p95_ms", "examples_per_second", "batch_size",
                "latency_slo_ms", "target_throughput_rps"] + [
        column for pair in METHODS.values() for column in pair]
    values = joined[positive].to_numpy(dtype=float)
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("measurements, targets, and predictions must be positive finite")
    if not np.isfinite(joined[["replica_count", "cpu_core_limit"]].to_numpy(dtype=float)).all():
        raise ValueError("resource configuration must be finite")
    if joined.groupby("workload_group")[GROUP_KEY[:-1]].nunique().gt(1).any().any():
        raise ValueError("workload group appears in more than one campaign/model/host")
    groups = []
    for _, slate in joined.groupby(GROUP_KEY, sort=True):
        slate = slate.sort_values(["replica_count", "cpu_core_limit"])
        configs = tuple(map(tuple, slate[["replica_count", "cpu_core_limit"]]
                                   .itertuples(index=False, name=None)))
        if configs != CONFIGS:
            raise ValueError("incomplete or duplicated eight-cell GPU/CPU slate")
        for column in ("latency_slo_ms", "target_throughput_rps", "batch_size"):
            if slate[column].nunique(dropna=False) != 1:
                raise ValueError(f"{column} changes within one workload slate")
        groups.append(slate)
    return pd.concat(groups, ignore_index=True)


def _margin_band(margin: float) -> str:
    if margin < 0.10:
        return "lt_0.10"
    if margin < 0.25:
        return "0.10_to_lt_0.25"
    return "ge_0.25"


def score_slate(slate: pd.DataFrame, method: str, scenario: str,
                latency_factor: float, rps_factor: float) -> dict:
    """Score one complete slate; truth is used only after choice is fixed."""
    if method not in METHODS or len(slate) != 8:
        raise ValueError("unknown method or incomplete slate")
    configs = tuple(map(tuple, slate[["replica_count", "cpu_core_limit"]]
                               .itertuples(index=False, name=None)))
    if configs != CONFIGS or latency_factor <= 0 or rps_factor <= 0:
        raise ValueError("invalid resource order or SLO sensitivity factor")
    target_latency = float(slate.latency_slo_ms.iloc[0] * latency_factor)
    target_rps = float(slate.target_throughput_rps.iloc[0] * rps_factor)
    batch = slate.batch_size.to_numpy(dtype=float)
    actual_latency = slate.latency_p95_ms.to_numpy(dtype=float)
    actual_rps = slate.examples_per_second.to_numpy(dtype=float) / batch
    prediction_latency = slate[METHODS[method][0]].to_numpy(dtype=float)
    prediction_rps = slate[METHODS[method][1]].to_numpy(dtype=float) / batch
    candidates = np.concatenate((actual_latency, actual_rps, prediction_latency,
                                 prediction_rps, [target_latency, target_rps]))
    if not np.isfinite(candidates).all() or np.any(candidates <= 0):
        raise ValueError("nonpositive or nonfinite decision input")
    truth = (actual_latency <= target_latency) & (actual_rps >= target_rps)
    predicted = (prediction_latency <= target_latency) & (prediction_rps >= target_rps)
    oracle_index = int(np.flatnonzero(truth)[0]) if truth.any() else None
    selected_index = int(np.flatnonzero(predicted)[0]) if predicted.any() else None
    oracle_feasible = oracle_index is not None
    selected = selected_index is not None
    success = bool(selected and truth[selected_index])
    minimum_margin = None
    if selected:
        minimum_margin = float(min(
            (target_latency - prediction_latency[selected_index]) / target_latency,
            (prediction_rps[selected_index] - target_rps) / target_rps))
        if minimum_margin < -1e-12:
            raise AssertionError("selected candidate has negative predicted margin")
    passing = int(truth.sum())
    row = {column: slate[column].iloc[0] for column in GROUP_KEY}
    row.update({
        "method": method, "scenario": scenario,
        "latency_factor": latency_factor, "rps_factor": rps_factor,
        "latency_target_ms": target_latency, "target_rps": target_rps,
        "oracle_feasible_configs": passing,
        "difficulty": "zero" if passing == 0 else "all_eight" if passing == 8 else "one_to_seven",
        "oracle_feasible": oracle_feasible, "oracle_gpu": None if oracle_index is None else CONFIGS[oracle_index][0],
        "oracle_cores": None if oracle_index is None else CONFIGS[oracle_index][1],
        "selected": selected, "selected_gpu": None if selected_index is None else CONFIGS[selected_index][0],
        "selected_cores": None if selected_index is None else CONFIGS[selected_index][1],
        "true_success": success, "false_approval": bool(selected and not success),
        "missed_feasible": bool(oracle_feasible and not success),
        "correct_min_shape": bool(success and selected_index == oracle_index),
        "minimum_predicted_relative_margin": minimum_margin,
        "margin_band": None if minimum_margin is None else _margin_band(minimum_margin),
    })
    return row


def _counts(part: pd.DataFrame) -> dict[str, int]:
    counts = {"groups": len(part)}
    for field in ("oracle_feasible", "selected", "true_success", "false_approval",
                  "missed_feasible", "correct_min_shape"):
        counts[field] = int(part[field].sum())
    counts["abstained"] = counts["groups"] - counts["selected"]
    if counts["selected"] != counts["true_success"] + counts["false_approval"]:
        raise AssertionError("selection accounting failed")
    return counts


def summarize(rows: pd.DataFrame) -> dict:
    original = rows[rows.scenario.eq("original")]
    synthetic = rows[~rows.scenario.eq("original")]
    def grouped(part: pd.DataFrame, columns: list[str]) -> list[dict]:
        return [{**dict(zip(columns, key if isinstance(key, tuple) else (key,))),
                 **_counts(group)}
                for key, group in part.groupby(columns, sort=True)]
    orig = [{"scope": "all", **record} for record in grouped(original, ["method"])]
    for cols, scope in [(["method", "campaign"], "campaign"),
                        (["method", "campaign", "model_id"], "campaign_model"),
                        (["method", "difficulty"], "difficulty"),
                        (["method", "campaign", "difficulty"], "campaign_difficulty")]:
        orig.extend({"scope": scope, **record} for record in grouped(original, cols))
    synth = [{"scope": "all", **record} for record in
             grouped(synthetic, ["method", "scenario", "latency_factor", "rps_factor"])]
    synth.extend({"scope": "campaign", **record} for record in
                 grouped(synthetic, ["method", "campaign", "scenario",
                                     "latency_factor", "rps_factor"]))
    selected = original[original.selected]
    margins = [{"scope": "all", **record} for record in
               grouped(selected, ["method", "margin_band"])]
    margins.extend({"scope": "campaign", **record} for record in
                    grouped(selected, ["method", "campaign", "margin_band"]))
    return {"original": orig, "synthetic_by_target": synth, "selected_margin_bands": margins}


def run(output: Path) -> dict:
    pins = verify_pins()
    snapshot = pd.read_parquet(INPUTS["snapshot"][0])
    predictions = pd.read_parquet(INPUTS["predictions"][0])
    manifest = pd.read_csv(INPUTS["manifest"][0])
    scored = validated_gpu_rows(snapshot, predictions, manifest)
    records = []
    for _, slate in scored.groupby(GROUP_KEY, sort=True):
        for method in METHODS:
            records.append(score_slate(slate, method, "original", 1.0, 1.0))
            for latency_factor in FACTORS:
                for rps_factor in FACTORS:
                    name = f"sensitivity_l{latency_factor:.2f}_r{rps_factor:.2f}"
                    records.append(score_slate(slate, method, name,
                                               latency_factor, rps_factor))
    rows = pd.DataFrame(records)
    groups_per_campaign = {str(key): int(value) for key, value in
                           (scored.groupby("campaign").workload_group.nunique()).items()}
    if len(rows) != 10 * len(METHODS) * sum(groups_per_campaign.values()):
        raise AssertionError("expected original plus nine sensitivity rows per method/group")
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "decision_rows.csv"
    rows.to_csv(rows_path, index=False)
    report = {
        "scope": "posthoc descriptive audit of frozen performance_v1 predictions; no model load, refit, policy tuning, or independent test claim",
        "input_sha256": pins,
        "script_sha256": sha256_file(Path(__file__)),
        "decision_rows_sha256": sha256_file(rows_path),
        "groups_per_campaign": groups_per_campaign,
        "resource_order": "fewest GPU replicas then fewest CPU affinity cores",
        "throughput_unit": "requests/s = examples_per_second / batch_size",
        "minimum_predicted_relative_margin": "min((target_latency - predicted_latency)/target_latency, (predicted_rps - target_rps)/target_rps) on chosen candidate",
        "missed_feasible_definition": "oracle_feasible and not true_success, including abstention and wrong approval",
        "scenario_caveat": "nine factor pairs reuse each group's same eight measurements and are not independent user requests",
        "interpretation_limit": "batch p95 and generated SLO on one host; two GPU replicas are not two VMs or a measured scaling action",
        "aggregates": summarize(rows),
    }
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/saved_gpu_shape_decision_audit_v1")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"groups_per_campaign": result["groups_per_campaign"],
                      "original_all": [row for row in result["aggregates"]["original"]
                                       if row["scope"] == "all"]}, indent=2))
