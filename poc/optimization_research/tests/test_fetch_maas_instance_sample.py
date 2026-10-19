"""Offline integrity, resume, and sampling checks for the MaaS ZIP reader."""

from __future__ import annotations

from io import BytesIO
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fetch_maas_instance_sample.py"
SPEC = importlib.util.spec_from_file_location("fetch_maas_instance_sample", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, code, headers, body=b""):
        self.status_code, self.headers, self.body = code, headers, body

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, archive: bytes, etag="FAKE-ETAG"):
        self.archive, self.etag = archive, etag
        self.request_count = 0
        self.force_200_at = None

    def head(self, url, **kwargs):
        return FakeResponse(200, {
            "Content-Length": str(len(self.archive)),
            "ETag": f'"{self.etag}"', "Accept-Ranges": "bytes",
        })

    def get(self, url, headers, **kwargs):
        self.request_count += 1
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"]).groups())
        if self.force_200_at == self.request_count:
            return FakeResponse(200, {"Content-Length": str(len(self.archive)),
                                      "ETag": f'"{self.etag}"'}, self.archive)
        body = self.archive[start:end + 1]
        return FakeResponse(206, {
            "Content-Range": f"bytes {start}-{end}/{len(self.archive)}",
            "Content-Length": str(len(body)), "ETag": f'"{self.etag}"',
        }, body)


