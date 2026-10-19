"""Check the SwissAI trace's mixed clocks and global arrival ordering."""

from __future__ import annotations

import hashlib
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import polars as pl


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fetch_swissai_trace as fetcher  # noqa: E402
import audit_swissai_trace as ingestion  # noqa: E402


class SwissAITraceIngestionTests(unittest.TestCase):
    def test_fetch_accepts_a_verified_compressed_local_copy(self) -> None:
        payload = b'{"created_at":"2025-01-01T00:00:00Z"}\n'
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "trace.jsonl.gz"
            with gzip.open(archive, "wb") as handle:
                handle.write(payload)
            with (
                patch.object(fetcher, "EXPECTED_SIZE", len(payload)),
                patch.object(fetcher, "EXPECTED_SHA256", hashlib.sha256(payload).hexdigest()),
            ):
                self.assertEqual(fetcher.fetch(Path(temp)), archive)

    def test_time_parser_preserves_microseconds_and_marks_naive(self) -> None:
        explicit, explicit_naive = ingestion.parse_utc_microseconds("2025-01-01T00:00:00.123Z")
        naive, naive_flag = ingestion.parse_utc_microseconds("2025-01-01 00:00:00.123")
        self.assertEqual(explicit, naive)
        self.assertEqual((explicit_naive, naive_flag), (False, True))
        self.assertEqual(ingestion.parse_utc_microseconds("\\N"), (None, False))

    def test_audit_sorts_all_rows_and_keeps_clock_provenance(self) -> None:
        records = [
            {
                "id": "not copied",
                "created_at": "2025-01-01T01:00:00.123Z",
                "finished_at": "2025-01-01 01:00:02.000",
                "model": "A",
                "status": "DEFAULT",
                "reported_token_input": 8,
                "reported_token_output": 12,
            },
            {
                "created_at": "2025-01-01T00:00:00.000Z",
                "finished_at": "2025-01-01T00:00:00.003Z",
                "model": "B",
                "status": "ERROR",
                "reported_token_input": -1,
                "reported_token_output": -1,
            },
            {
                "created_at": "2025-01-01T00:00:00.000Z",
                "finished_at": "\\N",
                "model": "B",
                "status": "DEFAULT",
                "reported_token_input": 2,
                "reported_token_output": 0,
            },
            {
                "created_at": "2025-01-01 00:00:00.500",
                "finished_at": "2025-01-01T00:00:01.500Z",
                "model": "B",
                "status": "DEFAULT",
                "reported_token_input": 3,
                "reported_token_output": 1,
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "trace.jsonl"
            source.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in records))
            compressed = root / "trace.jsonl.gz"
            with gzip.open(compressed, "wb") as handle:
                handle.write(source.read_bytes())
            with (
                patch.object(ingestion, "EXPECTED_SIZE", source.stat().st_size),
                patch.object(ingestion, "EXPECTED_SHA256", hashlib.sha256(source.read_bytes()).hexdigest()),
            ):
                result = ingestion.audit(compressed, root / "derived")
            table = pl.read_parquet(root / "derived/requests_sorted.parquet")
            self.assertEqual(table["source_row"].to_list(), [1, 2, 3, 0])
            self.assertEqual(table["elapsed_us"].to_list(), [3000, None, 1000000, 1877000])
            self.assertEqual(table["finished_naive"].to_list(), [False, False, False, True])
            self.assertEqual(table["created_naive"].to_list(), [False, False, True, False])
            self.assertNotIn("id", table.columns)
            self.assertNotIn("reported_token_output", table.columns)
            self.assertEqual(result["counts"]["source_order_violations"], 1)
            self.assertEqual(result["counts"]["default_status_missing_finished"], 1)
            self.assertEqual(result["counts"]["positive_duration"], 3)


if __name__ == "__main__":
    unittest.main()
