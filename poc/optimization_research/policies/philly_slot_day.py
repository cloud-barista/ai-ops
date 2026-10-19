"""Previous-day machine and GPU-slot risk calibration.

The input p0 is the frozen Philly pooled model's probability for a complete,
currently below-80% GPU-slot decision. An adapter must establish that source
condition and the truth/arrival time of each feedback record. This core only
uses feedback from the immediately preceding UTC day, available by day start.
It returns risk probabilities, never a deployment or scaling action.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit, logit


TARGET_FIELDS = frozenset({"machine_hash", "gpu_index", "decision_utc_s", "p0"})
FEEDBACK_FIELDS = TARGET_FIELDS | frozenset({"y", "label_available_at_utc_s"})
HORIZON_SECONDS = 1800
DAY_SECONDS = 86400
PENALTY = 20.0
MAX_SHIFT = 0.5


def _validate_frame(frame: pd.DataFrame, fields: frozenset[str], name: str) -> None:
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != fields:
        raise ValueError(f"{name}_fields_mismatch")
    if frame.empty:
        return
    if not frame.machine_hash.map(lambda x: isinstance(x, str) and bool(x)).all():
        raise ValueError(f"{name}_invalid_machine")
    for field in ("gpu_index", "decision_utc_s"):
        if not frame[field].map(lambda x: isinstance(x, (int, np.integer))
                                and not isinstance(x, (bool, np.bool_))).all():
            raise ValueError(f"{name}_invalid_{field}")
    if (frame.gpu_index.lt(0).any() or frame.decision_utc_s.le(0).any()
            or frame.decision_utc_s.mod(300).ne(0).any()):
        raise ValueError(f"{name}_invalid_time_or_slot")
    p = frame.p0.to_numpy(float)
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError(f"{name}_invalid_probability")
    if frame.duplicated(["machine_hash", "gpu_index", "decision_utc_s"]).any():
        raise ValueError(f"{name}_duplicate_decision")


def _exact_shift(y: np.ndarray, p: np.ndarray) -> float:
    """Same penalized bounded intercept as the frozen Philly comparator."""
    z = logit(np.clip(p, 1e-9, 1 - 1e-9))

    def derivative(delta: float) -> float:
        return float(np.sum(y - expit(z + delta)) - PENALTY * delta)

    if derivative(-MAX_SHIFT) <= 0:
        return -MAX_SHIFT
    if derivative(MAX_SHIFT) >= 0:
        return MAX_SHIFT
    return float(brentq(derivative, -MAX_SHIFT, MAX_SHIFT, xtol=1e-12))


def calibrate_day(targets: pd.DataFrame, previous_day_feedback: pd.DataFrame,
                  *, day_start_utc_s: int) -> pd.DataFrame:
    """Score one UTC day without accepting any target-day outcome column.

    `previous_day_feedback` is a caller-verified set of complete, below-80%
    decisions and their 30-minute outcomes. `label_available_at_utc_s` is an
    observed arrival time; for the archived Philly replay it is explicitly
    assumed to be `decision_utc_s + 1800` because no arrival log exists.
    First keep the earliest record per machine/slot/hour, then require its
    outcome to be complete and available by 00:00 UTC of the target day.
    Returned rows preserve target positional order.
    """
    if (not isinstance(day_start_utc_s, (int, np.integer))
            or isinstance(day_start_utc_s, (bool, np.bool_))
            or day_start_utc_s <= 0 or day_start_utc_s % DAY_SECONDS):
        raise ValueError("day_start_must_be_positive_utc_midnight")
    _validate_frame(targets, TARGET_FIELDS, "targets")
    _validate_frame(previous_day_feedback, FEEDBACK_FIELDS, "feedback")
    if (targets.decision_utc_s.lt(day_start_utc_s).any()
            or targets.decision_utc_s.ge(day_start_utc_s + DAY_SECONDS).any()):
        raise ValueError("targets_outside_selected_day")
    if (previous_day_feedback.decision_utc_s.lt(day_start_utc_s - DAY_SECONDS).any()
            or previous_day_feedback.decision_utc_s.ge(day_start_utc_s).any()):
        raise ValueError("feedback_outside_immediately_previous_day")
    if not previous_day_feedback.empty:
        y = previous_day_feedback.y.to_numpy()
        if not np.isin(y, [0, 1]).all():
            raise ValueError("feedback_invalid_outcome")
        arrivals = previous_day_feedback.label_available_at_utc_s
        if (not arrivals.map(lambda x: isinstance(x, (int, np.integer))
                             and not isinstance(x, (bool, np.bool_))).all()
                or arrivals.lt(previous_day_feedback.decision_utc_s + HORIZON_SECONDS).any()):
            raise ValueError("feedback_invalid_availability")

    out = targets.reset_index(drop=True).copy()
    out["machine_probability"] = out.p0.to_numpy(float)
    out["slot_probability"] = out.p0.to_numpy(float)
    out["machine_shift"] = 0.0
    out["slot_shift"] = 0.0
    out["previous_day_machine_rows"] = 0
    out["previous_day_slot_rows"] = 0
    if out.empty or previous_day_feedback.empty:
        return out

    hourly = previous_day_feedback.sort_values(
        ["decision_utc_s", "machine_hash", "gpu_index"], kind="mergesort").copy()
    hourly["hour"] = hourly.decision_utc_s // 3600
    hourly = hourly.drop_duplicates(["machine_hash", "gpu_index", "hour"], keep="first")
    available = hourly.loc[
        hourly.decision_utc_s.add(HORIZON_SECONDS).le(day_start_utc_s)
        & hourly.label_available_at_utc_s.le(day_start_utc_s)]
    if available.empty:
        return out

    machine_shift: dict[str, float] = {}
    slot_shift: dict[tuple[str, int], float] = {}
    machine_rows: dict[str, int] = {}
    slot_rows: dict[tuple[str, int], int] = {}
    for machine, one in available.groupby("machine_hash", sort=True):
        p = one.p0.to_numpy(float)
        y = one.y.to_numpy(float)
        delta = _exact_shift(y, p)
        machine_shift[machine] = delta
        machine_rows[machine] = len(one)
        machine_adjusted = expit(logit(np.clip(p, 1e-9, 1 - 1e-9)) + delta)
        for gpu, indices in one.groupby("gpu_index", sort=True).indices.items():
            chosen = np.asarray(indices, dtype=int)
            ps = machine_adjusted[chosen]
            ys = y[chosen]
            key = (machine, int(gpu))
            slot_shift[key] = float(np.clip(
                (ys - ps).sum() / ((ps * (1 - ps)).sum() + PENALTY),
                -MAX_SHIFT, MAX_SHIFT))
            slot_rows[key] = len(chosen)
    m = out.machine_hash.map(machine_shift).fillna(0).to_numpy(float)
    keys = pd.Series(list(zip(out.machine_hash, out.gpu_index.astype(int))),
                     index=out.index)
    g = keys.map(slot_shift).fillna(0).to_numpy(float)
    z = logit(np.clip(out.p0.to_numpy(float), 1e-9, 1 - 1e-9))
    out["machine_probability"] = expit(z + m)
    out["slot_probability"] = expit(z + m + g)
    out["machine_shift"] = m
    out["slot_shift"] = g
    out["previous_day_machine_rows"] = out.machine_hash.map(machine_rows).fillna(0).astype(int)
    out["previous_day_slot_rows"] = keys.map(slot_rows).fillna(0).astype(int)
    return out
