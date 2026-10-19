"""V2 tie semantics, information boundaries and clustered first-event scoring."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/evaluate_google_2011_early_exit_risk_v2.py"
SPEC = importlib.util.spec_from_file_location("google_2011_early_exit_risk_v2", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def attempt(job: str, task: str, submit: int, schedule: int, event: str) -> study.audit.Attempt:
    return study.audit.Attempt(job, task, submit * study.audit.US,
                               schedule * study.audit.US,
                               (schedule + 5) * study.audit.US, event)


def row(job: str, task: str, event: str = "FAIL", *, cpu: float = 0.1) -> study.Row:
    return study.Row(job, task, 0, 1, event, study.label_for_event(event),
                     "1", "9", cpu, 0.2)


def event(second: int, sequence: int, kind: str, *, missing: str = "") -> study.audit.Event:
    return study.audit.Event(second * study.audit.US, sequence, kind, missing,
                             "m" if kind == "1" else "", "u", "1", "9", "0.1", "0.2")


def test_terminal_update_or_duplicate_schedule_at_same_time_is_ambiguous() -> None:
    for other in ("7", "8", "1"):
        events = [event(2, 2, "2"), event(2, 3, other)]
        assert study.audit._first_recorded_event(events, study.audit.US) == (
            2 * study.audit.US, "ambiguous_state_tie")


def test_duplicate_identical_terminal_and_two_different_terminals_are_ambiguous() -> None:
    for second_code in ("2", "3"):
        events = [event(2, 2, "2"), event(2, 3, second_code)]
        assert study.audit._first_recorded_event(events, study.audit.US) == (
            2 * study.audit.US, "ambiguous_terminal_tie")


def test_terminal_then_retry_submit_is_only_ordered_exception() -> None:
    terminal_first = [event(2, 2, "2"), event(2, 3, "0")]
    submit_first = [event(2, 2, "0"), event(2, 3, "2")]
    assert study.audit._first_recorded_event(terminal_first, study.audit.US) == (
        2 * study.audit.US, "EVICT")
    assert study.audit._first_recorded_event(submit_first, study.audit.US) == (
        2 * study.audit.US, "ambiguous_state_tie")


def test_missing_marker_wins_over_same_time_terminal() -> None:
    events = [event(2, 2, "2"), event(2, 3, "8", missing="1")]
    assert study.audit._first_recorded_event(events, study.audit.US) == (
        2 * study.audit.US, "missing_marker")


def test_raw_first_event_tie_audit_counts_duplicate_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "task_events"
    source.mkdir()
    path = source / "part-00050-of-00500.csv.gz"
    records = [
        ["0", "", "j", "t", "", "0", "u", "1", "9", "0.1", "0.2"],
        ["1000000", "", "j", "t", "m", "1", "u", "1", "9", "0.1", "0.2"],
        ["2000000", "", "j", "t", "m", "2", "u", "1", "9", "0.1", "0.2"],
        ["2000000", "", "j", "t", "m", "8", "u", "1", "9", "0.1", "0.2"],
    ]
    with gzip.open(path, "wt", newline="") as stream:
        csv.writer(stream).writerows(records)
    monkeypatch.setattr(study, "SOURCE", tmp_path)
    result = study.raw_first_event_ties(50, [attempt("j", "t", 0, 1, "EVICT")])
    assert result["attempts_with_first_event_in_window"] == 1
    assert result["multi_row_first_event_timestamps"] == 0  # attempt() uses 6s, not 2s
    corrected = study.audit.Attempt("j", "t", 0, study.audit.US,
                                     2 * study.audit.US, "ambiguous_state_tie")
    result = study.raw_first_event_ties(50, [corrected])
    assert result["multi_row_first_event_timestamps"] == 1
    assert result["event_code_combinations"] == {"2+8": 1}
    with gzip.open(path, "wt", newline="") as stream:
        csv.writer(stream).writerows([*records, records[2]])
    duplicated = study.raw_first_event_ties(50, [corrected])
    assert duplicated["duplicate_identical_event_code_timestamps"] == 1
    assert duplicated["event_code_combinations"] == {"2+2+8": 1}


def test_first_ambiguous_attempt_is_not_replaced_by_later_successful_retry() -> None:
    attempts = [attempt("job", "task", 0, 1, "missing_marker"),
                attempt("job", "task", 10, 11, "FAIL")]
    selected = study.first_eligible_per_task(attempts, 4000 * study.audit.US)
    assert len(selected) == 1
    assert selected[0].next_event == "missing_marker"
    assert study.label_for_event(study.first_event_in_window(selected[0])) is None


def test_competing_first_events_are_negative_for_this_target_not_success() -> None:
    for event in ("FINISH", "KILL", "UPDATE_PENDING", "UPDATE_RUNNING", "LOST",
                  "no_recorded_event_in_window"):
        assert study.label_for_event(event) == 0
    for event in ("EVICT", "FAIL"):
        assert study.label_for_event(event) == 1
    for event in ("missing_marker", "ambiguous_terminal_tie", "SCHEDULE", "SUBMIT"):
        assert study.label_for_event(event) is None


def test_first_event_horizon_is_inclusive_and_later_event_is_no_event() -> None:
    at_boundary = study.audit.Attempt("j", "t", 0, 1_000_000, 1_801_000_000, "EVICT")
    beyond = study.audit.Attempt("j", "t", 0, 1_000_000, 1_801_000_001, "FAIL")
    assert study.first_event_in_window(at_boundary) == "EVICT"
    assert study.first_event_in_window(beyond) == "no_recorded_event_in_window"


def test_submit_only_features_ignore_future_schedule_machine_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "task_events"
    source.mkdir()
    path = source / "part-00050-of-00500.csv.gz"
    submit = ["0", "", "j", "t", "", "0", "u", "1", "9", "0.1", "0.2"]
    later_a = [["1000000", "", "j", "t", "host-A", "1", "u", "3", "2", "0.8", "0.9"],
               ["2000000", "", "j", "t", "host-A", "3", "u", "3", "2", "0.8", "0.9"]]
    later_b = [["1000000", "", "j", "t", "host-B", "1", "u", "0", "0", "0.01", "0.01"],
               ["2000000", "", "j", "t", "host-B", "8", "u", "0", "0", "0.01", "0.01"]]
    monkeypatch.setattr(study, "SOURCE", tmp_path)
    selected = [attempt("j", "t", 0, 1, "FAIL")]
    profiles = []
    for later in (later_a, later_b):
        with gzip.open(path, "wt", newline="") as stream:
            csv.writer(stream).writerows([submit, *later])
        profiles.append(study.read_submit_profiles(50, selected))
    assert profiles[0] == profiles[1] == {("j", "t", 0): ("1", "9", 0.1, 0.2)}


def test_job_disjoint_validation_removes_all_rows_from_shared_job() -> None:
    training = [row("shared", "a")]
    validation = [row("shared", "b"), row("new", "c"), row("new", "d")]
    actual = study.validation_without_training_jobs(training, validation)
    assert [r.task_index for r in actual] == ["c", "d"]
    assert not {r.job_id for r in training} & {r.job_id for r in actual}


def test_job_macro_weights_and_shrunk_cell_rate() -> None:
    rows = [row("big", str(i)) for i in range(100)] + [row("small", "0", "FINISH")]
    weights = study.job_weights(rows)
    assert np.isclose(weights[:100].sum(), 1)
    assert np.isclose(weights[-1], 1)
    global_rate, cells = study.fit_baselines(rows)
    assert np.isclose(global_rate, 0.5)
    assert np.isclose(cells[("1", "9")], 0.5)
    assert np.isclose(study.predict_baselines([row("unseen", "1")], global_rate, cells)["global"][0], 0.5)


def test_reserved_shards_cannot_be_opened_through_evaluator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(study, "SOURCE", tmp_path)
    for shard in (350, 400, 450):
        with pytest.raises(ValueError, match="reserved test shards"):
            study.rows_for_shard(shard)
        with pytest.raises(ValueError, match="reserved test shards"):
            study.read_submit_profiles(shard, [])


def test_job_macro_scoring_differs_from_row_micro_with_one_large_job() -> None:
    rows = [row("large", str(i)) for i in range(10)] + [row("small", "0", "FINISH")]
    predictions = {"global": np.full(11, 0.5),
                   "class_priority_shrunk": np.full(11, 0.5),
                   "hgb": np.full(11, 0.9)}
    metrics, jobs = study.score(rows, predictions)
    assert len(jobs) == 2
    assert np.isclose(metrics["job_macro_observed_rate"], 0.5)
    assert np.isclose(metrics["methods"]["hgb"]["job_macro_brier"], (0.01 + 0.81) / 2)
    assert metrics["methods"]["hgb"]["row_micro_brier"] < metrics["methods"]["hgb"]["job_macro_brier"]