def build_archive(parts: list[pa.Table]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for index, table in enumerate(parts):
            buffer = BytesIO()
            pq.write_table(table, buffer)
            archive.writestr(f"instance_week_5min/part_{index}.parquet", buffer.getvalue())
    return output.getvalue()


def make_table(rows):
    return pa.table({
        "instance_id": pa.array([row[0] for row in rows], type=pa.string()),
        "time_s": pa.array([row[1] for row in rows], type=pa.int64()),
        "cluster_id": pa.array([row[2] for row in rows], type=pa.string()),
        "gpu_count": pa.array([row[3] for row in rows], type=pa.float64()),
        "sm_util": pa.array([row[4] for row in rows], type=pa.float64()),
        "gpu_mem_util": pa.array([row[5] for row in rows], type=pa.float64()),
        "samples": pa.array([row[6] for row in rows], type=pa.int64()),
        "cpu_usage": pa.array([1.0] * len(rows), type=pa.float64()),
        "mem_usage": pa.array([2.0] * len(rows), type=pa.float64()),
    })


def zip64_end(archive: bytes) -> bytes:
    """Turn only the end records into ZIP64, preserving the original members."""
    location = archive.rfind(b"PK\x05\x06")
    assert location >= 0
    end = bytearray(archive[location:])
    _, _, _, count_disk, count, size, offset, _ = struct.unpack_from("<IHHHHIIH", end)
    assert count_disk == count
    zip64 = struct.pack("<IQHHIIQQQQ", 0x06064b50, 44, 45, 45, 0, 0,
                        count, count, size, offset)
    locator = struct.pack("<IIQI", 0x07064b50, 0, location, 1)
    struct.pack_into("<HHII", end, 8, 0xffff, 0xffff, 0xffffffff, 0xffffffff)
    return archive[:location] + zip64 + locator + end


class MaaSRangeSampleTests(unittest.TestCase):
    def setUp(self):
        self.selected = next(f"selected-{number}" for number in range(10000)
                             if MODULE.selected_id(f"selected-{number}")[1])
        self.other = next(f"other-{number}" for number in range(10000)
                          if not MODULE.selected_id(f"other-{number}")[1])
        self.t = 140 * 86400
        self.parts = [
            make_table([
                (self.selected, self.t, "c1", 1, 20, 25, 15),
                (self.selected, self.t + 300, "c1", 1, 101, 30, 0),
                (self.other, self.t, "c1", 2, 40, 20, 15),
            ]),
            make_table([
                (self.selected, self.t, "c1", 1, 20, 25, 15),
                (self.selected, self.t + 900, "c2", 2, 40, None, 15),
                (self.selected, self.t, "c2", 2, 20, 25, 15),
                (self.other, self.t + 300, "c1", 2, 40, 20, 15),
            ]),
        ]

    def client(self, archive):
        session = FakeSession(archive)
        return MODULE.RangeClient("https://test.invalid/maas.zip", len(archive),
                                  "FAKE-ETAG", session)

    def test_full_sample_audit_preserves_duplicates_and_invalid_values(self):
        archive = build_archive(self.parts)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            audit = MODULE.fetch_sample(output, self.client(archive),
                                        expected_members=2, expected_rows=7,
                                        chunk_size=257)
            self.assertTrue(audit["integrity_pass"])
            self.assertTrue(audit["schema_pass"])
            self.assertTrue(audit["all_members_verified"])
            self.assertEqual(audit["totals"]["selected_row_count"], 5)
            self.assertEqual(audit["totals"]["selected_unique_ids"], 1)
            self.assertEqual(audit["totals"]["numeric"]["samples"]["equal_zero"], 1)
            self.assertEqual(audit["totals"]["numeric"]["sm_util"]["above_upper"], 1)
            self.assertEqual(audit["totals"]["selected_key_audit"]["exact_key_duplicate_extra_rows"], 1)
            self.assertEqual(audit["totals"]["selected_key_audit"]["same_id_time_different_cluster_or_gpu_keys"], 1)
            table = pq.read_table(output / "instance_week_5min_sample.parquet")
            self.assertEqual(table.num_rows, 5)
            self.assertEqual(table.schema.names, MODULE.OUTPUT_SCHEMA.names)
            rows = table.to_pylist()
            self.assertTrue(any(row["sm_util"] == 101 and row["samples"] == 0 for row in rows))
            self.assertTrue(any(row["gpu_mem_util"] is None for row in rows))
            self.assertEqual(rows[0]["instance_hash"], hashlib.sha256(self.selected.encode()).hexdigest())
            expected_tie = hashlib.sha256(f"{self.selected}:{self.t + 600}".encode()).hexdigest()
            self.assertEqual(rows[0]["alarm_tie_hash"], expected_tie)
            self.assertEqual(audit["compact_sha256"],
                             MODULE.sha256_file(output / "instance_week_5min_sample.parquet"))
            self.assertFalse(list(output.glob("*.zip")))
            self.assertFalse(list((output / "instance_week_5min_work").glob("current_member*")))
            self.assertNotIn(self.selected, json.dumps(audit))

    def test_zip64_end_record(self):
        archive = zip64_end(build_archive(self.parts))
        members = MODULE.read_directory(self.client(archive), expected_members=2)
        self.assertEqual([member.name for member in members],
                         ["instance_week_5min/part_0.parquet",
                          "instance_week_5min/part_1.parquet"])
        self.assertGreater(MODULE.locate_member(self.client(archive), members[0]).data_offset, 0)

    def test_refuses_http_200_on_range(self):
        archive = build_archive(self.parts)
        client = self.client(archive)
        client.session.force_200_at = 1
        with self.assertRaisesRegex(ValueError, "expected 206"):
            MODULE.read_directory(client, expected_members=2)

    def test_resumes_only_verified_prefix(self):
        archive = build_archive(self.parts)
        client = self.client(archive)
        member = MODULE.locate_member(client, MODULE.read_directory(client, expected_members=2)[0])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            first_data_request = client.session.request_count + 1
            client.session.force_200_at = first_data_request + 1
            with self.assertRaisesRegex(ValueError, "expected 206"):
                MODULE.fetch_member(client, member, work, chunk_size=257)
            state = json.loads((work / "current_member.progress.json").read_text())
            self.assertEqual(state["downloaded_bytes"], 257)
            with (work / "current_member.part").open("ab") as stream:
                stream.write(b"uncheckpointed bytes")
            client.session.force_200_at = None
            partial, digest, headers = MODULE.fetch_member(client, member, work, chunk_size=257)
            self.assertEqual(partial.stat().st_size, member.size)
            self.assertEqual(digest, MODULE.sha256_file(partial))
            self.assertEqual(MODULE.crc32_file(partial), member.crc32)
            self.assertGreater(len(headers), 1)

    def test_crc_corruption_aborts_without_success_artifacts(self):
        archive = bytearray(build_archive(self.parts))
        client = self.client(bytes(archive))
        member = MODULE.locate_member(client, MODULE.read_directory(client, expected_members=2)[0])
        archive[member.data_offset + 200] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(ValueError, "CRC32 mismatch"):
                MODULE.fetch_sample(output, self.client(bytes(archive)),
                                    expected_members=2, expected_rows=7, chunk_size=257)
            self.assertFalse((output / "instance_week_5min_audit.json").exists())
            self.assertFalse((output / "instance_week_5min_sample.parquet").exists())
            failed = json.loads((output / "instance_week_5min_audit_failed.json").read_text())
            self.assertFalse(failed["integrity_pass"])

    def test_wrong_official_row_count_blocks_compact_then_resume_succeeds(self):
        archive = build_archive(self.parts)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(ValueError, "row count/schema/time domain mismatch"):
                MODULE.fetch_sample(output, self.client(archive),
                                    expected_members=2, expected_rows=8)
            self.assertFalse((output / "instance_week_5min_sample.parquet").exists())
            self.assertFalse((output / "instance_week_5min_audit.json").exists())
            failed = json.loads((output / "instance_week_5min_audit_failed.json").read_text())
            self.assertFalse(failed["schema_pass"])
            self.assertEqual(failed["totals"]["row_count"], 7)
            second_client = self.client(archive)
            audit = MODULE.fetch_sample(output, second_client,
                                        expected_members=2, expected_rows=7)
            self.assertTrue(audit["schema_pass"])
            self.assertFalse((output / "instance_week_5min_audit_failed.json").exists())
            self.assertLess(second_client.session.request_count, 10)

    def test_saved_member_reuse_checks_source_and_legacy_range_evidence(self):
        archive = build_archive(self.parts)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            MODULE.fetch_sample(output, self.client(archive),
                                expected_members=2, expected_rows=7, chunk_size=257)
            saved = output / "instance_week_5min_work/part_0.audit.json"
            original = json.loads(saved.read_text())
            self.assertEqual(original["source_url"], "https://test.invalid/maas.zip")
            self.assertEqual(original["source_archive_size"], len(archive))
            self.assertEqual(original["source_etag"], "FAKE-ETAG")

            changed_url = dict(original, source_url="https://other.invalid/maas.zip")
            MODULE.atomic_json(saved, changed_url)
            with self.assertRaisesRegex(ValueError, "source source_url differs"):
                MODULE.fetch_sample(output, self.client(archive),
                                    expected_members=2, expected_rows=7)

            legacy = {key: value for key, value in original.items()
                      if key not in ("source_url", "source_archive_size", "source_etag")}
            MODULE.atomic_json(saved, legacy)
            client = self.client(archive)
            resumed = MODULE.fetch_sample(output, client,
                                          expected_members=2, expected_rows=7)
            self.assertTrue(resumed["integrity_pass"])
            self.assertLess(client.session.request_count, 10)

            bad_etag = json.loads(json.dumps(legacy))
            bad_etag["http_ranges"][0]["etag"] = '"OTHER-ETAG"'
            MODULE.atomic_json(saved, bad_etag)
            with self.assertRaisesRegex(ValueError, "range audit differs"):
                MODULE.fetch_sample(output, self.client(archive),
                                    expected_members=2, expected_rows=7)

            bad_size = json.loads(json.dumps(legacy))
            bad_size["http_ranges"][0]["content_range"] = (
                bad_size["http_ranges"][0]["content_range"].rsplit("/", 1)[0] + "/999999"
            )
            MODULE.atomic_json(saved, bad_size)
            with self.assertRaisesRegex(ValueError, "range audit differs"):
                MODULE.fetch_sample(output, self.client(archive),
                                    expected_members=2, expected_rows=7)

            missing_range = json.loads(json.dumps(legacy))
            missing_range["http_ranges"] = missing_range["http_ranges"][1:]
            MODULE.atomic_json(saved, missing_range)
            with self.assertRaisesRegex(ValueError, "range audit differs"):
                MODULE.fetch_sample(output, self.client(archive),
                                    expected_members=2, expected_rows=7)

    def test_id_normalization_and_header_pin(self):
        self.assertEqual(MODULE.normalize_id(b"abc"), "abc")
        self.assertEqual(MODULE.normalize_id(123), "123")
        with self.assertRaises(UnicodeDecodeError):
            MODULE.normalize_id(b"\xff")
        archive = build_archive(self.parts)
        client = MODULE.RangeClient("https://test.invalid/maas.zip", len(archive),
                                    "WRONG", FakeSession(archive))
        with self.assertRaisesRegex(ValueError, "ETag differs"):
            client.head()


if __name__ == "__main__":
    unittest.main()
