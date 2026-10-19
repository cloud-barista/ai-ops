"""Platform-neutral fluid capacity replay; slots are NOT GPUs or VMs."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class Config:
    capacity_tokens_per_bin: float
    startup_bins: int = 1
    cooldown_bins: int = 2
    low_confirmations: int = 3
    min_slots: int = 1
    max_slots: int = 8
    scale_in_headroom: float = .8

    def __post_init__(self):
        if (not math.isfinite(self.capacity_tokens_per_bin)
                or self.capacity_tokens_per_bin <= 0):
            raise ValueError("capacity must be positive and finite")
        for name in ("startup_bins", "cooldown_bins", "low_confirmations",
                     "min_slots", "max_slots"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(name)
        if self.min_slots > self.max_slots or not 0 < self.scale_in_headroom < 1:
            raise ValueError("invalid bounds or headroom")


@dataclass
class State:
    active_slots: int
    pending_ready_bin: object = None
    last_action_bin: int = -10**9
    low_streak: int = 0
    backlog_tokens: float = 0.


def decide(state, forecast, bin_index, config):
    """Only past backlog and the supplied forecast are available here."""
    if not math.isfinite(forecast) or forecast < 0:
        state.low_streak = 0
        return "ABSTAIN", "forecast_unsupported"
    if state.pending_ready_bin is not None:
        state.low_streak = 0
        return "KEEP", "capacity_pending"
    required = forecast + state.backlog_tokens
    high = required > state.active_slots * config.capacity_tokens_per_bin
    low = (state.active_slots > config.min_slots and
           required <= config.scale_in_headroom * (state.active_slots - 1)
           * config.capacity_tokens_per_bin)
    state.low_streak = state.low_streak + 1 if low else 0
    if bin_index - state.last_action_bin < config.cooldown_bins:
        return "KEEP", "cooldown"
    if high and state.active_slots < config.max_slots:
        state.pending_ready_bin = bin_index + config.startup_bins
        state.last_action_bin = bin_index
        state.low_streak = 0
        return "SCALE_OUT", "predicted_token_capacity_shortfall"
    if low and state.low_streak >= config.low_confirmations:
        state.active_slots -= 1
        state.last_action_bin = bin_index
        state.low_streak = 0
        return "SCALE_IN", "confirmed_low_token_budget"
    return "KEEP", "bounded_capacity_hold"


def replay(observed, forecasts, config, initial_slots, fixed=False):
    y, p = np.asarray(observed, float), np.asarray(forecasts, float)
    if (len(y) != len(p) or not len(y) or not np.isfinite(y).all()
            or (y < 0).any() or not config.min_slots <= initial_slots <= config.max_slots):
        raise ValueError("invalid replay input")
    state = State(int(initial_slots))
    rows = []
    for t, incoming in enumerate(y):
        if state.pending_ready_bin is not None and state.pending_ready_bin <= t:
            state.active_slots += 1
            state.pending_ready_bin = None
        before = state.active_slots
        prior_backlog = state.backlog_tokens
        action, reason = ("KEEP", "fixed_training_capacity") if fixed else decide(state, p[t], t, config)
        capacity = state.active_slots * config.capacity_tokens_per_bin
        backlog = max(0., prior_backlog + incoming - capacity)
        rows.append({"bin": t, "forecast_tokens": float(p[t]) if np.isfinite(p[t]) else None,
                     "observed_tokens": float(incoming), "action": action, "reason": reason,
                     "slots_before": before, "active_slots": state.active_slots,
                     "pending_ready_bin": state.pending_ready_bin,
                     "requested_slots": state.active_slots + int(state.pending_ready_bin is not None),
                     "prior_backlog_tokens": prior_backlog, "backlog_tokens": backlog,
                     "instant_shortfall_tokens": max(0., incoming - capacity),
                     "idle_tokens": max(0., capacity - prior_backlog - incoming),
                     "capacity_tokens": capacity})
        state.backlog_tokens = backlog
    return rows


def feedback_correct(y, forecasts, scale, start, window=288, minimum=96):
    """Add past-only positive normalized q90 residual; no coverage guarantee."""
    y, p, m = (np.asarray(v, float) for v in (y, forecasts, scale))
    if len(y) != len(p) or len(p) != len(m) or not 0 <= start < len(y):
        raise ValueError("incompatible feedback input")
    result = p.copy()
    for t in range(start, len(y)):
        left = max(0, t - window)
        valid = (np.isfinite(y[left:t]) & np.isfinite(p[left:t])
                 & np.isfinite(m[left:t]) & (m[left:t] >= 1000))
        if valid.sum() >= minimum and np.isfinite(p[t]) and m[t] >= 1000:
            residual = (y[left:t][valid] - p[left:t][valid]) / m[left:t][valid]
            result[t] += m[t] * max(0., float(np.quantile(residual, .9)))
    return result


def summarize(rows):
    y = np.array([r["observed_tokens"] for r in rows])
    backlog = np.array([r["backlog_tokens"] for r in rows])
    actions = [r["action"] for r in rows if r["action"] in ("SCALE_OUT", "SCALE_IN")]
    total = float(y.sum())
    return {"bins": len(rows),
            "billed_slot_minutes": 5 * sum(r["requested_slots"] for r in rows),
            "active_slot_minutes": 5 * sum(r["active_slots"] for r in rows),
            "shortfall_fraction": sum(r["instant_shortfall_tokens"] for r in rows) / total if total else None,
            "backlog_bin_fraction": float(np.mean(backlog > 0)),
            "backlog_area_over_total_tokens": float(backlog.sum() / total) if total else None,
            "final_backlog_tokens": float(backlog[-1]),
            "scale_out_requests": actions.count("SCALE_OUT"),
            "scale_in_requests": actions.count("SCALE_IN"),
            "direction_reversals": sum(a != b for a, b in zip(actions, actions[1:])),
            "abstentions": sum(r["action"] == "ABSTAIN" for r in rows)}
