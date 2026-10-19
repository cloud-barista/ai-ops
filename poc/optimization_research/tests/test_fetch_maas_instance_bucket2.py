"""Synthetic extraction and source-integrity tests for unused MaaS ID bucket 2."""

from __future__ import annotations

from io import BytesIO
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import fetch_maas_instance_bucket2 as bucket
from test_fetch_maas_instance_sample import FakeSession, build_archive, make_table


def matching_id(prefix: str, wanted: int) -> str:
    return next(f"{prefix}-{index}" for index in range(100000)
                if bucket.bucket_for_id(f"{prefix}-{index}")[1] == wanted)


class Bucket2Tests(unittest.TestCase):
    def fixture(self):
        prior = matching_id("prior", 0)
        older = matching_id("older", 1)
        target = matching_id("target", 2)
        tables = []
        for index in range(21):
            t = (140 + index % 7) * 86400
            tables.append(make_table([
                (prior, t, "c0", 1., 10., 20., 15),
                (older, t + 300, "c1", 1., 20., 30., 15),
                (target, t + 600, "c2", 2., 30., 40., 15),
            ]))
        archive = build_archive(tables)
        client = bucket.source.RangeClient("https://test.invalid/maas.zip", len(archive),
                                           "FAKE-ETAG", FakeSession(archive))
        client.head()
        members = bucket.source.read_directory(client)
        with zipfile.ZipFile(BytesIO(archive)) as stored:
            records = []
            for listed in members:
                item = bucket.source.locate_member(client, listed)
                part = int(Path(item.name).stem.rsplit("_", 1)[1])
                records.append({
                    "name": item.name, "source_byte_offset": item.data_offset,
                    "source_size": item.size, "zip_crc32": f"{item.crc32:08x}",
                    "source_sha256": hashlib.sha256(stored.read(item.name)).hexdigest(),
                    "row_count": tables[part].num_rows,
                })
        return archive, client, {"members": records}, target

    def test_every_shard_selects_bucket2_and_preserves_hash(self):
        _, client, reference, target = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = bucket.fetch(output=Path(directory), client=client, reference=reference,
                                  expected_members=21, expected_rows=63)
            self.assertTrue(result["integrity_pass"])
            self.assertEqual(result["source_rows"], 63)
            self.assertEqual(result["selected_rows"], 21)
            self.assertEqual(result["selected_unique_ids"], 1)
            data = pq.read_table(Path(directory) / "instance_week_5min_bucket2.parquet")
            self.assertEqual(data.num_rows, 21)
            self.assertEqual(set(data.column("instance_hash").to_pylist()),
                             {hashlib.sha256(target.encode()).hexdigest()})
            self.assertFalse(list((Path(directory) / "work").glob("current_member*")))

    def test_member_hash_mismatch_rejects_selection(self):
        _, client, reference, _ = self.fixture()
        reference["members"][0]["source_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "source member SHA256 differs"):
                bucket.fetch(output=Path(directory), client=client, reference=reference,
                             expected_members=21, expected_rows=63)
            self.assertFalse((Path(directory) / "instance_week_5min_bucket2.parquet").exists())


if __name__ == "__main__":
    unittest.main()
