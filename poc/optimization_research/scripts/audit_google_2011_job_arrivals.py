"""Audit job arrivals in four official Google 2011 trace shards.

This is a read-only feasibility audit. A job event records Borg job lifecycle,
not an AI-MCMP deployment or scale action. The four shards are non-contiguous;
this script does not interpolate between them or create a predictive label.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


ROOT = Path(__file__).resolve().parents[1] / "data" / "external" / "google_cluster_2011_2"
PARTS = ("00050", "00200", "00400", "00450")
MANIFEST_SHA256 = "b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940"
SECOND_US = 1_000_000
FIVE_MIN_US = 300 * SECOND_US


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def official_hashes() -> dict[str, str]:
    manifest = ROOT / "SHA256SUM"
    if sha256(manifest) != MANIFEST_SHA256:
        raise ValueError("official SHA256SUM manifest changed")
    hashes = {}
    for line in manifest.read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        hashes[relative.lstrip("*")] = digest
    return hashes


def checked_path(relative: str, hashes: dict[str, str]) -> Path:
    path = ROOT / relative
    expected = hashes.get(relative)
    if not expected or sha256(path) != expected:
        raise ValueError(f"official checksum mismatch: {relative}")
    return path


def percentile_nearest(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(percentile * len(ordered) + 0.999999) - 1))
    return ordered[position]


def profile_part(part: str, hashes: dict[str, str]) -> tuple[dict, set[str]]:
    job_relative = f"job_events/part-{part}-of-00500.csv.gz"
    task_relative = f"task_events/part-{part}-of-00500.csv.gz"
    job_path = checked_path(job_relative, hashes)
    task_path = checked_path(task_relative, hashes)
    job_events: dict[str, list[tuple[int, int]]] = defaultdict(list)
    job_types: Counter[int] = Counter()
    missing_job_rows = 0
    job_start = 10**30
    job_end = 0
    with gzip.open(job_path, "rt", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) != 8:
                raise ValueError(f"unexpected job_events width: {part}")
            timestamp, job_id, event_type = int(row[0]), row[2], int(row[3])
            job_events[job_id].append((timestamp, event_type))
            job_types[event_type] += 1
            missing_job_rows += bool(row[1])
            job_start = min(job_start, timestamp)
            job_end = max(job_end, timestamp)

    task_jobs: set[str] = set()
    task_submit_jobs: set[str] = set()
    task_submits: dict[str, list[tuple[int, str, float, float]]] = defaultdict(list)
    task_events_total = 0
    task_start = 10**30
    task_end = 0
    task_submit_rows = 0
    missing_request_rows = 0
    with gzip.open(task_path, "rt", newline="") as stream:
        for row in csv.reader(stream):
            if len(row) != 13:
                raise ValueError(f"unexpected task_events width: {part}")
            task_events_total += 1
            timestamp, job_id = int(row[0]), row[2]
            task_jobs.add(job_id)
            task_start = min(task_start, timestamp)
            task_end = max(task_end, timestamp)
            if row[5] != "0":
                continue
            task_submit_rows += 1
            task_submit_jobs.add(job_id)
            if not row[9] or not row[10]:
                missing_request_rows += 1
                continue
            task_submits[job_id].append((timestamp, row[3], float(row[9]), float(row[10])))

    submitted = {job_id: min(t for t, kind in events if kind == 0)
                 for job_id, events in job_events.items() if any(kind == 0 for _, kind in events)}
    scheduled = {job_id for job_id, events in job_events.items()
                 if any(kind == 1 for _, kind in events)}
    terminal = {job_id for job_id, events in job_events.items()
                if any(kind in {2, 3, 4, 5, 6} for _, kind in events)}
    common_end = min(job_end, task_end)
    common_start = max(job_start, task_start)

    first_task_before_job = 0
    first_task_after_job = 0
    first_task_same_time = 0
    first_task_delays_us: list[int] = []
    complete_60s = 0
    task_counts_60s: list[int] = []
    task_indices_seen_by_1s = 0
    task_indices_seen_by_60s = 0
    jobs_all_60s_indices_seen_by_1s = 0
    cpu_requests_60s: list[float] = []
    ram_requests_60s: list[float] = []
    for job_id, submit_time in submitted.items():
        tasks = task_submits.get(job_id, [])
        if tasks:
            delta = min(t for t, _, _, _ in tasks) - submit_time
            first_task_before_job += delta < 0
            first_task_after_job += delta > 0
            first_task_same_time += delta == 0
            first_task_delays_us.append(delta)
        if submit_time > common_end - 60 * SECOND_US:
            continue
        complete_60s += 1
        # A retry with the same task index is not a second requested replica.
        first_by_index = {}
        for timestamp, index, cpu, ram in tasks:
            if submit_time <= timestamp <= submit_time + 60 * SECOND_US:
                if index not in first_by_index or timestamp < first_by_index[index][0]:
                    first_by_index[index] = (timestamp, cpu, ram)
        task_counts_60s.append(len(first_by_index))
        by_1s = sum(value[0] <= submit_time + SECOND_US for value in first_by_index.values())
        task_indices_seen_by_1s += by_1s
        task_indices_seen_by_60s += len(first_by_index)
        jobs_all_60s_indices_seen_by_1s += by_1s == len(first_by_index)
        cpu_requests_60s.append(sum(v[1] for v in first_by_index.values()))
        ram_requests_60s.append(sum(v[2] for v in first_by_index.values()))

    full_bins = range((common_start + FIVE_MIN_US - 1) // FIVE_MIN_US,
                      common_end // FIVE_MIN_US)
    bin_counts = Counter(timestamp // FIVE_MIN_US for timestamp in submitted.values())
    arrivals_5m = [bin_counts[bin_id] for bin_id in full_bins]

    result = {
        "part": part,
        "source_sha256": {job_relative: hashes[job_relative], task_relative: hashes[task_relative]},
        "job_event_time_us": [job_start, job_end],
        "task_event_time_us": [task_start, task_end],
        "common_time_us": [common_start, common_end],
        "job_event_rows": sum(job_types.values()),
        "job_event_type_counts": {str(kind): count for kind, count in sorted(job_types.items())},
        "job_event_missing_info_rows": missing_job_rows,
        "distinct_job_event_ids": len(job_events),
        "job_submit_ids": len(submitted),
        "job_submit_with_schedule_same_part": len(submitted.keys() & scheduled),
        "job_submit_with_terminal_same_part": len(submitted.keys() & terminal),
        "task_event_rows": task_events_total,
        "task_submit_rows": task_submit_rows,
        "task_submit_missing_cpu_or_ram_request_rows": missing_request_rows,
        "distinct_task_event_job_ids": len(task_jobs),
        "distinct_task_submit_job_ids": len(task_submit_jobs),
        "job_submit_with_task_submit_same_part": len(submitted.keys() & task_submit_jobs),
        "task_submit_job_with_any_job_event_same_part": len(task_submit_jobs & job_events.keys()),
        "first_task_submit_relative_to_job_submit": {
            "before": first_task_before_job,
            "same_microsecond": first_task_same_time,
            "after": first_task_after_job,
            "within_1s": sum(0 <= delay <= SECOND_US for delay in first_task_delays_us),
            "median_delay_us": median(first_task_delays_us) if first_task_delays_us else None,
            "p90_delay_us": percentile_nearest(first_task_delays_us, .9),
            "max_delay_us": max(first_task_delays_us, default=None),
        },
        "job_submits_with_full_60s_in_common_part": complete_60s,
        "distinct_task_indices_submitted_in_first_60s": {
            "zero_jobs": sum(value == 0 for value in task_counts_60s),
            "median_per_job": median(task_counts_60s) if task_counts_60s else None,
            "p90_per_job": percentile_nearest(task_counts_60s, .9),
            "max_per_job": max(task_counts_60s, default=None),
            "all_seen_by_1s_jobs": jobs_all_60s_indices_seen_by_1s,
            "seen_by_1s_task_indices": task_indices_seen_by_1s,
            "seen_by_60s_task_indices": task_indices_seen_by_60s,
        },
        "first_60s_task_cpu_request_sum_median_normalized": median(cpu_requests_60s) if cpu_requests_60s else None,
        "first_60s_task_ram_request_sum_median_normalized": median(ram_requests_60s) if ram_requests_60s else None,
        "full_5m_bins": len(arrivals_5m),
        "full_5m_job_arrivals": {
            "min": min(arrivals_5m, default=None),
            "median": median(arrivals_5m) if arrivals_5m else None,
            "p90": percentile_nearest(arrivals_5m, .9),
            "max": max(arrivals_5m, default=None),
        },
    }
    return result, set(job_events)


def main() -> None:
    hashes = official_hashes()
    checked_path("schema.csv", hashes)
    profiles = [profile_part(part, hashes) for part in PARTS]
    event_id_sets = [ids for _, ids in profiles]
    print(json.dumps({
        "official_manifest_sha256": MANIFEST_SHA256,
        "schema_sha256": hashes["schema.csv"],
        "parts": [profile for profile, _ in profiles],
        "cross_part_job_event_id_intersection": {
            f"{PARTS[i]}:{PARTS[j]}": len(event_id_sets[i] & event_id_sets[j])
            for i in range(len(PARTS)) for j in range(i + 1, len(PARTS))
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
