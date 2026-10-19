"""One-time Coding-to-Conversation input-demand transfer on the Azure trace.

``prepare_release`` only hashes local Coding artifacts. ``download`` and
``score_once`` require the SHA-256 of a separately reviewed release manifest.
The Conversation CSV is not opened by importing this module or preparing the
manifest. GeneratedTokens is never parsed during scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.request

import joblib
import numpy as np
import pandas as pd

import evaluate_azure_code_input_demand as coding


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
TEST_FILE = ROOT / "tests/test_azure_input_demand_cross_service.py"
PROTOCOL = ROOT / "reports/azure_input_demand_cross_service_protocol.md"
SOURCE = ROOT / "data/external/azure_llm_2024/AzureLLMInferenceTrace_conv_1week.csv"
OUTPUT = ROOT / "artifacts/azure_input_demand_cross_service_v1"
RELEASE = OUTPUT / "release_manifest.json"
RESULT = OUTPUT / "conversation_result.json"
SOURCE_URL = ("https://github.com/Azure/AzurePublicDataset/releases/download/"
              "dataset-llm-2024/AzureLLMInferenceTrace_conv_1week.csv")
SOURCE_BYTES = 1_135_195_393
SOURCE_SHA256 = "a0cc9b969a9bbf0fd811802cbf4323edd3a209ace791e3799ad4f9207f213941"
CONV_START = pd.Timestamp("2024-05-12T00:00:00Z")
FIRST_TEST_DAY = 2
LAST_TEST_DAY = 6
RELEASE_STATUS = "FROZEN_BEFORE_CONVERSATION_ACCESS"
CODING_CODE_SHA256 = "c4e923bf3980923b6188ea939e2aebe1511034ed8f9356bb55dd883c1109251b"
CODING_PROTOCOL_SHA256 = "748513fd859357bf1aed639b010c3dac79e534617d0d6ca8052103cc903fe35f"
CODING_TEST_SHA256 = "f6c6c7f93f61bc3d2458aa9540f2e96de0092c15bf2d47e4bc015be2aa558dcf"
CODING_MODEL_SHA256 = "72c3c812192abcd02cc4095d87f77b75da83935900d5f6a4e51afc37f77582b4"
CODING_RESULT_SHA256 = "a6d962475e83b357958e612dd3a557c30cf4e66de5beb67e680a8256b27eeedf"
PROTOCOL_SHA256 = "3f27a9b13a13d0b43f6eb18404af9e6f8fcb9021d1a0451788e35a615871758d"
ARTIFACT_PATHS = {
    "coding_source": coding.SOURCE,
    "coding_protocol": coding.PROTOCOL,
    "coding_code": Path(coding.__file__).resolve(),
    "coding_tests": coding.TEST_FILE,
    "coding_freeze": coding.OUTPUT / "freeze.json",
    "coding_model": coding.OUTPUT / "relative_q90_model.joblib",
    "coding_result": coding.OUTPUT / "results.json",
    "coding_manifest": coding.OUTPUT / "manifest.json",
    "cross_protocol": PROTOCOL,
    "cross_code": SCRIPT,
    "cross_tests": TEST_FILE,
}
PINNED_ARTIFACT_HASHES = {
    "coding_source": coding.SOURCE_SHA256,
    "coding_protocol": CODING_PROTOCOL_SHA256,
    "coding_code": CODING_CODE_SHA256,
    "coding_tests": CODING_TEST_SHA256,
    "coding_model": CODING_MODEL_SHA256,
    "coding_result": CODING_RESULT_SHA256,
    "cross_protocol": PROTOCOL_SHA256,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    coding.atomic_json(path, value)


def _record(path: Path) -> dict:
    target = path.resolve()
    if not target.is_relative_to(ROOT) or not target.is_file():
        raise ValueError(f"missing or external release artifact: {path}")
    return {"path": str(target.relative_to(ROOT)), "sha256": _sha256(target)}


def _verify_artifact(name: str, record: object) -> str:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise ValueError(f"release artifact {name} lacks path and SHA-256")
    expected = ARTIFACT_PATHS[name].resolve()
    relative = Path(record["path"])
    if relative.is_absolute() or (ROOT / relative).resolve() != expected:
        raise ValueError(f"release artifact {name} has a different path")
    if not expected.is_file() or _sha256(expected) != record["sha256"]:
        raise ValueError(f"release artifact {name} changed after freeze")
    pinned = PINNED_ARTIFACT_HASHES.get(name)
    if pinned is not None and record["sha256"] != pinned:
        raise ValueError(f"release artifact {name} differs from predeclared SHA-256")
    return record["sha256"]


def _check_coding_lineage(hashes: dict[str, str]) -> dict:
    """Keep Coding's frozen fit even if its retrospective gate was NO-GO."""
    freeze = json.loads(ARTIFACT_PATHS["coding_freeze"].read_text(encoding="utf-8"))
    result = json.loads(ARTIFACT_PATHS["coding_result"].read_text(encoding="utf-8"))
    manifest = json.loads(ARTIFACT_PATHS["coding_manifest"].read_text(encoding="utf-8"))
    if (freeze.get("status") != "FROZEN_BEFORE_CODING_MODEL_SCORE"
            or freeze.get("source_sha256") != hashes["coding_source"]
            or freeze.get("protocol_sha256") != hashes["coding_protocol"]
            or freeze.get("code_environment") != coding.code_environment()):
        raise ValueError("Coding pre-score freeze provenance mismatch")
    required_result = {
        "source_sha256": hashes["coding_source"],
        "protocol_sha256": hashes["coding_protocol"],
        "script_sha256": hashes["coding_code"],
        "test_sha256": hashes["coding_tests"],
        "freeze_sha256": hashes["coding_freeze"],
        "model_sha256": hashes["coding_model"],
    }
    if (result.get("status") not in ("RETROSPECTIVE_EXPLORATORY_GO",
                                     "RETROSPECTIVE_EXPLORATORY_NO_GO")
            or any(result.get(k) != v for k, v in required_result.items())
            or result.get("code_environment") != coding.code_environment()
            or result.get("Conversation_source_accessed") is not False):
        raise ValueError("Coding fit/result provenance mismatch")
    required_manifest = {**required_result,
                         "results_sha256": hashes["coding_result"]}
    if any(manifest.get(k) != v for k, v in required_manifest.items()):
        raise ValueError("Coding manifest does not link frozen result and model")
    gate = result.get("assessment_gate", {}).get("all_passed")
    if not isinstance(gate, bool):
        raise ValueError("Coding result lacks its retrospective gate")
    return {"coding_retrospective_gate_pass": gate,
            "coding_result_status": result["status"]}


