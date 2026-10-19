"""Paired offline metrics for the predeclared Philly GPU-risk comparison."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss


def _arrays(frame: pd.DataFrame, probability_column: str
            ) -> tuple[np.ndarray, np.ndarray]:
    required = {"y", probability_column, "machine_hash", "gpu_index",
                "decision_utc_s", "utc_day"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing score columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Cannot score an empty test stratum")
    y = frame["y"].to_numpy()
    p = frame[probability_column].to_numpy(dtype=float)
    if not np.isin(y, [0, 1]).all():
        raise ValueError("Only observed binary test labels may be scored")
    if not np.isfinite(p).all() or not ((0 <= p) & (p <= 1)).all():
        raise ValueError("Predicted probabilities must be finite and in [0, 1]")
    return y.astype(np.int8), p


def _tie_hash(machine_hash: str, gpu_index: int, decision_utc_s: int) -> str:
    value = f"{machine_hash}:{gpu_index}:{decision_utc_s}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def daily_top_five_percent(frame: pd.DataFrame, probability_column: str) -> dict:
    """Retrospective per-day ranking, with a fixed deterministic tie rule."""
    y, p = _arrays(frame, probability_column)
    work = frame[["utc_day", "machine_hash", "gpu_index", "decision_utc_s"]].copy()
    work["y"] = y
    work["p"] = p
    work["tie"] = [
        _tie_hash(str(m), int(g), int(d))
        for m, g, d in zip(work.machine_hash, work.gpu_index, work.decision_utc_s)
    ]
    daily = []
    for day, rows in work.groupby("utc_day", sort=True):
        ranked = rows.sort_values(["p", "tie"], ascending=[False, True],
                                  kind="mergesort")
        count = max(1, math.ceil(0.05 * len(ranked)))
        alerts = ranked.iloc[:count]
        positives = int(rows.y.sum())
        true_alerts = int(alerts.y.sum())
        daily.append({
            "utc_day": int(day), "rows": len(rows), "positives": positives,
            "alerts": count, "true_alerts": true_alerts,
            "precision": true_alerts / count,
            "recall": true_alerts / positives if positives else None,
        })
    total_alerts = sum(day["alerts"] for day in daily)
    total_true = sum(day["true_alerts"] for day in daily)
    total_positive = int(y.sum())
    return {
        "daily": daily, "alerts": total_alerts, "true_alerts": total_true,
        "precision": total_true / total_alerts,
        "recall": total_true / total_positive if total_positive else None,
    }


def binary_metrics(frame: pd.DataFrame, probability_column: str) -> dict:
    y, p = _arrays(frame, probability_column)
    return {
        "rows": len(frame), "positives": int(y.sum()),
        "prevalence": float(y.mean()), "mean_probability": float(p.mean()),
        "brier": float(brier_score_loss(y, p)),
        "average_precision": (float(average_precision_score(y, p))
                              if np.unique(y).size == 2 else None),
        "daily_top_5pct": daily_top_five_percent(frame, probability_column),
    }


def half_period_brier(frame: pd.DataFrame, first_probability: str,
                      second_probability: str) -> list[dict]:
    """Compare methods on the first and second half of sorted test UTC days."""
    y, a = _arrays(frame, first_probability)
    _, b = _arrays(frame, second_probability)
    days = sorted(int(day) for day in frame.utc_day.unique())
    midpoint = len(days) // 2
    result = []
    for index, dates in enumerate((days[:midpoint], days[midpoint:]), start=1):
        mask = frame.utc_day.isin(dates).to_numpy(bool)
        yy = y[mask]
        result.append({
            "half": index, "utc_days": dates, "rows": int(mask.sum()),
            "positives": int(yy.sum()), "negatives": int(len(yy) - yy.sum()),
            "first_brier": float(brier_score_loss(yy, a[mask])) if len(yy) else None,
            "second_brier": float(brier_score_loss(yy, b[mask])) if len(yy) else None,
        })
    return result


def paired_machine_bootstrap(frame: pd.DataFrame, first_probability: str,
                             second_probability: str, *, draws: int = 2000,
                             seed: int = 42) -> dict:
    """Cluster by machine; difference is first minus second Brier."""
    y, a = _arrays(frame, first_probability)
    _, b = _arrays(frame, second_probability)
    if draws <= 0:
        raise ValueError("draws must be positive")
    work = pd.DataFrame({
        "machine_hash": frame.machine_hash.to_numpy(),
        "delta": (a - y) ** 2 - (b - y) ** 2,
    })
    groups = work.groupby("machine_hash", sort=True).delta.agg(["sum", "count"])
    if len(groups) < 2:
        raise ValueError("At least two machines are needed for machine bootstrap")
    sums = groups["sum"].to_numpy(dtype=float)
    counts = groups["count"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    estimates = np.empty(draws, dtype=float)
    for draw in range(draws):
        ids = rng.integers(0, len(groups), size=len(groups))
        estimates[draw] = sums[ids].sum() / counts[ids].sum()
    return {
        "machines": len(groups), "draws": draws, "seed": seed,
        "point_delta": float(work.delta.mean()),
        "percentile_95": [float(x) for x in np.quantile(estimates, [0.025, 0.975])],
    }
