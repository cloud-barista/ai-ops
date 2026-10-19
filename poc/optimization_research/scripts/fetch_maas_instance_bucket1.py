"""Prepare a disjoint MaaS instance-ID bucket without opening the real holdout by default.

Real HTTP access is CLI-gated by a reviewed release manifest. The extractor
reuses the pinned ZIP range reader, CRC32/sha256 verification, and resume logic
from fetch_maas_instance_sample.py. Each source member is discarded before the
next is read. Original instance IDs are never written to output artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import fetch_maas_instance_sample as source


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
DEFAULT_OUTPUT = ROOT / "data/external/alibaba_maas_2026_bucket1"
PROTOCOL = ROOT / "reports/maas_sm_observability_protocol_v1.md"
DEVELOPMENT_TESTS = ROOT / "tests/test_evaluate_maas_sm_observability_v1.py"
SOURCE_AUDIT = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_audit.json"
PROTOCOL_SHA256 = "79e82f9a03093b8423097cd6e8f628a58513effaddb12f5d587a5bc11b56774b"
SOURCE_AUDIT_SHA256 = "3a24d64122dcf8e1fd9f68627acbd70dd878924483b29c334ae74a612e75cb88"
BUCKET = 1
TEMP_LIMIT_BYTES = 500_000_000
RELEASE_STATUS = "FROZEN_BEFORE_BUCKET1_ACCESS"
ARTIFACT_KEYS = ("protocol", "training_script", "evaluation_script", "development_tests",
                 "calibration_parameters", "baseline_parameters", "training_result", "validation_result",
                 "extractor", "source_audit")
STATE_HOLDOUT_PURPOSE = "maas_existing_state_model_new_id"
STATE_HOLDOUT_KEYS = ("protocol", "scorer", "scorer_tests", "extractor", "source_audit",
                      "state_metrics", "state_predictions", "state_cohort_rows",
                      "original_metrics", "pooled_model", "normal_model", "bucket0_selfcheck",
                      "state_script", "original_script", "source_fetcher")
SCHEMA = source.OUTPUT_SCHEMA


def id_digest_and_bucket(value: object) -> tuple[str, int]:
    normalized = source.normalize_id(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return digest.hex(), int.from_bytes(digest[:8], "big") % 100


def _verified_local_artifact(root: Path, record: object, label: str) -> tuple[Path, str]:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise ValueError(f"release manifest missing {label} path/hash")
    relative = Path(str(record["path"]))
    if relative.is_absolute():
        raise ValueError(f"{label} path must be relative to research root")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f"{label} path escapes or is missing from research root")
    digest = source.sha256_file(target)
    if digest != record["sha256"]:
        raise ValueError(f"{label} changed after release freeze")
    return target, digest


def _verify_state_model_release(manifest: dict, path: Path, root: Path) -> tuple[str, dict]:
    """New, separately declared purpose; the failed SM-study release stays closed."""
    expected = {
        "purpose": STATE_HOLDOUT_PURPOSE,
        "status": RELEASE_STATUS,
        "source_url": source.URL,
        "source_size": source.EXPECTED_SIZE,
        "source_etag": source.EXPECTED_ETAG,
        "source_members": source.EXPECTED_MEMBERS,
        "source_rows": source.EXPECTED_ROWS,
        "id_bucket": BUCKET,
        "bucket_modulus": 100,
        "bucket0_scores_exposed_before_design": True,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("state-model release metadata changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(STATE_HOLDOUT_KEYS):
        raise ValueError("state-model release artifacts missing or unexpected")
    observed = {key: _verified_local_artifact(root, artifacts[key], key)
                for key in STATE_HOLDOUT_KEYS}
    expected_paths = {
        "protocol": "reports/maas_existing_model_new_id_protocol.md",
        "scorer": "scripts/evaluate_maas_existing_model_new_id.py",
        "scorer_tests": "tests/test_evaluate_maas_existing_model_new_id.py",
        "extractor": "scripts/fetch_maas_instance_bucket1.py",
        "source_audit": "data/external/alibaba_maas_2026/instance_week_5min_audit.json",
        "state_metrics": "artifacts/maas_state_followup_v1/metrics.json",
        "state_predictions": "artifacts/maas_state_followup_v1/test_predictions.parquet",
        "state_cohort_rows": "artifacts/maas_state_followup_v1/cohort_rows.parquet",
        "original_metrics": "artifacts/maas_instance_risk_v1/metrics.json",
        "pooled_model": "artifacts/maas_state_followup_v1/new_pooled.joblib",
        "normal_model": "artifacts/maas_state_followup_v1/new_state_full_normal.joblib",
        "bucket0_selfcheck": "artifacts/maas_existing_model_new_id_v1/bucket0_selfcheck.json",
        "state_script": "scripts/evaluate_maas_state_followup.py",
        "original_script": "scripts/evaluate_maas_instance_risk.py",
        "source_fetcher": "scripts/fetch_maas_instance_sample.py",
    }
    if any(observed[key][0] != (root / relative).resolve()
           for key, relative in expected_paths.items()):
        raise ValueError("state-model release points to a different local artifact")
    if observed["source_audit"][1] != SOURCE_AUDIT_SHA256:
        raise ValueError("state-model release source audit changed")
    state = json.loads(observed["state_metrics"][0].read_text(encoding="utf-8"))
    parity = json.loads(observed["bucket0_selfcheck"][0].read_text(encoding="utf-8"))
    if (state.get("test_predictions_sha256") != observed["state_predictions"][1]
            or state.get("cohort_rows_sha256") != observed["state_cohort_rows"][1]
            or state.get("fitted_models", {}).get("new_pooled", {}).get("sha256")
                != observed["pooled_model"][1]
            or state.get("fitted_models", {}).get("new_state_full_normal", {}).get("sha256")
                != observed["normal_model"][1]
            or state.get("source_hashes", {}).get("original_metrics_sha256")
                != observed["original_metrics"][1]
            or parity.get("status") != "PARITY_PASS"
            or parity.get("state_metrics_sha256") != observed["state_metrics"][1]
            or parity.get("state_predictions_sha256") != observed["state_predictions"][1]
            or parity.get("state_cohort_rows_sha256") != observed["state_cohort_rows"][1]
            or parity.get("pooled_model_sha256") != observed["pooled_model"][1]
            or parity.get("normal_model_sha256") != observed["normal_model"][1]
            or parity.get("scorer_sha256") != observed["scorer"][1]
            or state.get("script_sha256") != observed["state_script"][1]
            or max(parity.get("maximum_absolute_errors", {}).values(), default=float("inf")) > 1e-12
            or parity.get("normal_beta_rates") != {
                "0": 0.033061253175105165, "1": 0.7215373459028281}
            or parity.get("rows") != 53_513):
        raise ValueError("state-model fit, predictions, or parity evidence changed")
    reference = json.loads(observed["source_audit"][0].read_text(encoding="utf-8"))
    if (not reference.get("integrity_pass") or not reference.get("all_members_verified")
            or not reference.get("schema_pass")
            or reference.get("totals", {}).get("row_count") != source.EXPECTED_ROWS
            or len(reference.get("members", [])) != source.EXPECTED_MEMBERS):
        raise ValueError("state-model release source audit is incomplete")
    return source.sha256_file(path), reference


def verify_release_manifest(path: Path, root: Path = ROOT) -> tuple[str, dict]:
    """Fail before network if protocol, code, fit, or validation changed."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("purpose") == STATE_HOLDOUT_PURPOSE:
        return _verify_state_model_release(manifest, path, root)
    expected = {
        "status": RELEASE_STATUS,
        "source_url": source.URL,
        "source_size": source.EXPECTED_SIZE,
        "source_etag": source.EXPECTED_ETAG,
        "source_members": source.EXPECTED_MEMBERS,
        "source_rows": source.EXPECTED_ROWS,
        "id_bucket": BUCKET,
        "bucket_modulus": 100,
        "validation_gate_pass": True,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"release manifest {key} is missing or different")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("release manifest lacks frozen artifacts")
    observed = {}
    for key in ARTIFACT_KEYS:
        observed[key] = _verified_local_artifact(root, artifacts.get(key), key)
    model_records = artifacts.get("fitted_models")
    if not isinstance(model_records, list) or not model_records:
        raise ValueError("release manifest lacks fitted model files")
    fitted = {}
    for index, record in enumerate(model_records):
        target, digest = _verified_local_artifact(root, record, f"fitted_models[{index}]")
        relative = str(target.relative_to(root.resolve()))
        if relative in fitted:
            raise ValueError("release manifest repeats a fitted model file")
        fitted[relative] = digest
    if observed["extractor"][0] != SCRIPT.resolve():
        raise ValueError("release manifest names a different extractor")
    if observed["protocol"] != (PROTOCOL.resolve(), PROTOCOL_SHA256):
        raise ValueError("release manifest names a different frozen SM protocol")
    if observed["development_tests"][0] != DEVELOPMENT_TESTS.resolve():
        raise ValueError("release manifest names different SM development tests")
    if observed["source_audit"][0] != SOURCE_AUDIT.resolve():
        raise ValueError("release manifest names a different 21-member source audit")
    if observed["source_audit"][1] != SOURCE_AUDIT_SHA256:
        raise ValueError("release manifest source audit differs from reviewed SHA256")
    if observed["evaluation_script"][0] == observed["extractor"][0]:
        raise ValueError("evaluation script must be separate from extractor")
    training = json.loads(observed["training_result"][0].read_text(encoding="utf-8"))
    validation = json.loads(observed["validation_result"][0].read_text(encoding="utf-8"))
    if validation.get("validation_gate_pass") is not True:
        raise ValueError("validation result has not passed its frozen gate")
    if not training or not validation:
        raise ValueError("fit/validation result is empty")
    result_links = {
        "protocol_sha256": observed["protocol"][1],
        "training_script_sha256": observed["training_script"][1],
        "evaluation_script_sha256": observed["evaluation_script"][1],
        "development_tests_sha256": observed["development_tests"][1],
        "training_result_sha256": observed["training_result"][1],
        "calibration_parameters_sha256": observed["calibration_parameters"][1],
        "baseline_parameters_sha256": observed["baseline_parameters"][1],
        "fitted_models_sha256": fitted,
    }
    if any(validation.get(key) != value for key, value in result_links.items()):
        raise ValueError("validation result does not link to frozen code, fit, or parameter files")
    versions = validation.get("dependency_versions")
    if not isinstance(versions, dict) or any(
            not isinstance(versions.get(name), str) or not versions[name]
            for name in ("python", "numpy", "pandas", "sklearn", "pyarrow")):
        raise ValueError("validation result lacks dependency versions")
    reference = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    expected_audit = {
        "source_url": source.URL, "source_size": source.EXPECTED_SIZE,
        "source_etag": source.EXPECTED_ETAG,
        "member_count": source.EXPECTED_MEMBERS,
        "integrity_pass": True, "all_members_verified": True, "schema_pass": True,
    }
    if any(reference.get(key) != value for key, value in expected_audit.items()):
        raise ValueError("pinned source audit integrity or provenance failed")
    if reference.get("totals", {}).get("row_count") != source.EXPECTED_ROWS:
        raise ValueError("pinned source audit row count changed")
    if len(reference.get("members", [])) != source.EXPECTED_MEMBERS:
        raise ValueError("pinned source audit member list changed")
    return source.sha256_file(path), reference


