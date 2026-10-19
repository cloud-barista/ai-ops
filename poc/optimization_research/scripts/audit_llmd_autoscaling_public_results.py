"""Audit public llm-d benchmark archives without retaining request payloads."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/llmd_autoscaling_public_results_audit_v1/audit.json"
REPO = "llm-d/llm-d-autoscaling"
COMMIT = "3c06c8814bcde4f7da0c7b4fadeed015e135a9de"
SESSIONS = {
    "villardl-20260903-134826-766": (
        "inference-perf-1788457751-murkd5_1",
        "613fb66322086329d27d520e0c6647776d5da6f495ebf924721eab8b81bbbec5"),
    "villardl-20260903-142058-979": (
        "inference-perf-1788459699-gov1fk_1",
        "60128dd8b58822ef023bf28b2229e00fcaf8183d6a08fbf29cd013668574f684"),
    "villardl-20260908-122801-752": (
        "inference-perf-1788884911-lx1rwl_1",
        "a85626863b53c51a94804ba46185ae1c0f1e844c93182a425211fd9f323b9062"),
}
MEMBERS = {
    "./", "./stdout.log", "./config.yaml", "./stderr.log", "./analysis/",
    "./analysis/distributions/", "./traffic_complete.yaml",
    "./stage_0_lifecycle_metrics.json", "./summary_lifecycle_metrics.json",
    "./guide_pd-disaggregation_2.yaml", "./.traffic_complete.lock/",
    "./per_request_lifecycle_metrics.json",
}


def _tar_read(data: bytes, member: str) -> bytes:
    process = subprocess.run(["tar", "--zstd", "-xOf", "-", member],
                             input=data, capture_output=True, check=True)
    return process.stdout


def _tar_members(data: bytes) -> list[str]:
    process = subprocess.run(["tar", "--zstd", "-tf", "-"], input=data,
                             capture_output=True, check=True)
    return process.stdout.decode("utf-8").splitlines()


def _read(url: str) -> bytes:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    return response.content


def run() -> dict:
    tree_url = f"https://api.github.com/repos/{REPO}/git/trees/{COMMIT}?recursive=1"
    tree = json.loads(_read(tree_url))
    if tree.get("truncated") or tree.get("sha") != COMMIT:
        raise ValueError("pinned repository tree unavailable or incomplete")
    blobs = [entry for entry in tree["tree"] if entry["type"] == "blob"
             and entry["path"].startswith("benchmark/results/")]
    workspace_paths = sorted(entry["path"] for entry in blobs
                             if entry["path"].endswith("/workspace.tar.zst"))
    expected_paths = sorted(
        f"benchmark/results/{session}/results/{run}/workspace.tar.zst"
        for session, (run, _) in SESSIONS.items())
    if workspace_paths != expected_paths:
        raise ValueError("public workspace inventory differs from the three audited sessions")

    sessions = {}
    for session, (run, expected_hash) in SESSIONS.items():
        path = f"benchmark/results/{session}/results/{run}/workspace.tar.zst"
        url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}"
        archive = _read(url)
        digest = hashlib.sha256(archive).hexdigest()
        if digest != expected_hash:
            raise ValueError(f"{session} pinned archive changed")
        members = _tar_members(archive)
        if set(members) != MEMBERS or len(members) != len(MEMBERS):
            raise ValueError(f"{session} archive inventory differs")
        config = yaml.safe_load(_tar_read(archive, "./config.yaml"))
        summary = json.loads(_tar_read(archive, "./summary_lifecycle_metrics.json"))
        per_request = json.loads(_tar_read(archive, "./per_request_lifecycle_metrics.json"))
        if not isinstance(per_request, list):
            raise ValueError("per-request metrics must be a list")
        errors = Counter(
            "success" if item.get("error") is None
            else str(item["error"].get("error_type", "unknown"))
            for item in per_request)
        if (len(per_request) != summary["load_summary"]["count"]
                or errors["success"] != summary["successes"]["count"]
                or len(per_request) - errors["success"] != summary["failures"]["count"]):
            raise ValueError(f"{session} request and summary counts differ")
        run_meta_path = f"benchmark/results/{session}/results/{run}/run_metadata.yaml"
        metadata_bytes = _read(
            f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{run_meta_path}")
        metadata = yaml.safe_load(metadata_bytes)
        sessions[session] = {
            "archive_path": path, "archive_bytes": len(archive),
            "archive_sha256": digest,
            "archive_member_names": members,
            "run_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
            "harness_start": metadata["harness_start"],
            "harness_stop": metadata["harness_stop"],
            "namespace": metadata["namespace"],
            "model": metadata["model"],
            "load_type": config["load"]["type"],
            "data_type": config["data"]["type"],
            "configured_request_rate": config["load"]["stages"][0]["rate"],
            "requests": len(per_request),
            "successes": errors["success"],
            "failures": len(per_request) - errors["success"],
            "error_types": dict(sorted(errors.items())),
            "per_request_fields": sorted({key for item in per_request for key in item}),
            "per_request_info_fields": sorted({key for item in per_request
                                               for key in (item.get("info") or {})}),
            "summary_fields": sorted(summary),
        }
    answer = {
        "status": "PUBLIC_ACTION_READY_JOIN_NO_GO",
        "repository": REPO, "commit": COMMIT,
        "committed_result_blob_count": len(blobs),
        "workspace_count": len(workspace_paths),
        "sessions": sessions,
        "reason": "Committed archives contain request outcomes but no scale command, replica Ready event, or replica time series joined to these requests.",
        "scope": "Three committed September 2026 llm-d benchmark sessions at the pinned commit; no claim about uncommitted cluster telemetry.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != answer:
            raise ValueError("saved audit changed")
    else:
        OUTPUT.write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    return answer


if __name__ == "__main__":
    record = run()
    print(json.dumps({"status": record["status"], "commit": record["commit"],
                      "workspace_count": record["workspace_count"],
                      "request_counts": {key: value["requests"]
                                         for key, value in record["sessions"].items()}},
                     sort_keys=True))