def prepare_release() -> dict:
    """Create a reviewable manifest before any full Conversation access."""
    if SOURCE.exists() or SOURCE.with_suffix(SOURCE.suffix + ".part").exists():
        raise ValueError("Conversation source already exists before release freeze")
    if RELEASE.exists():
        raise FileExistsError("cross-service release manifest already exists")
    records = {name: _record(path) for name, path in ARTIFACT_PATHS.items()}
    hashes = {name: record["sha256"] for name, record in records.items()}
    for name, expected in PINNED_ARTIFACT_HASHES.items():
        if hashes[name] != expected:
            raise ValueError(f"{name} differs from predeclared SHA-256")
    coding_link = _check_coding_lineage(hashes)
    manifest = {
        "status": RELEASE_STATUS,
        "conversation_asset": {"url": SOURCE_URL, "bytes": SOURCE_BYTES,
                               "sha256": SOURCE_SHA256,
                               "utc_start": CONV_START.isoformat(),
                               "utc_end_exclusive": (CONV_START + pd.Timedelta(days=7)).isoformat()},
        "test_days": ["2024-05-14", "2024-05-15", "2024-05-16",
                      "2024-05-17", "2024-05-18"],
        "methods": coding.METHODS,
        "known_exposure": "first and last 4 KiB of Conversation were inspected in an earlier feasibility audit; no full-period aggregates or scores",
        "artifacts": records,
        **coding_link,
    }
    _write_once(RELEASE, manifest)
    return {"manifest": str(RELEASE), "sha256": _sha256(RELEASE),
            **coding_link}


