"""Boundary and grouping tests for the score-free Alibaba GPU SM audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_alibaba_gpu_sm_change_support.py"
SPEC = importlib.util.spec_from_file_location("alibaba_gpu_sm_change", SCRIPT)
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def _workload_in_bucket(low: int, high: int) -> str:
    return next(
        f"synthetic-{i}" for i in range(10000)
        if low <= study.workload_bucket(f"synthetic-{i}") < high
    )


def _pair(
    pod: str, workload: str | None, day: int, before: float, after: float,
    *, server_before: str = "s", server_after: str = "s", gpu_count: int = 1,
) -> list[dict]:
    return [
        {
            "pod_id": pod, "workload_id": workload, "server_id": server,
            "gpu_request": 1, "server_gpu_count": gpu_count,
            "avg_gpu_sm_util": sm, "day": day, "hour": hour,
        }
        for hour, sm, server in (
            (0, before, server_before), (1, after, server_after)
        )
    ]


def test_workload_disjoint_date_split_and_strict_change_boundary() -> None:
    dev = _workload_in_bucket(0, 70)
    val = _workload_in_bucket(70, 85)
    test = _workload_in_bucket(85, 100)
    rows = (
        _pair("p-dev", dev, 0, 20, 30.01)
        + _pair("p-val", val, 1, 20, 30)  # Exactly ten is not positive.
        + _pair("p-test", test, 5, 0, 12)
        + _pair("p-unknown", None, 0, 0, 90)
        + _pair("p-moved-server", dev, 0, 0, 90, server_after="other")
    )
    # This pod has two workload IDs and must not enter either bucket.
    unstable = _pair("p-unstable", dev, 0, 0, 90)
    unstable[1]["workload_id"] = val
    result = study.audit_frame(pd.DataFrame(rows + unstable))
    one = result["cohorts"]["one_gpu_server_and_pod"]
    assert result["excluded_unstable_or_unknown_pods"] == 2
    assert one["development"]["eligible_pairs"] == 1
    assert one["development"]["changes_gt_10pp"] == 1
    assert one["development"]["server_changed_pairs_excluded"] == 1
    assert one["validation"]["eligible_pairs"] == 1
    assert one["validation"]["changes_gt_10pp"] == 0
    assert one["test"]["eligible_pairs"] == 1
    assert one["test"]["changes_gt_10pp"] == 1


def test_strict_server_cohort_excludes_multi_gpu_server_and_invalid_sm() -> None:
    dev = _workload_in_bucket(0, 70)
    rows = (
        _pair("p-multi", dev, 0, 3, 20, gpu_count=4)
        + _pair("p-invalid", dev, 0, -1, 40)
        + _pair("p-valid", dev, 0, 4, 16)
    )
    result = study.audit_frame(pd.DataFrame(rows))["cohorts"]
    assert result["one_gpu_pod"]["development"]["eligible_pairs"] == 2
    assert result["one_gpu_server_and_pod"]["development"]["eligible_pairs"] == 1


def test_duplicate_pod_hour_is_rejected_before_pairing() -> None:
    dev = _workload_in_bucket(0, 70)
    rows = _pair("p", dev, 0, 0, 20)
    with pytest.raises(ValueError, match="duplicate pod-hour"):
        study.audit_frame(pd.DataFrame(rows + [rows[0]]))
