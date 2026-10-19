"""Synthetic checks for PAX v2 time geometry; outcome cells are never numbers."""

from __future__ import annotations

import io
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_pax_transition_gap_bracket_v2 import (  # noqa: E402
    client_coverage_and_stability,
    evaluate_folder,
    sample_coverage,
    stage_counts,
    windows_touch,
)


T = datetime(2017, 8, 21, 12, 0)


def stamp(seconds: int) -> str:
    return (T + timedelta(seconds=seconds)).isoformat(sep=" ")


def archive(nodes: list[tuple[int, int]], result_seconds: list[int],
            client_rows: list[tuple[int, int]]) -> zipfile.ZipFile:
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as zf:
        zf.writestr("PAX/demo/runningVMs.csv", "".join(
            f"{(T + timedelta(seconds=s)).date()},{(T + timedelta(seconds=s)).time()},{n}\n"
            for s, n in nodes))
        zf.writestr("PAX/demo/clients.csv", "datetime,clients\n" + "".join(
            f"{stamp(s)},{n}\n" for s, n in client_rows))
        zf.writestr("PAX/demo/results.csv", "datetime,throughput,readRT\n" + "".join(
            f"{stamp(s)},OUTCOME_MUST_STAY_UNREAD,OUTCOME_MUST_STAY_UNREAD\n"
            for s in result_seconds))
    memory.seek(0)
    return zipfile.ZipFile(memory)


def baseline() -> tuple[list[tuple[int, int]], list[int], list[tuple[int, int]]]:
    nodes = [(-185, 2), (0, 2), (185, 3), (370, 3)]
    # No YCSB result inside the unobserved node transition gap.
    results = list(range(-180, 1, 10)) + list(range(195, 366, 10))
    clients = [(s, 10) for s in range(-180, 366, 5)]
    return nodes, results, clients


def test_bracketed_transition_eligible_without_any_gap_outcome() -> None:
    nodes, results, clients = baseline()
    with archive(nodes, results, clients) as zf:
        summary, rows = evaluate_folder("PAX/demo", zf)
    assert summary["raw_transitions"] == 1
    assert rows[0]["eligible"]
    assert rows[0]["unknown_transition_gap_seconds"] == 185
    assert rows[0]["result_timestamps"]["pre_count"] == 19
    assert rows[0]["result_timestamps"]["post_count"] == 18
    assert "OUTCOME_MUST_STAY_UNREAD" not in repr(rows)
    assert stage_counts(rows)["paired_windows_nonoverlap"] == 1


def test_result_at_first_new_observation_is_excluded_from_post() -> None:
    old_t, new_t = T, T + timedelta(seconds=185)
    times = [T + timedelta(seconds=s) for s in range(-180, 1, 10)]
    times += [new_t] + [T + timedelta(seconds=s) for s in range(195, 366, 10)]
    result = sample_coverage(sorted(times), old_t, new_t)
    assert result["pre_ok"] and result["post_ok"]
    assert result["post_count"] == 18


def test_client_change_only_inside_unknown_gap_is_rejected() -> None:
    nodes, results, clients = baseline()
    clients = [(s, 12 if s == 100 else n) for s, n in clients]
    with archive(nodes, results, clients) as zf:
        _, rows = evaluate_folder("PAX/demo", zf)
    assert not rows[0]["eligible"]
    assert "client_count_not_stable" in rows[0]["reasons"]


def test_missing_gap_client_samples_is_rejected() -> None:
    nodes, results, clients = baseline()
    clients = [(s, n) for s, n in clients if not 20 <= s <= 170]
    with archive(nodes, results, clients) as zf:
        _, rows = evaluate_folder("PAX/demo", zf)
    assert "client_timestamp_coverage" in rows[0]["reasons"]
    assert not rows[0]["client_counts"]["continuous_across_gap"]


def test_prior_and_next_same_state_node_samples_required() -> None:
    _, results, clients = baseline()
    with archive([(0, 2), (185, 3)], results, clients) as zf:
        _, rows = evaluate_folder("PAX/demo", zf)
    assert "node_state_side_support" in rows[0]["reasons"]
    assert not rows[0]["eligible"]


def test_duplicate_result_timestamp_fails_coverage() -> None:
    nodes, results, clients = baseline()
    with archive(nodes, results + [-100], clients) as zf:
        _, rows = evaluate_folder("PAX/demo", zf)
    assert "result_timestamp_coverage" in rows[0]["reasons"]


def test_paired_window_boundary_contact_counts_as_overlap() -> None:
    a = {"old_observed_at": T.isoformat(),
         "new_observed_at": (T + timedelta(seconds=185)).isoformat()}
    b = {"old_observed_at": (T + timedelta(seconds=545)).isoformat(),
         "new_observed_at": (T + timedelta(seconds=730)).isoformat()}
    assert windows_touch(a, b)
    b["old_observed_at"] = (T + timedelta(seconds=546)).isoformat()
    assert not windows_touch(a, b)


def test_client_median_one_and_ten_percent_boundary() -> None:
    old_t, new_t = T, T + timedelta(seconds=185)
    clients = [(T + timedelta(seconds=s), 10 if s < 185 else 11)
               for s in range(-180, 366, 5)]
    assert client_coverage_and_stability(clients, old_t, new_t)["stable"]
