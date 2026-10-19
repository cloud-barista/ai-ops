"""Synthetic boundary checks for the Google placement-delay label audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_google_2011_placement_delay.py"
SPEC = importlib.util.spec_from_file_location("google_placement_delay", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def event(seconds: int, job: str, task: str, kind: str, *,
          machine: str = "", missing: str = "") -> list[str]:
    return [str(seconds * 1_000_000), missing, job, task, machine, kind,
            "user", "1", "9", "0.1", "0.2", "0", "0"]


def test_common_right_censor_and_duplicate_submit_rules() -> None:
    rows = [
        event(0, "a", "0", "0"), event(5, "a", "0", "1", machine="m"),
        event(0, "b", "0", "0"), event(30, "b", "0", "1", machine="m"),
        event(0, "c", "0", "0"), event(601, "c", "0", "1", machine="m"),
        event(0, "d", "0", "0"), event(1, "d", "0", "0"),
        event(2, "d", "0", "1", machine="m"),
        event(700, "e", "0", "0"), event(701, "e", "0", "1", machine="m"),
        event(0, "g", "0", "0"), event(1, "g", "0", "1"),
        event(1200, "anchor", "0", "4"),
    ]
    summary, jobs = study.summarize_events(rows, 50)
    assert summary["single_submit_tasks"] == 5
    assert summary["clean_submit_tasks"] == 5
    assert summary["eligible_tasks"] == 4
    assert jobs == {"a", "b", "c", "g"}
    assert summary["distribution"]["5s"]["scheduled"] == 1
    assert summary["distribution"]["30s"]["scheduled"] == 2
    assert summary["distribution"]["600s"]["scheduled"] == 2
    assert summary["distribution"]["600s"]["not_scheduled"] == 2
    assert summary["not_scheduled_10m_breakdown"] == {
        "late_schedule_in_same_shard": 1,
        "no_valid_schedule_in_same_shard": 1,
        "killed_within_10m": 0,
    }


def test_schedule_at_horizon_is_observed_and_later_submit_is_censored() -> None:
    rows = [
        event(100, "a", "0", "0"),
        event(700, "a", "0", "1", machine="m"),
        event(101, "b", "0", "0"),
        event(700, "b", "0", "1", machine="m"),
        event(700, "anchor", "0", "4"),
    ]
    summary, jobs = study.summarize_events(rows, 50)
    assert jobs == {"a"}
    assert summary["eligible_tasks"] == 1
    assert summary["distribution"]["600s"]["scheduled"] == 1
