"""Synthetic, out-of-order JOB event tests for the five-second builder."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_google_2019_job_schedule_5s.py"
spec = importlib.util.spec_from_file_location("build_google_2019_job_schedule_5s", SCRIPT)
builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder)


def event(time: int | str | None, job_id: int, kind: int | None, **fields: object) -> dict:
    return {"time": time, "type": kind, "collection_id": job_id,
            "collection_type": 0, **fields}


def run_build(tmp_path: Path, rows: list[dict]) -> tuple[list[dict], dict, Path]:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "fixture.json.gz"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    destination = tmp_path / "output"
    metadata = builder.build_dataset(source, destination, "e",
                                     expected_sha256=builder.sha256(source))
    with (destination / "jobs.csv").open(newline="", encoding="utf-8") as stream:
        jobs = list(csv.DictReader(stream))
    return jobs, metadata, destination


def test_out_of_order_first_schedule_boundaries_and_submit_only_features(tmp_path: Path) -> None:
    base = 1_000_000_000
    anchor = base + builder.FIVE_MINUTES_US + 20_000_000
    rows = [
        # Deliberately put future events first and change their feature values.
        event(base + 5_000_000, 1, 3, priority=999, scheduler=999),
        event(base + 5_000_001, 2, 3),
        event(base + 4_000_000, 1, 1),
        event(base + 4_500_000, 1, 2),
        event(base, 1, 0, scheduling_class="2", priority="114", scheduler="1",
              alloc_collection_id="900", parent_collection_id=901,
              start_after_collection_ids=[902, "903"], user="do-not-export"),
        event(base, 2, 0, priority=100, alloc_collection_id=0,
              parent_collection_id=0, missing_type=0),
        event(base + 1_000_000, 3, 0),
        event(anchor - 10_000_000, 4, 0),  # Five-second complete; five-minute incomplete.
        event(anchor - 2_000_000, 5, 0),   # Five-second right-censored.
        event(anchor, 900, 9),
    ]
    jobs, metadata, destination = run_build(tmp_path, rows)
    assert [int(job["late_schedule_5s"]) for job in jobs] == [0, 1, 1, 1]
    assert [int(job["submit_time_us"]) for job in jobs] == [base, base, base + 1_000_000,
                                                             anchor - 10_000_000]
    first = jobs[0]
    assert [first[key] for key in builder.MODEL_FEATURES] == ["2", "114", "1", "1", "1", "2"]
    assert [jobs[1][key] for key in builder.MODEL_FEATURES] == ["-1", "100", "-1", "0", "0", "0"]
    assert "user" not in first and "do-not-export" not in (destination / "jobs.csv").read_text()
    assert len(first["job_key"]) == 64
    assert first["job_key"] != "1"
    audit = metadata["audit"]
    assert audit["valid_job_queue_rows"] == 1
    assert audit["valid_job_enable_rows"] == 1
    assert audit["excluded_incomplete_5s_ids"] == 1
    assert audit["would_lack_complete_5m_observation_ids"] == 1
    assert audit["label_0_jobs"] == 1 and audit["label_1_jobs"] == 3
    assert metadata["source"]["sha256"] == builder.sha256(tmp_path / "source" / "fixture.json.gz")
    assert metadata["csv_sha256"] == builder.sha256(destination / "jobs.csv")
    assert not list(destination.glob("*.sqlite3"))
    assert not list(destination.glob(".job_schedule_scratch_*"))


def test_competing_terminal_and_ambiguous_jobs_are_excluded(tmp_path: Path) -> None:
    base = 500_000_000
    rows = [
        event(base, 10, 0), event(base + 1_000_000, 10, 7),
        event(base + 2_000_000, 10, 3),  # KILL before schedule: late=1.
        event(base, 11, 0), event(base + 2_000_000, 11, 3),
        event(base + 3_000_000, 11, 5),  # Schedule wins.
        event(base, 12, 0), event(base + 2_000_000, 12, 3),
        event(base + 2_000_000, 12, 6),  # Same-time collision.
        event(base, 13, 0), event(base + 1_000_000, 13, 0),  # Duplicate submit.
        event(base, 14, 0), event(base + 1_000_000, 14, 3),
        event(base + 1_000_000, 14, 3),  # Duplicate first schedule.
        event(base, 15, 0), event(base - 1, 15, 3),  # Reversed schedule.
        event(base, 16, 0), event(base - 1, 16, 8),  # Reversed terminal.
        event(base, 17, 0), event(base + 1, 17, 1, missing_type=2),
        event(0, 18, 0), event(base, 18, 0),  # Sentinel submit invalidates ID.
        event(base, 19, 0, start_after_collection_ids=[None]),
        event(builder.INT64_MAX, 21, 0), event(base, 21, 0),
        event(None, 22, 0), event(base, 22, 0),
        event(base, 23, 0), event(base + 1, 23, None),
        {"time": base, "type": 0, "collection_id": 20, "collection_type": 1},
        event(base + 6_000_000, 900, 9),
    ]
    jobs, metadata, _ = run_build(tmp_path, rows)
    assert len(jobs) == 4
    assert [int(job["late_schedule_5s"]) for job in jobs] == [1, 0, 1, 1]
    audit = metadata["audit"]
    assert audit["label_1_competing_terminal_within_5s"] == 1
    assert audit["excluded_schedule_terminal_within_5s_collision_ids"] == 1
    assert audit["excluded_duplicate_submit_within_5s_ids"] == 1
    assert audit["excluded_duplicate_first_schedule_within_5s_ids"] == 1
    assert audit["excluded_reversed_schedule_ids"] == 1
    assert audit["excluded_reversed_terminal_ids"] == 1
    assert audit["excluded_bad_event_within_5s_ids"] == 2
    assert audit["excluded_pretrace_job_ids"] == 1
    assert audit["excluded_invalid_first_submit_feature_ids"] == 1
    assert audit["invalid_submit_feature_rows"] == 1
    assert audit["excluded_missing_or_invalid_type_rows"] == 1
    assert audit["excluded_invalid_or_sentinel_time_rows"] == 3
    assert audit["non_job_rows"] == 1


def test_events_after_label_window_cannot_select_jobs_out(tmp_path: Path) -> None:
    base = 1_000_000_000
    rows = [
        event(base, 30, 0), event(base + 6_000_000, 30, 3),
        event(base + 6_000_000, 30, 3), event(base + 6_000_000, 30, 6),
        event(base, 31, 0), event(base + 1_000_000, 31, 3),
        event(base + 100_000_000, 31, 1, missing_type=2),
        event(base + 6_000_000, 32, 0), event(base + 1_000_000, 32, 3),
        event(base, 32, 0),
        event(base + 120_000_000, 900, 9),
    ]
    jobs, metadata, _ = run_build(tmp_path, rows)
    assert [int(job["late_schedule_5s"]) for job in jobs] == [1, 0, 0]
    audit = metadata["audit"]
    assert audit["ignored_later_duplicate_first_schedule_ids"] == 1
    assert audit["ignored_later_schedule_terminal_collision_ids"] == 1
    assert audit["ignored_later_bad_event_ids"] == 1
    assert audit["ignored_later_duplicate_submit_ids"] == 1
    assert audit["labeled_jobs"] == 3


def test_hash_guard_fresh_output_and_fh_rejection(tmp_path: Path) -> None:
    rows = [event(10_000_000, 1, 0), event(16_000_000, 9, 9)]
    jobs, _, output = run_build(tmp_path, rows)
    assert len(jobs) == 1
    source = tmp_path / "source" / "fixture.json.gz"
    with pytest.raises(FileExistsError):
        builder.build_dataset(source, output, "e", expected_sha256=builder.sha256(source))
    with pytest.raises(ValueError, match="SHA-256"):
        builder.build_dataset(source, tmp_path / "wrong", "e", expected_sha256="0" * 64)
    assert not (tmp_path / "wrong").exists()
    fh = tmp_path / "google_cluster_2019_f"
    fh.mkdir()
    forbidden = fh / "fixture.json.gz"
    forbidden.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="f/h"):
        builder.build_dataset(forbidden, tmp_path / "forbidden", "e",
                              expected_sha256=builder.sha256(source))
    assert not (tmp_path / "forbidden").exists()
