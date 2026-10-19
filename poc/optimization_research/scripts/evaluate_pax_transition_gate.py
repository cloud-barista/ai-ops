#!/usr/bin/env python3
"""Count PAX node transitions eligible for a paired-observation audit.

This intentionally extracts only the datetime field from results.csv. It does
not parse, store, or calculate throughput, response-time, or other outcomes.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import itertools
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/pax_autoscale_feasibility/PAX.zip"
OUTPUT = RESEARCH / "artifacts/pax_transition_gate_v1"
PROTOCOL = RESEARCH / "reports/pax_transition_gate_protocol.md"
PROTOCOL_SHA256 = "492c510798a93260ede5d3f20662a7aa25a3dc33238a3230c4d8164795af4d2c"
SOURCE_SHA256 = "db15cfa75f05d44020a3f5c4f6f4930cbb1c2518dd3884391917615cea52a23a"
SOURCE_MD5 = "56cf6c11f79e0cffadeb2414784a789b"
SOURCE_BYTES = 1_307_826
WINDOW = timedelta(seconds=180)


def file_hashes(path: Path) -> tuple[str, str, int]:
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
            md5.update(block)
            size += len(block)
    return sha.hexdigest(), md5.hexdigest(), size


def parse_time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.strip())
    if stamp.tzinfo is not None:
        raise ValueError("timezone-bearing timestamp; protocol requires naive local time")
    return stamp


def parse_int(value: str, field: str) -> int:
    value = value.strip()
    result = int(value)
    if result < 0:
        raise ValueError(f"negative {field}: {value}")
    return result


def read_control(zf: zipfile.ZipFile, name: str, kind: str) -> list[tuple[datetime, int]]:
    with zf.open(name) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        if kind == "clients":
            header = next(reader)
            if header != ["datetime", "clients"]:
                raise ValueError(f"unexpected clients header in {name}: {header!r}")
        rows: list[tuple[datetime, int]] = []
        blank_seen = False
        for line_number, fields in enumerate(reader, start=2 if kind == "clients" else 1):
            if not fields or all(not field.strip() for field in fields):
                blank_seen = True
                continue
            if blank_seen:
                raise ValueError(f"{name}:{line_number}: content after blank line")
            if kind == "nodes":
                if len(fields) != 3:
                    raise ValueError(f"{name}:{line_number}: expected date,time,nodes")
                stamp = parse_time(fields[0] + " " + fields[1])
                count = parse_int(fields[2], "nodes")
            else:
                if len(fields) != 2:
                    raise ValueError(f"{name}:{line_number}: expected datetime,clients")
                stamp = parse_time(fields[0])
                count = parse_int(fields[1], "clients")
            rows.append((stamp, count))
    rows.sort()
    deduped: list[tuple[datetime, int]] = []
    for stamp, count in rows:
        if deduped and stamp == deduped[-1][0]:
            if count != deduped[-1][1]:
                raise ValueError(f"conflicting {kind} values at {name}:{stamp.isoformat()}")
            continue
        deduped.append((stamp, count))
    return deduped


def read_result_times_only(zf: zipfile.ZipFile, name: str) -> list[datetime]:
    """Split each raw row at its first comma, leaving all outcome fields unused."""
    times = []
    with zf.open(name) as source:
        header = source.readline().split(b",", 1)[0].decode("utf-8-sig").strip()
        if header != "datetime":
            raise ValueError(f"unexpected results header in {name}: {header!r}")
        blank_seen = False
        for line_number, row in enumerate(source, start=2):
            if not row.strip():
                blank_seen = True
                continue
            if blank_seen:
                raise ValueError(f"{name}:{line_number}: content after blank line")
            stamp_bytes, sep, _unused_outcomes = row.partition(b",")
            if not sep:
                raise ValueError(f"{name}:{line_number}: missing comma")
            times.append(parse_time(stamp_bytes.decode("utf-8")))
    return sorted(times)


def audit_trailing_blank_lines(zf: zipfile.ZipFile, names: list[str]) -> dict[str, int]:
    """Count only terminal empty rows, without interpreting CSV outcome fields."""
    counts = {}
    for name in names:
        blanks, blank_seen = 0, False
        with zf.open(name) as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    blank_seen = True
                    blanks += 1
                elif blank_seen:
                    raise ValueError(f"{name}:{line_number}: nonterminal blank line")
        if blanks:
            counts[name] = blanks
    return counts


def time_slice(times: list[datetime], left: datetime, right: datetime,
               *, include_left: bool, include_right: bool) -> list[datetime]:
    i = bisect.bisect_left(times, left) if include_left else bisect.bisect_right(times, left)
    j = bisect.bisect_right(times, right) if include_right else bisect.bisect_left(times, right)
    return times[i:j]


def result_coverage(times: list[datetime], t: datetime) -> dict:
    pre = time_slice(times, t - WINDOW, t, include_left=True, include_right=False)
    post = time_slice(times, t, t + WINDOW, include_left=False, include_right=True)

    def side_ok(values: list[datetime], side: str) -> bool:
        if len(values) < 12 or len(set(values)) != len(values):
            return False
        if any((b - a).total_seconds() > 30 for a, b in zip(values, values[1:])):
            return False
        if side == "pre":
            return values[0] <= t - timedelta(seconds=150) and values[-1] >= t - timedelta(seconds=30)
        return values[0] <= t + timedelta(seconds=30) and values[-1] >= t + timedelta(seconds=150)

    return {"pre_count": len(pre), "post_count": len(post),
            "pre_ok": side_ok(pre, "pre"), "post_ok": side_ok(post, "post")}


def client_stability(rows: list[tuple[datetime, int]], t: datetime) -> dict:
    times = [row[0] for row in rows]
    i = bisect.bisect_left(times, t - WINDOW)
    j = bisect.bisect_left(times, t)
    k = bisect.bisect_right(times, t)
    m = bisect.bisect_right(times, t + WINDOW)
    pre = [count for _, count in rows[i:j]]
    post = [count for _, count in rows[k:m]]
    result = {"pre_count": len(pre), "post_count": len(post),
              "pre_median": None, "post_median": None, "stable": False}
    if len(pre) < 12 or len(post) < 12:
        return result
    baseline = statistics.median(pre)
    after = statistics.median(post)
    result["pre_median"], result["post_median"] = baseline, after
    result["stable"] = bool(baseline >= 1 and all(
        abs(count - baseline) <= 0.1 * baseline + 1e-12 for count in itertools.chain(pre, post)
    ) and abs(after - baseline) <= 0.1 * baseline + 1e-12)
    return result


def evaluate_folder(folder: str, zf: zipfile.ZipFile) -> tuple[dict, list[dict]]:
    nodes = read_control(zf, folder + "/runningVMs.csv", "nodes")
    clients = read_control(zf, folder + "/clients.csv", "clients")
    results = read_result_times_only(zf, folder + "/results.csv")
    all_times = [t for t, _ in nodes] + [t for t, _ in clients] + results
    summary = {"folder": folder, "node_samples": len(nodes), "client_samples": len(clients),
               "result_timestamps": len(results),
               "time_start": min(all_times).isoformat() if all_times else None,
               "time_end": max(all_times).isoformat() if all_times else None}
    transitions = []
    for (previous_t, old), (t, new) in zip(nodes, nodes[1:]):
        if old != new:
            transitions.append((previous_t, t, old, new))
    transition_times = [row[1] for row in transitions]
    candidates = []
    for previous_t, t, old, new in transitions:
        adjacent_seconds = (t - previous_t).total_seconds()
        nearby_other = any(other != t and abs((other - t).total_seconds()) <= 180
                           for other in transition_times)
        result_info = result_coverage(results, t)
        client_info = client_stability(clients, t)
        reasons = []
        if adjacent_seconds > 120:
            reasons.append("observation_gap_gt_120s")
        if nearby_other:
            reasons.append("another_transition_within_3min")
        if not (result_info["pre_ok"] and result_info["post_ok"]):
            reasons.append("result_timestamp_coverage")
        if client_info["pre_count"] < 12 or client_info["post_count"] < 12:
            reasons.append("client_timestamp_coverage")
        elif not client_info["stable"]:
            reasons.append("client_count_not_stable")
        candidates.append({"folder": folder, "previous_observation": previous_t.isoformat(),
                           "transition_observed_at": t.isoformat(), "old_nodes": old,
                           "new_nodes": new, "direction": "out" if new > old else "in",
                           "adjacent_observation_seconds": adjacent_seconds,
                           "result_timestamps": result_info, "client_counts": client_info,
                           "reasons": reasons})
    # Only candidates surviving the earlier stages can become paired windows.
    preeligible = [c for c in candidates if not c["reasons"]]
    for a, b in itertools.combinations(preeligible, 2):
        delta = abs((parse_time(a["transition_observed_at"]) -
                     parse_time(b["transition_observed_at"])).total_seconds())
        if delta <= 360:
            for c in (a, b):
                if "paired_windows_overlap" not in c["reasons"]:
                    c["reasons"].append("paired_windows_overlap")
    for c in candidates:
        c["eligible"] = not c["reasons"]
    summary["raw_transitions"] = len(candidates)
    summary["eligible_transitions"] = sum(c["eligible"] for c in candidates)
    summary["raw_by_direction"] = dict(Counter(c["direction"] for c in candidates))
    summary["eligible_by_direction"] = dict(Counter(c["direction"] for c in candidates if c["eligible"]))
    gaps = [(b - a).total_seconds() for a, b in zip(transition_times, transition_times[1:])]
    summary["transition_gap_seconds"] = {"min": min(gaps) if gaps else None,
                                         "median": statistics.median(gaps) if gaps else None}
    return summary, candidates


def nonoverlap_support(folders: list[dict], candidates: list[dict]) -> dict:
    eligible = Counter(c["folder"] for c in candidates if c["eligible"])
    out = Counter(c["folder"] for c in candidates if c["eligible"] and c["direction"] == "out")
    inn = Counter(c["folder"] for c in candidates if c["eligible"] and c["direction"] == "in")
    usable = [f for f in folders if eligible[f["folder"]]]
    overlaps = []
    for a, b in itertools.combinations(folders, 2):
        if a["time_start"] and b["time_start"] and max(a["time_start"], b["time_start"]) <= min(a["time_end"], b["time_end"]):
            overlaps.append([a["folder"], b["folder"]])
    conflict = {frozenset(pair) for pair in overlaps}
    best = None
    any_go = False
    for mask in range(1 << len(usable)):
        subset = [usable[i] for i in range(len(usable)) if mask & (1 << i)]
        names = [f["folder"] for f in subset]
        if any(frozenset(pair).issubset(names) for pair in conflict):
            continue
        n, n_out, n_in = sum(eligible[x] for x in names), sum(out[x] for x in names), sum(inn[x] for x in names)
        item = {"folders": names, "transitions": n, "out": n_out, "in": n_in}
        if n >= 10 and len(names) >= 3 and n_out >= 3 and n_in >= 3:
            any_go = True
        key = (n, len(names), min(n_out, n_in), names)
        if best is None or key > best[0]:
            best = (key, item)
    return {"overlapping_time_range_pairs": overlaps,
            "best_nonoverlapping_folder_subset": best[1] if best else None,
            "limited_paired_observation_go": any_go}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    protocol_sha = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if protocol_sha != PROTOCOL_SHA256:
        raise RuntimeError(f"frozen protocol changed: {protocol_sha}")
    source_sha, source_md5, source_size = file_hashes(args.source)
    if (source_sha, source_md5, source_size) != (SOURCE_SHA256, SOURCE_MD5, SOURCE_BYTES):
        raise RuntimeError("PAX.zip differs from official MD5, expected size, or frozen SHA-256")
    with zipfile.ZipFile(args.source) as zf:
        bad_member = zf.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP CRC mismatch: {bad_member}")
        paths = set(zf.namelist())
        folders = sorted({name.rsplit("/", 1)[0] for name in paths if name.endswith("/runningVMs.csv")})
        complete = [name for name in folders if all(name + "/" + leaf in paths for leaf in
                    ("runningVMs.csv", "results.csv", "clients.csv"))]
        trailing_blanks = audit_trailing_blank_lines(
            zf, [folder + "/" + leaf for folder in complete
                 for leaf in ("runningVMs.csv", "results.csv", "clients.csv")])
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
    reason_counts = dict(Counter(reason for c in candidates for reason in c["reasons"]))
    adjacent = [c["adjacent_observation_seconds"] for c in candidates]
    artifact = {
        "protocol_sha256": protocol_sha,
        "source": {"path": str(args.source), "sha256": source_sha, "md5": source_md5,
                   "bytes": source_size, "zip_crc_ok": True},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "results_outcome_columns_parsed_or_used": False,
        "experiment_folders_with_running_vms": len(folders),
        "complete_folders": len(complete), "parsed_folders": len(summaries),
        "parse_errors": errors, "folders": summaries,
        "parsing_only_correction": {
            "initial_failure_artifact": "first_parse_failure.json",
            "reason": "Each complete experiment's clients.csv ends with one empty row; "
                      "results.csv also ends with one empty row. The first parser rejected "
                      "all nine clients.csv files at that terminal row.",
            "trailing_blank_lines_by_file": trailing_blanks,
        },
        "raw_transitions": len(candidates),
        "eligible_transitions": sum(c["eligible"] for c in candidates),
        "raw_by_direction": dict(Counter(c["direction"] for c in candidates)),
        "eligible_by_direction": dict(Counter(c["direction"] for c in candidates if c["eligible"])),
        "failure_reason_counts_nonexclusive": reason_counts,
        "adjacent_observation_seconds": {"min": min(adjacent) if adjacent else None,
                                         "median": statistics.median(adjacent) if adjacent else None,
                                         "max": max(adjacent) if adjacent else None},
        **support,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "gate_summary.json").write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    with (args.output / "transition_candidates.jsonl").open("w") as output:
        for row in candidates:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({k: artifact[k] for k in ("raw_transitions", "eligible_transitions",
                    "raw_by_direction", "eligible_by_direction", "failure_reason_counts_nonexclusive",
                    "limited_paired_observation_go")}, indent=2))


if __name__ == "__main__":
    main()