def _reference_members(reference: dict, expected_members: int,
                       expected_rows: int) -> dict[str, dict]:
    records = reference.get("members")
    if not isinstance(records, list) or len(records) != expected_members:
        raise ValueError("reference audit lacks all members")
    indexed = {}
    for record in records:
        name = record.get("name") if isinstance(record, dict) else None
        digest = record.get("source_sha256") if isinstance(record, dict) else None
        if (not isinstance(name, str) or not source.MEMBER_NAME.fullmatch(name)
                or name in indexed or not isinstance(digest, str) or len(digest) != 64):
            raise ValueError("reference audit has missing or duplicate member hashes")
        indexed[name] = record
    if sum(int(record.get("row_count", -1)) for record in records) != expected_rows:
        raise ValueError("reference audit member row counts differ from source")
    return indexed


def _check_reference_member(member: source.ZipMember, reference: dict) -> None:
    expected = {
        "name": member.name, "source_byte_offset": member.data_offset,
        "source_size": member.size, "zip_crc32": f"{member.crc32:08x}",
    }
    if any(reference.get(key) != value for key, value in expected.items()):
        raise ValueError(f"source member differs from prior full audit: {member.name}")


def _check_temp_budget(work: Path) -> None:
    used = sum(path.stat().st_size for path in work.iterdir() if path.is_file())
    if used >= TEMP_LIMIT_BYTES:
        raise OSError("bucket1 transient files reached the 0.5 GB cap")


