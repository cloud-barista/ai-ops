"""Read-only audit of the first post-SCHEDULE state event in two 2011 shards.

Each SUBMIT can start a separate attempt: an EVICT/FAIL may be followed by a
new SUBMIT for the same task. This audit deliberately never opens shard 00400.
It does not fit a model or infer effects of deployment/scaling actions.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "data/external/google_cluster_2011_2/task_events"
SOURCES = {
    50: "9e2e370e893a559c87e97b8ce92e6e1d688a1501ae762923650318dcf8ea828e",
    200: "df8befaa4899af4ee555384c590a5e5da8160ea63500ee599a3c3f0a6bc8cefc",
}
HORIZONS_SECONDS = (300, 1800)
US = 1_000_000
EVENT_NAME = {
    "0": "SUBMIT", "1": "SCHEDULE", "2": "EVICT", "3": "FAIL",
    "4": "FINISH", "5": "KILL", "6": "LOST",
    "7": "UPDATE_PENDING", "8": "UPDATE_RUNNING",
}
OUTCOME_CODES = frozenset("12345678")


@dataclass(frozen=True)
class Event:
    time: int
    sequence: int
    kind: str
    missing: str = ""
    machine: str = ""
    user: str = ""
    scheduling_class: str = ""
    priority: str = ""
    cpu_request: str = ""
    memory_request: str = ""


@dataclass(frozen=True)
class Attempt:
    job_id: str
    task_index: str
    submitted_at: int
    scheduled_at: int
    next_event_at: int | None
    next_event: str
    post_update_terminal_at: int | None = None
    post_update_terminal: str = "none"


def _positive_finite(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _valid_submit(event: Event) -> bool:
    return (
        not event.missing
        and bool(event.user and event.scheduling_class and event.priority)
        and _positive_finite(event.cpu_request) is not None
        and _positive_finite(event.memory_request) is not None
    )


def _first_recorded_event(events: list[Event], schedule_at: int) -> tuple[int | None, str]:
    """Find first post-schedule event; reject equal-time ambiguity."""
    future = [e for e in events if e.time >= schedule_at and e.kind in OUTCOME_CODES | {"0"}]
    if not future:
        return None, "none"
    first_at = min(e.time for e in future)
    same_time = [e for e in future if e.time == first_at]
    if first_at == schedule_at:
        return first_at, "ambiguous_zero_duration"
    if any(e.missing for e in same_time):
        return first_at, "missing_marker"
    terminal_codes = {e.kind for e in same_time if e.kind in "23456"}
    if len(terminal_codes) > 1:
        return first_at, "ambiguous_terminal_tie"
    if terminal_codes:
        # A retry SUBMIT at the exact terminal timestamp belongs to the next
        # attempt, so the observed terminal event has precedence here.
        return first_at, EVENT_NAME[terminal_codes.pop()]
    other_codes = {e.kind for e in same_time}
    if len(other_codes) > 1:
        return first_at, "ambiguous_state_tie"
    return first_at, EVENT_NAME[other_codes.pop()]


def _first_terminal_after_update(
    submit_segment: list[Event], update_at: int
) -> tuple[int | None, str]:
    """Find the next terminal record after an update, before the next SUBMIT.

    This is a diagnostic only. A later EVICT/FAIL is not part of the frozen
    first-recorded-event target because resource updates precede that terminal.
    """
    terminal = [
        e for e in submit_segment if e.time > update_at and e.kind in "23456"
    ]
    if not terminal:
        return None, "none"
    first_at = min(e.time for e in terminal)
    first = [e for e in terminal if e.time == first_at]
    if any(e.missing for e in first):
        return first_at, "missing_marker"
    kinds = {e.kind for e in first}
    if len(kinds) != 1:
        return first_at, "ambiguous_terminal_tie"
    return first_at, EVENT_NAME[kinds.pop()]


def attempts_for_task(
    job_id: str, task_index: str, raw_events: list[Event], shard_end: int
) -> list[Attempt]:
    """Extract all eligible SUBMIT→SCHEDULE attempts, including retries."""
    events = sorted(raw_events, key=lambda e: (e.time, e.sequence))
    submits = [i for i, event in enumerate(events) if event.kind == "0"]
    attempts = []
    for offset, submit_index in enumerate(submits):
        submit = events[submit_index]
        if not _valid_submit(submit):
            continue
        following = submits[offset + 1] if offset + 1 < len(submits) else len(events)
        segment = events[submit_index + 1:following]
        schedules = [e for e in segment if e.kind == "1" and e.time >= submit.time]
        if not schedules:
            continue
        schedule = schedules[0]
        if (
            schedule.missing
            or not schedule.machine
            or schedule.time - submit.time > 600 * US
        ):
            continue
        pre_schedule = [e for e in segment if e.time < schedule.time and e.kind != "1"]
        if any(e.kind in "2345678" or e.missing for e in pre_schedule):
            continue
        if (
            schedule.cpu_request
            and _positive_finite(schedule.cpu_request) != float(submit.cpu_request)
        ) or (
            schedule.memory_request
            and _positive_finite(schedule.memory_request) != float(submit.memory_request)
        ):
            continue
        # Include the next SUBMIT as a censoring event if no termination was
        # recorded before it. This avoids silently reusing a later retry.
        followup = [e for e in segment if e.sequence != schedule.sequence]
        if following < len(events):
            followup.append(events[following])
        next_at, next_kind = _first_recorded_event(followup, schedule.time)
        post_update_at, post_update_kind = (None, "none")
        if next_kind == "UPDATE_RUNNING" and next_at is not None:
            post_update_at, post_update_kind = _first_terminal_after_update(
                segment, next_at
            )
        attempts.append(
            Attempt(
                job_id, task_index, submit.time, schedule.time, next_at, next_kind,
                post_update_at, post_update_kind,
            )
        )
    return attempts


def summarize(attempts: list[Attempt], horizon_seconds: int, shard_end: int) -> dict:
    eligible = [a for a in attempts if a.scheduled_at <= shard_end - horizon_seconds * US]
    first_per_task = {}
    for attempt in sorted(eligible, key=lambda a: (a.submitted_at, a.scheduled_at)):
        first_per_task.setdefault((attempt.job_id, attempt.task_index), attempt)
    return {
        "horizon_seconds": horizon_seconds,
        "all_attempts": _summarize_cohort(eligible, horizon_seconds),
        "first_eligible_attempt_per_task": _summarize_cohort(
            list(first_per_task.values()), horizon_seconds
        ),
    }


def _summarize_cohort(attempts: list[Attempt], horizon_seconds: int) -> dict:
    outcome = Counter()
    job_per_outcome: dict[str, Counter] = defaultdict(Counter)
    task_per_outcome: dict[str, set[tuple[str, str]]] = defaultdict(set)
    abnormal_tasks_by_job: dict[str, set[str]] = defaultdict(set)
    post_update_terminal = Counter()
    post_update_positive_jobs = set()
    for attempt in attempts:
        if attempt.next_event_at is None or attempt.next_event_at > attempt.scheduled_at + horizon_seconds * US:
            label = "no_recorded_event_in_window"
        else:
            label = attempt.next_event
        outcome[label] += 1
        job_per_outcome[label][attempt.job_id] += 1
        task_per_outcome[label].add((attempt.job_id, attempt.task_index))
        if label in ("EVICT", "FAIL"):
            abnormal_tasks_by_job[attempt.job_id].add(attempt.task_index)
        if label == "UPDATE_RUNNING":
            if (
                attempt.post_update_terminal_at is not None
                and attempt.post_update_terminal_at <= attempt.scheduled_at + horizon_seconds * US
            ):
                post_update_terminal[attempt.post_update_terminal] += 1
                if attempt.post_update_terminal in ("EVICT", "FAIL"):
                    post_update_positive_jobs.add(attempt.job_id)
    abnormal = Counter()
    for label in ("EVICT", "FAIL"):
        abnormal.update(job_per_outcome[label])
    abnormal_unique_tasks = Counter({job: len(tasks) for job, tasks in abnormal_tasks_by_job.items()})
    top_job = abnormal.most_common(1)[0][0] if abnormal else None
    top_job_positives = [
        a for a in attempts
        if a.job_id == top_job
        and a.next_event in ("EVICT", "FAIL")
        and a.next_event_at is not None
        and a.next_event_at <= a.scheduled_at + horizon_seconds * US
    ]
    positive_durations = sorted(
        (a.next_event_at - a.scheduled_at) / US for a in top_job_positives
        if a.next_event_at is not None
    )
    return {
        "eligible_attempts": sum(outcome.values()),
        "distinct_tasks": len(set().union(*task_per_outcome.values())) if task_per_outcome else 0,
        "distinct_jobs": len(set().union(*(set(v) for v in job_per_outcome.values()))) if job_per_outcome else 0,
        "first_recorded_event": dict(sorted(outcome.items())),
        "jobs_by_first_recorded_event": {k: len(v) for k, v in sorted(job_per_outcome.items())},
        "tasks_by_first_recorded_event": {k: len(v) for k, v in sorted(task_per_outcome.items())},
        "evict_or_fail_jobs": len(abnormal),
        "largest_job_evict_or_fail": abnormal.most_common(1)[0][1] if abnormal else 0,
        "top_five_job_evict_or_fail": sum(n for _, n in abnormal.most_common(5)),
        "evict_or_fail_distinct_tasks": sum(abnormal_unique_tasks.values()),
        "largest_job_distinct_tasks_with_evict_or_fail": (
            abnormal_unique_tasks.most_common(1)[0][1] if abnormal_unique_tasks else 0
        ),
        "top_five_jobs_distinct_tasks_with_evict_or_fail": sum(
            n for _, n in abnormal_unique_tasks.most_common(5)
        ),
        "first_update_then_terminal_before_next_submit_within_window": dict(
            sorted(post_update_terminal.items())
        ),
        "first_update_then_evict_or_fail_jobs": len(post_update_positive_jobs),
        "largest_positive_job_retry_diagnostic": {
            "eligible_attempts": sum(a.job_id == top_job for a in attempts),
            "positive_attempts": len(top_job_positives),
            "positive_distinct_tasks": len({a.task_index for a in top_job_positives}),
            "positive_within_5_seconds": sum(seconds <= 5 for seconds in positive_durations),
            "median_seconds_until_exit": (
                positive_durations[len(positive_durations) // 2] if positive_durations else None
            ),
            "schedule_span_seconds": (
                (max(a.scheduled_at for a in top_job_positives)
                 - min(a.scheduled_at for a in top_job_positives)) / US
                if top_job_positives else None
            ),
        },
    }


def audit_shard(shard: int) -> tuple[dict, list[Attempt], int]:
    if shard not in SOURCES:
        raise ValueError("only preselected development shards 00050/00200 are available")
    path = ROOT / f"part-{shard:05d}-of-00500.csv.gz"
    source_hash = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            source_hash.update(chunk)
    digest = source_hash.hexdigest()
    if digest != SOURCES[shard]:
        raise ValueError(f"source SHA-256 mismatch for shard {shard:05d}")
    by_task: dict[tuple[str, str], list[Event]] = defaultdict(list)
    rows, end = 0, 0
    event_codes = Counter()
    missing_event_codes = Counter()
    with gzip.open(path, "rt", newline="") as source:
        for sequence, row in enumerate(csv.reader(source)):
            if len(row) < 11:
                raise ValueError(f"short event row in shard {shard:05d}")
            rows += 1
            time = int(row[0])
            end = max(end, time)
            event_codes[EVENT_NAME.get(row[5], row[5])] += 1
            if row[1]:
                missing_event_codes[EVENT_NAME.get(row[5], row[5])] += 1
            by_task[(row[2], row[3])].append(
                Event(time, sequence, row[5], row[1], row[4], row[6], row[7], row[8], row[9], row[10])
            )
    repeat_task_keys = 0
    pre_schedule_exit = Counter()
    same_time_submit_schedule = 0
    same_time_submit_terminal = 0
    same_time_schedule_terminal = 0
    for task_events in by_task.values():
        events = sorted(task_events, key=lambda e: (e.time, e.sequence))
        event_codes_at_time: dict[int, set[str]] = defaultdict(set)
        for event in events:
            event_codes_at_time[event.time].add(event.kind)
        same_time_submit_schedule += sum(
            "0" in codes and "1" in codes for codes in event_codes_at_time.values()
        )
        same_time_submit_terminal += sum(
            "0" in codes and bool(codes & set("23456"))
            for codes in event_codes_at_time.values()
        )
        same_time_schedule_terminal += sum(
            "1" in codes and bool(codes & set("23456"))
            for codes in event_codes_at_time.values()
        )
        submit_indices = [i for i, e in enumerate(events) if e.kind == "0"]
        repeat_task_keys += len(submit_indices) > 1
        for offset, submit_index in enumerate(submit_indices):
            stop = submit_indices[offset + 1] if offset + 1 < len(submit_indices) else len(events)
            segment = events[submit_index + 1:stop]
            schedules = [e for e in segment if e.kind == "1"]
            first_schedule_at = schedules[0].time if schedules else None
            pre = [
                e for e in segment
                if e.kind in "236" and not e.missing
                and (first_schedule_at is None or e.time < first_schedule_at)
            ]
            if pre:
                pre_schedule_exit[EVENT_NAME[pre[0].kind]] += 1
    attempts = [
        attempt
        for (job, task), events in by_task.items()
        for attempt in attempts_for_task(job, task, events, end)
    ]
    output = {
        "shard": shard,
        "target": "first_post_schedule_recorded_event; EVICT_or_FAIL_positive; UPDATE_RUNNING_separate",
        "source_sha256": digest,
        "event_rows": rows,
        "raw_event_codes": dict(sorted(event_codes.items())),
        "missing_info_event_codes": dict(sorted(missing_event_codes.items())),
        "task_keys_with_multiple_submits": repeat_task_keys,
        "submit_segments_with_pre_schedule_exit": dict(sorted(pre_schedule_exit.items())),
        "same_timestamp_submit_schedule_task_times": same_time_submit_schedule,
        "same_timestamp_submit_terminal_task_times": same_time_submit_terminal,
        "same_timestamp_schedule_terminal_task_times": same_time_schedule_terminal,
        "last_event_us": end,
        "horizons": {str(h): summarize(attempts, h, end) for h in HORIZONS_SECONDS},
    }
    return output, attempts, end


def main() -> None:
    training, train_attempts, train_end = audit_shard(50)
    validation, validation_attempts, validation_end = audit_shard(200)
    train_jobs = {a.job_id for a in train_attempts if a.scheduled_at <= train_end - 1800 * US}
    validation_jobs = {a.job_id for a in validation_attempts if a.scheduled_at <= validation_end - 1800 * US}
    new_job_attempts = [a for a in validation_attempts if a.job_id not in train_jobs]
    print(json.dumps({
        "training": training,
        "validation": validation,
        "train_validation_job_id_overlap": len(train_jobs & validation_jobs),
        "validation_unseen_job_only": {
            str(h): summarize(new_job_attempts, h, validation_end) for h in HORIZONS_SECONDS
        },
        "test_shard_00400_read": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
