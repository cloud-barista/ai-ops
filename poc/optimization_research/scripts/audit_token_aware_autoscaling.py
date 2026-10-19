#!/usr/bin/env python3
"""Check the pinned small llm-d experiment files and count observed transitions.

Replica columns are sampled Deployment state, not issued action events. Stage
JSON files contain aggregates, not timestamped request records.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[1] / "data/external/token_aware_autoscaling"
COMMIT = "7e70bf5f69c773eb6f51f870c5d23cf41f0efb04"
RUNS = (
    "2026-08-20-staged-ramp",
    "2026-08-20-prefill-heavy",
    "2026-08-20-decode-heavy",
)
STANDARD_HEADER = (
    "elapsed_s", "prefill_replicas", "prefill_ready", "decode_replicas",
    "decode_ready", "decode-tokenaware_metric", "prefill-tokenaware_metric",
)
RECOVERED_HEADER = (
    "epoch", "iso", "prefill_desired", "prefill_ready", "decode_desired",
    "decode_ready", "prefill_active", "decode_active",
)


def verify_sources(root: Path) -> dict:
    manifest = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))
    if manifest["commit"] != COMMIT or manifest["license"] != "Apache-2.0":
        raise ValueError("source commit or license changed")
    files = manifest["files"]
    if len(files) != 35 or len({item["path"] for item in files}) != len(files):
        raise ValueError("unexpected source file list")
    for item in files:
        path = root / item["path"]
        data = path.read_bytes()
        git_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if (len(data), git_blob, hashlib.sha256(data).hexdigest()) != (
            item["size"], item["git_blob_sha1"], item["sha256"]
        ):
            raise ValueError(f"source bytes changed: {item['path']}")
    tree_paths = {item["path"] for item in manifest["experiment_tree_blobs"]}
    downloaded_paths = {item["path"] for item in files if item["path"].startswith("experiments/")}
    if {item["path"] for item in files} - downloaded_paths != {
        "LICENSE", "benchmark/run-benchmark.sh"
    }:
        raise ValueError("unexpected nonexperiment source files")
    if len(tree_paths) != 42 or tree_paths - downloaded_paths != {
        f"experiments/{run}/{chart}.png"
        for run in RUNS
        for chart in ("latency_vs_qps", "throughput_vs_latency", "throughput_vs_qps")
    }:
        raise ValueError("experiment tree differs from the pinned inventory")
    return manifest


def _series_changes(times: list[int], values: list[int]) -> list[dict]:
    return [
        {"first_observed_at": times[i], "previous_sample_at": times[i - 1],
         "from": values[i - 1], "to": values[i]}
        for i in range(1, len(values)) if values[i] != values[i - 1]
    ]


def _matched_growth(times: list[int], desired: list[int], ready: list[int]) -> list[dict]:
    """Match sampled states, never infer exact transition-time bounds.

    The standard sampler reads each field in a separate kubectl call and
    timestamps the row afterwards. Within-row read times are unavailable.
    """
    out = []
    for i in range(1, len(times)):
        if desired[i] <= desired[i - 1]:
            continue
        next_change = next(
            (j for j in range(i + 1, len(times)) if desired[j] != desired[i]), len(times)
        )
        matched = next(
            (j for j in range(i, next_change) if ready[j] >= desired[i]), None
        )
        entry = {
            "target_first_observed_at": times[i],
            "target_previous_sample_at": times[i - 1],
            "target": desired[i],
            "ready_at_target_sample": ready[i],
            "matched_before_next_target_change": matched is not None,
        }
        if matched is not None:
            entry["ready_first_observed_at"] = times[matched]
            entry["ready_previous_sample_at"] = times[matched - 1] if matched else times[matched]
            entry["sampled_lag_s"] = times[matched] - times[i]
        out.append(entry)
    return out


def inspect_run(root: Path, run: str) -> dict:
    folder = root / "experiments" / run
    with (folder / "autoscaling-timeline.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        header = tuple(reader.fieldnames or ())
        expected = RECOVERED_HEADER if run.endswith("prefill-heavy") else STANDARD_HEADER
        if header != expected:
            raise ValueError(f"unexpected timeline header: {run}: {header}")
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"malformed timeline: {run}")
    time_key = "epoch" if header == RECOVERED_HEADER else "elapsed_s"
    times = [int(row[time_key]) for row in rows]
    gaps = [b - a for a, b in zip(times, times[1:])]
    if any(gap <= 0 for gap in gaps):
        raise ValueError(f"nonmonotone timeline: {run}")
    role_metrics = {}
    for role in ("prefill", "decode"):
        dkey = f"{role}_desired" if header == RECOVERED_HEADER else f"{role}_replicas"
        desired = [int(row[dkey]) for row in rows]
        ready = [int(row[f"{role}_ready"]) for row in rows]
        changes = _series_changes(times, desired)
        ready_changes = _series_changes(times, ready)
        growth = _matched_growth(times, desired, ready)
        role_metrics[role] = {
            "initial_desired": desired[0], "initial_ready": ready[0],
            "final_desired": desired[-1], "final_ready": ready[-1],
            "desired_changes": changes, "ready_changes": ready_changes,
            "growth_matches": growth,
            "samples_desired_above_ready": sum(d > r for d, r in zip(desired, ready)),
            "max_desired_minus_ready": max(d - r for d, r in zip(desired, ready)),
        }
    stages = []
    for stage in range(7):
        metrics = json.loads((folder / f"stage_{stage}_lifecycle_metrics.json").read_text())
        stages.append({
            "stage": stage,
            "success_count": metrics["successes"]["count"],
            "failure_count": metrics["failures"]["count"],
            "benchmark_time_seconds": metrics["benchmark_time_seconds"],
        })
    summary = json.loads((folder / "summary_lifecycle_metrics.json").read_text())
    successes = summary["successes"]["count"]
    failures = summary["failures"]["count"]
    if successes != sum(stage["success_count"] for stage in stages):
        raise ValueError(f"stage/summary success count mismatch: {run}")
    if failures != sum(stage["failure_count"] for stage in stages):
        raise ValueError(f"stage/summary failure count mismatch: {run}")
    return {
        "run": run,
        "timeline_source_clock": time_key,
        "timeline_samples": len(rows),
        "first_sample": times[0], "last_sample": times[-1],
        "sampling_span_s": times[-1] - times[0],
        "sampling_gap_min_s": min(gaps),
        "sampling_gap_median_s": statistics.median(gaps),
        "sampling_gap_max_s": max(gaps),
        "roles": role_metrics,
        "stages": stages,
        "summary_success_count": successes,
        "summary_failure_count": failures,
        "summary_benchmark_time_s": summary["benchmark_time_seconds"],
    }


def inspect(root: Path = DATA_DIR) -> dict:
    manifest = verify_sources(root)
    runs = [inspect_run(root, run) for run in RUNS]
    return {
        "source_commit": manifest["commit"],
        "source_files": len(manifest["files"]),
        "source_bytes": sum(item["size"] for item in manifest["files"]),
        "experiment_tree_blobs": len(manifest["experiment_tree_blobs"]),
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(json.dumps(inspect(args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