def _member_output_paths(output: Path, member: source.ZipMember) -> tuple[Path, Path]:
    stem = Path(member.name).stem
    return output / "shards" / f"{stem}.bucket1.parquet", output / "shards" / f"{stem}.bucket1.audit.json"


def extract_member(raw_path: Path, member: source.ZipMember, output: Path,
                   source_sha256: str, range_headers: list[dict],
                   client: source.RangeClient) -> dict:
    """Write every bucket-1 raw telemetry row, including duplicates and nulls."""
    parquet = pq.ParquetFile(raw_path)
    source._validate_schema(parquet.schema_arrow)
    shard, audit_path = _member_output_paths(output, member)
    temporary = output / "work" / "current_selected.tmp.parquet"
    selected_ids: set[str] = set()
    counters = {"row_count": 0, "selected_row_count": 0, "bucket0_row_count": 0,
                "time_mod_300": Counter(), "day_counts": Counter()}
    writer = pq.ParquetWriter(temporary, SCHEMA, compression="zstd")
    try:
        for batch in parquet.iter_batches(batch_size=65_536, columns=source.REQUIRED):
            counters["row_count"] += batch.num_rows
            columns = {name: batch.column(batch.schema.get_field_index(name))
                       for name in source.REQUIRED}
            ids = columns["instance_id"].to_pylist()
            times = columns["time_s"].to_pylist()
            indices, hashes, ties = [], [], []
            for index, raw_id in enumerate(ids):
                if raw_id is None or times[index] is None:
                    raise ValueError("source lacks instance ID or time; cannot assign complete bucket")
                normalized = source.normalize_id(raw_id)
                digest, bucket = id_digest_and_bucket(normalized)
                counters["bucket0_row_count"] += int(bucket == 0)
                if bucket != BUCKET:
                    continue
                indices.append(index)
                hashes.append(digest)
                selected_ids.add(digest)
                ties.append(hashlib.sha256(f"{normalized}:{int(times[index]) + 600}".encode("utf-8")).hexdigest())
            numbers = np.asarray(times, dtype=np.int64)
            residues, residue_counts = np.unique(numbers % 300, return_counts=True)
            days, day_counts = np.unique(numbers // 86400, return_counts=True)
            counters["time_mod_300"].update({str(int(k)): int(v) for k, v in zip(residues, residue_counts)})
            counters["day_counts"].update({str(int(k)): int(v) for k, v in zip(days, day_counts)})
            if not indices:
                continue
            take = pa.array(indices, type=pa.int32())
            values = [
                pa.array(hashes, type=pa.string()),
                pc.cast(pc.take(columns["time_s"], take), pa.int64()),
                pc.cast(pc.take(columns["cluster_id"], take), pa.string()),
                pc.cast(pc.take(columns["gpu_count"], take), pa.float64()),
                pc.cast(pc.take(columns["sm_util"], take), pa.float64()),
                pc.cast(pc.take(columns["gpu_mem_util"], take), pa.float64()),
                pc.cast(pc.take(columns["samples"], take), pa.float64()),
                pa.array(ties, type=pa.string()),
            ]
            writer.write_table(pa.Table.from_arrays(values, schema=SCHEMA))
            counters["selected_row_count"] += len(indices)
            _check_temp_budget(output / "work")
    finally:
        writer.close()
    if counters["row_count"] != parquet.metadata.num_rows:
        raise ValueError("Parquet member row count changed during extraction")
    _check_temp_budget(output / "work")
    audit = {
        "name": member.name,
        "source_url": client.url, "source_archive_size": client.size,
        "source_etag": client.etag,
        "source_byte_offset": member.data_offset, "source_size": member.size,
        "source_sha256": source_sha256, "zip_crc32": f"{member.crc32:08x}",
        "http_ranges": range_headers,
        "source_schema": [{"name": f.name, "type": str(f.type)} for f in parquet.schema_arrow],
        "bucket": BUCKET,
        "sample_rule": "SHA256(UTF-8 normalized instance_id)[:8] big-endian mod 100 == 1",
        "sample_sha256": source.sha256_file(temporary),
        "selected_unique_ids_in_member": len(selected_ids),
        **{key: dict(value) if isinstance(value, Counter) else value
           for key, value in counters.items()},
    }
    os.replace(temporary, shard)
    source.atomic_json(audit_path, audit)
    return audit


def _verify_saved_shard(output: Path, member: source.ZipMember,
                        client: source.RangeClient, reference: dict) -> dict:
    shard, audit_path = _member_output_paths(output, member)
    if shard.exists() != audit_path.exists():
        raise ValueError(f"incomplete saved bucket1 shard: {member.name}")
    if not shard.exists():
        return {}
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    source.validate_saved_member_source(audit, member, client)
    expected = {"name": member.name, "source_byte_offset": member.data_offset,
                "source_size": member.size, "zip_crc32": f"{member.crc32:08x}",
                "bucket": BUCKET}
    if any(audit.get(key) != value for key, value in expected.items()):
        raise ValueError(f"saved bucket1 source metadata changed: {member.name}")
    if (audit.get("source_sha256") != reference["source_sha256"]
            or audit.get("row_count") != reference["row_count"]):
        raise ValueError(f"saved bucket1 source differs from prior full audit: {member.name}")
    if source.sha256_file(shard) != audit.get("sample_sha256"):
        raise ValueError(f"saved bucket1 shard hash changed: {member.name}")
    return audit


def _combine_and_verify(output: Path, members: list[source.ZipMember],
                        audits: list[dict], expected_rows: int) -> dict:
    total = sum(int(a["row_count"]) for a in audits)
    if total != expected_rows:
        raise ValueError(f"source row count differs from pinned total: {total}")
    if len({json.dumps(a["source_schema"], sort_keys=True) for a in audits}) != 1:
        raise ValueError("source Parquet schema differs across shards")
    residues = set().union(*(a["time_mod_300"] for a in audits))
    days = set().union(*(a["day_counts"] for a in audits))
    if residues != {"0"} or days != {str(day) for day in range(140, 147)}:
        raise ValueError("source time domain differs from official one-week trace")
    combined = output / "instance_week_5min_bucket1.parquet"
    temporary = output / "instance_week_5min_bucket1.tmp.parquet"
    if combined.exists():
        raise ValueError("combined bucket1 file already exists without completed audit")
    unique_ids: set[str] = set()
    selected_rows = 0
    writer = pq.ParquetWriter(temporary, SCHEMA, compression="zstd")
    try:
        for member, audit in zip(members, audits):
            shard, _ = _member_output_paths(output, member)
            parquet = pq.ParquetFile(shard)
            if not parquet.schema_arrow.equals(SCHEMA):
                raise ValueError(f"bucket1 shard schema changed: {member.name}")
            if parquet.metadata.num_rows != audit["selected_row_count"]:
                raise ValueError(f"bucket1 shard row count changed: {member.name}")
            for batch in parquet.iter_batches(batch_size=65_536):
                hashes = batch.column(0).to_pylist()
                for digest in hashes:
                    if not isinstance(digest, str) or len(digest) != 64 or int.from_bytes(
                            bytes.fromhex(digest)[:8], "big") % 100 != BUCKET:
                        raise ValueError("non-bucket1 ID entered selected shard")
                    unique_ids.add(digest)
                writer.write_table(pa.Table.from_batches([batch], schema=SCHEMA))
                selected_rows += batch.num_rows
    finally:
        writer.close()
    if selected_rows != sum(int(a["selected_row_count"]) for a in audits):
        raise ValueError("selected row completeness mismatch")
    digest = source.sha256_file(temporary)
    os.replace(temporary, combined)
    return {"source_rows": total, "selected_rows": selected_rows,
            "selected_unique_ids": len(unique_ids),
            "bucket0_rows_seen_and_excluded": sum(a["bucket0_row_count"] for a in audits),
            "compact_sha256": digest, "compact_size": combined.stat().st_size}


def fetch_bucket1(output_dir: Path = DEFAULT_OUTPUT,
                  client: source.RangeClient | None = None,
                  expected_members: int = source.EXPECTED_MEMBERS,
                  expected_rows: int = source.EXPECTED_ROWS,
                  chunk_size: int = source.RANGE_CHUNK,
                  release_manifest: Path | None = None,
                  synthetic_reference: dict | None = None) -> dict:
    """Run only with a synthetic injected client or a validated CLI manifest."""
    if client is None:
        if release_manifest is None:
            raise ValueError("real bucket1 access requires a validated release manifest")
        release_manifest_sha256, reference_audit = verify_release_manifest(release_manifest)
        if (expected_members != source.EXPECTED_MEMBERS or expected_rows != source.EXPECTED_ROWS):
            raise ValueError("real source may not override pinned member/row counts")
        client = source.RangeClient()
    else:
        if (client.url != "https://test.invalid/maas.zip" or release_manifest is not None
                or synthetic_reference is None):
            raise ValueError("injected client is restricted to pinned synthetic test data")
        release_manifest_sha256 = None
        reference_audit = synthetic_reference
    references = _reference_members(reference_audit, expected_members, expected_rows)
    if output_dir.resolve() == (ROOT / "data/external/alibaba_maas_2026").resolve():
        raise ValueError("refusing to write bucket1 into bucket0 source directory")
    if not 0 < chunk_size <= source.RANGE_CHUNK:
        raise ValueError("invalid range chunk size")
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / "work"
    shards = output_dir / "shards"
    work.mkdir(exist_ok=True)
    shards.mkdir(exist_ok=True)
    audit_path = output_dir / "instance_week_5min_bucket1_audit.json"
    failure_path = output_dir / "instance_week_5min_bucket1_failed.json"
    combined = output_dir / "instance_week_5min_bucket1.parquet"
    if audit_path.exists():
        result = json.loads(audit_path.read_text(encoding="utf-8"))
        if (result.get("release_manifest_sha256") != release_manifest_sha256
                or not result.get("integrity_pass") or not result.get("all_members_verified")
                or source.sha256_file(combined) != result.get("compact_sha256")):
            raise ValueError("completed bucket1 extraction has changed")
        return result
    if combined.exists():
        raise ValueError("combined bucket1 data exists without success audit")
    try:
        client.head()
        members = source.read_directory(client, expected_members)
        observed_parts = {int(Path(member.name).stem.rsplit("_", 1)[1]) for member in members}
        if observed_parts != set(range(expected_members)):
            raise ValueError("source member sequence is incomplete")
        audits = []
        for index, item in enumerate(members, 1):
            member = source.locate_member(client, item)
            if member.name not in references:
                raise ValueError(f"member absent from prior full audit: {member.name}")
            reference = references[member.name]
            _check_reference_member(member, reference)
            saved = _verify_saved_shard(output_dir, member, client, reference)
            if saved:
                audits.append(saved)
                progress = work / "current_member.progress.json"
                if progress.exists() and json.loads(progress.read_text())["name"] == member.name:
                    (work / "current_member.part").unlink(missing_ok=True)
                    progress.unlink()
                continue
            if member.size + 200_000_000 >= TEMP_LIMIT_BYTES:
                raise OSError("member plus selected scratch may exceed 0.5 GB")
            if shutil.disk_usage(work).free < TEMP_LIMIT_BYTES:
                raise OSError("less than 0.5 GB disk available for bucket1 scratch")
            raw_path, raw_digest, ranges = source.fetch_member(client, member, work, chunk_size)
            if raw_digest != reference["source_sha256"]:
                raise ValueError(f"member SHA256 differs from prior full audit: {member.name}")
            _check_temp_budget(work)
            audit = extract_member(raw_path, member, output_dir, raw_digest, ranges, client)
            if audit["row_count"] != reference["row_count"]:
                raise ValueError(f"member row count differs from prior full audit: {member.name}")
            raw_path.unlink()
            (work / "current_member.progress.json").unlink()
            audits.append(audit)
            print(f"[{index}/{len(members)}] verified and selected {member.name}", flush=True)
        result = _combine_and_verify(output_dir, members, audits, expected_rows)
        report = {
            "source_url": client.url, "source_size": client.size,
            "source_etag": client.etag, "source_members": len(members),
            "source_rows": result["source_rows"],
            "id_bucket": BUCKET, "bucket_modulus": 100,
            "release_manifest_sha256": release_manifest_sha256,
            "prior_full_source_audit_sha256": (SOURCE_AUDIT_SHA256
                                                if release_manifest_sha256 is not None else None),
            "integrity_pass": True, "all_members_verified": True,
            "member_audits": audits, **result,
        }
        source.atomic_json(audit_path, report)
        failure_path.unlink(missing_ok=True)
        return report
    except Exception as exc:
        source.atomic_json(failure_path, {
            "integrity_pass": False, "source_url": client.url,
            "source_size": client.size, "source_etag": client.etag,
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="allow real source access after release checks")
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.fetch:
        print("No network access. After protocol/model/validation freeze, pass --fetch and --release-manifest.")
        return
    if args.release_manifest is None:
        parser.error("--fetch requires --release-manifest")
    result = fetch_bucket1(args.output_dir, release_manifest=args.release_manifest)
    print(json.dumps({key: result[key] for key in
                      ("source_rows", "selected_rows", "selected_unique_ids", "compact_sha256")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
