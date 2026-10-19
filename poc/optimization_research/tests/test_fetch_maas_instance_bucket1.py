"""Offline 21-shard selection and release-gate checks for MaaS bucket 1."""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import fetch_maas_instance_bucket1 as bucket
from test_fetch_maas_instance_sample import FakeSession, build_archive, make_table


def matching_id(label: str, wanted: int) -> str:
    return next(f"{label}-{index}" for index in range(100000)
                if bucket.id_digest_and_bucket(f"{label}-{index}")[1] == wanted)


class Bucket1ExtractorTests(unittest.TestCase):
    def client(self, archive: bytes):
        return bucket.source.RangeClient("https://test.invalid/maas.zip", len(archive),
                                         "FAKE-ETAG", FakeSession(archive))

    def parts(self):
        bucket0 = matching_id("bucket0", 0)
        bucket1 = matching_id("bucket1-shared", 1)
        bucket2 = matching_id("bucket2", 2)
        tables = []
        for part in range(21):
            t = (140 + part % 7) * 86400
            own = matching_id(f"bucket1-{part}", 1)
            rows = [
                (bucket0, t, "c0", 1., 11., 12., 15),
                (bucket1, t, "c1", 1., 22., None, 0 if part == 0 else 15),
                (own, t + 300, "c1", 1.5, 33., 34., 15),
                (bucket2, t + 600, "c2", 2., 44., 45., 15),
            ]
            if part == 0:
                rows.append((bucket1, t, "c1", 1., 22., None, 0))
            tables.append(make_table(rows))
        return tables, bucket0, bucket1, bucket2

    def reference(self, archive: bytes, tables: list) -> dict:
        client = self.client(archive)
        client.head()
        members = bucket.source.read_directory(client, expected_members=len(tables))
        with zipfile.ZipFile(BytesIO(archive)) as stored:
            records = []
            for item in members:
                member = bucket.source.locate_member(client, item)
                index = int(Path(member.name).stem.rsplit("_", 1)[1])
                records.append({
                    "name": member.name,
                    "source_byte_offset": member.data_offset,
                    "source_size": member.size,
                    "zip_crc32": f"{member.crc32:08x}",
                    "source_sha256": hashlib.sha256(stored.read(member.name)).hexdigest(),
                    "row_count": tables[index].num_rows,
                })
        return {"members": records}

    def test_all_21_shards_include_every_selected_row_and_exclude_bucket0(self):
        tables, bucket0, bucket1, bucket2 = self.parts()
        archive = build_archive(tables)
        reference = self.reference(archive, tables)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = bucket.fetch_bucket1(output, self.client(archive),
                                          expected_members=21, expected_rows=85,
                                          chunk_size=65_536,
                                          synthetic_reference=reference)
            self.assertTrue(result["integrity_pass"])
            self.assertTrue(result["all_members_verified"])
            self.assertEqual(result["source_members"], 21)
            self.assertEqual(result["source_rows"], 85)
            self.assertEqual(result["selected_rows"], 43)
            self.assertEqual(result["selected_unique_ids"], 22)
            self.assertEqual(result["bucket0_rows_seen_and_excluded"], 21)
            self.assertEqual(len(result["member_audits"]), 21)
            table = pq.read_table(output / "instance_week_5min_bucket1.parquet")
            self.assertEqual(table.num_rows, 43)
            self.assertEqual(table.schema.names, bucket.SCHEMA.names)
            ids = table.column("instance_hash").to_pylist()
            shared = hashlib.sha256(bucket1.encode()).hexdigest()
            self.assertEqual(ids.count(shared), 22)
            self.assertNotIn(hashlib.sha256(bucket0.encode()).hexdigest(), ids)
            self.assertNotIn(hashlib.sha256(bucket2.encode()).hexdigest(), ids)
            self.assertTrue(all(int.from_bytes(bytes.fromhex(value)[:8], "big") % 100 == 1
                                for value in ids))
            self.assertTrue(any(row["samples"] == 0 and row["gpu_mem_util"] is None
                                for row in table.to_pylist()))
            self.assertTrue(all(part["source_sha256"] and part["http_ranges"]
                                for part in result["member_audits"]))
            self.assertFalse(list((output / "work").glob("current_member*")))
            self.assertFalse(list(output.glob("*.zip")))
            self.assertNotIn(bucket1, json.dumps(result))
            second = bucket.fetch_bucket1(output, self.client(archive),
                                          expected_members=21, expected_rows=85,
                                          synthetic_reference=reference)
            self.assertEqual(second["compact_sha256"], result["compact_sha256"])

    def test_missing_part_number_is_rejected_before_member_download(self):
        tables, *_ = self.parts()
        archive = build_archive(tables[:-1])
        reference = self.reference(build_archive(tables), tables)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Expected 21 ZIP members"):
                bucket.fetch_bucket1(Path(directory), self.client(archive),
                                     expected_members=21, expected_rows=85,
                                     synthetic_reference=reference)
            self.assertFalse((Path(directory) / "instance_week_5min_bucket1.parquet").exists())

    def test_id_normalization_matches_bucket0_and_partitions_every_id(self):
        for raw in ("abc", b"abc", 123):
            digest, value = bucket.id_digest_and_bucket(raw)
            original, in_bucket0 = bucket.source.selected_id(raw)
            self.assertEqual(digest, original)
            self.assertEqual(in_bucket0, value == 0)
            self.assertEqual(int.from_bytes(bytes.fromhex(digest)[:8], "big") % 100, value)

    def test_real_client_requires_release_before_any_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "requires a validated release manifest"):
                bucket.fetch_bucket1(Path(directory))
        real_client = bucket.source.RangeClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "restricted to pinned synthetic"):
                bucket.fetch_bucket1(Path(directory), client=real_client,
                                     synthetic_reference={"members": []})

    def test_prior_full_audit_sha_mismatch_stops_before_selected_output(self):
        tables, *_ = self.parts()
        archive = build_archive(tables)
        reference = self.reference(archive, tables)
        reference["members"][0]["source_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SHA256 differs from prior full audit"):
                bucket.fetch_bucket1(Path(directory), self.client(archive),
                                     expected_members=21, expected_rows=85,
                                     synthetic_reference=reference)
            self.assertFalse((Path(directory) / "instance_week_5min_bucket1.parquet").exists())

    def test_release_manifest_rechecks_code_and_results_hashes(self):
        with tempfile.TemporaryDirectory(prefix="bucket1_release_", dir=ROOT) as directory:
            temporary = Path(directory)
            files = {
                "protocol": bucket.PROTOCOL,
                "evaluation_script": temporary / "evaluate.py",
                "training_script": temporary / "evaluate.py",
                "development_tests": bucket.DEVELOPMENT_TESTS,
                "calibration_parameters": temporary / "calibration.json",
                "baseline_parameters": temporary / "baseline.json",
                "training_result": temporary / "train.json",
                "validation_result": temporary / "validation.json",
                "extractor": bucket.SCRIPT,
                "source_audit": bucket.SOURCE_AUDIT,
            }
            files["evaluation_script"].write_text("# frozen evaluation\n")
            files["calibration_parameters"].write_text('{"temperature": 1.0}\n')
            files["baseline_parameters"].write_text('{"prevalence": 0.2}\n')
            files["training_result"].write_text('{"fit": true}\n')
            model = temporary / "model.joblib"
            model.write_bytes(b"fitted model")
            fitted_hashes = {str(model.relative_to(ROOT)): bucket.source.sha256_file(model)}
            validation = {
                "validation_gate_pass": True,
                "protocol_sha256": bucket.source.sha256_file(files["protocol"]),
                "training_script_sha256": bucket.source.sha256_file(files["training_script"]),
                "evaluation_script_sha256": bucket.source.sha256_file(files["evaluation_script"]),
                "development_tests_sha256": bucket.source.sha256_file(files["development_tests"]),
                "training_result_sha256": bucket.source.sha256_file(files["training_result"]),
                "calibration_parameters_sha256": bucket.source.sha256_file(
                    files["calibration_parameters"]),
                "baseline_parameters_sha256": bucket.source.sha256_file(
                    files["baseline_parameters"]),
                "fitted_models_sha256": fitted_hashes,
                "dependency_versions": {name: "test-version" for name in
                                        ("python", "numpy", "pandas", "sklearn", "pyarrow")},
            }
            files["validation_result"].write_text(json.dumps(validation))
            manifest = {
                "status": bucket.RELEASE_STATUS,
                "source_url": bucket.source.URL,
                "source_size": bucket.source.EXPECTED_SIZE,
                "source_etag": bucket.source.EXPECTED_ETAG,
                "source_members": bucket.source.EXPECTED_MEMBERS,
                "source_rows": bucket.source.EXPECTED_ROWS,
                "id_bucket": 1, "bucket_modulus": 100,
                "validation_gate_pass": True,
                "artifacts": {
                    name: {"path": str(path.relative_to(ROOT)),
                           "sha256": bucket.source.sha256_file(path)}
                    for name, path in files.items()
                },
            }
            manifest["artifacts"]["fitted_models"] = [
                {"path": str(model.relative_to(ROOT)),
                 "sha256": bucket.source.sha256_file(model)}
            ]
            path = temporary / "release.json"
            path.write_text(json.dumps(manifest))
            self.assertEqual(bucket.verify_release_manifest(path)[0],
                             bucket.source.sha256_file(path))
            test_record = manifest["artifacts"].pop("development_tests")
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "missing development_tests path/hash"):
                bucket.verify_release_manifest(path)
            manifest["artifacts"]["development_tests"] = test_record
            original_test_hash = manifest["artifacts"]["development_tests"]["sha256"]
            manifest["artifacts"]["development_tests"]["sha256"] = "0" * 64
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "development_tests changed"):
                bucket.verify_release_manifest(path)
            manifest["artifacts"]["development_tests"]["sha256"] = original_test_hash
            validation["development_tests_sha256"] = "0" * 64
            files["validation_result"].write_text(json.dumps(validation))
            manifest["artifacts"]["validation_result"]["sha256"] = bucket.source.sha256_file(
                files["validation_result"])
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "does not link to frozen code"):
                bucket.verify_release_manifest(path)
            validation["development_tests_sha256"] = original_test_hash
            files["validation_result"].write_text(json.dumps(validation))
            manifest["artifacts"]["validation_result"]["sha256"] = bucket.source.sha256_file(
                files["validation_result"])
            path.write_text(json.dumps(manifest))
            files["evaluation_script"].write_text("# changed after freeze\n")
            with self.assertRaisesRegex(ValueError, "training_script changed"):
                bucket.verify_release_manifest(path)
            files["evaluation_script"].write_text("# frozen evaluation\n")
            model.write_bytes(b"changed model")
            with self.assertRaisesRegex(ValueError, "fitted_models\\[0\\] changed"):
                bucket.verify_release_manifest(path)
            model.write_bytes(b"fitted model")
            validation["validation_gate_pass"] = False
            files["validation_result"].write_text(json.dumps(validation))
            manifest["artifacts"]["validation_result"]["sha256"] = bucket.source.sha256_file(
                files["validation_result"])
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "has not passed"):
                bucket.verify_release_manifest(path)


if __name__ == "__main__":
    unittest.main()
