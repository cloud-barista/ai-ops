"""Audit joinable CPU/RAM observations in three Google 2011 trace shards.

This counts observable, uninterrupted task episodes. It does not infer a safe
resource request or train a sizing policy: observed use is conditional on the
original allocation and on a task surviving the observation window.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "data/external/google_cluster_2011_2"
SHARDS = (50, 200, 400)
MICROSECONDS = 1_000_000
HORIZONS_MINUTES = (30, 60)


@dataclass(frozen=True)
class Event:
    time: int
    kind: str
    machine: str
    missing: str
    cpu_request: str
    memory_request: str


@dataclass
class Episode:
    start: int
    end: int
    machine: str
    cpu_request: float
    memory_request: float
    intervals: list[tuple[int, int]] = field(default_factory=list)
    valid_rows: int = 0
    max_cpu: float = 0.0
    max_memory: float = 0.0


def verified_paths() -> dict[str, str]:
    manifest = {}
    for line in (ROOT / "SHA256SUM").read_text().splitlines():
        if " *" in line:
            digest, name = line.split(" *", 1)
            manifest[name] = digest
    checks = {}
    for shard in SHARDS:
        for kind in ("task_events", "task_usage"):
            name = f"{kind}/part-{shard:05d}-of-00500.csv.gz"
            path = ROOT / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != manifest[name]:
                raise ValueError(f"SHA-256 mismatch for {name}")
            checks[name] = digest
    return checks


def interval_coverage(intervals: list[tuple[int, int]]) -> int:
    covered = 0
    right = 0
    for left, stop in sorted(intervals):
        if stop <= right:
            continue
        covered += stop - max(left, right)
        right = stop
    return covered


def audit_shard(shard: int) -> tuple[dict, dict[int, set[tuple[str, str]]]]:
    prefix = f"part-{shard:05d}-of-00500.csv.gz"
    events: dict[tuple[str, str], list[Event]] = defaultdict(list)
    event_min, event_max, event_rows = 10**30, 0, 0
    with gzip.open(ROOT / "task_events" / prefix, "rt", newline="") as source:
        for row in csv.reader(source):
            time = int(row[0])
            event_min, event_max = min(event_min, time), max(event_max, time)
            event_rows += 1
            events[(row[2], row[3])].append(
                Event(time, row[5], row[4], row[1], row[9], row[10])
            )

    counts = Counter()
    episodes: dict[int, dict[tuple[str, str], Episode]] = {
        minutes: {} for minutes in HORIZONS_MINUTES
    }
    for key, task_events in events.items():
        submits = [event for event in task_events if event.kind == "0"]
        if len(submits) != 1:
            continue
        counts["single_submit_keys"] += 1
        submit = submits[0]
        if submit.missing or not submit.cpu_request or not submit.memory_request:
            continue
        cpu_request = float(submit.cpu_request)
        memory_request = float(submit.memory_request)
        if not math.isfinite(cpu_request) or not math.isfinite(memory_request):
            continue
        if cpu_request <= 0 or memory_request <= 0:
            continue
        counts["positive_request_keys"] += 1
        schedules = [
            event
            for event in task_events
            if event.kind == "1"
            and event.time >= submit.time
            and event.machine
            and not event.missing
        ]
        if len(schedules) != 1:
            continue
        counts["single_schedule_keys"] += 1
        schedule = schedules[0]
        if schedule.time > submit.time + 600 * MICROSECONDS:
            continue
        if any(event.time < submit.time for event in task_events):
            continue
        if schedule.cpu_request and float(schedule.cpu_request) != cpu_request:
            continue
        if schedule.memory_request and float(schedule.memory_request) != memory_request:
            continue
        for minutes in HORIZONS_MINUTES:
            end = schedule.time + minutes * 60 * MICROSECONDS
            if end > event_max:
                continue
            counts[f"within_shard_{minutes}m"] += 1
            if any(
                event is not schedule
                and submit.time <= event.time < end
                and event.kind in "12345678"
                for event in task_events
            ):
                continue
            episodes[minutes][key] = Episode(
                schedule.time, end, schedule.machine, cpu_request, memory_request
            )

    usage_min, usage_max, usage_rows = 10**30, 0, 0
    with gzip.open(ROOT / "task_usage" / prefix, "rt", newline="") as source:
        for row in csv.reader(source):
            start, end = int(row[0]), int(row[1])
            usage_min, usage_max = min(usage_min, start), max(usage_max, end)
            usage_rows += 1
            key = (row[2], row[3])
            for minutes in HORIZONS_MINUTES:
                episode = episodes[minutes].get(key)
                if (
                    episode is None
                    or row[4] != episode.machine
                    or end <= episode.start
                    or start >= episode.end
                ):
                    continue
                if not row[10] or not row[13]:
                    continue
                memory, cpu = float(row[10]), float(row[13])
                if not math.isfinite(memory) or not math.isfinite(cpu):
                    continue
                if memory < 0 or cpu < 0:
                    continue
                episode.intervals.append(
                    (max(start, episode.start), min(end, episode.end))
                )
                episode.valid_rows += 1
                episode.max_memory = max(episode.max_memory, memory)
                episode.max_cpu = max(episode.max_cpu, cpu)

    output = {
        "shard": shard,
        "event_rows": event_rows,
        "event_range_s": [event_min / MICROSECONDS, event_max / MICROSECONDS],
        "usage_rows": usage_rows,
        "usage_range_s": [usage_min / MICROSECONDS, usage_max / MICROSECONDS],
        **counts,
    }
    eligible_keys: dict[int, set[tuple[str, str]]] = {}
    for minutes in HORIZONS_MINUTES:
        span = minutes * 60 * MICROSECONDS
        minimum_rows = 5 if minutes == 30 else 10
        minimum_coverage = 0.95 if minutes == 30 else 0.90
        active = episodes[minutes]
        eligible = {
            key: episode
            for key, episode in active.items()
            if episode.valid_rows >= minimum_rows
            and interval_coverage(episode.intervals) >= minimum_coverage * span
        }
        jobs = Counter(key[0] for key in active)
        output[f"{minutes}m"] = {
            "uninterrupted_episodes": len(active),
            "valid_and_covered_episodes": len(eligible),
            "minimum_coverage": minimum_coverage,
            "minimum_valid_rows": minimum_rows,
            "jobs_before_coverage": len(jobs),
            "largest_job_tasks_before_coverage": jobs.most_common(1)[0][1],
            "largest_five_jobs_tasks_before_coverage": sum(
                value for _, value in jobs.most_common(5)
            ),
            "observed_max_memory_above_request": sum(
                episode.max_memory > episode.memory_request
                for episode in eligible.values()
            ),
            "observed_max_cpu_above_request": sum(
                episode.max_cpu > episode.cpu_request for episode in eligible.values()
            ),
        }
        eligible_keys[minutes] = set(eligible)
    return output, eligible_keys


def main() -> None:
    checks = verified_paths()
    shards = []
    keys = []
    for shard in SHARDS:
        summary, kept = audit_shard(shard)
        shards.append(summary)
        keys.append(kept)
    overlap = {}
    for i in range(len(SHARDS)):
        for j in range(i + 1, len(SHARDS)):
            pair = f"{SHARDS[i]}-{SHARDS[j]}"
            overlap[pair] = {
                f"{minutes}m_task_keys": len(keys[i][minutes] & keys[j][minutes])
                for minutes in HORIZONS_MINUTES
            }
            overlap[pair].update(
                {
                    f"{minutes}m_job_ids": len(
                        {key[0] for key in keys[i][minutes]}
                        & {key[0] for key in keys[j][minutes]}
                    )
                    for minutes in HORIZONS_MINUTES
                }
            )
    print(json.dumps({"sha256": checks, "shards": shards, "overlap": overlap}, indent=2))


if __name__ == "__main__":
    main()
