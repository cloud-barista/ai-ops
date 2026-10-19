"""One permitted ISO-8601 parser repair for the frozen Azure transfer test.

The original v1 code and release remain unchanged. ``prepare_release`` hashes
the downloaded official CSV and the recorded pre-score parser failure but
does not parse rows, aggregate demand, load the model, or calculate scores.
``score_once`` needs the SHA-256 of the independently reviewed new manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import evaluate_azure_code_input_demand as coding
import evaluate_azure_input_demand_cross_service as v1


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
TEST_FILE = ROOT / "tests/test_azure_input_demand_cross_service_v1_1.py"
ADDENDUM = ROOT / "reports/azure_input_demand_cross_service_parser_addendum_v1_1.md"
PARSER_FAILURE = v1.OUTPUT / "parser_failure.json"
OUTPUT = ROOT / "artifacts/azure_input_demand_cross_service_v1_1"
RELEASE = OUTPUT / "release_manifest.json"
RESULT = OUTPUT / "conversation_result.json"
STARTED = OUTPUT / "conversation_scoring_started.json"
SUPPORT_FAILURE = OUTPUT / "conversation_support_failure.json"
SOURCE_FAILURE = OUTPUT / "conversation_source_failure.json"
SCORING_FAILURE = OUTPUT / "conversation_scoring_failure.json"
RELEASE_STATUS = "FROZEN_AFTER_PARSER_FAILURE_BEFORE_ANY_SCORE"
V1_RELEASE_SHA256 = "7b081a0bb601487a3a0e6400a07cc2b92c316e91a68a65d56e055e2f358fc09d"
V1_CODE_SHA256 = "40fe94fae0c0e0b58c87e7216ff71ad781c18154368b2ad1dd190df344a2d10f"
V1_TEST_SHA256 = "b87f64f3bd37dfdd0ed64e9058862022daaf1d3ad973a9460b81f18654077edb"
V1_PROTOCOL_SHA256 = "3f27a9b13a13d0b43f6eb18404af9e6f8fcb9021d1a0451788e35a615871758d"
PARSER_FAILURE_SHA256 = "83db06706496afdef4053b25a2f134a8a68422672b42dc437607125ad22acbbc"
ADDENDUM_SHA256 = "edcfc846da357f3ec4c99df41918a2df2e5bc4c611cdf6f701d39926e3ff5725"
ARTIFACT_PATHS = {
    "conversation_source": v1.SOURCE,
    "v1_release": v1.RELEASE,
    "v1_protocol": v1.PROTOCOL,
    "v1_code": v1.SCRIPT,
    "v1_tests": v1.TEST_FILE,
    "parser_failure": PARSER_FAILURE,
    "parser_addendum": ADDENDUM,
    "v1_1_code": SCRIPT,
    "v1_1_tests": TEST_FILE,
}
PINNED_HASHES = {
    "conversation_source": v1.SOURCE_SHA256,
    "v1_release": V1_RELEASE_SHA256,
    "v1_protocol": V1_PROTOCOL_SHA256,
    "v1_code": V1_CODE_SHA256,
    "v1_tests": V1_TEST_SHA256,
    "parser_failure": PARSER_FAILURE_SHA256,
    "parser_addendum": ADDENDUM_SHA256,
}


def aggregate_source_iso(path: Path, start: pd.Timestamp = v1.CONV_START,
                         n_slots: int = coding.N_SLOTS) -> tuple[np.ndarray, np.ndarray, int]:
    """Coding's fixed aggregation with only its timestamp format made explicit."""
    counts = np.zeros(n_slots, dtype=np.int64)
    tokens = np.zeros(n_slots, dtype=np.int64)
    rows = 0
    last_time_ns = None
    with pd.read_csv(path, usecols=["TIMESTAMP", "ContextTokens"],
                     dtype={"TIMESTAMP": "string", "ContextTokens": "int64"},
                     chunksize=750_000) as reader:
        for chunk in reader:
            times = pd.to_datetime(chunk["TIMESTAMP"], format="ISO8601",
                                   utc=True, errors="raise")
            time_ns = times.astype("int64").to_numpy()
            values = chunk["ContextTokens"].to_numpy(np.int64)
            if (not len(time_ns) or (np.diff(time_ns) < 0).any()
                    or (last_time_ns is not None and time_ns[0] < last_time_ns)
                    or (values <= 0).any()):
                raise ValueError("invalid, unsorted, or nonpositive Azure request rows")
            slot = (time_ns - start.value) // coding.SLOT_NS
            if (slot < 0).any() or (slot >= n_slots).any():
                raise ValueError("Azure request outside fixed seven days")
            counts += np.bincount(slot, minlength=n_slots).astype(np.int64)
            tokens += np.bincount(slot, weights=values,
                                  minlength=n_slots).astype(np.int64)
            rows += len(values)
            last_time_ns = int(time_ns[-1])
    if rows == 0 or int(counts.sum()) != rows:
        raise ValueError("empty or count-inconsistent Azure file")
    return tokens, counts, rows


