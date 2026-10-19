"""Audit the pinned BurstGPT v2.0 request trace before model evaluation."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from fetch_burstgpt_v2 import EXPECTED_SHA256, EXPECTED_SIZE, FILENAME, digest_file


EXPECTED_COLUMNS = [
    "Timestamp",
    "Session ID",
    "Elapsed time",
    "Model",
    "Request tokens",
    "Response tokens",
    "Total tokens",
    "Log Type",
]


def audit(path: Path) -> dict:
    size, sha256 = digest_file(path)
    if size != EXPECTED_SIZE or sha256 != EXPECTED_SHA256:
        raise ValueError("BurstGPT source differs from the pinned v2.0 release asset")

    counts: Counter[str] = Counter()
    by_model_log: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_day: defaultdict[int, Counter[str]] = defaultdict(Counter)
    previous = -math.inf
    first_time = math.inf
    last_time = -math.inf
    max_finish = -math.inf
    max_duration = -math.inf
    max_gap = 0.0
    with path.open("r", newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        columns = next(reader)
        if columns != EXPECTED_COLUMNS:
            raise ValueError(f"Unexpected BurstGPT schema: {columns}")
        for row in reader:
            counts["rows"] += 1
            if len(row) != len(columns):
                counts["invalid_column_count"] += 1
                continue
            try:
                timestamp = float(row[0])
                duration = float(row[2])
                request_tokens = int(row[4])
                response_tokens = int(row[5])
                total_tokens = int(row[6])
            except ValueError:
                counts["invalid_numeric"] += 1
                continue
            if not math.isfinite(timestamp) or not math.isfinite(duration):
                counts["nonfinite_numeric"] += 1
                continue

            model, log_type = row[3], row[7]
            group = by_model_log[(model, log_type)]
            day = by_day[int(timestamp // 86400)]
            for bucket in (counts, group, day):
                bucket["parsed"] += 1
                if duration == 0:
                    bucket["zero_elapsed"] += 1
                elif duration > 0:
                    bucket["positive_elapsed"] += 1
                else:
                    bucket["negative_elapsed"] += 1
                if response_tokens == 0:
                    bucket["zero_response_tokens"] += 1
                elif response_tokens > 0:
                    bucket["positive_response_tokens"] += 1
                else:
                    bucket["negative_response_tokens"] += 1
                if duration > 0 and response_tokens > 0:
                    bucket["positive_elapsed_and_response"] += 1
                if duration == 0 and response_tokens > 0:
                    bucket["zero_elapsed_positive_response"] += 1

            if not row[1]:
                counts["missing_session_id"] += 1
            if request_tokens < 0:
                counts["negative_request_tokens"] += 1
            if request_tokens == 0:
                counts["zero_request_tokens"] += 1
            if request_tokens + response_tokens != total_tokens:
                counts["token_total_mismatch"] += 1
            if timestamp < previous:
                counts["timestamp_order_violations"] += 1
            if timestamp == previous:
                counts["tied_timestamp"] += 1
            if duration % 1 != 0:
                counts["fractional_elapsed"] += 1
            if duration == 0 and response_tokens >= 64:
                counts["zero_elapsed_at_least_64_response_tokens"] += 1
            if duration == 0 and response_tokens >= 128:
                counts["zero_elapsed_at_least_128_response_tokens"] += 1
            if math.isfinite(previous):
                max_gap = max(max_gap, timestamp - previous)
            previous = timestamp
            first_time = min(first_time, timestamp)
            last_time = max(last_time, timestamp)
            max_finish = max(max_finish, timestamp + duration)
            max_duration = max(max_duration, duration)

    day_indices = sorted(by_day)
    missing_days = [day for day in range(day_indices[0], day_indices[-1] + 1) if day not in by_day]
    return {
        "source": {
            "file": FILENAME,
            "size_bytes": size,
            "sha256": sha256,
            "columns": EXPECTED_COLUMNS,
        },
        "counts": dict(sorted(counts.items())),
        "time": {
            "timestamp_min_seconds": first_time,
            "timestamp_max_seconds": last_time,
            "max_completion_seconds": max_finish,
            "elapsed_max_seconds": max_duration,
            "max_interarrival_gap_seconds": max_gap,
            "day_min": day_indices[0],
            "day_max": day_indices[-1],
            "days_present": len(day_indices),
            "missing_days": missing_days,
        },
        "by_model_log": {
            f"{model} | {log_type}": dict(sorted(bucket.items()))
            for (model, log_type), bucket in sorted(by_model_log.items())
        },
        "by_day": {str(day): dict(sorted(by_day[day].items())) for day in day_indices},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/burstgpt_v2" / FILENAME,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/burstgpt_v2/audit.json",
    )
    arguments = parser.parse_args()
    result = audit(arguments.input)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Audited {result['counts']['rows']:,} rows; wrote {arguments.output}")
