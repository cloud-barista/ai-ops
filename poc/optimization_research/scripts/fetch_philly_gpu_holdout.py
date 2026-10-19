"""Stream the disjoint Philly machine holdout from the pinned source archive.

The selection rule is fixed at SHA256(machineId UTF-8) % 100 in [5, 15).
This collector does not read or modify the original 5% sample. It preserves
source row order, timestamp text, NA cells, and complete irregular CSV rows.
There is no probe mode: a promoted Parquet always has a full archive audit.
"""

from __future__ import annotations

from collections import Counter
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile

import requests

if __package__:
    from . import fetch_philly_gpu_sample as source
else:
    import fetch_philly_gpu_sample as source


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "external" / "msr_philly_2017_holdout"
OUTPUT_NAME = "gpu_util_machine_hash5to14pct.parquet"
AUDIT_NAME = "gpu_util_holdout_audit.json"
FROZEN_PROTOCOL = ROOT / "reports" / "philly_machine_holdout_protocol_frozen.md"
ORIGINAL_MODEL_SPEC = ROOT / "artifacts" / "philly_machine_holdout_model_v1" / "model_spec.json"
ORIGINAL_MODEL_KIND = "philly_original_machines_only_frozen_holdout_model_spec"
ORIGINAL_NORMALIZED_SHA256 = "15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58"
ORIGINAL_SCORE_SHA256 = "cb3080d46c39c748a894065ab59c93df167fcb182c2bddb75e2205a816ddc4d2"
ORIGINAL_DRY_RUN_SHA256 = "bccc573ea0f4f8fca9357d72bdc56f60788c85c31c2065d75f639322412bbf0f"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOWER_RESIDUE = 5
UPPER_RESIDUE = 15
MODULUS = 100
SELECTION_RULE = "5 <= int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 15"


def machine_bucket(machine_id: str) -> int:
    return int.from_bytes(hashlib.sha256(machine_id.encode("utf-8")).digest(), "big") % MODULUS


def machine_selected(machine_id: str) -> bool:
    return LOWER_RESIDUE <= machine_bucket(machine_id) < UPPER_RESIDUE


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pre_fetch_freeze(protocol: Path, expected_protocol_sha256: str,
                            model_spec: Path, expected_model_spec_sha256: str) -> dict:
    """Require pinned original-only inference and a frozen plan before HTTP access."""
    for label, value in (("protocol", expected_protocol_sha256),
                         ("model spec", expected_model_spec_sha256)):
        if not SHA256_RE.fullmatch(value):
            raise ValueError(f"Expected {label} SHA256 must be lowercase hex")
    if sha256_file(protocol) != expected_protocol_sha256:
        raise ValueError("Holdout protocol differs from explicitly expected SHA256")
    if sha256_file(model_spec) != expected_model_spec_sha256:
        raise ValueError("Original-only model spec differs from explicitly expected SHA256")
    text = protocol.read_text(encoding="utf-8")
    if not re.search(r"^\*\*상태:\s*FROZEN\b", text, flags=re.MULTILINE):
        raise ValueError("Holdout protocol is not FROZEN")
    if re.search(r"\*\*PENDING\b", text):
        raise ValueError("Holdout protocol still has pending items")
    collector_sha = sha256_file(Path(__file__))
    pinned = (("original-only model spec", "model_spec.json", expected_model_spec_sha256),
              ("holdout collector", "fetch_philly_gpu_holdout.py", collector_sha))
    for label, filename, digest in pinned:
        if not any(filename in line and digest in line for line in text.splitlines()):
            raise ValueError(f"Holdout protocol does not pin current {label} SHA256")
    spec = json.loads(model_spec.read_text(encoding="utf-8"))
    expected_provenance = {
        "kind": ORIGINAL_MODEL_KIND,
        "original_normalized_source_sha256": ORIGINAL_NORMALIZED_SHA256,
        "original_score_sha256": ORIGINAL_SCORE_SHA256,
        "original_dry_run_sha256": ORIGINAL_DRY_RUN_SHA256,
    }
    if any(spec.get(key) != value for key, value in expected_provenance.items()):
        raise ValueError("Model spec is not pinned to original Philly machines")
    return {
        "frozen_holdout_protocol_sha256": expected_protocol_sha256,
        "original_only_model_spec_sha256": expected_model_spec_sha256,
        "holdout_collector_script_sha256": collector_sha,
    }