def _failure_record() -> dict:
    if v1._sha256(PARSER_FAILURE) != PARSER_FAILURE_SHA256:
        raise ValueError("recorded v1 parser failure changed")
    failure = json.loads(PARSER_FAILURE.read_text(encoding="utf-8"))
    required = {"status": "PRE_SCORE_TIMESTAMP_PARSE_FAILURE",
                "release_manifest_sha256": V1_RELEASE_SHA256,
                "conversation_source_sha256": v1.SOURCE_SHA256,
                "first_rejected_timestamp": "2024-05-12 00:35:42+00:00",
                "result_json_created": False,
                "scoring_started_json_created": False,
                "model_loaded": False,
                "scores_computed": False}
    if any(failure.get(key) != value for key, value in required.items()):
        raise ValueError("recorded v1 failure is not pre-score parser failure")
    if ((v1.OUTPUT / "conversation_scoring_started.json").exists()
            or v1.RESULT.exists()):
        raise ValueError("v1 has a score or scoring-start marker")
    return failure


def _record(path: Path) -> dict:
    target = path.resolve()
    if not target.is_relative_to(ROOT) or not target.is_file():
        raise ValueError(f"missing or external v1.1 artifact: {path}")
    return {"path": str(target.relative_to(ROOT)), "sha256": v1._sha256(target)}


def _verify_artifacts(records: object) -> dict[str, str]:
    if not isinstance(records, dict) or set(records) != set(ARTIFACT_PATHS):
        raise ValueError("v1.1 release artifact set changed")
    hashes = {}
    for name, path in ARTIFACT_PATHS.items():
        record = records[name]
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ValueError(f"v1.1 artifact {name} lacks path/SHA")
        target = path.resolve()
        relative = Path(record["path"])
        if relative.is_absolute() or (ROOT / relative).resolve() != target:
            raise ValueError(f"v1.1 artifact {name} path changed")
        if not target.is_file() or v1._sha256(target) != record["sha256"]:
            raise ValueError(f"v1.1 artifact {name} SHA changed")
        if name in PINNED_HASHES and record["sha256"] != PINNED_HASHES[name]:
            raise ValueError(f"v1.1 artifact {name} differs from pinned source")
        hashes[name] = record["sha256"]
    if ARTIFACT_PATHS["conversation_source"].stat().st_size != v1.SOURCE_BYTES:
        raise ValueError("official Conversation file size changed")
    return hashes


def prepare_release() -> dict:
    """Hash-only freeze; Conversation values and aggregates remain unread."""
    if RELEASE.exists():
        raise FileExistsError("v1.1 release already frozen")
    _failure_record()
    v1.verify_release_manifest(V1_RELEASE_SHA256)
    records = {name: _record(path) for name, path in ARTIFACT_PATHS.items()}
    hashes = _verify_artifacts(records)
    old = json.loads(v1.RELEASE.read_text(encoding="utf-8"))
    manifest = {
        "status": RELEASE_STATUS,
        "reason": "valid whole-second UTC timestamp rejected before any v1 aggregate, support check, model load or score",
        "parser_change": "pd.to_datetime(..., format='ISO8601', utc=True, errors='raise')",
        "official_source_bytes": v1.SOURCE_BYTES,
        "official_source_sha256": v1.SOURCE_SHA256,
        "test_days": old["test_days"],
        "methods": old["methods"],
        "coding_model_sha256": old["artifacts"]["coding_model"]["sha256"],
        "coding_result_sha256": old["artifacts"]["coding_result"]["sha256"],
        "artifacts": records,
        "source_integrity_pass": (hashes["conversation_source"] == v1.SOURCE_SHA256),
    }
    v1._write_once(RELEASE, manifest)
    return {"status": RELEASE_STATUS, "manifest": str(RELEASE),
            "sha256": v1._sha256(RELEASE)}


def verify_release(reviewed_sha256: str) -> tuple[str, dict]:
    if not isinstance(reviewed_sha256, str) or len(reviewed_sha256) != 64:
        raise ValueError("independently reviewed v1.1 release SHA-256 required")
    digest = v1._sha256(RELEASE)
    if digest != reviewed_sha256:
        raise ValueError("v1.1 release changed after review")
    manifest = json.loads(RELEASE.read_text(encoding="utf-8"))
    if (manifest.get("status") != RELEASE_STATUS
            or manifest.get("parser_change") !=
            "pd.to_datetime(..., format='ISO8601', utc=True, errors='raise')"
            or manifest.get("official_source_bytes") != v1.SOURCE_BYTES
            or manifest.get("official_source_sha256") != v1.SOURCE_SHA256
            or manifest.get("source_integrity_pass") is not True):
        raise ValueError("v1.1 release changed the parser or official source")
    hashes = _verify_artifacts(manifest.get("artifacts"))
    _failure_record()
    _, old = v1.verify_release_manifest(V1_RELEASE_SHA256)
    if (manifest.get("test_days") != old["test_days"]
            or manifest.get("methods") != old["methods"]
            or manifest.get("coding_model_sha256") !=
            old["artifacts"]["coding_model"]["sha256"]
            or manifest.get("coding_result_sha256") !=
            old["artifacts"]["coding_result"]["sha256"]
            or hashes["v1_release"] != V1_RELEASE_SHA256):
        raise ValueError("v1.1 release changed the frozen Coding/v1 contract")
    return digest, manifest


