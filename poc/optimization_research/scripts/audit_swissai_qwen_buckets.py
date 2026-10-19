"""Verify the pinned SwissAI Qwen bucket trace without opening future-reuse labels.

The input is a gzip copy of the exact raw JSONL byte stream. This audit reads
the decompressed bytes once, checks their pinned SHA-256/length/row count, and
publishes only structurally eligible rows in UTC order. No output, finish time,
future request, cache hit, or Y60 label is read into the compact table.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import orjson
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from audit_swissai_trace import parse_utc_microseconds
from fetch_swissai_trace import EXPECTED_SHA256 as EXPECTED_ORIGINAL_SHA256
from fetch_swissai_trace import EXPECTED_SIZE as EXPECTED_ORIGINAL_BYTES


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/swissai_serving_trace"
EXPECTED_RAW_SHA256 = "58b2265b98a10ec3bb2a0b455c520731c1296967906c7adc7b6d3710a013e402"
EXPECTED_RAW_BYTES = 4_617_817_342
EXPECTED_ROWS = 3_994_435
EXPECTED_MODEL = "Qwen/Qwen3-32B"
REVISION = "c3dd66602b564ce189f7db81115620413e646cd5"
SPLIT_BOUNDARIES = (
    ("train", "2025-05-14T00:00:00+00:00", "2025-08-01T00:00:00+00:00"),
    ("validation", "2025-08-01T00:00:00+00:00", "2025-08-08T00:00:00+00:00"),
    ("locked_test", "2025-08-08T00:00:00+00:00", "2025-08-23T00:00:00+00:00"),
    ("posthoc", "2025-08-23T00:00:00+00:00", "2025-09-07T00:00:00+00:00"),
)
SCHEMA = pa.schema([
    pa.field("created_utc_us", pa.int64(), nullable=False),
    pa.field("id_sha256", pa.large_binary(), nullable=False),
    pa.field("source_row", pa.int32(), nullable=False),
    pa.field("created_naive", pa.bool_(), nullable=False),
    pa.field("token_count", pa.int64(), nullable=False),
    pa.field("complete_blocks", pa.int32(), nullable=False),
    pa.field("bucket_ids", pa.large_list(pa.uint64()), nullable=False),
])


def _utc_us(value: str) -> int:
    dt = datetime.fromisoformat(value)
    delta = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


SPLITS = tuple((name, _utc_us(start), _utc_us(end)) for name, start, end in SPLIT_BOUNDARIES)


def split_for_timestamp(created_us: int) -> str:
    if created_us < SPLITS[0][1]:
        return "outside_before"
    for name, start, end in SPLITS:
        if start <= created_us < end:
            return name
    return "outside_after"


def parse_structure(record: object) -> tuple[dict | None, list[str]]:
    """Return permitted request-time fields and every structural exclusion reason."""
    if not isinstance(record, dict):
        return None, ["nonobject_json"]
    reasons: list[str] = []
    raw_id = record.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        reasons.append("invalid_id")
    created, naive = parse_utc_microseconds(record.get("created_at"))
    if created is None:
        reasons.append("invalid_created_at")
    token_count = record.get("token_count")
    if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0 or token_count > 2**63 - 1:
        reasons.append("invalid_token_count")
    bucket_ids = record.get("bucket_ids")
    if not isinstance(bucket_ids, list):
        reasons.append("invalid_bucket_list")
    elif any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**64 - 1
        for value in bucket_ids
    ):
        reasons.append("invalid_bucket_id_value")
    if "invalid_token_count" not in reasons and isinstance(bucket_ids, list):
        if len(bucket_ids) != (token_count + 15) // 16:
            reasons.append("bucket_length_mismatch")
        if token_count // 16 == 0:
            reasons.append("zero_complete_blocks")
        if token_count // 16 > 2**31 - 1:
            reasons.append("too_many_complete_blocks")
    if record.get("model") != EXPECTED_MODEL:
        reasons.append("unexpected_model")
    if reasons:
        return None, reasons
    assert isinstance(raw_id, str) and isinstance(created, int)
    assert isinstance(token_count, int) and isinstance(bucket_ids, list)
    n = token_count // 16
    return {
        "created_utc_us": created,
        "id_sha256": hashlib.sha256(raw_id.encode("utf-8")).digest(),
        "created_naive": naive,
        "token_count": token_count,
        "complete_blocks": n,
        "bucket_ids": bucket_ids[:n],
    }, []


def _write_batch(writer: pq.ParquetWriter, batch: dict[str, list]) -> None:
    if not batch["source_row"]:
        return
    writer.write_table(pa.Table.from_arrays(
        [pa.array(batch[field.name], type=field.type) for field in SCHEMA], schema=SCHEMA,
    ))


def _bucket_bin(n: int) -> str:
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    for end in (4, 8, 16, 32, 64):
        if n <= end:
            return f"{end // 2 + 1}-{end}"
    return "65+"


def _sorted_support(sorted_path: Path) -> tuple[dict, int]:
    """Count eligible support and prefix diversity; never inspect future outcomes."""
    support: dict[str, dict] = {}
    seen_prefixes: set[bytes] = set()
    active_split: str | None = None
    last_time: int | None = None
    group_size = 0
    previous_sort_key: tuple[int, bytes] | None = None
    rows = 0

    def finish_time_group() -> None:
        if active_split is not None and group_size > 1:
            support[active_split]["simultaneous_timestamp_groups"] += 1
            support[active_split]["rows_in_simultaneous_groups"] += group_size

    def finish_split() -> None:
        if active_split is not None:
            finish_time_group()
            support[active_split]["unique_full_prefixes"] = len(seen_prefixes)

    columns = list(SCHEMA.names)
    for batch in pq.ParquetFile(sorted_path).iter_batches(batch_size=50_000, columns=columns):
        table = pa.Table.from_batches([batch])
        for row in table.to_pylist():
            created = row["created_utc_us"]
            row_hash = row["id_sha256"]
            if len(row_hash) != 32:
                raise ValueError("Compact id_sha256 length changed")
            key = (created, row_hash)
            if previous_sort_key is not None and key <= previous_sort_key:
                raise ValueError("Compact rows are not strictly sorted by UTC time and SHA256(id)")
            previous_sort_key = key
            name = split_for_timestamp(created)
            if name != active_split:
                finish_split()
                seen_prefixes.clear()
                active_split = name
                last_time = None
                group_size = 0
                support.setdefault(name, {
                    "eligible_rows": 0,
                    "eligible_after_first_60m": 0,
                    "eligible_with_full_60m_horizon": 0,
                    "eligible_scoring_window": 0,
                    "unique_full_prefixes": 0,
                    "simultaneous_timestamp_groups": 0,
                    "rows_in_simultaneous_groups": 0,
                    "complete_blocks_histogram": {},
                })
            if last_time is None:
                group_size = 1
                last_time = created
            elif last_time != created:
                finish_time_group()
                group_size = 1
                last_time = created
            else:
                group_size += 1
            stats = support[name]
            stats["eligible_rows"] += 1
            n = row["complete_blocks"]
            hist = stats["complete_blocks_histogram"]
            bin_name = _bucket_bin(n)
            hist[bin_name] = hist.get(bin_name, 0) + 1
            digest = hashlib.sha256(orjson.dumps(row["bucket_ids"])).digest()
            seen_prefixes.add(digest)
            for split_name, start, end in SPLITS:
                if name == split_name:
                    if created >= start + 60 * 60 * 1_000_000:
                        stats["eligible_after_first_60m"] += 1
                    if created < end - 60 * 60 * 1_000_000:
                        stats["eligible_with_full_60m_horizon"] += 1
                    if start + 60 * 60 * 1_000_000 <= created < end - 60 * 60 * 1_000_000:
                        stats["eligible_scoring_window"] += 1
                    break
            rows += 1
    finish_split()
    return support, rows


def compare_original_ids(
    original_trace: Path,
    bucket_ids: set[bytes],
    *,
    expected_bytes: int = EXPECTED_ORIGINAL_BYTES,
    expected_sha256: str = EXPECTED_ORIGINAL_SHA256,
) -> dict:
    """Reconcile Qwen IDs with the pinned base trace, without output/finish data."""
    digest = hashlib.sha256()
    original_default_ids: set[bytes] = set()
    original_other_status_ids: set[bytes] = set()
    counts: Counter[str] = Counter()
    with Path(original_trace).open("rb") as source:
        for line in source:
            digest.update(line)
            counts["raw_rows"] += 1
            counts["raw_bytes"] += len(line)
            try:
                record = orjson.loads(line)
            except orjson.JSONDecodeError as exc:
                raise ValueError("Pinned original trace has invalid JSON") from exc
            if not isinstance(record, dict) or record.get("model") != EXPECTED_MODEL:
                continue
            counts["qwen_rows"] += 1
            is_default = record.get("status") == "DEFAULT"
            counts["qwen_default_rows" if is_default else "qwen_nondefault_rows"] += 1
            raw_id = record.get("id")
            if not isinstance(raw_id, str) or not raw_id:
                counts["qwen_invalid_id"] += 1
                continue
            id_hash = hashlib.sha256(raw_id.encode("utf-8")).digest()
            if is_default:
                if id_hash in original_default_ids:
                    counts["qwen_default_duplicate_id_extra_occurrences"] += 1
                original_default_ids.add(id_hash)
            else:
                original_other_status_ids.add(id_hash)
    observed_sha = digest.hexdigest()
    if counts["raw_bytes"] != expected_bytes or observed_sha != expected_sha256:
        raise ValueError("Pinned original trace differs from expected length/SHA-256")
    default_missing_in_bucket = original_default_ids - bucket_ids
    bucket_absent_from_default = bucket_ids - original_default_ids
    return {
        "original_trace_sha256": observed_sha,
        "counts": dict(sorted(counts.items())),
        "qwen_default_unique_ids": len(original_default_ids),
        "bucket_unique_nonempty_raw_ids": len(bucket_ids),
        "matched_qwen_default_unique_ids": len(original_default_ids & bucket_ids),
        "qwen_default_ids_missing_from_bucket": len(default_missing_in_bucket),
        "bucket_ids_absent_from_qwen_default": len(bucket_absent_from_default),
        "bucket_ids_found_only_in_qwen_nondefault": len(
            bucket_absent_from_default & original_other_status_ids
        ),
        "bucket_ids_absent_from_all_original_qwen": len(
            bucket_absent_from_default - original_other_status_ids
        ),
        "raw_ids_published": False,
        "missing_reason_proven": False,
    }


def audit(
    source_gz: Path,
    output_dir: Path,
    *,
    expected_raw_bytes: int = EXPECTED_RAW_BYTES,
    expected_raw_sha256: str = EXPECTED_RAW_SHA256,
    expected_rows: int = EXPECTED_ROWS,
    original_trace: Path | None = None,
    expected_original_bytes: int = EXPECTED_ORIGINAL_BYTES,
    expected_original_sha256: str = EXPECTED_ORIGINAL_SHA256,
) -> dict:
    """Audit pinned gzip source and atomically publish compact/support artifacts."""
    source_gz, output_dir = Path(source_gz), Path(output_dir)
    if source_gz.suffix != ".gz":
        raise ValueError("Source must be a gzip copy of the raw JSONL stream")
    compact_path = output_dir / "qwen_buckets_sorted.parquet"
    quality_path = output_dir / "qwen_buckets_quality.json"
    if compact_path.exists() or quality_path.exists():
        raise FileExistsError("Refusing to replace an existing Qwen audit artifact")
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, unsorted_name = tempfile.mkstemp(prefix=".qwen_unsorted_", suffix=".parquet", dir=output_dir)
    os.close(fd)
    fd, sorted_name = tempfile.mkstemp(prefix=".qwen_sorted_", suffix=".parquet", dir=output_dir)
    os.close(fd)
    unsorted_path = Path(unsorted_name)
    sorted_temp = Path(sorted_name)
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    seen_ids: set[bytes] = set()
    duplicate_ids: set[bytes] = set()
    previous_created: int | None = None
    first_created: int | None = None
    last_created: int | None = None
    batch = {field.name: [] for field in SCHEMA}
    try:
        with pq.ParquetWriter(unsorted_path, SCHEMA, compression="zstd") as writer:
            with gzip.open(source_gz, "rb") as source:
                for source_row, line in enumerate(source):
                    counts["raw_rows"] += 1
                    counts["raw_bytes"] += len(line)
                    digest.update(line)
                    try:
                        record = orjson.loads(line)
                    except orjson.JSONDecodeError:
                        counts["invalid_json"] += 1
                        continue
                    if isinstance(record, dict):
                        model_counts[str(record.get("model", "<missing>"))] += 1
                        status_counts[str(record.get("status", "<missing>"))] += 1
                        raw_id = record.get("id")
                        if isinstance(raw_id, str) and raw_id:
                            id_hash = hashlib.sha256(raw_id.encode("utf-8")).digest()
                            if id_hash in seen_ids:
                                duplicate_ids.add(id_hash)
                                counts["duplicate_id_extra_occurrences"] += 1
                            else:
                                seen_ids.add(id_hash)
                        created, naive = parse_utc_microseconds(record.get("created_at"))
                        if created is not None:
                            counts["parseable_created_at"] += 1
                            if naive:
                                counts["naive_created_at_assumed_utc"] += 1
                            else:
                                counts["timezone_aware_created_at"] += 1
                            if previous_created is not None and created < previous_created:
                                counts["source_time_order_inversions"] += 1
                            previous_created = created
                            first_created = created if first_created is None else min(first_created, created)
                            last_created = created if last_created is None else max(last_created, created)
                    parsed, reasons = parse_structure(record)
                    for reason in reasons:
                        counts[reason] += 1
                    if parsed is None:
                        counts["excluded_structural_rows"] += 1
                        continue
                    parsed["source_row"] = source_row
                    for name in batch:
                        batch[name].append(parsed[name])
                    counts["structurally_eligible_rows"] += 1
                    if len(batch["source_row"]) == 50_000:
                        _write_batch(writer, batch)
                        batch = {field.name: [] for field in SCHEMA}
            _write_batch(writer, batch)
        raw_sha256 = digest.hexdigest()
        if (counts["raw_rows"] != expected_rows or counts["raw_bytes"] != expected_raw_bytes
                or raw_sha256 != expected_raw_sha256):
            raise ValueError(
                "Decompressed source differs from pinned raw SHA-256/length/row count: "
                f"rows={counts['raw_rows']} bytes={counts['raw_bytes']} sha256={raw_sha256}"
            )
        # The raw file is grouped by request ID, not in arrival order. Hash-ID
        # ties are deterministic; every duplicate ID is excluded, including its
        # first copy, before the chronology and support audit.
        frame = pl.read_parquet(unsorted_path)
        if len(frame) != counts["structurally_eligible_rows"]:
            raise ValueError("Unsorted compact row count changed")
        if duplicate_ids:
            frame = frame.filter(~pl.col("id_sha256").is_in(list(duplicate_ids)))
        frame = frame.sort(["created_utc_us", "id_sha256"])
        frame.write_parquet(sorted_temp, compression="zstd", row_group_size=100_000)
        del frame
        support, compact_rows = _sorted_support(sorted_temp)
        if compact_rows != pq.ParquetFile(sorted_temp).metadata.num_rows:
            raise ValueError("Sorted compact row count changed")
        counts["unique_nonempty_raw_ids"] = len(seen_ids)
        counts["duplicate_id_values"] = len(duplicate_ids)
        counts["duplicate_id_rows"] = len(duplicate_ids) + counts["duplicate_id_extra_occurrences"]
        counts["compact_eligible_rows"] = compact_rows
        original_comparison = None
        if original_trace is not None:
            original_comparison = compare_original_ids(
                original_trace, seen_ids,
                expected_bytes=expected_original_bytes,
                expected_sha256=expected_original_sha256,
            )
        compact_digest = hashlib.sha256()
        with sorted_temp.open("rb") as compact_handle:
            for chunk in iter(lambda: compact_handle.read(8 * 1024 * 1024), b""):
                compact_digest.update(chunk)
        compact_sha256 = compact_digest.hexdigest()
        result = {
            "kind": "swissai_qwen_bucket_structure_audit_v1",
            "source": {
                "official_revision": REVISION,
                "file": "qwen3-32b-buckets.jsonl",
                "gzip_path": str(source_gz),
                "gzip_bytes": source_gz.stat().st_size,
                "decompressed_bytes": counts["raw_bytes"],
                "decompressed_sha256": raw_sha256,
                "raw_rows": counts["raw_rows"],
                "pinned_source_verified": True,
            },
            "interpretation": {
                "naive_created_at_assumed_timezone": "UTC",
                "eligible_full_blocks": "floor(token_count / 16); right-padded partial block excluded",
                "order": ["created_utc_us", "SHA256(id) bytes"],
                "raw_ids_or_output_fields_in_compact": False,
                "future_reuse_labels_or_cache_outcomes_computed": False,
                "duplicate_id_policy": "all copies excluded from compact",
                "full_prefix_uniqueness": "SHA256 of canonical JSON array of complete bucket IDs",
                "original_id_reconciliation_population": "all bucket-source rows with nonempty string IDs, including structurally excluded rows",
            },
            "counts": dict(sorted(counts.items())),
            "by_model": dict(sorted(model_counts.items())),
            "by_status": dict(sorted(status_counts.items())),
            "time": {"min_created_utc_us": first_created, "max_created_utc_us": last_created},
            "support_by_fixed_split": support,
            "original_trace_id_reconciliation": original_comparison,
            "compact": {
                "path": str(compact_path),
                "rows": compact_rows,
                "bytes": sorted_temp.stat().st_size,
                "sha256": compact_sha256,
                "columns": SCHEMA.names,
            },
        }
        fd, quality_name = tempfile.mkstemp(
            prefix=".qwen_quality_", suffix=".json", dir=output_dir
        )
        quality_temp = Path(quality_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            # Neither artifact is replaced. Clean up the first link if the
            # second fails, so a rerun never sees a half-published pair.
            os.link(sorted_temp, compact_path)
            try:
                os.link(quality_temp, quality_path)
            except Exception:
                compact_path.unlink(missing_ok=True)
                raise
        finally:
            quality_temp.unlink(missing_ok=True)
        return result
    finally:
        unsorted_path.unlink(missing_ok=True)
        sorted_temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA / "qwen3-32b-buckets.jsonl.gz")
    parser.add_argument("--output-dir", type=Path, default=DATA)
    parser.add_argument(
        "--original-trace", type=Path,
        help="Optionally reconcile Qwen IDs against the pinned base trace without reading its outcome fields",
    )
    args = parser.parse_args()
    result = audit(args.input, args.output_dir, original_trace=args.original_trace)
    print(json.dumps({
        "source_verified": result["source"]["pinned_source_verified"],
        "raw_rows": result["source"]["raw_rows"],
        "eligible_rows": result["compact"]["rows"],
        "quality_path": str(args.output_dir / "qwen_buckets_quality.json"),
        "compact_path": result["compact"]["path"],
    }, indent=2))


if __name__ == "__main__":
    main()
