"""Audit ETH's request trace and produce a small, time-sorted research table."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import orjson
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from fetch_swissai_trace import EXPECTED_SHA256, EXPECTED_SIZE, FILENAME, local_source_path, open_source


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SCHEMA = pa.schema(
    [
        pa.field("created_utc_us", pa.int64(), nullable=False),
        pa.field("finished_utc_us", pa.int64()),
        pa.field("elapsed_us", pa.int64()),
        pa.field("model", pa.string()),
        pa.field("status", pa.string()),
        pa.field("reported_input_tokens", pa.int64()),
        pa.field("created_naive", pa.bool_(), nullable=False),
        pa.field("finished_naive", pa.bool_(), nullable=False),
        pa.field("source_row", pa.int32(), nullable=False),
    ]
)


def parse_utc_microseconds(value: object) -> tuple[int | None, bool]:
    """Interpret explicit UTC or naive clock text as UTC; report the assumption."""
    if not isinstance(value, str) or value in ("", "\\N"):
        return None, False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    naive = timestamp.tzinfo is None
    if naive:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    delta = timestamp.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds, naive


def _empty_batch() -> dict[str, list]:
    return {field.name: [] for field in SCHEMA}


def _write_batch(writer: pq.ParquetWriter, batch: dict[str, list]) -> None:
    if not batch["source_row"]:
        return
    table = pa.Table.from_arrays(
        [pa.array(batch[field.name], type=field.type) for field in SCHEMA],
        schema=SCHEMA,
    )
    writer.write_table(table)


def audit(source: Path, output: Path) -> dict:
    if not source.name.endswith(".gz") and source.stat().st_size != EXPECTED_SIZE:
        raise ValueError("Input size differs from pinned ETH dataset")
    output.mkdir(parents=True, exist_ok=True)
    unsorted_path = output / ".requests_unsorted.parquet"
    sorted_temp = output / ".requests_sorted.parquet.tmp"
    sorted_path = output / "requests_sorted.parquet"
    if sorted_path.exists():
        raise ValueError(f"Refusing to replace existing {sorted_path}")

    source_digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    by_status: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_model: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_day: defaultdict[str, Counter[str]] = defaultdict(Counter)
    by_model_day: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_time_format: defaultdict[str, Counter[str]] = defaultdict(Counter)
    min_created = None
    max_created = None
    max_finished = None
    previous_created = None
    batch = _empty_batch()
    source_bytes = 0
    with pq.ParquetWriter(unsorted_path, SCHEMA, compression="zstd", use_dictionary=True) as writer:
        with open_source(source) as handle:
            for source_row, line in enumerate(handle):
                source_bytes += len(line)
                source_digest.update(line)
                counts["source_rows"] += 1
                try:
                    record = orjson.loads(line)
                except orjson.JSONDecodeError:
                    counts["invalid_json"] += 1
                    continue
                created, created_naive = parse_utc_microseconds(record.get("created_at"))
                finished, finished_naive = parse_utc_microseconds(record.get("finished_at"))
                if created is None:
                    counts["missing_or_invalid_created"] += 1
                    continue
                if created_naive:
                    counts["naive_created_assumed_utc"] += 1
                if finished is None:
                    counts["missing_or_invalid_finished"] += 1
                elif finished_naive:
                    counts["naive_finished_assumed_utc"] += 1
                else:
                    counts["explicit_finished_timezone"] += 1

                status = record.get("status")
                model = record.get("model")
                input_tokens = record.get("reported_token_input")
                output_tokens = record.get("reported_token_output")
                if not isinstance(status, str):
                    status = "<missing>"
                    counts["missing_status"] += 1
                if not isinstance(model, str):
                    model = "<missing>"
                    counts["missing_model"] += 1
                if not isinstance(input_tokens, int):
                    input_tokens = None
                if input_tokens is None or input_tokens < 0:
                    counts["input_tokens_unavailable"] += 1
                if not isinstance(output_tokens, int) or output_tokens < 0:
                    counts["output_tokens_unavailable"] += 1
                if previous_created is not None and created < previous_created:
                    counts["source_order_violations"] += 1
                previous_created = created
                min_created = created if min_created is None else min(created, min_created)
                max_created = created if max_created is None else max(created, max_created)
                if finished is not None:
                    max_finished = finished if max_finished is None else max(finished, max_finished)
                duration = None if finished is None else finished - created
                if duration is None:
                    counts["unlabelled"] += 1
                elif duration > 0:
                    counts["positive_duration"] += 1
                elif duration == 0:
                    counts["zero_duration"] += 1
                else:
                    counts["negative_duration"] += 1

                day = datetime.fromtimestamp(created / 1_000_000, timezone.utc).date().isoformat()
                created_format = "naive" if created_naive else "explicit"
                finished_format = (
                    "missing" if finished is None else "naive" if finished_naive else "explicit"
                )
                format_bucket = by_time_format[f"{created_format} | {finished_format}"]
                for bucket in (
                    by_status[status],
                    by_model[model],
                    by_day[day],
                    by_model_day[(model, day)],
                    format_bucket,
                ):
                    bucket["rows"] += 1
                    if duration is not None and duration > 0:
                        bucket["positive_duration"] += 1
                    if finished is None:
                        bucket["missing_finished"] += 1
                    if duration is not None and duration <= 0:
                        bucket["nonpositive_duration"] += 1
                if status == "DEFAULT" and finished is None:
                    counts["default_status_missing_finished"] += 1

                batch["created_utc_us"].append(created)
                batch["finished_utc_us"].append(finished)
                batch["elapsed_us"].append(duration)
                batch["model"].append(model)
                batch["status"].append(status)
                batch["reported_input_tokens"].append(input_tokens)
                batch["created_naive"].append(created_naive)
                batch["finished_naive"].append(finished_naive)
                batch["source_row"].append(source_row)
                counts["compact_rows"] += 1
                if len(batch["source_row"]) == 100_000:
                    _write_batch(writer, batch)
                    batch = _empty_batch()
        _write_batch(writer, batch)

    observed_sha = source_digest.hexdigest()
    if source_bytes != EXPECTED_SIZE:
        raise ValueError(f"ETH original size mismatch: {source_bytes}")
    if observed_sha != EXPECTED_SHA256:
        raise ValueError(f"ETH original SHA256 mismatch: {observed_sha}")

    # The source is grouped by model and is not in arrival order. Sorting this
    # compact table prevents an accidental source-row chronological split.
    frame = pl.read_parquet(unsorted_path)
    if frame.height != counts["compact_rows"]:
        raise ValueError("Compact row count changed before sorting")
    frame = frame.sort(["created_utc_us", "source_row"])
    if not frame["created_utc_us"].is_sorted():
        raise ValueError("Compact request table is not chronologically sorted")
    frame.write_parquet(sorted_temp, compression="zstd", row_group_size=200_000)
    del frame
    sorted_count = pq.ParquetFile(sorted_temp).metadata.num_rows
    if sorted_count != counts["compact_rows"]:
        raise ValueError("Sorted row count changed")
    os.replace(sorted_temp, sorted_path)
    unsorted_path.unlink()

    result = {
        "source": {
            "revision": "c3dd66602b564ce189f7db81115620413e646cd5",
            "file": FILENAME,
            "bytes": EXPECTED_SIZE,
            "sha256": observed_sha,
        },
        "interpretation": {
            "naive_finished_at_assumed_timezone": "UTC",
            "missing_finished_marker": "\\N",
            "reported_input_tokens_used_as_predictor": False,
            "sorted_by": ["created_utc_us", "source_row"],
            "raw_id_and_output_tokens_in_compact_table": False,
        },
        "counts": dict(sorted(counts.items())),
        "by_status": {key: dict(sorted(value.items())) for key, value in sorted(by_status.items())},
        "by_model": {key: dict(sorted(value.items())) for key, value in sorted(by_model.items())},
        "by_day": {key: dict(sorted(value.items())) for key, value in sorted(by_day.items())},
        "by_model_day": {
            f"{day} | {model}": dict(sorted(value.items()))
            for (model, day), value in sorted(by_model_day.items(), key=lambda item: (item[0][1], item[0][0]))
        },
        "by_time_format": {
            key: dict(sorted(value.items())) for key, value in sorted(by_time_format.items())
        },
        "time": {
            "min_created_utc_us": min_created,
            "max_created_utc_us": max_created,
            "max_finished_utc_us": max_finished,
        },
        "compact": {
            "path": str(sorted_path),
            "rows": sorted_count,
            "bytes": sorted_path.stat().st_size,
        },
    }
    (output / "audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=local_source_path(Path(__file__).resolve().parents[1] / "data/external/swissai_serving_trace"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/swissai_serving_trace",
    )
    args = parser.parse_args()
    result = audit(args.input, args.output)
    print(f"Audited and sorted {result['compact']['rows']:,} rows", flush=True)