def _scan_gpu(member_file, writer: source.SampleWriter) -> dict:
    rows = 0
    header_present = False
    machines: dict[str, int] = {}
    selected_flags: dict[str, bool] = {}
    selected_machine_ids = set()
    selected_seen_keys: set[int] = set()
    selected_duplicate_rows = 0
    non_minute_rows = 0
    status = Counter()
    field_count_histogram = Counter()
    timezone_rows = Counter()
    min_epoch = None
    max_epoch = None
    for raw in csv.reader(source.iter_text_lines(member_file)):
        if rows == 0 and raw and raw[0] == "time":
            header_present = True
            continue
        if not raw or (len(raw) == 1 and raw[0] == ""):
            raise ValueError("GPU CSV contains an empty row")
        rows += 1
        (time_text, epoch, tz, machine_id, values, local_status,
         gpu_field_count, raw_irregular_json) = source.parse_gpu_row(raw, rows)
        field_count_histogram[gpu_field_count] += 1
        status.update(local_status)
        timezone_rows[tz] += 1
        min_epoch = epoch if min_epoch is None else min(min_epoch, epoch)
        max_epoch = epoch if max_epoch is None else max(max_epoch, epoch)
        non_minute_rows += epoch % 60 != 0
        if machine_id not in machines:
            machines[machine_id] = len(machines)
            selected_flags[machine_id] = machine_selected(machine_id)
        if selected_flags[machine_id]:
            selected_machine_ids.add(machine_id)
            if not 0 <= epoch < 2**32:
                raise ValueError(f"GPU CSV row {rows} timestamp outside packed-key range")
            key = (machines[machine_id] << 32) | epoch
            if key in selected_seen_keys:
                selected_duplicate_rows += 1
            else:
                selected_seen_keys.add(key)
            writer.add(rows, time_text, epoch, machine_id, values,
                       gpu_field_count, raw_irregular_json)
        if rows % 50_000 == 0:
            source.require_free_space(writer.path.parent)
        if rows % 1_000_000 == 0:
            print(f"gpu rows scanned: {rows:,}; holdout rows written: {writer.rows:,}", flush=True)
    return {
        "rows": rows,
        "header_present": header_present,
        "machine_count": len(machines),
        "selected_machine_count": len(selected_machine_ids),
        "selected_machine_ids": sorted(selected_machine_ids),
        "selected_rows": writer.rows + len(writer.batch["source_row_index"]),
        "selected_duplicate_machine_utc_minute_rows": selected_duplicate_rows,
        "non_minute_timestamp_rows": non_minute_rows,
        "gpu_cell_status": dict(status),
        "gpu_field_count_histogram": dict(field_count_histogram),
        "timezone_rows": dict(timezone_rows),
        "min_time_utc_s": min_epoch,
        "max_time_utc_s": max_epoch,
    }


