"""Small synthetic checks for the frozen PAX eligibility rules."""

from __future__ import annotations

import io
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_pax_transition_gate import (  # noqa: E402
    client_stability,
    evaluate_folder,
    nonoverlap_support,
    read_control,
    read_result_times_only,
    result_coverage,
)


T = datetime(2017, 8, 21, 12, 0)


def stamp(seconds: int) -> str:
    return (T + timedelta(seconds=seconds)).isoformat(sep=" ")


def archive(nodes: list[tuple[int, int]], start: int, end: int) -> zipfile.ZipFile:
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as zf:
        zf.writestr("PAX/demo/runningVMs.csv", "".join(
            f"{(T + timedelta(seconds=s)).date()},{(T + timedelta(seconds=s)).time()},{n}\n"
            for s, n in nodes))
        zf.writestr("PAX/demo/clients.csv", "datetime,clients\n" + "".join(
            f"{stamp(s)},10\n" for s in range(start, end + 1, 10)))
        # Outcome cells are deliberately non-numeric; only the time is parsed.
        zf.writestr("PAX/demo/results.csv", "datetime,throughput,readC,readRT,updateC,updateRT\n" + "".join(
            f"{stamp(s)},UNTOUCHED,UNTOUCHED,UNTOUCHED,UNTOUCHED,UNTOUCHED\n"
            for s in range(start, end + 1, 10)))
    memory.seek(0)
    return zipfile.ZipFile(memory)


def test_one_isolated_transition_with_full_windows_is_eligible() -> None:
    with archive([(-60, 2), (0, 3), (60, 3)], -180, 180) as zf:
        summary, candidates = evaluate_folder("PAX/demo", zf)
    assert summary["raw_transitions"] == 1
    assert candidates[0]["eligible"]
    assert candidates[0]["result_timestamps"]["pre_count"] == 18
    assert candidates[0]["result_timestamps"]["post_count"] == 18
    assert "UNTOUCHED" not in repr(candidates)


def test_overlapping_three_minute_windows_reject_both() -> None:
    with archive([(-60, 2), (0, 3), (60, 3), (240, 3), (300, 2)], -180, 480) as zf:
        _, candidates = evaluate_folder("PAX/demo", zf)
    assert len(candidates) == 2
    assert all(not row["eligible"] for row in candidates)
    assert all("paired_windows_overlap" in row["reasons"] for row in candidates)


def test_result_duplicates_and_client_change_fail() -> None:
    times = [T + timedelta(seconds=s) for s in range(-180, 181, 10)]
    assert result_coverage(times, T)["pre_ok"]
    times.append(T - timedelta(seconds=100))
    assert not result_coverage(sorted(times), T)["pre_ok"]
    clients = [(T + timedelta(seconds=s), 10 if s < 0 else 12)
               for s in range(-180, 181, 10)]
    assert not client_stability(clients, T)["stable"]


def test_overlapping_experiment_ranges_not_counted_together() -> None:
    folders = [
        {"folder": "a", "time_start": "2017-01-01T00:00:00", "time_end": "2017-01-01T01:00:00"},
        {"folder": "b", "time_start": "2017-01-01T00:30:00", "time_end": "2017-01-01T02:00:00"},
        {"folder": "c", "time_start": "2017-01-02T00:00:00", "time_end": "2017-01-02T01:00:00"},
    ]
    candidates = [{"folder": folder, "eligible": True, "direction": direction}
                  for folder in ("a", "b", "c") for direction in ("out", "in")]
    result = nonoverlap_support(folders, candidates)
    assert result["overlapping_time_range_pairs"] == [["a", "b"]]
    assert len(result["best_nonoverlapping_folder_subset"]["folders"]) == 2


def test_only_terminal_blank_rows_are_ignored() -> None:
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as zf:
        zf.writestr("clients_ok.csv", "datetime,clients\n2017-08-21 12:00:00,1\n\n")
        zf.writestr("clients_bad.csv", "datetime,clients\n\n2017-08-21 12:00:00,1\n")
        zf.writestr("results_ok.csv", "datetime,throughput\n2017-08-21 12:00:00,UNTOUCHED\n\n")
        zf.writestr("results_bad.csv", "datetime,throughput\n\n2017-08-21 12:00:00,UNTOUCHED\n")
    memory.seek(0)
    with zipfile.ZipFile(memory) as zf:
        assert len(read_control(zf, "clients_ok.csv", "clients")) == 1
        assert len(read_result_times_only(zf, "results_ok.csv")) == 1
        with pytest.raises(ValueError, match="content after blank line"):
            read_control(zf, "clients_bad.csv", "clients")
        with pytest.raises(ValueError, match="content after blank line"):
            read_result_times_only(zf, "results_bad.csv")
