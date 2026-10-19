"""Time-causal reuse labels and request-time features for Qwen bucket traces.

The caller must pass only one frozen UTC split at a time. In particular, no
test records should be passed to ``future_reuse_labels`` before the validation
gate permits test scoring.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from itertools import groupby
import math
from typing import Iterable


MINUTE_US = 60 * 1_000_000
HORIZON_US = 60 * MINUTE_US
DAY_US = 24 * HORIZON_US


@dataclass(frozen=True, slots=True)
class Request:
    created_utc_us: int
    bucket_ids: tuple[int, ...]
    token_count: int

    def __post_init__(self) -> None:
        if type(self.created_utc_us) is not int or type(self.token_count) is not int:
            raise TypeError("request_time_and_token_count_must_be_int")
        if not self.bucket_ids or type(self.bucket_ids) is not tuple:
            raise ValueError("complete_bucket_prefix_required")
        if any(type(value) is not int or value < 0 for value in self.bucket_ids):
            raise ValueError("bucket_ids_must_be_nonnegative_ints")
        if self.token_count // 16 != len(self.bucket_ids):
            raise ValueError("token_count_complete_block_length_mismatch")


def _ordered_groups(records: Iterable[Request], begin_us: int, end_us: int):
    previous_time = None
    for time_us, group in groupby(enumerate(records), key=lambda item: item[1].created_utc_us):
        if time_us < begin_us or time_us >= end_us:
            raise ValueError("request_outside_frozen_split")
        if previous_time is not None and time_us <= previous_time:
            raise ValueError("requests_not_strictly_group_time_ordered")
        previous_time = time_us
        yield time_us, list(group)


def future_reuse_labels(records: Iterable[Request], begin_us: int, end_us: int) -> list[int]:
    """Return 1/0 future full-prefix reuse, or -1 for warmup/censored rows.

    A later request matches a prior request when it begins with the prior
    request's entire *complete-block* sequence. Events at the same timestamp
    cannot match one another. All pending targets expire after 60 minutes.
    """
    if end_us - begin_us <= 2 * HORIZON_US:
        raise ValueError("split_too_short_for_warmup_and_complete_horizon")
    labels: list[int] = []
    pending: dict[tuple[int, ...], deque[int]] = {}
    expiry: deque[tuple[int, int, tuple[int, ...]]] = deque()
    for time_us, group in _ordered_groups(records, begin_us, end_us):
        while expiry and expiry[0][0] + HORIZON_US < time_us:
            _, old_index, old_prefix = expiry.popleft()
            if labels[old_index] == 0:
                queue = pending[old_prefix]
                if queue[0] != old_index:
                    raise AssertionError("pending_expiry_order_changed")
                queue.popleft()
                if not queue:
                    del pending[old_prefix]

        # The whole group reads prior state before any member is inserted.
        for index, request in group:
            prefix = request.bucket_ids
            for length in range(1, len(prefix) + 1):
                waiting = pending.pop(prefix[:length], None)
                if waiting is not None:
                    for old_index in waiting:
                        labels[old_index] = 1
            if index != len(labels):
                raise AssertionError("nonconsecutive_source_index")
            labels.append(-1)

        for index, request in group:
            if begin_us + HORIZON_US <= time_us and time_us + HORIZON_US < end_us:
                prefix = request.bucket_ids
                labels[index] = 0
                pending.setdefault(prefix, deque()).append(index)
                expiry.append((time_us, index, prefix))
    return labels


def historical_features(
    records: Iterable[Request], begin_us: int, end_us: int,
    selected_indices: set[int] | None = None,
) -> dict[int, dict[str, float]]:
    """Compute only strictly earlier 5/60-minute statistics for selected rows."""
    if end_us <= begin_us:
        raise ValueError("invalid_split")
    recent5: deque[tuple[int, tuple[int, ...]]] = deque()
    recent60: deque[tuple[int, tuple[int, ...], tuple[tuple[int, ...], ...]]] = deque()
    exact5: Counter[tuple[int, ...]] = Counter()
    exact60: Counter[tuple[int, ...]] = Counter()
    longer_prefix60: Counter[tuple[int, ...]] = Counter()
    last_exact: dict[tuple[int, ...], int] = {}
    output: dict[int, dict[str, float]] = {}
    for time_us, group in _ordered_groups(records, begin_us, end_us):
        while recent5 and recent5[0][0] < time_us - 5 * MINUTE_US:
            _, old_prefix = recent5.popleft()
            exact5[old_prefix] -= 1
            if exact5[old_prefix] == 0:
                del exact5[old_prefix]
        while recent60 and recent60[0][0] < time_us - HORIZON_US:
            _, old_prefix, old_short = recent60.popleft()
            exact60[old_prefix] -= 1
            if exact60[old_prefix] == 0:
                del exact60[old_prefix]
            for key in old_short:
                longer_prefix60[key] -= 1
                if longer_prefix60[key] == 0:
                    del longer_prefix60[key]

        for index, request in group:
            if selected_indices is not None and index not in selected_indices:
                continue
            prefix = request.bucket_ids
            previous = last_exact.get(prefix)
            day_number = time_us // DAY_US
            feature = {
                "complete_blocks": float(len(prefix)),
                "token_remainder": float(request.token_count % 16),
                "utc_hour": float((time_us // HORIZON_US) % 24),
                "utc_weekday": float((day_number + 3) % 7),
                "arrivals_5m": float(len(recent5)),
                "exact_5m": float(exact5.get(prefix, 0)),
                "exact_60m": float(exact60.get(prefix, 0)),
                "seconds_since_exact": math.nan if previous is None else (time_us - previous) / 1_000_000,
            }
            for depth in (1, 2, 4):
                feature[f"prefix_{depth}_60m"] = (
                    float(exact60.get(prefix[:depth], 0) + longer_prefix60.get(prefix[:depth], 0))
                    if len(prefix) >= depth else math.nan
                )
            output[index] = feature

        for _, request in group:
            prefix = request.bucket_ids
            short = tuple(prefix[:depth] for depth in (1, 2, 4) if len(prefix) >= depth and len(prefix) != depth)
            recent5.append((time_us, prefix))
            exact5[prefix] += 1
            recent60.append((time_us, prefix, short))
            exact60[prefix] += 1
            for key in short:
                longer_prefix60[key] += 1
            last_exact[prefix] = time_us
    return output
