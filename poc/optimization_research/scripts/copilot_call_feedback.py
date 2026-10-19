"""Causal replay of a q90 residual correction for recorded call counts."""
import numpy as np


def correct_completed(target, prediction, scale, horizon, first_origin=864,
                      window=288, minimum=96, delivery_lag=0):
    """At t, use only i >= first_origin with i+h+1+lag <= t.

    Targets and forecasts are in calls per five-minute bin, scale in calls.
    Source feedback is assumed available on label completion plus the supplied
    simulated lag. This function issues no deployment or scaling action.
    """
    y, p, m = (np.asarray(v, dtype=float) for v in (target, prediction, scale))
    if any(v.ndim != 1 for v in (y, p, m)) or len(y) != len(p) or len(p) != len(m):
        raise ValueError('incompatible arrays')
    for name, v, low in [('horizon', horizon, 0), ('first_origin', first_origin, 0),
                         ('window', window, 1), ('minimum', minimum, 1),
                         ('delivery_lag', delivery_lag, 0)]:
        if isinstance(v, bool) or not isinstance(v, int) or v < low:
            raise ValueError('invalid ' + name)
    if not len(p) or horizon >= len(p) or first_origin >= len(p) or minimum > window:
        raise ValueError('invalid replay range')
    out = p.copy()
    n = np.zeros(len(p), dtype=np.int64)
    delta = np.zeros(len(p), dtype=float)
    last_available = np.full(len(p), -1, dtype=np.int64)
    for t in range(first_origin, len(p)):
        end = t - horizon - delivery_lag
        left = max(first_origin, end - window)
        if end <= left or not np.isfinite(p[t]) or p[t] < 0 or not np.isfinite(m[t]) or m[t] < 1:
            continue
        ids = np.arange(left, end)
        valid = (np.isfinite(y[ids]) & (y[ids] >= 0) & np.isfinite(p[ids])
                 & (p[ids] >= 0) & np.isfinite(m[ids]) & (m[ids] >= 1))
        ids = ids[valid]
        n[t] = len(ids)
        if len(ids):
            last_available[t] = int(ids[-1] + horizon + 1 + delivery_lag)
        if len(ids) >= minimum:
            residual = (y[ids] - p[ids]) / m[ids]
            delta[t] = max(0., float(np.quantile(residual, .9, method='linear')))
            out[t] += m[t] * delta[t]
    return out, n, delta, last_available
