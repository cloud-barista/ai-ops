"""One-time disjoint-machine test of day-ahead Philly GPU risk calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

import analyze_philly_day_ahead_calibration_exploratory as development
import evaluate_philly_machine_calibration as replay
import fetch_philly_day_ahead_holdout as collector
import fetch_philly_gpu_sample as source
import normalize_philly_day_ahead_holdout as normalize_wrapper
import normalize_philly_gpu_sample as normalizer


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1"
FREEZE = OUTPUT / "freeze.json"
SUPPORT = OUTPUT / "support_audit.json"
STARTED = OUTPUT / "score_started.json"
RESULT = OUTPUT / "result.json"
PREDICTIONS = OUTPUT / "test_predictions.parquet"
PROTOCOL = ROOT / "reports/philly_day_ahead_calibration_holdout_protocol_frozen.md"
TEST_FILE = ROOT / "tests/test_philly_day_ahead_holdout.py"
MODEL_SPEC = ROOT / "artifacts/philly_machine_holdout_model_v1/model_spec.json"
MODEL_SHA256 = "861005a57cbf9f370a8f36682ca9004a3301af01f45e9c797949bf6319ad76a3"
OLD_AUDITS = (
    ROOT / "data/external/msr_philly_2017/gpu_util_audit.json",
    ROOT / "data/external/msr_philly_2017_holdout/gpu_util_holdout_audit.json",
    ROOT / "data/external/msr_philly_2017_calibration_holdout/gpu_util_calibration_holdout_audit.json",
)
CODE_INPUTS = {
    "collector_sha256": Path(collector.__file__),
    "inner_collector_sha256": ROOT / "scripts/fetch_philly_machine_calibration.py",
    "source_parser_sha256": Path(source.__file__),
    "normalizer_wrapper_sha256": Path(normalize_wrapper.__file__),
    "normalizer_sha256": Path(normalizer.__file__),
    "candidate_builder_sha256": ROOT / "scripts/prepare_philly_gpu_risk_candidates.py",
    "replay_sha256": Path(replay.__file__),
    "development_method_sha256": Path(development.__file__),
    "evaluator_sha256": Path(__file__),
    "test_sha256": TEST_FILE,
    "protocol_sha256": PROTOCOL,
    "model_spec_sha256": MODEL_SPEC,
}
START_DAY = 17501  # 2017-12-01 UTC
END_DAY_EXCLUSIVE = 17516  # 2017-12-16 UTC


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(path)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def freeze() -> dict:
    if collector.OUTPUT_DIR.exists() or OUTPUT.exists():
        raise FileExistsError("new bucket or freeze already exists")
    if sha256(MODEL_SPEC) != MODEL_SHA256:
        raise ValueError("original-only Philly model changed")
    if not TEST_FILE.is_file() or not PROTOCOL.is_file():
        raise FileNotFoundError("final protocol or synthetic test missing")
    development_result = ROOT / "artifacts/philly_day_ahead_calibration_exploratory_v1/summary.json"
    prior = json.loads(development_result.read_text(encoding="utf-8"))
    if (prior["status"] != "POST_HOC_EXPLORATORY_ONLY"
            or prior["script_sha256"] != sha256(Path(development.__file__))):
        raise ValueError("development method or provenance changed")
    record = {
        "status": "FROZEN_BEFORE_BUCKET_25_35_COLLECTION",
        "source_commit": source.SOURCE_COMMIT,
        "source_archive_sha256": source.EXPECTED_SHA256,
        "source_archive_bytes": source.EXPECTED_SIZE,
        "sample_lower_inclusive": collector.LOWER_RESIDUE,
        "sample_upper_exclusive": collector.UPPER_RESIDUE,
        "source_gpu_csv_rows": 44_750_640,
        "test_first_utc_day": START_DAY,
        "test_last_utc_day_exclusive": END_DAY_EXCLUSIVE,
        "minimum_free_bytes": collector.MIN_FREE_BYTES,
        "maximum_sample_bytes": collector.MAX_SAMPLE_BYTES,
        "development_summary_sha256": sha256(development_result),
        "old_source_audit_sha256": {path.parent.name: sha256(path) for path in OLD_AUDITS},
        **{name: sha256(path) for name, path in CODE_INPUTS.items()},
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    atomic_json(FREEZE, record)
    return record


def verify_freeze() -> dict:
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (frozen.get("status") != "FROZEN_BEFORE_BUCKET_25_35_COLLECTION"
            or frozen.get("source_archive_sha256") != source.EXPECTED_SHA256
            or frozen.get("model_spec_sha256") != MODEL_SHA256
            or frozen.get("sample_lower_inclusive") != collector.LOWER_RESIDUE
            or frozen.get("sample_upper_exclusive") != collector.UPPER_RESIDUE):
        raise ValueError("new machine freeze changed")
    for key, path in CODE_INPUTS.items():
        if frozen.get(key) != sha256(path):
            raise ValueError(f"frozen {key} changed")
    for path in OLD_AUDITS:
        if frozen["old_source_audit_sha256"].get(path.parent.name) != sha256(path):
            raise ValueError("earlier source audit changed")
    return frozen


def verify_source_lineage(frozen: dict) -> dict:
    raw_path = collector.OUTPUT_DIR / collector.OUTPUT_NAME
    raw_audit_path = collector.OUTPUT_DIR / collector.AUDIT_NAME
    normalized_path = normalize_wrapper.NORMALIZED
    normalized_audit_path = normalize_wrapper.AUDIT
    raw = json.loads(raw_audit_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_audit_path.read_text(encoding="utf-8"))
    if (raw.get("complete") is not True or raw.get("integrity_pass") is not True
            or raw.get("pre_fetch_freeze_sha256") != sha256(FREEZE)
            or raw.get("wrapper_collector_sha256") != frozen["collector_sha256"]
            or raw.get("inner_collector_sha256") != frozen["inner_collector_sha256"]
            or raw.get("compressed_sha256") != source.EXPECTED_SHA256
            or raw.get("compressed_bytes_read") != source.EXPECTED_SIZE
            or raw.get("sample_rule") != collector.SELECTION_RULE
            or raw.get("gpu_csv", {}).get("rows") != 44_750_640
            or raw.get("sample_sha256") != sha256(raw_path)):
        raise ValueError("new bucket source lineage invalid")
    selected = raw["gpu_csv"]["selected_machine_ids"]
    prior_ids = set()
    first = json.loads(OLD_AUDITS[0].read_text(encoding="utf-8"))["gpu_csv"]
    for path in OLD_AUDITS:
        old = json.loads(path.read_text(encoding="utf-8"))
        previous = old["gpu_csv"]
        prior_ids.update(previous["selected_machine_ids"])
        for key in ("rows", "machine_count", "min_time_utc_s", "max_time_utc_s",
                    "gpu_field_count_histogram"):
            if previous[key] != first[key]:
                raise ValueError("previous full-source audits disagree")
    for key in ("rows", "machine_count", "min_time_utc_s", "max_time_utc_s",
                "gpu_field_count_histogram"):
        if raw["gpu_csv"][key] != first[key]:
            raise ValueError("new full-source audit differs from earlier audits")
    if (not selected or len(set(selected)) != len(selected)
            or any(not collector.selected(mid) for mid in selected)
            or set(selected) & prior_ids):
        raise ValueError("new machine IDs overlap or escape fixed hash interval")
    if (normalized.get("integrity_pass") is not True
            or normalized.get("source_sha256") != raw["sample_sha256"]
            or normalized.get("source_archive_sha256") != source.EXPECTED_SHA256
            or normalized.get("normalization_script_sha256") != frozen["normalizer_sha256"]
            or normalized.get("normalizer_wrapper_sha256") != frozen["normalizer_wrapper_sha256"]
            or normalized.get("output_sha256") != sha256(normalized_path)
            or normalized.get("rows") != raw["gpu_csv"]["selected_rows"]
            or normalized.get("source_row_index_sequence_sha256")
            != normalized.get("derived_row_index_sequence_sha256")
            or set(normalized.get("machines", {})) != set(selected)):
        raise ValueError("normalized new machine rows changed")
    return {"raw_audit_sha256": sha256(raw_audit_path),
            "normalized_audit_sha256": sha256(normalized_audit_path),
            "normalized_sha256": sha256(normalized_path),
            "raw_machines": len(selected), "raw_rows": int(raw["gpu_csv"]["selected_rows"]),
            "width": {mid: {"rows": int(info["rows"]),
                            "invalid_rows": int(info["invalid_rows"])}
                      for mid, info in normalized["machines"].items()}}


def score_free_support(examples: pd.DataFrame, info: dict, lineage: dict) -> dict:
    test = examples.loc[examples.split.eq("test") &
                        examples.cohort.eq("complete") &
                        examples.current_state.eq("below_80")]
    eligible = test.loc[test.future_complete]
    halves = info["test_half_gate_counts"]
    days = sorted(int(day) for day in eligible.utc_day.unique())
    width_ok = all(record["invalid_rows"] <= .01*record["rows"]
                   for record in lineage["width"].values())
    checks = {
        "all_new_machine_width_mismatch_at_most_1pct": width_ok,
        "all_15_test_days_present": days == list(range(START_DAY, END_DAY_EXCLUSIVE)),
        "at_least_25_eligible_machines": eligible.machine_hash.nunique() >= 25,
        "at_least_400000_complete_decisions": len(eligible) >= 400_000,
        "each_half_at_least_5000_positives_and_negatives": (
            len(halves) == 2 and all(
                item["positive"] >= 5000 and item["negative"] >= 5000
                for item in halves)),
    }
    return {"all_passed": bool(all(checks.values())), "checks": checks,
            "eligible_machines": int(eligible.machine_hash.nunique()),
            "complete_decisions": int(len(eligible)),
            "candidate_decisions": int(len(test)),
            "observed_days": days,
            "half_class_counts": halves,
            "test_candidate_feature_observability_sha256": replay._candidate_fingerprint(test),
            "test_complete_feature_observability_sha256": replay._candidate_fingerprint(eligible)}


def audit() -> dict:
    frozen = verify_freeze()
    if SUPPORT.exists() or STARTED.exists():
        raise FileExistsError("score-free support audit already exists")
    lineage = verify_source_lineage(frozen)
    examples, info = replay.examples_from_normalized(
        normalize_wrapper.NORMALIZED, low=25, high=35, labels=False)
    support = score_free_support(examples, info, lineage)
    result = {"status": "SCORE_FREE_SUPPORT_PASS" if support["all_passed"] else
              "SUPPORT_NO_GO_NO_SCORE",
              "freeze_sha256": sha256(FREEZE),
              "lineage": lineage, "support": support}
    atomic_json(SUPPORT, result)
    return result


def machine_bootstrap(frame: pd.DataFrame, p0: np.ndarray,
                      candidate: np.ndarray) -> dict:
    y = frame.y.to_numpy(float)
    ids = frame.machine_hash.to_numpy(str)
    grouped = pd.DataFrame({"machine": ids,
                            "base": (y-p0)**2,
                            "candidate": (y-candidate)**2}).groupby("machine")
    totals = grouped.agg(n=("base", "size"),
                         base_sum=("base", "sum"),
                         candidate_sum=("candidate", "sum"))
    n = totals.n.to_numpy(float)
    diff = totals.candidate_sum.to_numpy(float)-totals.base_sum.to_numpy(float)
    rng = np.random.default_rng(43)
    draws = rng.integers(0, len(n), size=(2000, len(n)))
    values = diff[draws].sum(axis=1)/n[draws].sum(axis=1)
    return {"draws": 2000, "seed": 43,
            "candidate_minus_base_row_brier": float(diff.sum()/n.sum()),
            "percentile_95": [float(np.quantile(values, .025)),
                              float(np.quantile(values, .975))]}


def evaluate_scores(frame: pd.DataFrame, p0: np.ndarray,
                    machine: np.ndarray, global_: np.ndarray) -> dict:
    probabilities = {"frozen_pooled": p0,
                     "previous_day_global": global_,
                     "previous_day_machine": machine}
    score = development.metrics(frame, probabilities)
    boot = machine_bootstrap(frame, p0, machine)
    candidate = score["previous_day_machine"]
    base = score["frozen_pooled"]
    global_score = score["previous_day_global"]
    checks = {
        "row_brier_3pct_below_frozen_pooled": candidate["row_brier"] <= .97*base["row_brier"],
        "row_brier_1pct_below_global_day_ahead": candidate["row_brier"] <= .99*global_score["row_brier"],
        "machine_equal_brier_below_both": candidate["machine_equal_brier"] < min(
            base["machine_equal_brier"], global_score["machine_equal_brier"]),
        "both_calendar_halves_better_than_frozen": (
            candidate["first_seven_days_row_brier"] < base["first_seven_days_row_brier"]
            and candidate["last_eight_days_row_brier"] < base["last_eight_days_row_brier"]),
        "at_least_60pct_machines_improve": score["machine_win_count"] >= math.ceil(
            .6*score["machine_count"]),
        "average_precision_not_worse_than_frozen": candidate["average_precision"] >= base["average_precision"],
        "machine_bootstrap_upper_below_zero": boot["percentile_95"][1] < 0,
    }
    return {"methods": score, "machine_bootstrap": boot,
            "gate": {"all_passed": bool(all(checks.values())),
                     "checks": checks,
                     "failed": [key for key, passed in checks.items() if not passed]}}


def score_once() -> dict:
    frozen = verify_freeze()
    support = json.loads(SUPPORT.read_text(encoding="utf-8"))
    if (support["status"] != "SCORE_FREE_SUPPORT_PASS"
            or support["freeze_sha256"] != sha256(FREEZE)
            or support["lineage"] != verify_source_lineage(frozen)
            or STARTED.exists() or RESULT.exists() or PREDICTIONS.exists()):
        raise ValueError("support, lineage, or one-time score boundary failed")
    atomic_json(STARTED, {"status": "ONE_TIME_NEW_MACHINE_SCORE_STARTED",
                          "freeze_sha256": sha256(FREEZE),
                          "support_sha256": sha256(SUPPORT)})
    examples, info = replay.examples_from_normalized(
        normalize_wrapper.NORMALIZED, low=25, high=35, labels=True)
    test = examples.loc[examples.split.eq("test") &
                        examples.cohort.eq("complete") &
                        examples.current_state.eq("below_80")].copy()
    if (len(test) != support["support"]["complete_decisions"]
            or replay._candidate_fingerprint(test)
            != support["support"]["test_complete_feature_observability_sha256"]
            or int(test.y.sum()) != sum(item["positive"]
                                       for item in support["support"]["half_class_counts"])):
        raise ValueError("scored rows differ from score-free support audit")
    model = replay._load_model()
    test["p0"] = model.predict_pooled(test)
    test = test.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                            kind="mergesort").reset_index(drop=True)
    adaptive, global_, daily = development.predict_day_ahead(test)
    p0 = test.p0.to_numpy(float)
    assessment = evaluate_scores(test, p0, adaptive, global_)
    rows = test[["machine_hash", "gpu_index", "decision_utc_s", "utc_day", "y"]].copy()
    rows["frozen_pooled"] = p0
    rows["previous_day_global"] = global_
    rows["previous_day_machine"] = adaptive
    temp = PREDICTIONS.with_name(PREDICTIONS.name+".tmp")
    rows.to_parquet(temp, index=False, compression="zstd")
    os.replace(temp, PREDICTIONS)
    result = {"status": "NEW_MACHINE_EXPLORATORY_GO" if assessment["gate"]["all_passed"] else
              "NEW_MACHINE_NO_GO",
              "scope": "Philly complete/below80 GPU-utilization risk; no VM action or user SLO",
              "freeze_sha256": sha256(FREEZE),
              "support_sha256": sha256(SUPPORT),
              "predictions_sha256": sha256(PREDICTIONS),
              "test_rows": int(len(test)),
              "test_machines": int(test.machine_hash.nunique()),
              "calibration_support_by_day": daily,
              "assessment": assessment,
              "development_cohorts_exposed_before_method_selection": True,
              "new_bucket_rows_seen_before_freeze": False}
    atomic_json(RESULT, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "audit", "score_once"))
    args = parser.parse_args()
    output = (freeze() if args.command == "freeze" else
              audit() if args.command == "audit" else score_once())
    print(json.dumps({"status": output["status"],
                      "support": output.get("support", {}).get("checks"),
                      "gate": output.get("assessment", {}).get("gate")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
