"""Freeze, collect, and score unseen Philly [35,45) machines exactly once."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests

import compare_philly_day_ahead_baselines_posthoc as comparator
import evaluate_philly_day_ahead_holdout as earlier
import evaluate_philly_machine_calibration as replay
import explore_philly_slot_day_calibration as development
import fetch_philly_gpu_sample as source
import fetch_philly_machine_calibration as collector
import normalize_philly_gpu_sample as normalizer


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/msr_philly_2017_slot_holdout"
OUT = ROOT / "artifacts/philly_slot_day_holdout_v1"
PROTOCOL = ROOT / "reports/philly_slot_day_calibration_holdout_protocol_frozen.md"
DEVELOPMENT = ROOT / "artifacts/philly_slot_day_calibration_exploratory_v1/results.json"
FREEZE = OUT / "freeze.json"
RAW = DATA / "gpu_util_machine_hash35to44pct.parquet"
RAW_AUDIT = DATA / "gpu_util_slot_holdout_audit.json"
MACHINES = DATA / "machine_list.json"
NORMALIZED = DATA / "gpu_util_normalized_first_width.parquet"
NORMALIZED_AUDIT = DATA / "gpu_util_normalized_first_width_audit.json"
SUPPORT = OUT / "support_audit.json"
STARTED = OUT / "score_started.json"
RESULT = OUT / "result.json"
PREDICTIONS = OUT / "test_predictions.parquet"
LOWER, UPPER = 35, 45
MIN_FREE_BYTES = 2_800_000_000
MAX_SAMPLE_BYTES = 300_000_000
SELECTION_RULE = "35 <= int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 45"
SOURCE_AUDITS = (
    *earlier.OLD_AUDITS,
    ROOT / "data/external/msr_philly_2017_day_ahead_holdout/gpu_util_day_ahead_holdout_audit.json",
)
PINNED_FILES = {
    "protocol_sha256": PROTOCOL,
    "evaluator_sha256": Path(__file__).resolve(),
    "development_code_sha256": Path(development.__file__),
    "development_protocol_sha256": ROOT / "reports/philly_slot_day_calibration_exploratory_protocol.md",
    "development_results_sha256": DEVELOPMENT,
    "source_parser_sha256": Path(source.__file__),
    "inner_collector_sha256": Path(collector.__file__),
    "normalizer_sha256": Path(normalizer.__file__),
    "candidate_builder_sha256": ROOT / "scripts/prepare_philly_gpu_risk_candidates.py",
    "replay_sha256": Path(replay.__file__),
    "old_evaluator_sha256": Path(earlier.__file__),
    "old_baseline_sha256": Path(comparator.__file__),
    "model_spec_sha256": earlier.MODEL_SPEC,
    "method_test_sha256": ROOT / "tests/test_explore_philly_slot_day_calibration.py",
    "holdout_test_sha256": ROOT / "tests/test_evaluate_philly_slot_day_holdout.py",
}


def selected(machine_id: str) -> bool:
    return LOWER <= collector.machine_bucket(machine_id) < UPPER


def require_room(path: Path) -> None:
    fs = os.statvfs(path)
    if fs.f_bavail * fs.f_frsize < MIN_FREE_BYTES:
        raise RuntimeError("less than 2.8 GB free for streamed sample")


def freeze() -> dict:
    if DATA.exists() or OUT.exists():
        raise FileExistsError("new machine data or freeze already exists")
    prior = json.loads(DEVELOPMENT.read_text(encoding="utf-8"))
    if (prior.get("status") != "GO_TO_NEW_MACHINE_COLLECTION"
            or not all(prior.get("gate", {}).values())
            or prior.get("script_sha256") != earlier.sha256(Path(development.__file__))
            or prior.get("protocol_sha256") != earlier.sha256(
                ROOT / "reports/philly_slot_day_calibration_exploratory_protocol.md")
            or earlier.sha256(earlier.MODEL_SPEC) != earlier.MODEL_SHA256):
        raise ValueError("development result or original-only model changed")
    if not all(path.is_file() for path in PINNED_FILES.values()):
        raise FileNotFoundError("protocol, implementation, or contract test missing")
    require_room(ROOT)
    record = {
        "status": "FROZEN_BEFORE_BUCKET_35_45_COLLECTION",
        "source_commit": source.SOURCE_COMMIT,
        "source_archive_sha256": source.EXPECTED_SHA256,
        "source_archive_bytes": source.EXPECTED_SIZE,
        "source_gpu_csv_rows": 44_750_640,
        "sample_lower_inclusive": LOWER,
        "sample_upper_exclusive": UPPER,
        "minimum_free_bytes": MIN_FREE_BYTES,
        "maximum_compact_sample_bytes": MAX_SAMPLE_BYTES,
        "prior_audit_sha256": {path.parent.name: earlier.sha256(path) for path in SOURCE_AUDITS},
        **{key: earlier.sha256(path) for key, path in PINNED_FILES.items()},
    }
    OUT.mkdir(parents=True, exist_ok=False)
    earlier.atomic_json(FREEZE, record)
    return record


def verify_freeze() -> dict:
    record = json.loads(FREEZE.read_text(encoding="utf-8"))
    if (record.get("status") != "FROZEN_BEFORE_BUCKET_35_45_COLLECTION"
            or record.get("source_archive_sha256") != source.EXPECTED_SHA256
            or record.get("source_archive_bytes") != source.EXPECTED_SIZE
            or record.get("sample_lower_inclusive") != LOWER
            or record.get("sample_upper_exclusive") != UPPER):
        raise ValueError("wrong or changed freeze")
    for key, path in PINNED_FILES.items():
        if record.get(key) != earlier.sha256(path):
            raise ValueError(f"frozen {key} changed")
    for path in SOURCE_AUDITS:
        if record["prior_audit_sha256"].get(path.parent.name) != earlier.sha256(path):
            raise ValueError("prior source audit changed")
    return record


def collect() -> dict:
    frozen = verify_freeze()
    if DATA.exists():
        raise FileExistsError("new machine collection already started")
    DATA.mkdir(parents=True, exist_ok=False)
    require_room(DATA)
    with requests.Session() as session:
        source.validate_http_head(session)
        with session.get(source.URL, stream=True, timeout=(30, 180),
                         headers={"Accept-Encoding": "identity",
                                  "User-Agent": "ai-mcmp-research/philly-slot-day-holdout"}) as response:
            if (response.status_code != 200
                    or int(response.headers.get("Content-Length", "-1")) != source.EXPECTED_SIZE
                    or response.headers.get("ETag") != source.EXPECTED_ETAG
                    or response.headers.get("Content-Encoding", "identity") != "identity"):
                raise ValueError("pinned Philly GET headers differ")
            response.raw.decode_content = False
            with ExitStack() as overrides:
                overrides.enter_context(patch.object(collector, "machine_selected", selected))
                overrides.enter_context(patch.object(collector, "LOWER_RESIDUE", LOWER))
                overrides.enter_context(patch.object(collector, "UPPER_RESIDUE", UPPER))
                overrides.enter_context(patch.object(collector, "SELECTION_RULE", SELECTION_RULE))
                overrides.enter_context(patch.object(source, "MIN_FREE_BYTES", MIN_FREE_BYTES))
                audit = collector.scan_archive(response.raw, output=RAW,
                                               expected_size=source.EXPECTED_SIZE,
                                               expected_sha256=source.EXPECTED_SHA256,
                                               max_sample_bytes=MAX_SAMPLE_BYTES)
    ids = audit["gpu_csv"]["selected_machine_ids"]
    prior_ids = set()
    for path in SOURCE_AUDITS:
        prior_ids.update(json.loads(path.read_text(encoding="utf-8"))
                         ["gpu_csv"]["selected_machine_ids"])
    if (audit.get("complete") is not True or audit.get("integrity_pass") is not True
            or audit["gpu_csv"]["rows"] != frozen["source_gpu_csv_rows"]
            or audit.get("sample_rule") != SELECTION_RULE
            or not ids or any(not selected(mid) for mid in ids)
            or set(ids) & prior_ids):
        raise ValueError("new machine sample failed source or ID audit")
    machine_records = audit["machine_list"].pop("records")
    earlier.atomic_json(MACHINES, machine_records)
    audit["machine_list"]["path"] = str(MACHINES)
    audit["machine_list"]["sha256"] = earlier.sha256(MACHINES)
    audit.update({"http_content_length": source.EXPECTED_SIZE,
                  "http_etag": source.EXPECTED_ETAG,
                  "pre_fetch_freeze_sha256": earlier.sha256(FREEZE),
                  "wrapper_collector_sha256": frozen["evaluator_sha256"],
                  "inner_collector_sha256": frozen["inner_collector_sha256"],
                  "minimum_free_bytes": MIN_FREE_BYTES,
                  "maximum_compact_sample_bytes": MAX_SAMPLE_BYTES})
    earlier.atomic_json(RAW_AUDIT, audit)
    return audit


def normalize() -> dict:
    frozen = verify_freeze()
    raw = json.loads(RAW_AUDIT.read_text(encoding="utf-8"))
    if (raw.get("complete") is not True or raw.get("integrity_pass") is not True
            or raw.get("pre_fetch_freeze_sha256") != earlier.sha256(FREEZE)
            or raw.get("sample_rule") != SELECTION_RULE
            or raw.get("sample_sha256") != earlier.sha256(RAW)
            or NORMALIZED.exists() or NORMALIZED_AUDIT.exists()):
        raise ValueError("unverified or already normalized sample")
    require_room(DATA)
    with patch.object(normalizer, "MIN_FREE_BYTES", MIN_FREE_BYTES):
        result = normalizer.normalize_sample(RAW, RAW_AUDIT, NORMALIZED,
                                             NORMALIZED_AUDIT)
    if (result["rows"] != raw["gpu_csv"]["selected_rows"]
            or result["source_row_index_sequence_sha256"]
            != result["derived_row_index_sequence_sha256"]):
        raise ValueError("normalized row sequence changed")
    result["normalizer_wrapper_sha256"] = frozen["evaluator_sha256"]
    result["minimum_free_bytes"] = MIN_FREE_BYTES
    temp = NORMALIZED_AUDIT.with_name(NORMALIZED_AUDIT.name + ".wrapper.tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    os.replace(temp, NORMALIZED_AUDIT)
    return result


def verify_lineage(frozen: dict) -> dict:
    raw = json.loads(RAW_AUDIT.read_text(encoding="utf-8"))
    norm = json.loads(NORMALIZED_AUDIT.read_text(encoding="utf-8"))
    previous = [json.loads(path.read_text(encoding="utf-8"))["gpu_csv"]
                for path in SOURCE_AUDITS]
    expected = previous[0]
    for field in ("rows", "machine_count", "min_time_utc_s", "max_time_utc_s",
                  "gpu_field_count_histogram"):
        if (any(item[field] != expected[field] for item in previous)
                or raw["gpu_csv"][field] != expected[field]):
            raise ValueError("full archive audit differs")
    ids = raw["gpu_csv"]["selected_machine_ids"]
    seen = {mid for item in previous for mid in item["selected_machine_ids"]}
    if (raw.get("complete") is not True or raw.get("integrity_pass") is not True
            or raw.get("compressed_sha256") != source.EXPECTED_SHA256
            or raw.get("compressed_bytes_read") != source.EXPECTED_SIZE
            or raw.get("sample_sha256") != earlier.sha256(RAW)
            or raw.get("pre_fetch_freeze_sha256") != earlier.sha256(FREEZE)
            or raw.get("wrapper_collector_sha256") != frozen["evaluator_sha256"]
            or raw.get("sample_rule") != SELECTION_RULE
            or not ids or len(ids) != len(set(ids))
            or any(not selected(mid) for mid in ids) or seen & set(ids)):
        raise ValueError("raw source lineage invalid")
    if (norm.get("integrity_pass") is not True
            or norm.get("source_sha256") != raw["sample_sha256"]
            or norm.get("source_archive_sha256") != source.EXPECTED_SHA256
            or norm.get("normalization_script_sha256") != frozen["normalizer_sha256"]
            or norm.get("normalizer_wrapper_sha256") != frozen["evaluator_sha256"]
            or norm.get("output_sha256") != earlier.sha256(NORMALIZED)
            or norm.get("rows") != raw["gpu_csv"]["selected_rows"]
            or norm.get("source_row_index_sequence_sha256")
            != norm.get("derived_row_index_sequence_sha256")
            or set(norm.get("machines", {})) != set(ids)):
        raise ValueError("normalized source lineage invalid")
    return {
        "raw_audit_sha256": earlier.sha256(RAW_AUDIT),
        "normalized_audit_sha256": earlier.sha256(NORMALIZED_AUDIT),
        "normalized_sha256": earlier.sha256(NORMALIZED),
        "raw_machines": len(ids),
        "raw_rows": int(raw["gpu_csv"]["selected_rows"]),
        "width": {mid: {"rows": int(info["rows"]),
                         "invalid_rows": int(info["invalid_rows"])}
                  for mid, info in norm["machines"].items()},
    }


def audit() -> dict:
    frozen = verify_freeze()
    if SUPPORT.exists() or STARTED.exists():
        raise FileExistsError("score-free support already exists")
    lineage = verify_lineage(frozen)
    examples, info = replay.examples_from_normalized(NORMALIZED, low=LOWER,
                                                      high=UPPER, labels=False)
    support = earlier.score_free_support(examples, info, lineage)
    record = {"status": "SCORE_FREE_SUPPORT_PASS" if support["all_passed"]
              else "SUPPORT_NO_GO_NO_SCORE",
              "freeze_sha256": earlier.sha256(FREEZE),
              "lineage": lineage, "support": support}
    earlier.atomic_json(SUPPORT, record)
    return record


def score_once() -> dict:
    frozen = verify_freeze()
    support = json.loads(SUPPORT.read_text(encoding="utf-8"))
    if (support["status"] != "SCORE_FREE_SUPPORT_PASS"
            or support["freeze_sha256"] != earlier.sha256(FREEZE)
            or support["lineage"] != verify_lineage(frozen)
            or STARTED.exists() or RESULT.exists() or PREDICTIONS.exists()):
        raise ValueError("support, lineage, or one-time score boundary failed")
    earlier.atomic_json(STARTED, {"status": "ONE_TIME_NEW_MACHINE_SCORE_STARTED",
                                  "freeze_sha256": earlier.sha256(FREEZE),
                                  "support_sha256": earlier.sha256(SUPPORT)})
    examples, _ = replay.examples_from_normalized(NORMALIZED, low=LOWER,
                                                  high=UPPER, labels=True)
    test = examples.loc[examples.split.eq("test") &
                        examples.cohort.eq("complete") &
                        examples.current_state.eq("below_80")].copy()
    if (len(test) != support["support"]["complete_decisions"]
            or replay._candidate_fingerprint(test)
            != support["support"]["test_complete_feature_observability_sha256"]
            or int(test.y.sum()) != sum(item["positive"] for item in
                                        support["support"]["half_class_counts"])):
        raise ValueError("scored rows differ from score-free support")
    model = replay._load_model()
    test["p0"] = model.predict_pooled(test)
    test = test.sort_values(["decision_utc_s", "machine_hash", "gpu_index"],
                            kind="mergesort").reset_index(drop=True)
    standard = comparator.predict(test)
    independently_exact, slot = development.slot_day_predictions(test)
    if not np.allclose(independently_exact, standard["previous_day_exact"],
                       atol=1e-12, rtol=0):
        raise ValueError("strong exact baseline not reproduced")
    predictions = {"p0": standard["p0"],
                   "previous_day_newton": standard["previous_day_newton"],
                   "previous_day_exact": standard["previous_day_exact"],
                   "previous_day_machine_slot": slot}
    scores = development.metrics(test, predictions)
    boot = earlier.machine_bootstrap(test, predictions["previous_day_exact"], slot)
    exact, candidate = scores["previous_day_exact"], scores["previous_day_machine_slot"]
    loss = pd.DataFrame({"machine": test.machine_hash,
                         "exact": (test.y.to_numpy(float) - predictions["previous_day_exact"]) ** 2,
                         "slot": (test.y.to_numpy(float) - slot) ** 2}).groupby("machine").mean()
    wins = int((loss.slot < loss.exact).sum())
    checks = {
        "row_brier_at_least_1pct_below_exact":
            candidate["row_brier"] <= 0.99 * exact["row_brier"],
        "machine_equal_brier_below_exact":
            candidate["machine_equal_brier"] < exact["machine_equal_brier"],
        "both_calendar_halves_below_exact":
            (candidate["first_seven_days_brier"] < exact["first_seven_days_brier"]
             and candidate["last_eight_days_brier"] < exact["last_eight_days_brier"]),
        "at_least_60pct_machines_improve": wins >= math.ceil(0.6 * len(loss)),
        "ap_drop_less_than_0p005":
            candidate["average_precision"] > exact["average_precision"] - 0.005,
        "machine_bootstrap_upper_below_zero": boot["percentile_95"][1] < 0,
    }
    rows = test[["machine_hash", "gpu_index", "decision_utc_s", "utc_day", "y"]].copy()
    for name, values in predictions.items():
        rows[name] = values
    temp = PREDICTIONS.with_name(PREDICTIONS.name + ".tmp")
    rows.to_parquet(temp, index=False, compression="zstd")
    os.replace(temp, PREDICTIONS)
    result = {"status": "NEW_MACHINE_EXPLORATORY_GO" if all(checks.values())
              else "NEW_MACHINE_NO_GO",
              "scope": "Philly GPU utilization risk only; no VM action or user SLO",
              "freeze_sha256": earlier.sha256(FREEZE),
              "support_sha256": earlier.sha256(SUPPORT),
              "predictions_sha256": earlier.sha256(PREDICTIONS),
              "test_rows": len(test), "test_machines": len(loss),
              "machines_slot_better_than_exact": wins,
              "scores": scores,
              "machine_bootstrap": boot,
              "gate": {"all_passed": bool(all(checks.values())), "checks": checks,
                       "failed": [key for key, passed in checks.items() if not passed]},
              "prior_three_cohorts_exposed_before_method_selection": True,
              "new_bucket_rows_seen_before_freeze": False}
    earlier.atomic_json(RESULT, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "collect", "normalize", "audit", "score_once"))
    args = parser.parse_args()
    result = {"freeze": freeze, "collect": collect, "normalize": normalize,
              "audit": audit, "score_once": score_once}[args.command]()
    print(json.dumps({"command": args.command, "status": result.get("status"),
                      "support": result.get("support", {}).get("checks"),
                      "gate": result.get("gate")}, sort_keys=True))


if __name__ == "__main__":
    main()
