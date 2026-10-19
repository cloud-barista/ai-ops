"""Synthetic checks for the local SwissAI label/parameter extraction."""

from __future__ import annotations

import hashlib
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_swissai_output_tokens as extraction  # noqa: E402


def fixture(root: Path, *, source_rows: list[int] | None = None) -> tuple[Path, Path, Path, dict]:
    rows = [
        {
            "created_at": "2025-01-01T00:00:30Z", "model": "secret-model-A",
            "reported_token_output": "12", "model_parameters": {
                "temperature": "0.7", "top_p": 0.8, "frequency_penalty": False,
                "presence_penalty": "null", "max_tokens": "128",
            },
        },
        {
            "created_at": "2025-01-01T00:00:10Z", "model": "secret-model-B",
            "reported_token_output": True, "model_parameters": {
                "temperature": [0.5], "top_p": None, "max_tokens": {"x": 10},
            },
        },
        {
            "created_at": "2025-01-01T00:00:10Z", "model": "secret-model-C",
            "reported_token_output": 0, "model_parameters": json.dumps({
                "temperature": 0, "top_p": "0.95", "frequency_penalty": "-0.2",
                "presence_penalty": 1.25, "max_tokens": "2e3",
            }),
        },
        {
            "created_at": "2025-01-01T00:00:20Z", "model": "secret-model-D",
            "reported_token_output": "9223372036854775808", "model_parameters": "null",
        },
    ]
    raw = root / "trace.jsonl"
    raw.write_bytes(b"".join(json.dumps(row).encode("utf-8") + b"\n" for row in rows))
    compact = root / "requests_sorted.parquet"
    source_rows = [1, 2, 3, 0] if source_rows is None else source_rows
    created_by_source = [30, 10, 10, 20]
    pq.write_table(pa.table({
        "created_utc_us": pa.array([created_by_source[i] if i < 4 else 25 for i in source_rows], type=pa.int64()),
        "source_row": pa.array(source_rows, type=pa.int32()),
    }), compact)
    output = root / "local/output_tokens_sorted.parquet"
    hashes = {
        "expected_raw_size": raw.stat().st_size,
        "expected_raw_sha": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "expected_compact_sha": hashlib.sha256(compact.read_bytes()).hexdigest(),
        "expected_rows": len(rows),
    }
    return raw, compact, output, hashes


class SwissAIOutputTokenExtractionTests(unittest.TestCase):
    def test_parsers_reject_booleans_containers_nonfinite_and_fractional_labels(self) -> None:
        for value in (True, False, None, [], {}, -1, "-1", "null", "NaN", "Inf", 2.5,
                      "3.1", "9223372036854775808"):
            self.assertIsNone(extraction.parse_output_tokens(value), value)
        for value, expected in [(0, 0), (5.0, 5), (" 5 ", 5), ("5.0", 5), ("2e2", 200)]:
            self.assertEqual(extraction.parse_output_tokens(value), expected)
        for value in (True, False, None, [], {}, "null", "NaN", "1e999"):
            self.assertIsNone(extraction.parse_numeric(value), value)
        self.assertEqual(extraction.parse_numeric("-0.25"), -0.25)

    def test_extract_joins_every_raw_row_once_in_compact_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw, compact, output, pinned = fixture(Path(temp))
            result = extraction.extract(raw, compact, output, **pinned)
            table = pq.read_table(output)
            self.assertEqual(result["rows"], 4)
            self.assertEqual(result["order"], ["created_utc_us", "source_row"])
            self.assertEqual(table.schema.names, list(extraction.SCHEMA.names))
            self.assertEqual(table["source_row"].to_pylist(), [1, 2, 3, 0])
            self.assertEqual(table["reported_output_tokens"].to_pylist(), [None, 0, None, 12])
            self.assertEqual(table["temperature"].to_pylist(), [None, 0.0, None, 0.7])
            self.assertEqual(table["top_p"].to_pylist(), [None, 0.95, None, 0.8])
            self.assertEqual(table["max_tokens"].to_pylist(), [None, 2000.0, None, 128.0])
            self.assertEqual(table["frequency_penalty"].to_pylist(), [None, -0.2, None, None])
            self.assertEqual(table["presence_penalty"].to_pylist(), [None, 1.25, None, None])
            self.assertNotIn("model", str(result))
            self.assertNotIn("model", table.schema.names)
            compressed = raw.with_name("trace.jsonl.gz")
            with gzip.open(compressed, "wb") as handle:
                handle.write(raw.read_bytes())
            compressed_output = output.with_name("output_tokens_from_gzip.parquet")
            compressed_result = extraction.extract(compressed, compact, compressed_output, **pinned)
            compressed_table = pq.read_table(compressed_output)
            self.assertEqual(compressed_result["output_sha256"], result["output_sha256"])
            self.assertEqual(compressed_table.to_pylist(), table.to_pylist())
            with self.assertRaises(FileExistsError):
                extraction.extract(raw, compact, output, **pinned)

    def test_rejects_tampering_before_publishing_any_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw, compact, output, pinned = fixture(Path(temp))
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "Raw trace size or SHA-256"):
                extraction.extract(raw, compact, output, **pinned)
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
        with tempfile.TemporaryDirectory() as temp:
            raw, compact, output, pinned = fixture(Path(temp))
            compact.write_bytes(compact.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "Sorted compact SHA-256"):
                extraction.extract(raw, compact, output, **pinned)
            self.assertFalse(output.exists())

    def test_duplicate_or_misordered_source_rows_never_publish_partial_output(self) -> None:
        for source_rows, error in [
            ([1, 1, 3, 0], "Duplicate compact source_row"),
            ([0, 1, 2, 3], "not sorted"),
            ([1, 2, 3, 4], "outside the raw trace"),
        ]:
            with self.subTest(source_rows=source_rows), tempfile.TemporaryDirectory() as temp:
                raw, compact, output, pinned = fixture(Path(temp), source_rows=source_rows)
                with self.assertRaisesRegex(ValueError, error):
                    extraction.extract(raw, compact, output, **pinned)
                self.assertFalse(output.exists())
                self.assertEqual(list(output.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
