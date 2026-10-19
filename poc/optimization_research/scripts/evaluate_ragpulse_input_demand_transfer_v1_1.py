"""Narrow parser repair for the frozen RAGPulse transfer test."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re

import joblib
import numpy as np

import evaluate_azure_code_input_demand as coding
import evaluate_ragpulse_input_demand_transfer as original


ROOT = Path(__file__).resolve().parents[1]
ADDENDUM = ROOT / "reports/ragpulse_input_demand_transfer_parser_addendum_v1_1.md"
TEST_FILE = ROOT / "tests/test_ragpulse_input_demand_transfer_v1_1.py"
FAILURE = original.OUTPUT / "parser_failure.json"
OUTPUT = ROOT / "artifacts/ragpulse_input_demand_transfer_v1_1"
RELEASE = OUTPUT / "release_manifest.json"
NUMERIC_SECONDS = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def seconds(value: object, line_no: int) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"invalid timestamp at line {line_no}")
    if isinstance(value, str):
        if not NUMERIC_SECONDS.fullmatch(value):
            raise ValueError(f"invalid timestamp string at line {line_no}")
        representation = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f"nonfinite timestamp at line {line_no}")
        representation = str(value)
    else:
        raise ValueError(f"invalid timestamp type at line {line_no}")
    try:
        parsed = Decimal(representation)
    except InvalidOperation as exc:
        raise ValueError(f"invalid timestamp decimal at line {line_no}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"invalid timestamp value at line {line_no}")
    return parsed


def aggregate_jsonl(path: Path, n_slots: int = original.N_SLOTS) -> tuple[np.ndarray, np.ndarray, int]:
    tokens = np.zeros(n_slots, dtype=np.int64)
    counts = np.zeros(n_slots, dtype=np.int64)
    previous_timestamp = Decimal(-1)
    rows = 0
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                stamp = seconds(record["timestamp"], line_no)
                value = record["input_length"]
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"invalid JSON or field at line {line_no}") from exc
            if (stamp >= n_slots * 300 or stamp < previous_timestamp):
                raise ValueError(f"timestamp outside grid or out of order at line {line_no}")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid input_length at line {line_no}")
            slot = int(stamp // 300)
            if tokens[slot] > np.iinfo(np.int64).max - value:
                raise ValueError("token sum overflow")
            tokens[slot] += value
            counts[slot] += 1
            previous_timestamp = stamp
            rows += 1
    if rows != original.EXPECTED_ROWS or int(counts.sum()) != rows:
        raise ValueError("unexpected official request count")
    return tokens, counts, rows


def expected_release() -> dict:
    frozen_path = original.OUTPUT / "freeze.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    model_path = original.OUTPUT / "weekend_free_coding_model.joblib"
    if (frozen["status"] != "FROZEN_BEFORE_RAGPULSE_BODY"
            or frozen["environment"] != original.environment()
            or frozen["model_sha256"] != coding.file_sha256(model_path)):
        raise ValueError("original model freeze changed")
    failure = json.loads(FAILURE.read_text(encoding="utf-8"))
    if (failure["status"] != "PARSER_FAILED_BEFORE_SUPPORT_OR_MODEL_SCORE"
            or failure["freeze_sha256"] != coding.file_sha256(frozen_path)
            or failure["v1_script_sha256"] != coding.file_sha256(Path(original.__file__))):
        raise ValueError("parser failure record changed")
    source_sha = original.verify_external_source()
    if failure["source_sha256"] != source_sha:
        raise ValueError("parser failure source changed")
    return {"status": "RELEASE_BEFORE_RAGPULSE_AGGREGATION_AND_SCORE",
            "source_sha256": source_sha,
            "source_git_blob_sha1": original.SOURCE_GIT_BLOB_SHA1,
            "v1_freeze_sha256": coding.file_sha256(frozen_path),
            "v1_model_sha256": coding.file_sha256(model_path),
            "v1_script_sha256": coding.file_sha256(Path(original.__file__)),
            "v1_test_sha256": coding.file_sha256(original.TEST_FILE),
            "v1_protocol_sha256": coding.file_sha256(original.PROTOCOL),
            "parser_failure_sha256": coding.file_sha256(FAILURE),
            "addendum_sha256": coding.file_sha256(ADDENDUM),
            "v1_1_script_sha256": coding.file_sha256(Path(__file__)),
            "v1_1_test_sha256": coding.file_sha256(TEST_FILE)}


def release() -> dict:
    if RELEASE.exists() or (original.OUTPUT / "evaluation_started.json").exists():
        raise FileExistsError("original or repair score already released")
    record = expected_release()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    coding.atomic_json(RELEASE, record)
    return record


def evaluate_once() -> dict:
    frozen_release = json.loads(RELEASE.read_text(encoding="utf-8"))
    if frozen_release != expected_release():
        raise ValueError("v1.1 release hash or source changed")
    if (OUTPUT / "evaluation_started.json").exists():
        raise FileExistsError("v1.1 RAGPulse score already started")
    y, counts, rows = aggregate_jsonl(original.SOURCE)
    if (len(y) != original.N_SLOTS or rows != original.EXPECTED_ROWS
            or int(counts.sum()) != rows):
        raise ValueError("RAGPulse fixed grid invalid")
    frame = original.design(y)
    support = original.daily_support(frame)
    if not support["all_passed"]:
        result = {"status": "SUPPORT_NO_GO_NO_SCORE",
                  "release_sha256": coding.file_sha256(RELEASE),
                  "source_sha256": frozen_release["source_sha256"],
                  "request_rows": rows, "support": support}
        coding.atomic_json(OUTPUT / "support_no_go.json", result)
        return result
    bundle = joblib.load(original.OUTPUT / "weekend_free_coding_model.joblib")
    raw = original.predict_raw(bundle, frame)
    scale = frame["scale"].to_numpy(float)
    for day in range(original.FIRST_TEST_DAY, original.LAST_TEST_DAY + 1):
        for forecast in raw.values():
            original.daily_multiplier(y.astype(float), forecast, scale, day-1)
    coding.atomic_json(OUTPUT / "evaluation_started.json", {
        "status": "ONE_TIME_RAGPULSE_V1_1_SCORE_STARTED",
        "release_sha256": coding.file_sha256(RELEASE),
        "source_sha256": frozen_release["source_sha256"],
        "support": support})
    assessment = original.score(frame, raw)
    result = {"status": "INDEPENDENT_TRANSFER_EXPLORATORY_GO"
              if assessment["gate"]["all_passed"] else
              "INDEPENDENT_TRANSFER_NO_GO",
              "scope": "recorded RAGPulse sample next-five-minute input token sums",
              "release_sha256": coding.file_sha256(RELEASE),
              "source_sha256": frozen_release["source_sha256"],
              "source_git_blob_sha1": original.SOURCE_GIT_BLOB_SHA1,
              "model_sha256": frozen_release["v1_model_sha256"],
              "request_rows": rows, "support": support,
              "assessment": assessment,
              "target_outcomes_seen_before_freeze": False,
              "first_timestamp_representation_inspected_after_freeze": True,
              "method_hypothesis_informed_by_prior_Conversation_failure": True}
    coding.atomic_json(OUTPUT / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("release", "evaluate_once"))
    args = parser.parse_args()
    result = release() if args.command == "release" else evaluate_once()
    print(json.dumps({"status": result["status"],
                      "support": result.get("support"),
                      "gate": result.get("assessment", {}).get("gate")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