def verify_release_manifest(expected_sha256: str) -> tuple[str, dict]:
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("a reviewed release SHA-256 is required")
    actual = _sha256(RELEASE)
    if actual != expected_sha256:
        raise ValueError("release manifest changed since independent review")
    manifest = json.loads(RELEASE.read_text(encoding="utf-8"))
    expected_asset = {"url": SOURCE_URL, "bytes": SOURCE_BYTES,
                      "sha256": SOURCE_SHA256,
                      "utc_start": CONV_START.isoformat(),
                      "utc_end_exclusive": (CONV_START + pd.Timedelta(days=7)).isoformat()}
    expected_days = [(CONV_START + pd.Timedelta(days=day)).strftime("%Y-%m-%d")
                     for day in range(FIRST_TEST_DAY, LAST_TEST_DAY + 1)]
    if (manifest.get("status") != RELEASE_STATUS
            or manifest.get("conversation_asset") != expected_asset
            or manifest.get("test_days") != expected_days
            or tuple(manifest.get("methods", ())) != coding.METHODS):
        raise ValueError("release manifest changed the frozen source or evaluation")
    records = manifest.get("artifacts")
    if not isinstance(records, dict) or set(records) != set(ARTIFACT_PATHS):
        raise ValueError("release manifest has missing or extra artifacts")
    hashes = {name: _verify_artifact(name, records[name]) for name in ARTIFACT_PATHS}
    coding_link = _check_coding_lineage(hashes)
    if any(manifest.get(key) != value for key, value in coding_link.items()):
        raise ValueError("release manifest Coding gate/status changed")
    return actual, manifest


