"""Synthetic episode boundaries for the Google 2011 early-exit audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_google_2011_early_exit.py"
SPEC = importlib.util.spec_from_file_location("google_early_exit", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def event(seconds: int, sequence: int, kind: str, *, missing: str = "") -> study.Event:
    return study.Event(
        seconds * study.US, sequence, kind, missing,
        "m" if kind == "1" else "", "u", "1", "9", "0.1", "0.2",
    )


def test_retry_is_a_new_attempt_and_first_per_task_does_not_count_it_twice() -> None:
    events = [
        event(0, 0, "0"), event(1, 1, "1"), event(2, 2, "3"),
        event(2, 3, "0"), event(3, 4, "1"), event(5, 5, "4"),
    ]
    attempts = study.attempts_for_task("j", "t", events, 4000 * study.US)
    assert [a.next_event for a in attempts] == ["FAIL", "FINISH"]
    all_attempts = study.summarize(attempts, 300, 4000 * study.US)["all_attempts"]
    first = study.summarize(attempts, 300, 4000 * study.US)["first_eligible_attempt_per_task"]
    assert all_attempts["first_recorded_event"] == {"FAIL": 1, "FINISH": 1}
    assert first["first_recorded_event"] == {"FAIL": 1}


def test_duplicate_schedule_and_same_time_terminal_are_censored() -> None:
    duplicate = [event(0, 0, "0"), event(1, 1, "1"), event(3, 2, "1"), event(4, 3, "3")]
    same_time = [event(0, 0, "0"), event(1, 1, "1"), event(1, 2, "3")]
    a = study.attempts_for_task("j", "a", duplicate, 4000 * study.US)
    b = study.attempts_for_task("j", "b", same_time, 4000 * study.US)
    assert [x.next_event for x in a + b] == ["SCHEDULE", "ambiguous_zero_duration"]


def test_duplicate_pending_submit_and_unsorted_input_use_latest_submit() -> None:
    events = [
        event(0, 0, "0"), event(1, 1, "0"),
        event(2, 2, "1"), event(3, 3, "2"),
    ]
    attempts = study.attempts_for_task("j", "t", list(reversed(events)), 4000 * study.US)
    assert len(attempts) == 1
    assert attempts[0].submitted_at == 1 * study.US
    assert attempts[0].next_event == "EVICT"


def test_pre_schedule_failure_does_not_become_running_failure() -> None:
    events = [
        event(0, 0, "0"), event(1, 1, "3"),
        event(1, 2, "0"), event(2, 3, "1"), event(4, 4, "2"),
    ]
    attempts = study.attempts_for_task("j", "t", events, 4000 * study.US)
    assert len(attempts) == 1
    assert attempts[0].submitted_at == 1 * study.US
    assert attempts[0].next_event == "EVICT"


def test_missing_info_censors_and_horizon_boundary_is_inclusive() -> None:
    missing = [event(0, 0, "0"), event(1, 1, "1"), event(2, 2, "3", missing="0")]
    boundary = [event(0, 0, "0"), event(1, 1, "1"), event(301, 2, "2")]
    a = study.attempts_for_task("j", "a", missing, 4000 * study.US)
    b = study.attempts_for_task("j", "b", boundary, 4000 * study.US)
    assert a[0].next_event == "missing_marker"
    assert study.summarize(b, 300, 4000 * study.US)["all_attempts"]["first_recorded_event"] == {"EVICT": 1}
    assert study.summarize(b, 299, 4000 * study.US)["all_attempts"]["first_recorded_event"] == {"no_recorded_event_in_window": 1}


def test_update_masks_later_failure_in_first_event_target() -> None:
    events = [
        event(0, 0, "0"), event(1, 1, "1"),
        event(10, 2, "8"), event(60, 3, "3"),
    ]
    attempts = study.attempts_for_task("j", "t", events, 4000 * study.US)
    result = study.summarize(attempts, 300, 4000 * study.US)["first_eligible_attempt_per_task"]
    assert result["first_recorded_event"] == {"UPDATE_RUNNING": 1}
    assert result["first_update_then_terminal_before_next_submit_within_window"] == {"FAIL": 1}
    assert result["first_update_then_evict_or_fail_jobs"] == 1


def test_later_finish_and_next_submit_do_not_create_masked_failure() -> None:
    finished = [
        event(0, 0, "0"), event(1, 1, "1"),
        event(10, 2, "8"), event(20, 3, "4"), event(30, 4, "3"),
    ]
    retried = [
        event(0, 0, "0"), event(1, 1, "1"), event(10, 2, "8"),
        event(20, 3, "0"), event(21, 4, "1"), event(22, 5, "3"),
    ]
    a = study.attempts_for_task("j", "finished", finished, 4000 * study.US)
    b = study.attempts_for_task("j", "retried", retried, 4000 * study.US)
    assert study.summarize(a, 300, 4000 * study.US)["all_attempts"]["first_update_then_terminal_before_next_submit_within_window"] == {"FINISH": 1}
    assert [attempt.next_event for attempt in b] == ["UPDATE_RUNNING", "FAIL"]
    assert b[0].post_update_terminal == "none"
    assert study.summarize(b, 300, 4000 * study.US)["first_eligible_attempt_per_task"]["first_update_then_terminal_before_next_submit_within_window"] == {}


def test_post_update_exit_after_horizon_is_not_counted() -> None:
    events = [
        event(0, 0, "0"), event(1, 1, "1"),
        event(10, 2, "8"), event(302, 3, "2"),
    ]
    attempts = study.attempts_for_task("j", "t", events, 4000 * study.US)
    five_minutes = study.summarize(attempts, 300, 4000 * study.US)["first_eligible_attempt_per_task"]
    thirty_minutes = study.summarize(attempts, 1800, 4000 * study.US)["first_eligible_attempt_per_task"]
    assert five_minutes["first_recorded_event"] == {"UPDATE_RUNNING": 1}
    assert five_minutes["first_update_then_terminal_before_next_submit_within_window"] == {}
    assert thirty_minutes["first_update_then_terminal_before_next_submit_within_window"] == {"EVICT": 1}


def test_locked_test_shard_is_rejected_before_any_path_is_opened() -> None:
    with pytest.raises(ValueError, match="preselected development shards"):
        study.audit_shard(400)
