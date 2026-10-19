"""Normalize the verified Philly machine sample using each machine's first row.

No future outcomes are used. The original source-order Parquet remains intact.
One row is emitted for every input row. When a later row has a different GPU
field width than the first observed row for its machine, all GPU values in the
derived row are null and `row_usable` is false.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import struct

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "external" / "msr_philly_2017"
SOURCE = DIR / "gpu_util_machine_hash5pct.parquet"
SOURCE_AUDIT = DIR / "gpu_util_audit.json"
OUTPUT = DIR / "gpu_util_normalized_first_width.parquet"
OUTPUT_AUDIT = DIR / "gpu_util_normalized_first_width_audit.json"
MAX_OUTPUT_BYTES = 1_900_000_000
MIN_FREE_BYTES = 5_000_000_000
BATCH_SIZE = 50_000
SUPPORTED_SLOTS = {2, 4, 8}
SCHEMA = pa.schema([
    ("source_row_index", pa.int64()),
    ("time_text", pa.string()),
    ("time_utc_s", pa.int64()),
    ("machine_id", pa.string()),
    ("source_gpu_field_count", pa.int16()),
    ("first_observed_gpu_slots", pa.int16()),
    ("expected_gpu_slots", pa.int16()),
    ("observed_gpu_slots", pa.int16()),
    ("width_matches_first", pa.bool_()),
    ("row_usable", pa.bool_()),
    ("row_invalid_reason", pa.string()),
    *[(f"gpu{i}_util", pa.float64()) for i in range(8)],
])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def require_space(path: Path) -> None:
    stat = os.statvfs(path)
    available = stat.f_bavail * stat.f_frsize
    if available < MIN_FREE_BYTES:
        raise RuntimeError(f"Free disk fell below 5 GB guard: {available} bytes")


def decode_source_values(row: dict) -> tuple[int, list[float | None]]:
    raw_json = row["raw_irregular_fields_json"]
    source_count = int(row["source_gpu_field_count"])
    if raw_json is None:
        if source_count != 8:
            raise ValueError("Row without raw fields does not have eight source GPU fields")
        values = [row[f"gpu{i}_util"] for i in range(8)]
        return 8, values
    if source_count not in {3, 5, 16}:
        raise ValueError(f"Unexpected raw source GPU field count {source_count}")
    fields = json.loads(raw_json)
    if not isinstance(fields, list) or len(fields) < 3:
        raise ValueError("Raw irregular field array is malformed")
    if fields[0] != row["time_text"] or fields[1] != row["machine_id"]:
        raise ValueError("Raw irregular row identity differs from sample columns")
    tokens = fields[2:]
    expected_raw_count = source_count + (1 if source_count == 16 else 0)
    if (len(tokens) != expected_raw_count or not tokens or tokens[-1] != ""
            or (len(tokens) > 1 and tokens[-2] == "")):
        raise ValueError("Raw GPU tokens do not match collector field count and terminal delimiter")
    tokens = tokens[:-1]
    values = []
    for token in tokens:
        if token in {"NA", ""}:
            values.append(None)
            continue
        try:
            value = float(token)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unexpected raw GPU utilization {token!r}") from exc
        if not math.isfinite(value):
            raise ValueError("Nonfinite raw GPU utilization")
        values.append(value)
    return len(values), values


def normalize_row(row: dict, first_width_by_machine: dict[str, int]
                  ) -> tuple[dict, str | None]:
    machine_id = row["machine_id"]
    observed, values = decode_source_values(row)
    first = first_width_by_machine.setdefault(machine_id, observed)
    reason = None
    if first not in SUPPORTED_SLOTS:
        reason = "unsupported_first_observed_width"
    elif observed != first:
        reason = "width_changed_after_first_observation"
    usable = reason is None
    if not usable:
        values = [None] * 8
    else:
        values = values + [None] * (8 - first)
    out = {
        "source_row_index": row["source_row_index"],
        "time_text": row["time_text"],
        "time_utc_s": row["time_utc_s"],
        "machine_id": machine_id,
        "source_gpu_field_count": row["source_gpu_field_count"],
        "first_observed_gpu_slots": first,
        "expected_gpu_slots": first,
        "observed_gpu_slots": observed,
        "width_matches_first": observed == first,
        "row_usable": usable,
        "row_invalid_reason": reason,
    }
    out.update({f"gpu{i}_util": values[i] for i in range(8)})
    return out, reason


def normalize_sample(source: Path, source_audit: Path, output: Path,
                     audit_path: Path) -> dict:
    source_info = json.loads(source_audit.read_text())
    if not source_info.get("integrity_pass") or not source_info.get("complete"):
        raise ValueError("Source audit does not certify full archive integrity")
    source_sha = sha256_file(source)
    if source_sha != source_info.get("sample_sha256"):
        raise ValueError("Source sample SHA256 differs from verified ingestion audit")
    if output.exists() or audit_path.exists():
        raise FileExistsError("Refusing to overwrite normalized sample or its audit")
    require_space(output.parent)
    temp = output.with_name(output.name + ".incomplete")
    first_width: dict[str, int] = {}
    per_machine: dict[str, dict] = {}
    observed_width_counts = Counter()
    invalid_reasons = Counter()
    original_index_digest = hashlib.sha256()
    last_source_index = 0
    rows = 0
    usable_rows = 0
    writer = None
    try:
        source_file = pq.ParquetFile(source)
        writer = pq.ParquetWriter(temp, SCHEMA, compression="zstd")
        for batch in source_file.iter_batches(batch_size=BATCH_SIZE):
            raw = batch.to_pydict()
            output_rows = []
            for values in zip(*(raw[name] for name in raw)):
                row = dict(zip(raw, values))
                source_index = int(row["source_row_index"])
                if source_index <= last_source_index:
                    raise ValueError("Source row indices are not strictly increasing")
                last_source_index = source_index
                original_index_digest.update(struct.pack("<Q", source_index))
                out, reason = normalize_row(row, first_width)
                machine_id = row["machine_id"]
                observed_width_counts[out["observed_gpu_slots"]] += 1
                item = per_machine.setdefault(machine_id, {
                    "first_source_row_index": source_index,
                    "first_time_text": row["time_text"],
                    "first_observed_gpu_slots": out["first_observed_gpu_slots"],
                    "rows": 0, "usable_rows": 0, "invalid_rows": 0,
                })
                item["rows"] += 1
                if reason:
                    invalid_reasons[reason] += 1
                    item["invalid_rows"] += 1
                else:
                    usable_rows += 1
                    item["usable_rows"] += 1
                rows += 1
                output_rows.append(out)
            table = pa.Table.from_pylist(output_rows, schema=SCHEMA)
            writer.write_table(table, row_group_size=len(output_rows))
            if temp.stat().st_size > MAX_OUTPUT_BYTES:
                raise RuntimeError("Normalized sample exceeded 1.9 GB guard")
            require_space(output.parent)
        writer.close()
        writer = None
        if temp.stat().st_size > MAX_OUTPUT_BYTES:
            raise RuntimeError("Normalized sample exceeded 1.9 GB guard")
        if rows != source_info["gpu_csv"]["selected_rows"]:
            raise ValueError("Normalized row count differs from ingestion sample count")
        if sorted(first_width) != source_info["gpu_csv"]["selected_machine_ids"]:
            raise ValueError("Normalized machine IDs differ from ingestion sample IDs")
        derived_index_digest = hashlib.sha256()
        replay_rows = 0
        for batch in pq.ParquetFile(temp).iter_batches(
                batch_size=BATCH_SIZE, columns=["source_row_index"]):
            for index in batch.column(0).to_pylist():
                derived_index_digest.update(struct.pack("<Q", index))
                replay_rows += 1
        if replay_rows != rows or derived_index_digest.digest() != original_index_digest.digest():
            raise ValueError("Derived source-row index sequence differs from source")
        normalized_sha = sha256_file(temp)
        script_sha = sha256_file(Path(__file__))
        result = {
            "source_parquet": str(source), "source_sha256": source_sha,
            "source_audit": str(source_audit),
            "source_archive_sha256": source_info["compressed_sha256"],
            "normalization_script_sha256": script_sha,
            "normalization_rule": "Per machine, fix GPU slot width from first source row; accept only matching 2/4/8-field rows. A single trailing empty CSV field is a delimiter. All changed-width rows are null placeholders.",
            "output_parquet": str(output), "output_sha256": normalized_sha,
            "output_bytes": temp.stat().st_size,
            "rows": rows, "usable_rows": usable_rows,
            "invalid_rows": rows - usable_rows,
            "observed_gpu_width_rows": {
                str(width): count for width, count in sorted(observed_width_counts.items())
            },
            "invalid_reason_rows": dict(invalid_reasons),
            "source_row_index_sequence_sha256": original_index_digest.hexdigest(),
            "derived_row_index_sequence_sha256": derived_index_digest.hexdigest(),
            "machines": per_machine,
            "integrity_pass": True,
        }
        audit_temp = audit_path.with_name(audit_path.name + ".tmp")
        audit_temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        os.replace(temp, output)
        os.replace(audit_temp, audit_path)
        return result
    except BaseException:
        if writer is not None:
            writer.close()
        temp.unlink(missing_ok=True)
        audit_path.with_name(audit_path.name + ".tmp").unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    result = normalize_sample(SOURCE, SOURCE_AUDIT, OUTPUT, OUTPUT_AUDIT)
    print(json.dumps({
        "rows": result["rows"], "usable_rows": result["usable_rows"],
        "invalid_rows": result["invalid_rows"],
        "output_bytes": result["output_bytes"],
    }, sort_keys=True))