def download_conversation(reviewed_release_sha256: str) -> dict:
    """Download only after release review; never overwrite the source."""
    release_hash, _ = verify_release_manifest(reviewed_release_sha256)
    if SOURCE.exists():
        raise FileExistsError("Conversation CSV already exists")
    temporary = SOURCE.with_suffix(SOURCE.suffix + ".part")
    if temporary.exists():
        raise FileExistsError("partial Conversation download already exists")
    if shutil.disk_usage(SOURCE.parent).free < SOURCE_BYTES + 250_000_000:
        raise OSError("insufficient disk for Conversation CSV and download scratch")
    request = urllib.request.Request(SOURCE_URL,
                                     headers={"User-Agent": "ai-mcmp-research/1.0",
                                              "Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
            while block := response.read(8 * 1024 * 1024):
                size += len(block)
                if size > SOURCE_BYTES:
                    raise ValueError("Conversation download exceeded official size")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if size != SOURCE_BYTES or digest.hexdigest() != SOURCE_SHA256:
            raise ValueError("Conversation source size or official SHA-256 mismatch")
        os.replace(temporary, SOURCE)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "CONVERSATION_DOWNLOADED_UNSCORED",
            "release_manifest_sha256": release_hash,
            "source_bytes": size, "source_sha256": digest.hexdigest()}


def support_before_score(frame: pd.DataFrame, counts: np.ndarray, nrows: int) -> dict:
    """Use only prior-hour scale and source counts before loading the model."""
    if len(frame) != coding.N_SLOTS or len(counts) != coding.N_SLOTS:
        raise ValueError("Conversation fixed seven-day grid is incomplete")
    if nrows <= 0 or int(counts.sum()) != nrows or (counts < 0).any():
        raise ValueError("Conversation row-count audit failed")
    daily = {}
    for day in range(7):
        left, right = day * 288, (day + 1) * 288
        name = (CONV_START + pd.Timedelta(days=day)).strftime("%Y-%m-%d")
        scale = frame.iloc[left:right]["scale"].to_numpy(float)
        eligible = int(np.sum(scale >= coding.MIN_SCALE))
        daily[name] = {"source_rows": int(counts[left:right].sum()),
                       "empty_bins": int(np.sum(counts[left:right] == 0)),
                       "eligible_bins": eligible, "ineligible_bins": 288 - eligible}
    test = list(daily.items())[FIRST_TEST_DAY:LAST_TEST_DAY + 1]
    checks = {
        "first_and_last_UTC_slots_observed": bool(counts[0] > 0 and counts[-1] > 0),
        "each_of_seven_days_has_source_rows": all(v["source_rows"] > 0 for v in daily.values()),
        "each_test_day_at_most_5pct_ineligible": all(v["ineligible_bins"] <= 14
                                                    for _, v in test),
    }
    return {"source_rows": int(nrows), "source_bins": int(len(counts)),
            "daily": daily, "checks": checks,
            "all_passed": bool(all(checks.values()))}


def transfer_gate(scored: dict) -> dict:
    if len(scored.get("per_day", {})) != 5:
        raise ValueError("cross-service gate needs all five days")
    losses = scored["equal_day_mean_absolute_token_pinball"]
    proposed = losses["relative_q90_hgb"]
    checks = {f"mean_pinball_5pct_below_{name}":
              proposed <= .95 * losses[name] for name in coding.BASELINES}
    wins = sum(day["methods"]["relative_q90_hgb"]["absolute_token_pinball"]
               < min(day["methods"][base]["absolute_token_pinball"]
                     for base in coding.BASELINES)
               for day in scored["per_day"].values())
    checks["at_least_four_of_five_daily_wins"] = wins >= 4
    checks["coverage_each_day_0_85_to_0_95"] = all(
        .85 <= day["methods"]["relative_q90_hgb"]["coverage"] <= .95
        for day in scored["per_day"].values())
    checks["support_each_day_at_least_0_95"] = scored[
        "support_fraction_each_day_at_least_0_95"]
    return {"all_passed": bool(all(checks.values())), "checks": checks,
            "daily_wins": int(wins),
            "failed": [name for name, passed in checks.items() if not passed]}


def score_once(reviewed_release_sha256: str) -> dict:
    """One integrity-checked, support-gated score of May 14--18 only."""
    release_hash, manifest = verify_release_manifest(reviewed_release_sha256)
    started = OUTPUT / "conversation_scoring_started.json"
    failure = OUTPUT / "conversation_support_failure.json"
    if RESULT.exists() or started.exists() or failure.exists():
        raise FileExistsError("Conversation score or support decision already exists")
    if SOURCE.stat().st_size != SOURCE_BYTES or _sha256(SOURCE) != SOURCE_SHA256:
        raise ValueError("Conversation CSV differs from official size or SHA-256")
    with SOURCE.open("r", encoding="utf-8", newline="") as stream:
        if stream.readline().rstrip("\r\n") != "TIMESTAMP,ContextTokens,GeneratedTokens":
            raise ValueError("Conversation CSV header differs from official three columns")
    y, counts, rows = coding.aggregate_source(SOURCE, start=CONV_START,
                                              n_slots=coding.N_SLOTS)
    frame = coding.design_from_aggregates(y, start=CONV_START)
    support = support_before_score(frame, counts, rows)
    if not support["all_passed"]:
        report = {"status": "CONVERSATION_SUPPORT_NO_GO_NO_MODEL_SCORE",
                  "release_manifest_sha256": release_hash,
                  "source_sha256": SOURCE_SHA256, "support": support}
        _write_once(failure, report)
        return report
    _write_once(started, {"status": "ONE_TIME_CONVERSATION_SCORE_STARTED",
                          "release_manifest_sha256": release_hash,
                          "source_sha256": SOURCE_SHA256})
    bundle = joblib.load(ARTIFACT_PATHS["coding_model"])
    forecasts = coding.predict_from_bundle(bundle, frame)
    scored = coding.score_period(frame, forecasts, FIRST_TEST_DAY, LAST_TEST_DAY,
                                start=CONV_START)
    gate = transfer_gate(scored)
    first = FIRST_TEST_DAY * 288
    last = (LAST_TEST_DAY + 1) * 288
    eligible = (frame.iloc[first:last]["scale"] >= coding.MIN_SCALE).to_numpy()
    target = frame.iloc[first:last]["y"].to_numpy(float)[eligible]
    scale = frame.iloc[first:last]["scale"].to_numpy(float)[eligible]
    overall = {name: coding.metric(target, prediction[first:last][eligible], scale)
               for name, prediction in forecasts.items()}
    report = {
        "status": "CROSS_SERVICE_EXPLORATORY_GO" if gate["all_passed"]
                  else "CROSS_SERVICE_NO_GO",
        "scope": "observed public Conversation subset input-token sums; no SLO or VM action",
        "release_manifest_sha256": release_hash,
        "conversation_source_sha256": SOURCE_SHA256,
        "coding_model_sha256": manifest["artifacts"]["coding_model"]["sha256"],
        "cross_protocol_sha256": PROTOCOL_SHA256,
        "cross_code_sha256": _sha256(SCRIPT),
        "cross_tests_sha256": _sha256(TEST_FILE),
        "source_audit_and_pre_score_support": support,
        "test": scored, "pooled_eligible_bins": int(eligible.sum()),
        "pooled_metrics": overall, "transfer_gate": gate,
        "known_exposure": manifest["known_exposure"],
    }
    _write_once(RESULT, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare_release", "download", "score_once"))
    parser.add_argument("--reviewed-release-sha256")
    args = parser.parse_args()
    if args.command == "prepare_release":
        if args.reviewed_release_sha256 is not None:
            parser.error("prepare_release must run before independent review")
        result = prepare_release()
    else:
        if args.reviewed_release_sha256 is None:
            parser.error("download and score_once require --reviewed-release-sha256")
        result = (download_conversation(args.reviewed_release_sha256)
                  if args.command == "download"
                  else score_once(args.reviewed_release_sha256))
    print(json.dumps({key: result[key] for key in
                      ("status", "sha256", "source_sha256", "transfer_gate")
                      if key in result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
