"""Extract a pinned, disjoint MaaS instance-ID bucket after release freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import fetch_maas_instance_sample as source


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/external/alibaba_maas_2026_bucket2"
TRIAL = ROOT / "artifacts/maas_state_day_calibration_holdout_v1"
RELEASE = TRIAL / "release_manifest.json"
ORIGINAL_AUDIT = ROOT / "data/external/alibaba_maas_2026/instance_week_5min_audit.json"
AUDIT = OUT / "instance_week_5min_bucket2_audit.json"
COMPACT = OUT / "instance_week_5min_bucket2.parquet"
BUCKET = 2
SOURCE_AUDIT_SHA256 = "3a24d64122dcf8e1fd9f68627acbd70dd878924483b29c334ae74a612e75cb88"
ARTIFACTS = {
    "protocol": "reports/maas_state_day_calibration_new_id_protocol.md",
    "development_report": "reports/maas_state_day_calibration_development.md",
    "development_result": "artifacts/maas_state_day_calibration_dev_v1/results.json",
    "development_script": "scripts/explore_maas_state_day_calibration.py",
    "scorer": "scripts/evaluate_maas_state_day_calibration_holdout.py",
    "scorer_tests": "tests/test_evaluate_maas_state_day_calibration_holdout.py",
    "extractor": "scripts/fetch_maas_instance_bucket2.py",
    "extractor_tests": "tests/test_fetch_maas_instance_bucket2.py",
    "source_fetcher": "scripts/fetch_maas_instance_sample.py",
    "source_audit": "data/external/alibaba_maas_2026/instance_week_5min_audit.json",
    "state_script": "scripts/evaluate_maas_state_followup.py",
    "original_script": "scripts/evaluate_maas_instance_risk.py",
    "state_metrics": "artifacts/maas_state_followup_v1/metrics.json",
    "state_rows": "artifacts/maas_state_followup_v1/cohort_rows.parquet",
    "state_predictions": "artifacts/maas_state_followup_v1/test_predictions.parquet",
    "pooled_model": "artifacts/maas_state_followup_v1/new_pooled.joblib",
    "bucket1_audit": "data/external/alibaba_maas_2026_bucket1/instance_week_5min_bucket1_audit.json",
    "bucket1_raw": "data/external/alibaba_maas_2026_bucket1/instance_week_5min_bucket1.parquet",
    "bucket1_result": "artifacts/maas_existing_model_new_id_v1/results.json",
    "bucket1_predictions": "artifacts/maas_existing_model_new_id_v1/test_predictions.parquet",
    "preflight": "artifacts/maas_state_day_calibration_holdout_v1/preflight.json",
}


def bucket_for_id(value: object) -> tuple[str, int, str]:
    normalized = source.normalize_id(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return digest.hex(), int.from_bytes(digest[:8], "big") % 100, normalized


def verify_release(path: Path = RELEASE) -> tuple[str, dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "status": "FROZEN_BEFORE_BUCKET2_ACCESS",
        "purpose": "maas_state_day_calibration_new_id",
        "bucket": BUCKET,
        "bucket_modulus": 100,
        "source_url": source.URL,
        "source_size": source.EXPECTED_SIZE,
        "source_etag": source.EXPECTED_ETAG,
        "source_members": source.EXPECTED_MEMBERS,
        "source_rows": source.EXPECTED_ROWS,
        "bucket0_and_bucket1_scores_exposed_before_design": True,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("bucket2 release metadata changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACTS):
        raise ValueError("bucket2 release artifact set differs")
    hashes = {}
    for key, relative in ARTIFACTS.items():
        record = artifacts[key]
        if not isinstance(record, dict) or record.get("path") != relative:
            raise ValueError(f"bucket2 release {key} path differs")
        path_to_file = ROOT / relative
        if not path_to_file.is_file():
            raise ValueError(f"bucket2 release {key} missing")
        digest = source.sha256_file(path_to_file)
        if record.get("sha256") != digest:
            raise ValueError(f"bucket2 release {key} changed")
        hashes[key] = digest
    reference = json.loads(ORIGINAL_AUDIT.read_text(encoding="utf-8"))
    if (hashes["source_audit"] != SOURCE_AUDIT_SHA256
            or not reference.get("integrity_pass") or not reference.get("all_members_verified")
            or not reference.get("schema_pass")
            or reference.get("totals", {}).get("row_count") != source.EXPECTED_ROWS
            or len(reference.get("members", [])) != source.EXPECTED_MEMBERS):
        raise ValueError("source 21-member audit incomplete")
    trial = json.loads((ROOT / ARTIFACTS["development_result"]).read_text(encoding="utf-8"))
    state = json.loads((ROOT / ARTIFACTS["state_metrics"]).read_text(encoding="utf-8"))
    earlier = json.loads((ROOT / ARTIFACTS["bucket1_result"]).read_text(encoding="utf-8"))
    preflight = json.loads((ROOT / ARTIFACTS["preflight"]).read_text(encoding="utf-8"))
    if (trial.get("status") != "EXPOSED_COHORT_DEVELOPMENT_ONLY"
            or trial.get("bucket1_raw_sha256") != hashes["bucket1_raw"]
            or trial.get("model_sha256") != hashes["pooled_model"]
            or state.get("fitted_models", {}).get("new_pooled", {}).get("sha256")
                != hashes["pooled_model"]
            or state.get("cohort_rows_sha256") != hashes["state_rows"]
            or state.get("test_predictions_sha256") != hashes["state_predictions"]
            or earlier.get("predictions_sha256") != hashes["bucket1_predictions"]
            or preflight.get("status") != "PREFLIGHT_PASS"
            or preflight.get("scorer_sha256") != hashes["scorer"]
            or preflight.get("development_result_sha256") != hashes["development_result"]
            or preflight.get("model_sha256") != hashes["pooled_model"]
            or preflight.get("saved_prediction_max_abs_error", 1) > 1e-12):
        raise ValueError("fitted model, development, or preflight evidence differs")
    return source.sha256_file(path), reference


def member_reference(reference: dict) -> dict[str, dict]:
    indexed = {}
    for record in reference["members"]:
        name = record["name"]
        if name in indexed or not source.MEMBER_NAME.fullmatch(name):
            raise ValueError("invalid source member audit")
        indexed[name] = record
    if len(indexed) != source.EXPECTED_MEMBERS:
        raise ValueError("source member count changed")
    return indexed


def select_member(raw: Path, member: source.ZipMember, selected: Path) -> tuple[int, int]:
    parquet = pq.ParquetFile(raw)
    source._validate_schema(parquet.schema_arrow)
    rows = chosen = 0
    writer = pq.ParquetWriter(selected, source.OUTPUT_SCHEMA, compression="zstd")
    try:
        for batch in parquet.iter_batches(batch_size=65_536, columns=source.REQUIRED):
            rows += batch.num_rows
            columns = {name: batch.column(batch.schema.get_field_index(name))
                       for name in source.REQUIRED}
            ids = columns["instance_id"].to_pylist()
            times = columns["time_s"].to_pylist()
            indices, hashes, ties = [], [], []
            for index, value in enumerate(ids):
                if value is None or times[index] is None:
                    raise ValueError("source ID or time missing")
                digest, bucket, normalized = bucket_for_id(value)
                if bucket == BUCKET:
                    indices.append(index)
                    hashes.append(digest)
                    ties.append(hashlib.sha256(
                        f"{normalized}:{int(times[index]) + 600}".encode("utf-8")).hexdigest())
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
            writer.write_table(pa.Table.from_arrays(values, schema=source.OUTPUT_SCHEMA))
            chosen += len(indices)
    finally:
        writer.close()
    if rows != parquet.metadata.num_rows:
        raise ValueError("Parquet member row count changed during bucket2 selection")
    return rows, chosen


def fetch(*, release_manifest: Path = RELEASE, output: Path = OUT,
          client: source.RangeClient | None = None, reference: dict | None = None,
          expected_members: int = source.EXPECTED_MEMBERS,
          expected_rows: int = source.EXPECTED_ROWS) -> dict:
    if client is None:
        release_hash, reference = verify_release(release_manifest)
        client = source.RangeClient()
    else:
        if client.url != "https://test.invalid/maas.zip" or reference is None:
            raise ValueError("injected client must be synthetic")
        release_hash = None
    if output.resolve() == (ROOT / "data/external/alibaba_maas_2026").resolve():
        raise ValueError("refusing to overwrite bucket0")
    output.mkdir(parents=True, exist_ok=True)
    work = output / "work"
    shards = output / "shards"
    work.mkdir(exist_ok=True)
    shards.mkdir(exist_ok=True)
    if AUDIT.exists() and output == OUT:
        completed = json.loads(AUDIT.read_text(encoding="utf-8"))
        if (completed.get("release_manifest_sha256") != release_hash
                or source.sha256_file(COMPACT) != completed.get("compact_sha256")):
            raise ValueError("completed bucket2 extraction changed")
        return completed
    client.head()
    members = source.read_directory(client, expected_members=expected_members)
    records = member_reference(reference)
    audits = []
    for index, listed in enumerate(members, 1):
        item = source.locate_member(client, listed)
        record = records[item.name]
        if any(record.get(field) != value for field, value in {
                "name": item.name, "source_byte_offset": item.data_offset,
                "source_size": item.size, "zip_crc32": f"{item.crc32:08x}"}.items()):
            raise ValueError("source member directory differs from full audit")
        shard = shards / (Path(item.name).stem + ".bucket2.parquet")
        shard_audit = shard.with_suffix(".audit.json")
        if shard.exists() and shard_audit.exists():
            saved = json.loads(shard_audit.read_text(encoding="utf-8"))
            if (saved["name"] != item.name
                    or saved["source_sha256"] != record["source_sha256"]
                    or saved["selected_sha256"] != source.sha256_file(shard)
                    or saved["source_rows"] != record["row_count"]
                    or pq.read_metadata(shard).num_rows != saved["selected_rows"]):
                raise ValueError("saved bucket2 shard differs")
            audits.append(saved)
            continue
        if shard.exists() or shard_audit.exists():
            raise ValueError("incomplete bucket2 shard")
        if shutil.disk_usage(work).free < 500_000_000:
            raise OSError("less than 0.5 GB free for bucket2 member")
        raw_path, digest, ranges = source.fetch_member(client, item, work)
        if digest != record["source_sha256"]:
            raise ValueError("source member SHA256 differs from full audit")
        temporary = shard.with_suffix(".tmp.parquet")
        try:
            n, chosen = select_member(raw_path, item, temporary)
            if n != record["row_count"]:
                raise ValueError("source member row count differs")
            temporary.replace(shard)
            saved = {"name": item.name, "source_sha256": digest,
                     "source_rows": n, "selected_rows": chosen,
                     "selected_sha256": source.sha256_file(shard),
                     "range_count": len(ranges)}
            source.atomic_json(shard_audit, saved)
        finally:
            raw_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            (work / "current_member.progress.json").unlink(missing_ok=True)
        audits.append(saved)
        print(f"[{index}/{len(members)}] verified and selected {item.name}", flush=True)
    if sum(item["source_rows"] for item in audits) != expected_rows:
        raise ValueError("official source total changed")
    combined = output / "instance_week_5min_bucket2.parquet"
    tmp_combined = output / "instance_week_5min_bucket2.tmp.parquet"
    if combined.exists():
        raise ValueError("bucket2 combined file exists without valid final audit")
    writer = pq.ParquetWriter(tmp_combined, source.OUTPUT_SCHEMA, compression="zstd")
    try:
        for item in members:
            shard = shards / (Path(item.name).stem + ".bucket2.parquet")
            for batch in pq.ParquetFile(shard).iter_batches(batch_size=65_536):
                writer.write_batch(batch)
    finally:
        writer.close()
    tmp_combined.replace(combined)
    selected = pd.read_parquet(combined, columns=["instance_hash"])
    values = selected.instance_hash.unique()
    if any(int.from_bytes(bytes.fromhex(value)[:8], "big") % 100 != BUCKET for value in values):
        raise ValueError("non-bucket2 ID entered combined file")
    answer = {"integrity_pass": True, "all_members_verified": True,
              "source_url": client.url, "source_size": client.size, "source_etag": client.etag,
              "source_members": len(members), "source_rows": expected_rows,
              "id_bucket": BUCKET, "bucket_modulus": 100,
              "release_manifest_sha256": release_hash,
              "prior_full_source_audit_sha256": source.sha256_file(ORIGINAL_AUDIT) if release_hash else None,
              "selected_rows": len(selected), "selected_unique_ids": len(values),
              "compact_sha256": source.sha256_file(combined), "member_audits": audits}
    source.atomic_json(output / "instance_week_5min_bucket2_audit.json", answer)
    return answer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--release-manifest", type=Path)
    args = parser.parse_args()
    if not args.fetch:
        print("No remote access. Pass --fetch and a frozen release manifest.")
    else:
        if args.release_manifest is None:
            parser.error("--fetch requires --release-manifest")
        result = fetch(release_manifest=args.release_manifest)
        print(json.dumps({key: result[key] for key in
                          ("source_rows", "selected_rows", "selected_unique_ids", "compact_sha256")},
                         sort_keys=True))
