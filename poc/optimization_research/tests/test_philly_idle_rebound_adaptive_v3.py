"""Synthetic causal and accounting tests for the score-free Philly v3 design."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_philly_idle_rebound_adaptive_v3 as audit
import philly_idle_rebound_adaptive_v3 as method


T = method.VALIDATION_START_S


def test_anchor_choice_uses_input_keys_only_and_unknown_occupies_spacing():
    keys = [method.CandidateKey("m", T + d) for d in (0, 1800, 3600, 5400, 7200, 9000)]
    keys += [method.CandidateKey("other", T), method.CandidateKey("other", T + 1800)]
    selected = method.select_input_anchors(reversed(keys))
    assert selected == [method.CandidateKey("m", T), method.CandidateKey("other", T),
                        method.CandidateKey("m", T + 7200)]
    # An unknown first label does not trigger hindsight replacement by +1800.
    outcomes = [method.AnchorOutcome(k.machine_id, k.decision_utc_s,
                                     None if k.decision_utc_s == T else 1)
                for k in selected]
    assert [(r.machine_id, r.decision_utc_s) for r in outcomes] == [
        (k.machine_id, k.decision_utc_s) for k in selected]
    with pytest.raises(ValueError, match="Duplicate"):
        method.select_input_anchors(keys + keys[:1])


def test_input_eligibility_at_decision_does_not_read_future_gpu_windows():
    grid = np.arange(T - 2_100, T - 2_100 + 40 * 300, 300)
    peak = np.zeros(len(grid))
    valid = np.ones(len(grid), dtype=bool)
    first = list(audit.input_candidates("m", grid, peak, valid))[0]
    assert first[0] == method.CandidateKey("m", T)
    assert audit.label_anchor(grid, peak, valid, first[1]) == 0
    future = first[1] + 2
    peak[future] = 70
    assert list(audit.input_candidates("m", grid, peak, valid))[0] == first
    assert audit.label_anchor(grid, peak, valid, first[1]) == 1
    valid[future] = False
    assert list(audit.input_candidates("m", grid, peak, valid))[0] == first
    assert audit.label_anchor(grid, peak, valid, first[1]) is None


def test_maturity_is_strict_unknown_never_updates_and_train_is_not_double_counted():
    old = method.AnchorOutcome("m", T - 1800, 1)
    known = method.AnchorOutcome("m", T, 1)
    unknown = method.AnchorOutcome("z", T, None)
    future = method.AnchorOutcome("z", T + 1800, 0)
    history = [old, known, unknown, future]
    assert method.matured_outcomes(history, T + 7500) == []
    assert method.matured_outcomes(history, T + 7501) == [known]
    assert method.matured_outcomes(history, T + 9300) == [known]
    assert method.matured_outcomes(history, T + 9301) == [known, future]
    before = method.probabilities(history, "m", T + 7500,
                                  fleet_source_machine_ids=frozenset(("m", "z")))
    assert all(p == method.P0 for p in before.values())
    # Changing an unexpired outcome cannot alter support or any prediction.
    altered = [old, known, unknown, method.AnchorOutcome("z", T + 1800, 1)]
    assert method.probabilities(history, "m", T + 9300,
                                fleet_source_machine_ids=frozenset(("m", "z"))) == method.probabilities(
        altered, "m", T + 9300, fleet_source_machine_ids=frozenset(("m", "z")))


def test_cumulative_fleet_and_local_support_uses_distinct_days_and_loo():
    fleet = [method.AnchorOutcome(f"f{k}", T + day * 86400 + k * 1800,
                                  int(k < 2))
             for day in range(5) for k in range(24)]
    local = [method.AnchorOutcome("target", T + day * 86400 + k * 7200,
                                  int(k == 0))
             for day in range(3) for k in range(8)]
    d = T + 5 * 86400
    fleet_ids = frozenset(r.machine_id for r in fleet)
    fleet_stats, local_stats = method.support_for_decision(
        fleet + local, "target", d, fleet_source_machine_ids=fleet_ids)
    assert fleet_stats.days == 5 and fleet_stats.positive == 10
    assert fleet_stats.negative == 110 and fleet_stats.ess >= 100
    assert fleet_stats.fleet_ok
    assert local_stats.days == 3 and local_stats.positive == 3
    assert local_stats.negative == 21 and local_stats.ess >= 20
    assert local_stats.local_ok
    values = method.probabilities(fleet + local, "target", d,
                                  fleet_source_machine_ids=fleet_ids)
    p_fleet = (40 * method.P0 + fleet_stats.weighted_positive) / (40 + fleet_stats.weighted_n)
    p_local = (20 * p_fleet + local_stats.weighted_positive) / (20 + local_stats.weighted_n)
    assert values["candidate"] == pytest.approx(p_local)
    assert values["decayed_fleet_3d_40"] == pytest.approx(p_fleet)
    expanded_fleet = (40 * method.P0 + 10) / (40 + len(fleet))
    expanded_local = (20 * expanded_fleet + 3) / (20 + len(local))
    assert values["expanding_hierarchical_40_20"] == pytest.approx(expanded_local)
    # A different target cannot borrow this target's local labels.
    for_target = method.probabilities(fleet + local, "target", d,
                                      fleet_source_machine_ids=fleet_ids)
    for_other = method.probabilities(fleet + local, "unseen", d,
                                     fleet_source_machine_ids=fleet_ids | {"target"})
    assert for_target["decayed_machine_3d_20"] != for_other["decayed_machine_3d_20"]
    assert for_target["decayed_fleet_3d_40"] != for_other["decayed_fleet_3d_40"]
    with pytest.raises(ValueError, match="another holdout machine"):
        method.probabilities(fleet + local + [method.AnchorOutcome("other_holdout", T, 1)],
                             "target", d, fleet_source_machine_ids=fleet_ids)


def test_support_thresholds_are_cumulative_not_per_day_and_require_local_both_classes():
    ok_fleet = method.Support(100, 5, 10, 0, 100, 10)
    assert ok_fleet.fleet_ok  # positive count is cumulative; no daily requirement
    assert not method.Support(99.99, 5, 10, 100, 100, 10).fleet_ok
    assert not method.Support(100, 4, 10, 100, 100, 10).fleet_ok
    assert not method.Support(100, 5, 9, 100, 100, 9).fleet_ok
    assert method.Support(20, 3, 3, 3, 20, 3).local_ok
    assert not method.Support(19.99, 3, 3, 3, 20, 3).local_ok
    assert not method.Support(20, 2, 3, 3, 20, 3).local_ok
    assert not method.Support(20, 3, 2, 18, 20, 2).local_ok
    assert not method.Support(20, 3, 18, 2, 20, 18).local_ok


def test_support_fraction_denominator_is_every_input_candidate(monkeypatch):
    supported = method.Support(100, 5, 10, 90, 100, 10)
    local = method.Support(20, 3, 3, 17, 20, 3)
    empty = method.Support(0, 0, 0, 0, 0, 0)
    keys = [method.CandidateKey(f"m{k}", T + k * 1800) for k in range(10)]
    def support(_, machine_id, decision_utc_s, *, fleet_source_machine_ids):
        k = int(machine_id[1:])
        return (supported, local if k == 0 else empty) if k < 5 else (empty, empty)
    monkeypatch.setattr(audit.method, "support_for_decision", support)
    ids = frozenset(k.machine_id for k in keys)
    result = audit.summarize_support(keys, [], ids)
    assert result["fleet_supported_candidates"] == 5
    assert result["fleet_and_local_supported_candidates"] == 1
    assert result["fleet_support_fraction"] == 0.5
    assert result["fleet_and_local_support_fraction"] == 0.1
    assert result["support_gate"]["all_pass"]
    result = audit.summarize_support(keys + [method.CandidateKey("m10", T + 18000)], [],
                                     ids | {"m10"})
    assert not result["support_gate"]["all_pass"]


def test_simple_baselines_and_ties_are_predeclared():
    a = method.AnchorOutcome("fleet", T, 1)
    d = T + 9000
    result = method.probabilities([a], "target", d,
                                  fleet_source_machine_ids=frozenset(("fleet",)))
    assert result["candidate"] == method.P0  # fleet support gate is still closed
    assert result["expanding_fleet_40"] == pytest.approx((40 * method.P0 + 1) / 41)
    assert result["decayed_fleet_3d_40"] > method.P0
    assert result["rolling_fleet_7d_40"] == result["expanding_fleet_40"]
    assert result["decayed_machine_3d_20"] == method.P0
    scores = {name: 0.1 for name in method.BASELINE_TIE_ORDER}
    assert method.best_simple_baseline(scores) == "frozen_train_beta"
    scores["rolling_fleet_7d_40"] = 0.09
    assert method.best_simple_baseline(scores) == "rolling_fleet_7d_40"
    assert method.low_risk_tie_key("m", T) == method.low_risk_tie_key("m", T)
    assert method.low_risk_tie_key("m", T) != method.low_risk_tie_key("n", T)


def test_score_free_loader_excludes_december_before_window_build(monkeypatch):
    class ReaderReached(Exception):
        pass
    monkeypatch.setattr(audit, "verify_freeze", lambda _: {})
    monkeypatch.setattr(audit.original, "verify_source", lambda _: (Path("pinned.parquet"), {}))
    def fake_read(_, *, columns, filters):
        assert filters == [("time_utc_s", "<", audit.VALIDATION_END_S)]
        assert "machine_id" in columns and "gpu0_util" in columns
        raise ReaderReached
    monkeypatch.setattr(audit.pd, "read_parquet", fake_read)
    with pytest.raises(ReaderReached):
        audit.audit(Path("freeze.json"))


def test_fleet_allow_list_must_match_original_source_and_hash_bucket():
    original_id = next(f"source-{i}" for i in range(10_000)
                       if audit.original.machine_bucket(f"source-{i}") < 5)
    holdout_id = next(f"holdout-{i}" for i in range(10_000)
                      if 5 <= audit.original.machine_bucket(f"holdout-{i}") < 15)
    recorded = {original_id: {"rows": 10}}
    assert audit.verified_development_fleet_ids(frozenset((original_id,)),
                                                 {"machines": recorded}) == {original_id}
    with pytest.raises(ValueError, match="original <5"):
        audit.verified_development_fleet_ids(frozenset((holdout_id,)),
                                              {"machines": {holdout_id: {}}})
    with pytest.raises(ValueError, match="original <5"):
        audit.verified_development_fleet_ids(frozenset((original_id, holdout_id)),
                                              {"machines": recorded})
