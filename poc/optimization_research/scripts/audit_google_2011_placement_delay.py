"""Read-only audit of placement-delay labels in three Google 2011 event shards.

This script counts observable SUBMIT-to-SCHEDULE labels. It does not train a
model, score predictions, or infer AI-App/VM deployment or scaling outcomes.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, NamedTuple


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data/external/google_cluster_2011_2"
SHARDS = (50, 200, 400)
MANIFEST_SHA256 = "b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940"
MICROSECONDS = 1_000_000
HORIZON_SECONDS = 600
THRESHOLDS_SECONDS = (5, 30, 600)


class Event(NamedTuple):
    time_us: int
    kind: str
    missing: str
    machine: str
    user: str
    scheduling_class: str
    priority: str
    cpu_request: str
    memory_request: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_event_paths() -> dict[int, Path]:
    """Check the official manifest and every event shard before reading rows."""
    manifest_path = SOURCE / "SHA256SUM"
    if sha256(manifest_path) != MANIFEST_SHA256:
        raise ValueError("Official SHA256SUM manifest changed")
    manifest = {}
    for line in manifest_path.read_text().splitlines():
        if " *" in line:
            digest, name = line.split(" *", 1)
            manifest[name] = digest
    paths = {}
    for shard in SHARDS:
        name = f"task_events/part-{shard:05d}-of-00500.csv.gz"
        path = SOURCE / name
        if sha256(path) != manifest[name]:
            raise ValueError(f"Official SHA-256 mismatch: {name}")
        paths[shard] = path
    return paths


def parse_event(row: list[str]) -> Event:
    if len(row) != 13:
        raise ValueError(f"Expected 13 task_events columns, got {len(row)}")
    return Event(int(row[0]), row[5], row[1], row[4], row[6], row[7],
                 row[8], row[9], row[10])


def valid_submit(event: Event) -> bool:
    if event.kind != "0" or event.missing:
        return False
    if not all((event.user, event.scheduling_class, event.priority,
                event.cpu_request, event.memory_request)):
        return False
    try:
        cpu, memory = float(event.cpu_request), float(event.memory_request)
    except ValueError:
        return False
    return all(math.isfinite(value) and value > 0 for value in (cpu, memory))


def valid_schedule(event: Event, submit_time_us: int) -> bool:
    return (event.kind == "1" and event.time_us >= submit_time_us
            and not event.missing and bool(event.machine))


def summarize_events(rows: Iterable[list[str]], shard: int) -> tuple[dict, set[str]]:
    """Audit one complete, time-contiguous event shard; return eligible jobs."""
    by_task: dict[tuple[str, str], list[Event]] = defaultdict(list)
    event_types: Counter[str] = Counter()
    start_us, stop_us = 10**30, 0
    event_rows = 0
    for row in rows:
        event_rows += 1
        event = parse_event(row)
        by_task[(row[2], row[3])].append(event)
        event_types[event.kind] += 1
        start_us = min(start_us, event.time_us)
        stop_us = max(stop_us, event.time_us)
    if not event_rows:
        raise ValueError("Empty task_events shard")

    counts: Counter[str] = Counter()
    cohort_jobs: Counter[str] = Counter()
    unscheduled_jobs: dict[int, Counter[str]] = {
        seconds: Counter() for seconds in THRESHOLDS_SECONDS
    }
    threshold_counts: dict[int, Counter[str]] = {
        seconds: Counter() for seconds in THRESHOLDS_SECONDS
    }
    for (job, _task), events in by_task.items():
        submits = [event for event in events if event.kind == "0"]
        if len(submits) != 1:
            continue
        counts["single_submit_tasks"] += 1
        submit = submits[0]
        if not valid_submit(submit) or any(
                event.time_us < submit.time_us for event in events):
            continue
        counts["clean_submit_tasks"] += 1
        # Common, completely observable 10-minute cohort for every threshold.
        if submit.time_us + HORIZON_SECONDS * MICROSECONDS > stop_us:
            continue
        counts["eligible_tasks"] += 1
        cohort_jobs[job] += 1
        if any(event.missing and submit.time_us < event.time_us
               <= submit.time_us + HORIZON_SECONDS * MICROSECONDS
               for event in events):
            counts["marked_missing_post_event_within_10m"] += 1
        schedules = sorted(
            (event for event in events
             if valid_schedule(event, submit.time_us)),
            key=lambda event: event.time_us,
        )
        delay_us = (schedules[0].time_us - submit.time_us) if schedules else None
        for seconds in THRESHOLDS_SECONDS:
            if delay_us is not None and delay_us <= seconds * MICROSECONDS:
                threshold_counts[seconds]["scheduled"] += 1
            else:
                threshold_counts[seconds]["not_scheduled"] += 1
                unscheduled_jobs[seconds][job] += 1
        if delay_us is not None and delay_us > HORIZON_SECONDS * MICROSECONDS:
            counts["late_schedule_in_same_shard"] += 1
        elif delay_us is None:
            counts["no_valid_schedule_in_same_shard"] += 1
        if delay_us is None or delay_us > HORIZON_SECONDS * MICROSECONDS:
            if any(event.kind == "5" and submit.time_us < event.time_us
                   <= submit.time_us + HORIZON_SECONDS * MICROSECONDS
                   for event in events):
                counts["not_scheduled_killed_within_10m"] += 1

    distribution = {}
    for seconds in THRESHOLDS_SECONDS:
        negatives = unscheduled_jobs[seconds]
        negative_count = threshold_counts[seconds]["not_scheduled"]
        largest = max(negatives.values(), default=0)
        distribution[f"{seconds}s"] = {
            "scheduled": threshold_counts[seconds]["scheduled"],
            "not_scheduled": negative_count,
            "not_scheduled_jobs": len(negatives),
            "largest_not_scheduled_job_tasks": largest,
            "largest_not_scheduled_job_fraction": (
                largest / negative_count if negative_count else 0.0
            ),
        }
    summary = {
        "shard": f"{shard:05d}",
        "event_rows": event_rows,
        "event_range_us": [start_us, stop_us],
        "event_types": dict(sorted(event_types.items())),
        "single_submit_tasks": counts["single_submit_tasks"],
        "clean_submit_tasks": counts["clean_submit_tasks"],
        "eligible_tasks": counts["eligible_tasks"],
        "eligible_jobs": len(cohort_jobs),
        "largest_eligible_job_tasks": max(cohort_jobs.values(), default=0),
        "marked_missing_post_event_within_10m": counts[
            "marked_missing_post_event_within_10m"
        ],
        "distribution": distribution,
        "not_scheduled_10m_breakdown": {
            "late_schedule_in_same_shard": counts["late_schedule_in_same_shard"],
            "no_valid_schedule_in_same_shard": counts["no_valid_schedule_in_same_shard"],
            "killed_within_10m": counts["not_scheduled_killed_within_10m"],
        },
    }
    return summary, set(cohort_jobs)


def main() -> None:
    paths = verified_event_paths()
    summaries = []
    job_sets = []
    for shard, path in paths.items():
        with gzip.open(path, "rt", newline="") as source:
            summary, jobs = summarize_events(csv.reader(source), shard)
        summaries.append(summary)
        job_sets.append(jobs)
    overlaps = {}
    for left in range(len(SHARDS)):
        for right in range(left + 1, len(SHARDS)):
            overlaps[f"{SHARDS[left]:05d}_{SHARDS[right]:05d}"] = len(
                job_sets[left] & job_sets[right]
            )
    result = {
        "source_manifest_sha256": MANIFEST_SHA256,
        "input_event_files_sha256": {
            str(shard): sha256(path) for shard, path in paths.items()
        },
        "common_observation_horizon_seconds": HORIZON_SECONDS,
        "thresholds_seconds": list(THRESHOLDS_SECONDS),
        "shards": summaries,
        "eligible_job_overlap": overlaps,
    }
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
