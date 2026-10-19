"""Frozen-design probability rules for Philly idle rebound, never run by audit.

The score-free support audit imports only key selection and support functions.
All updates use observed labels whose assumed delivery time is strictly before
the prediction decision. The old train cohort is already summarized in P0.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable


VALIDATION_START_S = 1_510_790_400
LABEL_MATURITY_S = 7_500
ANCHOR_SPACING_S = 7_200
HALF_LIFE_S = 3 * 86_400
ROLLING_S = 7 * 86_400
P0 = (493 + 1) / (6_345 + 2)
FLEET_PSEUDOCOUNT = 40
LOCAL_PSEUDOCOUNT = 20
BASELINE_TIE_ORDER = (
    "frozen_train_beta", "expanding_fleet_40", "decayed_fleet_3d_40",
    "rolling_fleet_7d_40", "decayed_machine_3d_20",
    "expanding_hierarchical_40_20",
)


@dataclass(frozen=True)
class CandidateKey:
    machine_id: str
    decision_utc_s: int


@dataclass(frozen=True)
class AnchorOutcome:
    machine_id: str
    decision_utc_s: int
    rebound: int | None

    @property
    def available_utc_s(self) -> int:
        return self.decision_utc_s + LABEL_MATURITY_S


@dataclass(frozen=True)
class Support:
    ess: float
    days: int
    positive: int
    negative: int
    weighted_n: float
    weighted_positive: float

    @property
    def fleet_ok(self) -> bool:
        return self.ess >= 100 and self.days >= 5 and self.positive >= 10

    @property
    def local_ok(self) -> bool:
        return (self.ess >= 20 and self.days >= 3
                and self.positive >= 3 and self.negative >= 3)


def select_input_anchors(candidates: Iterable[CandidateKey]) -> list[CandidateKey]:
    """Use only input eligibility keys; no label or future-completeness field."""
    ordered = sorted(candidates, key=lambda row: (row.decision_utc_s, row.machine_id))
    chosen: list[CandidateKey] = []
    prior: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for row in ordered:
        if not row.machine_id or not isinstance(row.decision_utc_s, int):
            raise ValueError("Anchor needs a machine ID and integer decision time")
        key = (row.machine_id, row.decision_utc_s)
        if key in seen:
            raise ValueError("Duplicate machine/decision input candidate")
        seen.add(key)
        if row.decision_utc_s % 1_800:
            raise ValueError("Decision is outside the UTC 30-minute grid")
        previous = prior.get(row.machine_id)
        if previous is None or row.decision_utc_s - previous >= ANCHOR_SPACING_S:
            chosen.append(row)
            prior[row.machine_id] = row.decision_utc_s
    return chosen


def matured_outcomes(outcomes: Iterable[AnchorOutcome], decision_utc_s: int,
                     *, online_start_s: int = VALIDATION_START_S) -> list[AnchorOutcome]:
    """The train cohort is never re-added to the online state."""
    result = []
    for row in outcomes:
        if row.rebound not in (0, 1, None):
            raise ValueError("Rebound must be 0, 1 or unknown")
        if row.decision_utc_s < online_start_s:
            continue
        if row.rebound is not None and row.available_utc_s < decision_utc_s:
            result.append(row)
    return result


def _weighted_support(rows: Iterable[AnchorOutcome], decision_utc_s: int,
                      *, decayed: bool = True) -> Support:
    weighted_n = weighted_positive = weighted_sq = 0.0
    positive = negative = 0
    days: set[int] = set()
    for row in rows:
        if row.rebound not in (0, 1) or row.available_utc_s >= decision_utc_s:
            raise ValueError("Support may only contain known, previously matured labels")
        weight = 2 ** (-(decision_utc_s - row.available_utc_s) / HALF_LIFE_S) if decayed else 1.0
        weighted_n += weight
        weighted_sq += weight * weight
        weighted_positive += weight * row.rebound
        positive += row.rebound
        negative += 1 - row.rebound
        days.add(row.decision_utc_s // 86_400)
    ess = weighted_n * weighted_n / weighted_sq if weighted_sq else 0.0
    return Support(ess, len(days), positive, negative, weighted_n, weighted_positive)


def _checked_history(outcomes: Iterable[AnchorOutcome], target_machine: str,
                     decision_utc_s: int, fleet_source_machine_ids: frozenset[str],
                     online_start_s: int) -> list[AnchorOutcome]:
    if not target_machine or not fleet_source_machine_ids:
        raise ValueError("Target and explicit fleet-source machines are required")
    rows = tuple(outcomes)
    unexpected = {r.machine_id for r in rows
                  if r.machine_id not in fleet_source_machine_ids and r.machine_id != target_machine}
    if unexpected:
        raise ValueError("Outcome history contains another holdout machine")
    return matured_outcomes(rows, decision_utc_s, online_start_s=online_start_s)


def support_for_decision(outcomes: Iterable[AnchorOutcome], target_machine: str,
                         decision_utc_s: int, *, fleet_source_machine_ids: frozenset[str],
                         online_start_s: int = VALIDATION_START_S
                         ) -> tuple[Support, Support]:
    matured = _checked_history(outcomes, target_machine, decision_utc_s,
                               fleet_source_machine_ids, online_start_s)
    fleet = _weighted_support((r for r in matured if r.machine_id != target_machine),
                              decision_utc_s)
    local = _weighted_support((r for r in matured if r.machine_id == target_machine),
                              decision_utc_s)
    return fleet, local


def probabilities(outcomes: Iterable[AnchorOutcome], target_machine: str,
                  decision_utc_s: int, *, fleet_source_machine_ids: frozenset[str],
                  online_start_s: int = VALIDATION_START_S
                  ) -> dict[str, float]:
    """Synthetic-testable decision rule; score-free audit never calls this."""
    matured = _checked_history(outcomes, target_machine, decision_utc_s,
                               fleet_source_machine_ids, online_start_s)
    fleet_rows = [r for r in matured if r.machine_id != target_machine]
    local_rows = [r for r in matured if r.machine_id == target_machine]
    fleet = _weighted_support(fleet_rows, decision_utc_s)
    local = _weighted_support(local_rows, decision_utc_s)
    p_fleet = (FLEET_PSEUDOCOUNT * P0 + fleet.weighted_positive) / (
        FLEET_PSEUDOCOUNT + fleet.weighted_n)
    p_local = (LOCAL_PSEUDOCOUNT * p_fleet + local.weighted_positive) / (
        LOCAL_PSEUDOCOUNT + local.weighted_n)
    candidate = p_local if fleet.fleet_ok and local.local_ok else (
        p_fleet if fleet.fleet_ok else P0)
    expanding = _weighted_support(fleet_rows, decision_utc_s, decayed=False)
    recent = _weighted_support((r for r in fleet_rows
                                if decision_utc_s - ROLLING_S <= r.available_utc_s),
                               decision_utc_s, decayed=False)
    machine_only = (LOCAL_PSEUDOCOUNT * P0 + local.weighted_positive) / (
        LOCAL_PSEUDOCOUNT + local.weighted_n)
    p_expanding_fleet = (FLEET_PSEUDOCOUNT * P0 + expanding.weighted_positive) / (
        FLEET_PSEUDOCOUNT + expanding.weighted_n)
    expanding_local = _weighted_support(local_rows, decision_utc_s, decayed=False)
    p_expanding_local = (LOCAL_PSEUDOCOUNT * p_expanding_fleet
                         + expanding_local.weighted_positive) / (
                             LOCAL_PSEUDOCOUNT + expanding_local.weighted_n)
    expanding_hierarchical = p_expanding_local if fleet.fleet_ok and local.local_ok else (
        p_expanding_fleet if fleet.fleet_ok else P0)
    return {
        "candidate": candidate,
        "frozen_train_beta": P0,
        "expanding_fleet_40": p_expanding_fleet,
        "decayed_fleet_3d_40": p_fleet,
        "rolling_fleet_7d_40": (FLEET_PSEUDOCOUNT * P0 + recent.weighted_positive)
                               / (FLEET_PSEUDOCOUNT + recent.weighted_n),
        "decayed_machine_3d_20": machine_only,
        "expanding_hierarchical_40_20": expanding_hierarchical,
    }


def low_risk_tie_key(machine_id: str, decision_utc_s: int) -> str:
    raw = f"{machine_id}:{decision_utc_s}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def best_simple_baseline(brier: dict[str, float]) -> str:
    if set(brier) != set(BASELINE_TIE_ORDER):
        raise ValueError("Every frozen simple baseline is required")
    if any(not 0 <= value <= 1 for value in brier.values()):
        raise ValueError("Brier scores must be finite in [0,1]")
    best = min(brier.values())
    return next(name for name in BASELINE_TIE_ORDER if brier[name] <= best + 1e-12)
