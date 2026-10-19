"""Posthoc validation-only support audit for fixed-depth Qwen bucket prefixes.

This script does not train a model or open the locked-test split. Its target is
distinct from the frozen full-prefix Y60 target and must not be treated as a
pre-registered validation of that target.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import groupby
import json
import os
from pathlib import Path

from evaluate_swissai_qwen_prefix import (
    ROOT, bounds, sha256, stream_rows, verify_source,
)
from swissai_qwen_reuse_core import DAY_US, HORIZON_US


DEPTHS = (1, 2, 4, 8, 16, 32)
DEFAULT_OUTPUT = ROOT / "artifacts/swissai_qwen_prefix_depth_support_v1/support.json"


def _day_label(time_us: int) -> str:
    return datetime.fromtimestamp(time_us / 1_000_000, tz=timezone.utc).date().isoformat()


def audit_validation_rows(
    rows: list[tuple[int, bytes, int, tuple[int, ...]]],
    begin_us: int,
    end_us: int,
    depths: tuple[int, ...] = DEPTHS,
) -> dict:
    """Count later reuse of the first d complete blocks within 60 minutes.

    Later requests must contain at least d blocks. A same-timestamp group is
    queried against future state before any member updates that state.
    """
    if end_us - begin_us <= 2 * HORIZON_US:
        raise ValueError("split_too_short")
    if not depths or any(type(d) is not int or d < 1 for d in depths) or len(set(depths)) != len(depths):
        raise ValueError("invalid_depths")
    previous_time = None
    for created, _, token_count, prefix in rows:
        if not begin_us <= created < end_us:
            raise ValueError("row_outside_validation")
        if previous_time is not None and created < previous_time:
            raise ValueError("rows_not_time_sorted")
        if len(prefix) < 1 or len(prefix) != token_count // 16:
            raise ValueError("invalid_complete_block_prefix")
        previous_time = created

    next_seen: dict[int, dict[tuple[int, ...], int]] = {d: {} for d in depths}
    totals: dict[int, Counter[str]] = {d: Counter() for d in depths}
    by_day: dict[int, dict[str, Counter[str]]] = {
        d: defaultdict(Counter) for d in depths
    }
    time_counts: Counter[str] = Counter()
    for created, group_iter in groupby(reversed(rows), key=lambda row: row[0]):
        group = list(group_iter)
        if created < begin_us + HORIZON_US:
            time_counts["warmup_rows"] += len(group)
        elif created + HORIZON_US >= end_us:
            time_counts["future_censored_rows"] += len(group)
        else:
            time_counts["complete_window_rows"] += len(group)
            day = _day_label(created)
            for _, _, _, prefix in group:
                for depth in depths:
                    if len(prefix) < depth:
                        totals[depth]["too_short_rows"] += 1
                        by_day[depth][day]["too_short_rows"] += 1
                        continue
                    next_time = next_seen[depth].get(prefix[:depth])
                    if next_time is not None and next_time <= created:
                        raise AssertionError("same_time_future_state")
                    key = "positive" if next_time is not None and next_time - created <= HORIZON_US else "negative"
                    totals[depth][key] += 1
                    by_day[depth][day][key] += 1
        # Include all later requests, even ones whose own future is censored:
        # they may provide an observed outcome for an earlier candidate.
        for _, _, _, prefix in group:
            for depth in depths:
                if len(prefix) >= depth:
                    next_seen[depth][prefix[:depth]] = created

    n_days = (end_us - begin_us + DAY_US - 1) // DAY_US
    result_depths = {}
    for depth in depths:
        counts = totals[depth]
        eligible = counts["positive"] + counts["negative"]
        if eligible + counts["too_short_rows"] != time_counts["complete_window_rows"]:
            raise AssertionError("depth_rows_do_not_reconcile")
        days = {}
        supported_days = 0
        for index in range(n_days):
            label = _day_label(begin_us + index * DAY_US)
            day = by_day[depth][label]
            if day["positive"] >= 100 and day["negative"] >= 100:
                supported_days += 1
            days[label] = {
                "positive": day["positive"],
                "negative": day["negative"],
                "too_short_rows": day["too_short_rows"],
            }
        result_depths[str(depth)] = {
            "eligible_rows": eligible,
            "positive": counts["positive"],
            "negative": counts["negative"],
            "too_short_rows": counts["too_short_rows"],
            "days_with_positive_and_negative_at_least_100": supported_days,
            "days": days,
        }
    if sum(time_counts.values()) != len(rows):
        raise AssertionError("time_rows_do_not_reconcile")
    return {
        "split": "validation",
        "begin_utc": datetime.fromtimestamp(begin_us / 1_000_000, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end_us / 1_000_000, timezone.utc).isoformat(),
        "horizon_seconds": HORIZON_US // 1_000_000,
        "source_rows": len(rows),
        "time_support": {
            "warmup_rows": time_counts["warmup_rows"],
            "future_censored_rows": time_counts["future_censored_rows"],
            "complete_window_rows": time_counts["complete_window_rows"],
        },
        "depths": result_depths,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing_to_overwrite:{args.output}")
    source = verify_source()
    begin_us, end_us = bounds("validation")
    result = audit_validation_rows(list(stream_rows("validation")), begin_us, end_us)
    result["interpretation"] = {
        "status": "posthoc_exploratory_support_only",
        "model_trained": False,
        "locked_test_labels_or_scores_computed": False,
        "same_timestamp_requests_can_match": False,
        "source_split_opened": "validation_only",
    }
    result["provenance"] = {
        "compact_sha256": source["compact_sha256"],
        "quality_sha256": source["quality_sha256"],
        "original_protocol_sha256": source["code"]["protocol_sha256"],
        "audit_script_sha256": sha256(Path(__file__)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with args.output.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
