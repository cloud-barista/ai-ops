"""Extract local SwissAI output-token labels and request parameters.

The output follows the pinned compact table's chronological row order. It
contains no model strings or request IDs and must remain a local research file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from fetch_swissai_trace import (
    EXPECTED_SHA256, EXPECTED_SIZE, file_digest, local_source_path, open_source,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/external/swissai_serving_trace"
EXPECTED_COMPACT_SHA256 = "d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b"
EXPECTED_ROWS = 16_329_237
PARAMETERS = (
    "temperature", "top_p", "frequency_penalty", "presence_penalty", "max_tokens",
)
SCHEMA = pa.schema([
    pa.field("source_row", pa.int32(), nullable=False),
    pa.field("reported_output_tokens", pa.int64()),
    *(pa.field(name, pa.float64()) for name in PARAMETERS),
])
MAX_INT64 = np.iinfo(np.int64).max


def parse_numeric(value: object) -> float | None:
    """Read a finite JSON number or decimal string; never coerce booleans."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            result = float(value)
        except (OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            decimal = Decimal(value.strip())
            if not decimal.is_finite():
                return None
            result = float(decimal)
        except (InvalidOperation, OverflowError, ValueError):
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def parse_output_tokens(value: object) -> int | None:
    """Keep only nonnegative, exactly integral values representable as int64."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        parsed = int(value)
    elif isinstance(value, str):
        try:
            decimal = Decimal(value.strip())
            if not decimal.is_finite() or decimal != decimal.to_integral_value():
                return None
            parsed = int(decimal)
        except (InvalidOperation, OverflowError, ValueError):
            return None
    else:
        return None
    return parsed if 0 <= parsed <= MAX_INT64 else None


def parse_parameters(value: object) -> dict[str, float | None]:
    """The trace normally stores an object; tolerate a JSON-encoded object."""
    if isinstance(value, str):
        try:
            value = orjson.loads(value)
        except orjson.JSONDecodeError:
            value = None
    if not isinstance(value, dict):
        value = {}
    return {name: parse_numeric(value.get(name)) for name in PARAMETERS}


def _read_raw(
    raw_path: Path, expected_rows: int, expected_sha: str,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    labels = np.full(expected_rows, -1, dtype=np.int64)
    params = {name: np.full(expected_rows, np.nan, dtype=np.float64) for name in PARAMETERS}
    digest = hashlib.sha256()
    count = 0
    with open_source(raw_path) as handle:
        for source_row, line in enumerate(handle):
            if source_row >= expected_rows:
                raise ValueError("Raw trace has more rows than the pinned compact table")
            count += 1
            digest.update(line)
            try:
                record = orjson.loads(line)
            except orjson.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at source_row={source_row}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Non-object JSON at source_row={source_row}")
            parsed = parse_output_tokens(record.get("reported_token_output"))
            if parsed is not None:
                labels[source_row] = parsed
            for name, value in parse_parameters(record.get("model_parameters")).items():
                if value is not None:
                    params[name][source_row] = value
    if count != expected_rows:
        raise ValueError("Raw trace has fewer rows than the pinned compact table")
    if digest.hexdigest() != expected_sha:
        raise ValueError("Raw trace changed after its initial SHA-256 verification")
    return labels, params


def _write_sorted(
    compact_path: Path, temporary: Path, labels: np.ndarray,
    params: dict[str, np.ndarray], expected_rows: int,
) -> None:
    compact = pq.ParquetFile(compact_path)
    if compact.metadata.num_rows != expected_rows:
        raise ValueError("Compact row count differs from the pinned raw trace")
    required = {"source_row", "created_utc_us"}
    if not required.issubset(compact.schema_arrow.names):
        raise ValueError("Compact table lacks source_row or created_utc_us")

    seen = np.zeros(expected_rows, dtype=np.bool_)
    total = 0
    previous: tuple[int, int] | None = None
    with pq.ParquetWriter(temporary, SCHEMA, compression="zstd") as writer:
        for batch in compact.iter_batches(
            batch_size=100_000, columns=["created_utc_us", "source_row"]
        ):
            if batch.column("source_row").null_count or batch.column("created_utc_us").null_count:
                raise ValueError("Null source_row or created_utc_us in compact table")
            source_rows = batch.column("source_row").to_numpy(zero_copy_only=False).astype(
                np.int64, copy=False
            )
            created = batch.column("created_utc_us").to_numpy(zero_copy_only=False).astype(
                np.int64, copy=False
            )
            if len(source_rows) == 0:
                continue
            if np.any(source_rows < 0) or np.any(source_rows >= expected_rows):
                raise ValueError("Compact source_row lies outside the raw trace")
            if len(np.unique(source_rows)) != len(source_rows) or np.any(seen[source_rows]):
                raise ValueError("Duplicate compact source_row; join is not one-to-one")
            if previous is not None and (int(created[0]), int(source_rows[0])) <= previous:
                raise ValueError("Compact rows are not sorted by created_utc_us, source_row")
            if np.any(created[1:] < created[:-1]) or np.any(
                (created[1:] == created[:-1]) & (source_rows[1:] <= source_rows[:-1])
            ):
                raise ValueError("Compact rows are not sorted by created_utc_us, source_row")
            previous = int(created[-1]), int(source_rows[-1])
            seen[source_rows] = True
            columns = [pa.array(source_rows.astype(np.int32, copy=False), type=pa.int32())]
            selected_labels = labels[source_rows]
            columns.append(pa.array(
                selected_labels, mask=selected_labels < 0, type=pa.int64()
            ))
            for name in PARAMETERS:
                selected = params[name][source_rows]
                columns.append(pa.array(selected, mask=np.isnan(selected), type=pa.float64()))
            writer.write_table(pa.Table.from_arrays(columns, schema=SCHEMA))
            total += len(source_rows)
    if total != expected_rows or not np.all(seen):
        raise ValueError("Compact source_row join is not a bijection with raw rows")
    if pq.ParquetFile(temporary).metadata.num_rows != expected_rows:
        raise ValueError("Output Parquet row count changed during writing")


def extract(
    raw_path: Path,
    compact_path: Path,
    output_path: Path,
    *,
    expected_raw_size: int = EXPECTED_SIZE,
    expected_raw_sha: str = EXPECTED_SHA256,
    expected_compact_sha: str = EXPECTED_COMPACT_SHA256,
    expected_rows: int = EXPECTED_ROWS,
) -> dict[str, object]:
    """Verify both inputs, then publish a fully joined local Parquet atomically.

    The default hashes and count identify the exact source used by the frozen
    protocol. Override them only for small, synthetic tests.
    """
    raw_path, compact_path, output_path = map(Path, (raw_path, compact_path, output_path))
    if expected_rows < 1 or expected_rows > np.iinfo(np.int32).max:
        raise ValueError("expected_rows must fit nonempty source_row int32")
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace existing output {output_path}")
    if output_path.resolve() in (raw_path.resolve(), compact_path.resolve()):
        raise ValueError("Output path must differ from both inputs")
    raw_size, raw_sha = file_digest(raw_path)
    if raw_size != expected_raw_size or raw_sha != expected_raw_sha:
        raise ValueError("Raw trace size or SHA-256 differs from pinned source")
    compact_size, compact_sha = file_digest(compact_path)
    if compact_sha != expected_compact_sha:
        raise ValueError("Sorted compact SHA-256 differs from pinned source")

    labels, params = _read_raw(raw_path, expected_rows, expected_raw_sha)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _write_sorted(compact_path, temporary, labels, params, expected_rows)
        if file_digest(compact_path)[1] != expected_compact_sha:
            raise ValueError("Sorted compact changed after its initial SHA-256 verification")
        output_size, output_sha = file_digest(temporary)
        # The temporary file is on the same filesystem. Linking publishes the
        # complete file atomically and refuses to overwrite an existing result.
        os.link(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "kind": "swissai_output_token_extraction_v1",
        "rows": expected_rows,
        "raw_bytes": raw_size,
        "raw_sha256": raw_sha,
        "compact_bytes": compact_size,
        "compact_sha256": compact_sha,
        "output_path": str(output_path),
        "output_bytes": output_size,
        "output_sha256": output_sha,
        "columns": SCHEMA.names,
        "order": ["created_utc_us", "source_row"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=local_source_path(DATA))
    parser.add_argument("--compact", type=Path, default=DATA / "requests_sorted.parquet")
    parser.add_argument("--output", type=Path, default=DATA / "output_tokens_sorted.parquet")
    args = parser.parse_args()
    result = extract(args.raw, args.compact, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
