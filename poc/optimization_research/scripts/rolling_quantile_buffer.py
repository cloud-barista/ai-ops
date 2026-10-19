"""Exact fixed-size rolling quantile in completion/feedback order.

This keeps only the newest ``capacity`` values. Call ``add`` when a result
becomes available, not when its request is submitted. The quantile uses the
same linear interpolation as ``numpy.quantile``.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
import math


class RollingQuantileBuffer:
    def __init__(self, capacity: int, quantile: float = 0.9) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if not 0 <= quantile <= 1:
            raise ValueError("quantile must be in [0, 1]")
        self.capacity = capacity
        self.quantile = quantile
        self._arrival_order: deque[float] = deque()
        self._sorted: list[float] = []

    def __len__(self) -> int:
        return len(self._arrival_order)

    def add(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("rolling observations must be finite")
        insort(self._sorted, value)
        self._arrival_order.append(value)
        if len(self._arrival_order) > self.capacity:
            expired = self._arrival_order.popleft()
            index = bisect_left(self._sorted, expired)
            if index == len(self._sorted) or self._sorted[index] != expired:
                raise AssertionError("rolling quantile state is inconsistent")
            self._sorted.pop(index)

    def value(self) -> float | None:
        if not self._sorted:
            return None
        position = (len(self._sorted) - 1) * self.quantile
        lower = int(position)
        upper = min(lower + 1, len(self._sorted) - 1)
        weight = position - lower
        return float(self._sorted[lower] * (1 - weight) + self._sorted[upper] * weight)