def score_once(reviewed_sha256: str) -> dict:
    """Attempt one parser-corrected score with all original support/gates."""
    release_sha, manifest = verify_release(reviewed_sha256)
    if any(path.exists() for path in
           (RESULT, STARTED, SUPPORT_FAILURE, SOURCE_FAILURE, SCORING_FAILURE)):
        raise FileExistsError("v1.1 source/support/score decision already recorded")
    with v1.SOURCE.open("r", encoding="utf-8", newline="") as stream:
        if stream.readline().rstrip("\r\n") != "TIMESTAMP,ContextTokens,GeneratedTokens":
            report = {"status": "CONVERSATION_SOURCE_NO_GO_NO_MODEL_SCORE",
                      "release_manifest_sha256": release_sha,
                      "error": "official CSV header mismatch"}
            v1._write_once(SOURCE_FAILURE, report)
            return report
    try:
        y, counts, rows = aggregate_source_iso(v1.SOURCE)
        frame = coding.design_from_aggregates(y, start=v1.CONV_START)
        support = v1.support_before_score(frame, counts, rows)
    except Exception as error:
        report = {"status": "CONVERSATION_SOURCE_NO_GO_NO_MODEL_SCORE",
                  "release_manifest_sha256": release_sha,
                  "error_type": type(error).__name__, "error": str(error)}
        v1._write_once(SOURCE_FAILURE, report)
        return report
    if not support["all_passed"]:
        report = {"status": "CONVERSATION_SUPPORT_NO_GO_NO_MODEL_SCORE",
                  "release_manifest_sha256": release_sha,
                  "source_sha256": v1.SOURCE_SHA256, "support": support}
        v1._write_once(SUPPORT_FAILURE, report)
        return report
    v1._write_once(STARTED, {"status": "ONE_TIME_V1_1_SCORE_STARTED",
                            "release_manifest_sha256": release_sha,
                            "source_sha256": v1.SOURCE_SHA256})
    try:
        bundle = joblib.load(v1.ARTIFACT_PATHS["coding_model"])
        forecasts = coding.predict_from_bundle(bundle, frame)
        scored = coding.score_period(frame, forecasts, v1.FIRST_TEST_DAY,
                                    v1.LAST_TEST_DAY, start=v1.CONV_START)
        gate = v1.transfer_gate(scored)
        first = v1.FIRST_TEST_DAY * 288
        last = (v1.LAST_TEST_DAY + 1) * 288
        eligible = (frame.iloc[first:last]["scale"] >= coding.MIN_SCALE).to_numpy()
        target = frame.iloc[first:last]["y"].to_numpy(float)[eligible]
        scale = frame.iloc[first:last]["scale"].to_numpy(float)[eligible]
        pooled = {name: coding.metric(target, prediction[first:last][eligible], scale)
                  for name, prediction in forecasts.items()}
        report = {
            "status": "CROSS_SERVICE_EXPLORATORY_GO" if gate["all_passed"]
                      else "CROSS_SERVICE_NO_GO",
            "scope": "observed public Conversation subset input-token sums; no SLO or VM action",
            "parser_repair": manifest["parser_change"],
            "v1_parser_failure_sha256": PARSER_FAILURE_SHA256,
            "release_manifest_sha256": release_sha,
            "conversation_source_sha256": v1.SOURCE_SHA256,
            "coding_model_sha256": manifest["coding_model_sha256"],
            "parser_addendum_sha256": ADDENDUM_SHA256,
            "v1_1_code_sha256": v1._sha256(SCRIPT),
            "v1_1_tests_sha256": v1._sha256(TEST_FILE),
            "source_audit_and_pre_score_support": support,
            "test": scored, "pooled_eligible_bins": int(eligible.sum()),
            "pooled_metrics": pooled, "transfer_gate": gate,
            "known_exposure": "first/last 4 KiB and first rejected timestamp exposed before v1.1 freeze",
        }
        v1._write_once(RESULT, report)
        return report
    except Exception as error:
        report = {"status": "CONVERSATION_SCORING_ERROR_NO_RETRY",
                  "release_manifest_sha256": release_sha,
                  "error_type": type(error).__name__, "error": str(error)}
        v1._write_once(SCORING_FAILURE, report)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare_release", "score_once"))
    parser.add_argument("--reviewed-release-sha256")
    args = parser.parse_args()
    if args.command == "prepare_release":
        if args.reviewed_release_sha256 is not None:
            parser.error("prepare_release does not accept a reviewed SHA")
        result = prepare_release()
    else:
        if args.reviewed_release_sha256 is None:
            parser.error("score_once needs --reviewed-release-sha256")
        result = score_once(args.reviewed_release_sha256)
    print(json.dumps({key: result[key] for key in
                      ("status", "sha256", "transfer_gate") if key in result},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
