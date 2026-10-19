"""Focused invariants for nested donor margin selection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("cold_start_selective_safety", SCRIPT_DIR / "evaluate_cold_start_selective_safety.py")
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)


def _grid() -> dict:
    margin = np.full((2, 2, 8), -1.0)
    margin[:, 0, 0] = 0.10
    margin[:, 0, 1] = 0.30
    truth = np.zeros_like(margin, dtype=bool)
    truth[:, 0, 1] = True
    return dict(margin=margin, truth=truth,
                group=np.array(["g0", "g1"]), model=np.array(["m", "m"]),
                config=np.zeros((2, 8, 2), dtype=int))


def test_positive_margin_can_choose_more_expensive_candidate_or_abstain() -> None:
    grid = _grid()
    zero = study.choices(grid, 0.0)
    strict = study.choices(grid, 0.20)
    assert zero["rank"][:, 0].tolist() == [0, 0]
    assert strict["rank"][:, 0].tolist() == [1, 1]
    assert strict["rank"][:, 1].tolist() == [-1, -1]
    assert study._counts(zero)["false_approvals"] == 2
    assert study._counts(strict)["successful"] == 2


def test_inner_selection_needs_each_donor_approval_floor() -> None:
    inner = {}
    for donor in ("a", "b"):
        baseline = dict(selected=100, approved_groups=8, successful=80, false_approvals=20)
        by_margin = {"0.00": baseline}
        for margin in study.CANDIDATES:
            by_margin[f"{margin:.2f}"] = dict(selected=60, approved_groups=6,
                                               successful=55, false_approvals=5)
        inner[donor] = by_margin
    selected, _ = study.select_threshold(inner)
    assert selected == 0.05
    inner["b"]["0.05"]["approved_groups"] = 4
    selected, summary = study.select_threshold(inner)
    assert selected == 0.10
    assert not summary["0.05"]["each_donor_floor_met"]


def test_no_eligible_donor_margin_means_abstain_all() -> None:
    inner = {"a": {"0.00": dict(successful=100)}}
    for margin in study.CANDIDATES:
        inner["a"][f"{margin:.2f}"] = dict(selected=0, approved_groups=0,
                                              successful=0, false_approvals=0)
    selected, summary = study.select_threshold(inner)
    assert selected is None
    assert summary["0.50"]["false_approval_rate"] is None


def test_all_none_partial_strata_are_separate() -> None:
    grid = _grid()
    grid["truth"][0, 1, :] = True  # one all-feasible query
    grid["truth"][1, 1, :] = False  # one no-feasible query
    decision = study.choices(grid, 0.0)
    strata = study._per_stratum(grid, decision, "h", "zero")
    assert dict(zip(strata.stratum, strata.queries)) == {"none": 1, "partial": 2, "all": 1}
    assert strata.queries.sum() == 4


def test_zero_approval_outer_policy_fails_usefulness_gate() -> None:
    rows = []
    for method, selected, success, false, groups in (("zero", 100, 80, 20, 8),
                                                      ("fixed_005", 60, 55, 5, 6),
                                                      ("donor_selected", 0, 0, 0, 0)):
        rows.append(dict(fold="held", model_id="m", method=method,
                         selected=selected, successful=success,
                         false_approvals=false, approved_groups=groups))
    result = study._gate(pd.DataFrame(rows))
    assert not result["all_pass"]
    assert not result["criteria"]["all_models_approval_floor"]


def test_finalizer_converts_numpy_boolean_without_changing_value() -> None:
    from finalize_cold_start_selective_safety import _json_default

    assert _json_default(np.bool_(True)) is True
    assert _json_default(np.bool_(False)) is False


def test_abstention_audit_separates_wrong_feasible_choice() -> None:
    from audit_cold_start_selective_abstention import audit

    per_model = pd.DataFrame([dict(fold="h", model_id="m", method="selected",
                                   queries=10, selected=5, successful=3,
                                   false_approvals=2, oracle_feasible=6,
                                   missed_feasible=3)])
    strata = pd.DataFrame([
        dict(fold="h", model_id="m", method="selected", stratum="none",
             queries=4, selected=1, successful=0, false_approvals=1),
        dict(fold="h", model_id="m", method="selected", stratum="partial",
             queries=5, selected=3, successful=2, false_approvals=1),
        dict(fold="h", model_id="m", method="selected", stratum="all",
             queries=1, selected=1, successful=1, false_approvals=0),
    ])
    row = audit(per_model, strata).iloc[0]
    assert row.feasible_query_wrong_approvals == 1
    assert row.feasible_query_abstentions == 2
