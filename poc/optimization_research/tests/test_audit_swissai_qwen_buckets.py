"""Synthetic checks for the pinned Qwen bucket integrity/support audit."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_swissai_qwen_buckets as qwen  # noqa: E402


def request(raw_id: str, created: str, tokens: int, buckets: list[int], **extra) -> dict:
    return {
        "id": raw_id, "created_at": created, "model": qwen.EXPECTED_MODEL,
        "status": "DEFAULT", "token_count": tokens, "bucket_ids": buckets,
        "reported_token_output": 400, "finished_at": "2099-01-01T00:00:00Z",
        **extra,
    }


def fixture(root: Path) -> tuple[Path, Path, dict]:
    rows = [
        request("duplicate", "2025-05-14T00:30:00Z", 32, [1, 2]),
        request("b", "2025-05-14T01:30:00Z", 17, [1, 2]),
        request("c", "2025-05-14T01:30:00Z", 33, [1, 2, 999]),
        request("d", "2025-08-01T01:30:00Z", 16, [1]),
        request("zero", "2025-08-01T01:31:00Z", 0, []),
        request("duplicate", "2025-08-02T01:30:00Z", 32, [1, 2]),
        request("bad-length", "2025-08-03T01:30:00Z", 32, [1]),
        request("test", "2025-08-08T01:30:00Z", 16, [1]),
        request("post", "2025-08-24T01:30:00", 16, [4]),
        request("wrong-model", "2025-08-25T01:30:00Z", 16, [4], model="Other"),
    ]
    raw = b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows)
    source = root / "qwen3-32b-buckets.jsonl.gz"
    with source.open("wb") as stream:
        with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
            zipped.write(raw)
    expected = {
        "expected_raw_bytes": len(raw),
        "expected_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_rows": len(rows),
    }
    return source, root / "audit", expected


class QwenBucketAuditTests(unittest.TestCase):
    def test_parser_excludes_bad_values_and_drops_only_partial_final_block(self) -> None:
        good, reasons = qwen.parse_structure(request("x", "2025-05-14T00:00:00Z", 33, [4, 5, 6]))
        self.assertEqual(reasons, [])
        self.assertEqual(good["bucket_ids"], [4, 5])
        self.assertEqual(good["complete_blocks"], 2)
        for row, reason in [
            (request("x", "2025-05-14T00:00:00Z", True, [4]), "invalid_token_count"),
            (request("x", "2025-05-14T00:00:00Z", -1, []), "invalid_token_count"),
            (request("x", "2025-05-14T00:00:00Z", 16, [True]), "invalid_bucket_id_value"),
            (request("x", "2025-05-14T00:00:00Z", 16, [-1]), "invalid_bucket_id_value"),
            (request("x", "2025-05-14T00:00:00Z", 16, [4, 5]), "bucket_length_mismatch"),
            (request("x", "2025-05-14T00:00:00Z", 15, [4]), "zero_complete_blocks"),
            (request("", "2025-05-14T00:00:00Z", 16, [4]), "invalid_id"),
            (request("x", "bad", 16, [4]), "invalid_created_at"),
        ]:
            with self.subTest(reason=reason):
                self.assertIn(reason, qwen.parse_structure(row)[1])

    def test_verified_source_is_sorted_and_support_only_has_request_time_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output, expected = fixture(Path(temp))
            result = qwen.audit(source, output, **expected)
            self.assertEqual(result["source"]["decompressed_sha256"], expected["expected_raw_sha256"])
            self.assertEqual(result["counts"]["raw_rows"], 10)
            self.assertEqual(result["counts"]["duplicate_id_rows"], 2)
            self.assertEqual(result["counts"]["zero_complete_blocks"], 1)
            self.assertEqual(result["counts"]["bucket_length_mismatch"], 1)
            self.assertEqual(result["counts"]["unexpected_model"], 1)
            self.assertEqual(result["counts"]["compact_eligible_rows"], 5)
            self.assertEqual(result["support_by_fixed_split"]["train"]["eligible_rows"], 2)
            self.assertEqual(result["support_by_fixed_split"]["train"]["simultaneous_timestamp_groups"], 1)
            self.assertEqual(result["support_by_fixed_split"]["train"]["rows_in_simultaneous_groups"], 2)
            self.assertEqual(result["support_by_fixed_split"]["train"]["eligible_scoring_window"], 2)
            self.assertEqual(result["support_by_fixed_split"]["validation"]["eligible_rows"], 1)
            self.assertEqual(result["support_by_fixed_split"]["locked_test"]["eligible_rows"], 1)
            self.assertEqual(result["support_by_fixed_split"]["posthoc"]["eligible_rows"], 1)
            table = pq.read_table(output / "qwen_buckets_sorted.parquet")
            self.assertEqual(table.num_rows, 5)
            self.assertNotIn("id", table.schema.names)
            self.assertNotIn("reported_token_output", table.schema.names)
            self.assertNotIn("finished_at", table.schema.names)
            expected_tied_rows = sorted(
                [("b", 1, [1]), ("c", 2, [1, 2])],
                key=lambda item: hashlib.sha256(item[0].encode()).digest(),
            )
            self.assertEqual(table["bucket_ids"].to_pylist()[0:2], [row[2] for row in expected_tied_rows])
            self.assertEqual(table["source_row"].to_pylist()[0:2], [row[1] for row in expected_tied_rows])
            self.assertTrue(table["created_naive"].to_pylist()[-1])
            self.assertEqual(result["compact"]["sha256"], hashlib.sha256(
                (output / "qwen_buckets_sorted.parquet").read_bytes()
            ).hexdigest())
            self.assertTrue((output / "qwen_buckets_quality.json").exists())
            with self.assertRaises(FileExistsError):
                qwen.audit(source, output, **expected)

    def test_bad_pinned_hash_leaves_no_published_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output, expected = fixture(Path(temp))
            expected["expected_raw_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "differs from pinned"):
                qwen.audit(source, output, **expected)
            self.assertFalse((output / "qwen_buckets_sorted.parquet").exists())
            self.assertFalse((output / "qwen_buckets_quality.json").exists())
            self.assertEqual(list(output.glob(".qwen_*")), [])

    def test_original_id_reconciliation_reports_counts_without_raw_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trace.jsonl"
            rows = [
                {"id": "a", "model": qwen.EXPECTED_MODEL, "status": "DEFAULT"},
                {"id": "b", "model": qwen.EXPECTED_MODEL, "status": "DEFAULT"},
                {"id": "c", "model": qwen.EXPECTED_MODEL, "status": "DEFAULT"},
                {"id": "d", "model": qwen.EXPECTED_MODEL, "status": "FAILED"},
                {"id": "x", "model": "Other", "status": "DEFAULT"},
            ]
            raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
            path.write_bytes(raw)
            bucket_ids = {hashlib.sha256(raw_id.encode()).digest() for raw_id in ("a", "b", "d", "z")}
            result = qwen.compare_original_ids(
                path, bucket_ids,
                expected_bytes=len(raw), expected_sha256=hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(result["matched_qwen_default_unique_ids"], 2)
            self.assertEqual(result["qwen_default_ids_missing_from_bucket"], 1)
            self.assertEqual(result["bucket_ids_absent_from_qwen_default"], 2)
            self.assertEqual(result["bucket_ids_found_only_in_qwen_nondefault"], 1)
            self.assertEqual(result["bucket_ids_absent_from_all_original_qwen"], 1)
            self.assertNotIn('"c"', json.dumps(result))
            with self.assertRaisesRegex(ValueError, "Pinned original trace differs"):
                qwen.compare_original_ids(path, bucket_ids, expected_bytes=len(raw), expected_sha256="0" * 64)

    def test_scoring_support_excludes_both_warmup_and_exact_end_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            start, end = qwen.SPLITS[1][1:]
            times = [start + 59 * 60_000_000, start + 60 * 60_000_000, end - 60 * 60_000_000]
            rows = [{
                "created_utc_us": at, "id_sha256": bytes([i + 1]) * 32,
                "source_row": i, "created_naive": False, "token_count": 16,
                "complete_blocks": 1, "bucket_ids": [i + 1],
            } for i, at in enumerate(times)]
            path = Path(temp) / "boundary.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=qwen.SCHEMA), path)
            support, count = qwen._sorted_support(path)
            self.assertEqual(count, 3)
            self.assertEqual(support["validation"]["eligible_after_first_60m"], 2)
            self.assertEqual(support["validation"]["eligible_with_full_60m_horizon"], 2)
            self.assertEqual(support["validation"]["eligible_scoring_window"], 1)

    def test_simultaneous_groups_are_counted_once_across_split_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            train = qwen.SPLITS[0][2] - 2 * 60 * 60_000_000
            validation = qwen.SPLITS[1][1] + 2 * 60 * 60_000_000
            rows = [{
                "created_utc_us": at, "id_sha256": bytes([i + 1]) * 32,
                "source_row": i, "created_naive": False, "token_count": 16,
                "complete_blocks": 1, "bucket_ids": [i + 1],
            } for i, at in enumerate((train, train, validation, validation))]
            path = Path(temp) / "groups.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=qwen.SCHEMA), path)
            support, count = qwen._sorted_support(path)
            self.assertEqual(count, 4)
            self.assertEqual(support["train"]["simultaneous_timestamp_groups"], 1)
            self.assertEqual(support["train"]["rows_in_simultaneous_groups"], 2)
            self.assertEqual(support["validation"]["simultaneous_timestamp_groups"], 1)
            self.assertEqual(support["validation"]["rows_in_simultaneous_groups"], 2)


if __name__ == "__main__":
    unittest.main()
