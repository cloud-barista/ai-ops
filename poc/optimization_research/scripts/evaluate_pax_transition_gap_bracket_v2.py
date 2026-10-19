#!/usr/bin/env python3
"""PAX v2 outcome-blind eligibility for windows bracketing observed node changes.

Only the datetime field of results.csv is read. Throughput/latency fields are
never parsed, stored, scored, or used to select transitions.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evaluate_pax_transition_gate import (
    SOURCE,
    SOURCE_BYTES,
    SOURCE_MD5,
    SOURCE_SHA256,
    file_hashes,
    nonoverlap_support,
    read_control,
    read_result_times_only,
    time_slice,
)


RESEARCH = Path(__file__).resolve().parents[1]
OUTPUT = RESEARCH / "artifacts/pax_transition_gap_bracket_v2"
PROTOCOL = RESEARCH / "reports/pax_transition_gap_bracket_v2_protocol.md"
PROTOCOL_SHA256 = "8663b50f846517916985063ae7a88fe787364e6ac6d9bdcb600e1f6026bdf436"
PARSER = Path(__file__).with_name("evaluate_pax_transition_gate.py")
PARSER_SHA256 = "d5a958d4bb687da8e1c73779eb883179de74e2c4918e4dcfeddc48ced1c5f562"
WINDOW = timedelta(seconds=180)
NODE_SIDE_MAX = timedelta(seconds=240)
MAX_SAMPLE_GAP = timedelta(seconds=30)


def check_frozen_inputs(source: Path) -> dict:
    protocol_hash = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    parser_hash = hashlib.sha256(PARSER.read_bytes()).hexdigest()
    if protocol_hash != PROTOCOL_SHA256:
        raise RuntimeError(f"v2 protocol changed: {protocol_hash}")
    if parser_hash != PARSER_SHA256:
        raise RuntimeError(f"v1 parser dependency changed: {parser_hash}")
    source_sha, source_md5, source_bytes = file_hashes(source)
    if (source_sha, source_md5, source_bytes) != (SOURCE_SHA256, SOURCE_MD5, SOURCE_BYTES):
        raise RuntimeError("source PAX.zip differs from frozen verified original")
    return {"path": str(source), "sha256": source_sha, "md5": source_md5,
            "bytes": source_bytes, "zip_crc_ok": True}


def sample_coverage(times: list[datetime], old_t: datetime, new_t: datetime) -> dict:
    """Coverage of separate outcome/client windows, not the unknown gap."""
    pre = time_slice(times, old_t - WINDOW, old_t, include_left=True, include_right=True)
    post = time_slice(times, new_t, new_t + WINDOW,
                      include_left=False, include_right=True)

    def valid(values: list[datetime], anchor: datetime, side: str) -> bool:
        if len(values) < 12 or len(set(values)) != len(values):
            return False
        if any(b - a > MAX_SAMPLE_GAP for a, b in zip(values, values[1:])):
            return False
        if side == "pre":
            return values[0] <= anchor - timedelta(seconds=150) and values[-1] >= anchor - timedelta(seconds=30)
        return values[0] <= anchor + timedelta(seconds=30) and values[-1] >= anchor + timedelta(seconds=150)

    return {"pre_count": len(pre), "post_count": len(post),
            "pre_ok": valid(pre, old_t, "pre"),
            "post_ok": valid(post, new_t, "post")}


def client_coverage_and_stability(rows: list[tuple[datetime, int]],
                                  old_t: datetime, new_t: datetime) -> dict:
    times = [stamp for stamp, _ in rows]
    cover = sample_coverage(times, old_t, new_t)
    entire = [(stamp, count) for stamp, count in rows
              if old_t - WINDOW <= stamp <= new_t + WINDOW]
    pre_counts = [count for stamp, count in entire if stamp <= old_t]
    post_counts = [count for stamp, count in entire if new_t < stamp <= new_t + WINDOW]
    continuous = bool(entire and all(
        right[0] - left[0] <= MAX_SAMPLE_GAP for left, right in zip(entire, entire[1:])
    ))
    baseline = statistics.median(pre_counts) if pre_counts else None
    after = statistics.median(post_counts) if post_counts else None
    stable = bool(cover["pre_ok"] and cover["post_ok"] and continuous
                  and baseline is not None and after is not None and baseline >= 1
                  and all(abs(count - baseline) <= 0.1 * baseline + 1e-12
                          for _, count in entire)
                  and abs(after - baseline) <= 0.1 * baseline + 1e-12)
    return {**cover, "continuous_across_gap": continuous,
            "pre_median": baseline, "post_median": after, "stable": stable}


def transition_interferes(other: tuple[datetime, datetime, int, int],
                          old_t: datetime, new_t: datetime) -> bool:
    """Conservative closed-interval intersection, counting boundary contact."""
    other_old, other_new, _, _ = other
    return ((other_old <= old_t and other_new >= old_t - WINDOW)
            or (other_old <= new_t + WINDOW and other_new >= new_t))


def windows_touch(a: dict, b: dict) -> bool:
    def intervals(c: dict) -> list[tuple[datetime, datetime]]:
        old_t = datetime.fromisoformat(c["old_observed_at"])
        new_t = datetime.fromisoformat(c["new_observed_at"])
        return [(old_t - WINDOW, old_t), (new_t, new_t + WINDOW)]

    return any(max(x0, y0) <= min(x1, y1)
               for x0, x1 in intervals(a) for y0, y1 in intervals(b))


def evaluate_folder(folder: str, zf: zipfile.ZipFile) -> tuple[dict, list[dict]]:
    nodes = read_control(zf, folder + "/runningVMs.csv", "nodes")
    clients = read_control(zf, folder + "/clients.csv", "clients")
    result_times = read_result_times_only(zf, folder + "/results.csv")
    all_times = [stamp for stamp, _ in nodes + clients] + result_times
    transitions = [(i, nodes[i][0], nodes[i + 1][0], nodes[i][1], nodes[i + 1][1])
                   for i in range(len(nodes) - 1) if nodes[i][1] != nodes[i + 1][1]]
    brackets = [(old_t, new_t, old, new) for _, old_t, new_t, old, new in transitions]
    candidates = []
    for transition_index, (i, old_t, new_t, old, new) in enumerate(transitions):
        stable_before = bool(i > 0 and nodes[i - 1][1] == old
                             and old_t - nodes[i - 1][0] <= NODE_SIDE_MAX)
        stable_after = bool(i + 2 < len(nodes) and nodes[i + 2][1] == new
                            and nodes[i + 2][0] - new_t <= NODE_SIDE_MAX)
        node_sides_ok = stable_before and stable_after
        interfering = any(transition_interferes(other, old_t, new_t)
                          for j, other in enumerate(brackets) if j != transition_index)
        result = sample_coverage(result_times, old_t, new_t)
        client = client_coverage_and_stability(clients, old_t, new_t)
        reasons = []
        if not node_sides_ok:
            reasons.append("node_state_side_support")
        if interfering:
            reasons.append("other_transition_touches_window")
        if not (result["pre_ok"] and result["post_ok"]):
            reasons.append("result_timestamp_coverage")
        if not (client["pre_ok"] and client["post_ok"] and client["continuous_across_gap"]):
            reasons.append("client_timestamp_coverage")
        elif not client["stable"]:
            reasons.append("client_count_not_stable")
        candidates.append({"folder": folder, "old_observed_at": old_t.isoformat(),
                           "new_observed_at": new_t.isoformat(), "old_nodes": old,
                           "new_nodes": new, "direction": "out" if new > old else "in",
                           "unknown_transition_gap_seconds": (new_t - old_t).total_seconds(),
                           "node_state_sides": {"before_old_ok": stable_before,
                                                "after_new_ok": stable_after},
                           "other_transition_touches_window": interfering,
                           "result_timestamps": result, "client_counts": client,
                           "reasons": reasons})
    preeligible = [c for c in candidates if not c["reasons"]]
    for a, b in itertools.combinations(preeligible, 2):
        if windows_touch(a, b):
            for candidate in (a, b):
                candidate["reasons"].append("paired_windows_touch")
    for candidate in candidates:
        candidate["eligible"] = not candidate["reasons"]
    summary = {"folder": folder, "node_samples": len(nodes), "client_samples": len(clients),
               "result_timestamps": len(result_times),
               "time_start": min(all_times).isoformat() if all_times else None,
               "time_end": max(all_times).isoformat() if all_times else None,
               "raw_transitions": len(candidates),
               "eligible_transitions": sum(c["eligible"] for c in candidates),
               "raw_by_direction": dict(Counter(c["direction"] for c in candidates)),
               "eligible_by_direction": dict(Counter(c["direction"] for c in candidates
                                                     if c["eligible"]))}
    return summary, candidates


def stage_counts(candidates: list[dict]) -> dict:
    steps = (
        ("node_state_sides", lambda c: all(c["node_state_sides"].values())),
        ("no_interfering_transition", lambda c: not c["other_transition_touches_window"]),
        ("result_timestamp_coverage", lambda c: all((c["result_timestamps"]["pre_ok"], c["result_timestamps"]["post_ok"]))),
        ("client_timestamp_coverage", lambda c: all((c["client_counts"]["pre_ok"], c["client_counts"]["post_ok"], c["client_counts"]["continuous_across_gap"]))),
        ("client_stability", lambda c: c["client_counts"]["stable"]),
        ("paired_windows_nonoverlap", lambda c: "paired_windows_touch" not in c["reasons"]),
    )
    surviving = candidates
    counts = {"raw": len(surviving)}
    for name, predicate in steps:
        surviving = [candidate for candidate in surviving if predicate(candidate)]
        counts[name] = len(surviving)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source = check_frozen_inputs(args.source)
    with zipfile.ZipFile(args.source) as zf:
        bad_member = zf.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP CRC mismatch: {bad_member}")
        paths = set(zf.namelist())
        folders = sorted({name.rsplit("/", 1)[0] for name in paths
                          if name.endswith("/runningVMs.csv")})
        complete = [name for name in folders if all(name + "/" + leaf in paths
                    for leaf in ("runningVMs.csv", "clients.csv", "results.csv"))]
        summaries, candidates, errors = [], [], {}
        for folder in complete:
            try:
                summary, rows = evaluate_folder(folder, zf)
            except (ValueError, KeyError) as exc:
                errors[folder] = str(exc)
                continue
            summaries.append(summary)
            candidates.extend(rows)
    support = nonoverlap_support(summaries, candidates)
    gaps = [c["unknown_transition_gap_seconds"] for c in candidates]
    artifact = {
        "protocol_sha256": PROTOCOL_SHA256, "parser_dependency_sha256": PARSER_SHA256,
        "source": source,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "results_outcome_columns_parsed_or_used": False,
        "experiment_folders_with_running_vms": len(folders),
        "complete_folders": len(complete), "parsed_folders": len(summaries),
        "parse_errors": errors, "folders": summaries,
        "raw_transitions": len(candidates),
        "eligible_transitions": sum(c["eligible"] for c in candidates),
        "raw_by_direction": dict(Counter(c["direction"] for c in candidates)),
        "eligible_by_direction": dict(Counter(c["direction"] for c in candidates
                                              if c["eligible"])),
        "failure_reason_counts_nonexclusive": dict(Counter(
            reason for c in candidates for reason in c["reasons"])),
        "sequential_stage_counts": stage_counts(candidates),
        "unknown_transition_gap_seconds": {
            "min": min(gaps) if gaps else None,
            "median": statistics.median(gaps) if gaps else None,
            "max": max(gaps) if gaps else None,
        },
        **support,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "gate_summary.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    with (args.output / "transition_candidates.jsonl").open("w") as output:
        for row in candidates:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({key: artifact[key] for key in (
        "raw_transitions", "eligible_transitions", "raw_by_direction",
        "eligible_by_direction", "sequential_stage_counts",
        "limited_paired_observation_go")}, indent=2))


if __name__ == "__main__":
    main()