def scan_archive(raw_stream, *, output: Path, expected_size: int,
                 expected_sha256: str,
                 max_sample_bytes: int = source.MAX_SAMPLE_BYTES) -> dict:
    """Verify the complete gzip/tar and promote only the selected rows."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing holdout: {output}")
    sample_temp = output.with_name(output.name + ".incomplete")
    if sample_temp.exists():
        raise FileExistsError(f"Refusing to overwrite incomplete holdout: {sample_temp}")
    hashed = source.HashingReader(raw_stream)
    writer = None
    members = []
    gpu_audit = None
    machine_audit = None
    try:
        writer = source.SampleWriter(sample_temp, max_sample_bytes)
        with gzip.GzipFile(fileobj=hashed, mode="rb") as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="r|", bufsize=1 << 20) as archive:
                for member in archive:
                    members.append({
                        "name": member.name,
                        "size": member.size,
                        "type": member.type.decode(),
                        "is_file": member.isfile(),
                    })
                    if member.name == source.MACHINE_FILE:
                        if not member.isfile():
                            raise ValueError("Machine-list tar member is not a regular file")
                        with archive.extractfile(member) as machine_file:
                            machine_audit = source._scan_machine_list(machine_file)
                    elif member.name == source.GPU_FILE:
                        if not member.isfile():
                            raise ValueError("GPU tar member is not a regular file")
                        with archive.extractfile(member) as gpu_file:
                            gpu_audit = _scan_gpu(gpu_file, writer)
            while gzip_stream.read(4 * 1024 * 1024):
                pass
        if gpu_audit is None or machine_audit is None:
            raise ValueError("GPU CSV or machine-list CSV member is missing")
        file_entries = [entry for entry in members if entry["is_file"]]
        file_names = {entry["name"] for entry in file_entries}
        if file_names != source.EXPECTED_FILES or len(file_entries) != len(source.EXPECTED_FILES):
            raise ValueError(f"Tar file set differs from documented five files: {file_names!r}")
        if hashed.count != expected_size:
            raise ValueError(f"Compressed byte count {hashed.count} != pinned {expected_size}")
        compressed_sha = hashed.digest.hexdigest()
        if compressed_sha != expected_sha256:
            raise ValueError("Compressed SHA256 differs from pinned LFS OID")
        known = {item["machine_id"] for item in machine_audit["records"]}
        gpu_audit["selected_machines_missing_from_machine_list"] = sorted(
            set(gpu_audit["selected_machine_ids"]) - known
        )
        writer.close()
        writer = None
        os.replace(sample_temp, output)
        sample_digest = hashlib.sha256()
        with output.open("rb") as sample_file:
            while block := sample_file.read(4 * 1024 * 1024):
                sample_digest.update(block)
        return {
            "complete": True,
            "integrity_pass": True,
            "source_commit": source.SOURCE_COMMIT,
            "source_url": source.URL,
            "collector_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_parser_script_sha256": hashlib.sha256(Path(source.__file__).read_bytes()).hexdigest(),
            "compressed_bytes_read": hashed.count,
            "compressed_sha256": compressed_sha,
            "tar_members": members,
            "gpu_csv": gpu_audit,
            "machine_list": machine_audit,
            "sample_rule": SELECTION_RULE,
            "sample_bucket_modulus": MODULUS,
            "sample_bucket_lower_inclusive": LOWER_RESIDUE,
            "sample_bucket_upper_exclusive": UPPER_RESIDUE,
            "sample_parquet": str(output),
            "sample_bytes": output.stat().st_size,
            "sample_sha256": sample_digest.hexdigest(),
        }
    except BaseException:
        if writer is not None:
            writer.writer.close()
        sample_temp.unlink(missing_ok=True)
        raise


def run(*, expected_protocol_sha256: str, expected_model_spec_sha256: str,
        protocol: Path = FROZEN_PROTOCOL,
        model_spec: Path = ORIGINAL_MODEL_SPEC) -> dict:
    """Fetch the pinned archive once; keep only the verified holdout sample."""
    freeze = verify_pre_fetch_freeze(
        protocol, expected_protocol_sha256, model_spec, expected_model_spec_sha256)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source.require_free_space(OUTPUT_DIR)
    output = OUTPUT_DIR / OUTPUT_NAME
    audit_path = OUTPUT_DIR / AUDIT_NAME
    machine_path = OUTPUT_DIR / "machine_list.json"
    if any(path.exists() for path in (output, audit_path, machine_path)):
        raise FileExistsError("Refusing to overwrite holdout sample or audit files")
    with requests.Session() as session:
        source.validate_http_head(session)
        with session.get(source.URL, stream=True, timeout=(30, 180),
                         headers={"Accept-Encoding": "identity",
                                  "User-Agent": "ai-mcmp-research/philly-holdout-stream-audit"}) as response:
            if response.status_code != 200:
                raise ValueError(f"Pinned Philly GET returned {response.status_code}")
            if int(response.headers.get("Content-Length", "-1")) != source.EXPECTED_SIZE:
                raise ValueError("Pinned Philly GET Content-Length changed")
            if response.headers.get("ETag") != source.EXPECTED_ETAG:
                raise ValueError("Pinned Philly GET ETag changed")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("Pinned Philly GET has unexpected Content-Encoding")
            response.raw.decode_content = False
            audit = scan_archive(response.raw, output=output,
                                 expected_size=source.EXPECTED_SIZE,
                                 expected_sha256=source.EXPECTED_SHA256)
    machine_records = audit["machine_list"].pop("records")
    machine_temp = machine_path.with_name(machine_path.name + ".tmp")
    machine_temp.write_text(json.dumps(machine_records, indent=2, sort_keys=True) + "\n")
    os.replace(machine_temp, machine_path)
    audit["machine_list"]["path"] = str(machine_path)
    audit["machine_list"]["sha256"] = hashlib.sha256(machine_path.read_bytes()).hexdigest()
    audit["http_content_length"] = source.EXPECTED_SIZE
    audit["http_etag"] = source.EXPECTED_ETAG
    audit["pre_fetch_freeze"] = freeze
    audit_temp = audit_path.with_name(audit_path.name + ".tmp")
    audit_temp.write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(audit_temp, audit_path)
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-holdout-protocol-sha256", required=True)
    parser.add_argument("--expected-original-model-spec-sha256", required=True)
    parser.add_argument("--holdout-protocol", type=Path, default=FROZEN_PROTOCOL)
    parser.add_argument("--original-model-spec", type=Path, default=ORIGINAL_MODEL_SPEC)
    args = parser.parse_args()
    result = run(expected_protocol_sha256=args.expected_holdout_protocol_sha256,
                 expected_model_spec_sha256=args.expected_original_model_spec_sha256,
                 protocol=args.holdout_protocol,
                 model_spec=args.original_model_spec)
    print(json.dumps({
        "complete": result["complete"],
        "gpu_rows": result["gpu_csv"]["rows"],
        "holdout_rows": result["gpu_csv"]["selected_rows"],
        "holdout_bytes": result["sample_bytes"],
        "audit": str(OUTPUT_DIR / AUDIT_NAME),
    }, sort_keys=True), flush=True)
