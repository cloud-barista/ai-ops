"""Guard source integrity and the sampled-transition interpretation."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_token_aware_autoscaling.py"
SPEC = importlib.util.spec_from_file_location("audit_token_aware_autoscaling", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_growth_match_stops_at_next_target_change() -> None:
    matches = audit._matched_growth(
        times=[0, 15, 30, 45],
        desired=[1, 2, 3, 3],
        ready=[1, 1, 2, 3],
    )
    assert len(matches) == 2
    assert matches[0]["matched_before_next_target_change"] is False
    assert matches[1]["matched_before_next_target_change"] is True
    assert matches[1]["sampled_lag_s"] == 15


def test_growth_lag_is_only_a_difference_between_row_timestamps() -> None:
    matches = audit._matched_growth(
        times=[0, 16, 32], desired=[1, 2, 2], ready=[1, 1, 2]
    )
    assert matches[0]["sampled_lag_s"] == 16
    assert "lag_interval_s" not in matches[0]


def test_pinned_originals_reproduce_denominators() -> None:
    result = audit.inspect()
    assert (result["source_files"], result["source_bytes"]) == (35, 260_916)
    assert sum(run["timeline_samples"] for run in result["runs"]) == 776
    assert sum(run["summary_success_count"] for run in result["runs"]) == 5_612
    assert sum(run["summary_failure_count"] for run in result["runs"]) == 295
    assert sum(
        len(role["growth_matches"])
        for run in result["runs"] for role in run["roles"].values()
    ) == 11
