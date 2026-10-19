"""Verify and extract the pinned Google 2019 cell-e JOB arrival series.

The single collection-event object is fetched by GCS generation, checked against
its official size and MD5, and recorded with local SHA-256. Extraction is
blocked until the locally verified object exists. This module never
stores collection IDs or user names in the resulting time series.

Cell-e is an independent generic Borg workload test, not an AI-App scale-action
outcome dataset. The evaluation protocol must be frozen before ``fetch``.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data" / "external" / "google_cluster_2019_e"
ARTIFACT = RESEARCH / "artifacts" / "google_2019_cell_e_selector_v1"
OBJECT_LIST = SOURCE / "collection_events_object_manifest.json"
SCHEMA = SOURCE / "collection_events.schema.json"
VERIFIED = SOURCE / "verified_collection_events_manifest.json"
SERIES = ARTIFACT / "series.json"
FREEZE = RESEARCH / "artifacts" / "google_2019_cell_e_selector_v1" / "freeze.json"
EXPECTED_OBJECT_LIST_SHA256 = "21bead24c5d90136a289c5fadda8cd93a0bebe09394109a6dcd95c843c9afbdd"
EXPECTED_SCHEMA_SHA256 = "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3"
FIVE_MIN_US = 300_000_000
INT64_MAX = 2**63 - 1
EXPECTED_NAMES = [f"collection_events-{index:012d}.json.gz" for index in range(1)]
EXPECTED_COMPRESSED_BYTES = 254_347_684
REQUIRED_FIELDS = frozenset(("time", "type", "collection_id", "collection_type"))
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect(path: Path) -> tuple[int, str, str]:
    """Return compressed byte count, GCS-style MD5, and local SHA-256."""
    size = 0
    md5 = hashlib.md5()
    shasum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            md5.update(block)
            shasum.update(block)
    return size, base64.b64encode(md5.digest()).decode(), shasum.hexdigest()


def metadata_preflight() -> tuple[list[dict], str]:
    """Validate metadata and schema without reading or fetching event objects."""
    metadata_sha = sha256(OBJECT_LIST)
    if metadata_sha != EXPECTED_OBJECT_LIST_SHA256:
        raise ValueError("cell-e official object metadata manifest changed")
    if sha256(SCHEMA) != EXPECTED_SCHEMA_SHA256:
        raise ValueError("cell-e official collection-event schema changed")
    manifest = json.loads(OBJECT_LIST.read_text())
    if manifest.get("bucket") != "clusterdata_2019_e":
        raise ValueError("unexpected GCS bucket")
    entries = manifest.get("objects")
    if not isinstance(entries, list) or [entry.get("name") for entry in entries] != EXPECTED_NAMES:
        raise ValueError("cell-e must have exactly one pinned JSON gzip object")
    if sum(entry["bytes"] for entry in entries) != EXPECTED_COMPRESSED_BYTES:
        raise ValueError("cell-e total compressed size changed")
    for entry in entries:
        if not isinstance(entry["bytes"], int) or entry["bytes"] <= 0:
            raise ValueError("invalid pinned object size")
        if not isinstance(entry.get("generation"), str) or not entry["generation"].isdecimal():
            raise ValueError("invalid pinned GCS generation")
        if len(base64.b64decode(entry["md5_base64"], validate=True)) != 16:
            raise ValueError("invalid pinned GCS MD5")
    schema = json.loads(SCHEMA.read_text())
    if not isinstance(schema, list) or not REQUIRED_FIELDS <= {column.get("name") for column in schema}:
        raise ValueError("essential collection-event fields absent from official schema")
    return entries, metadata_sha


def require_pre_download_freeze(metadata_sha: str) -> None:
    """Avoid opening the held-out cell before the method and extractor are fixed."""
    if not FREEZE.exists():
        raise ValueError("pre-download cell-e freeze.json is required before fetch")
    frozen = json.loads(FREEZE.read_text())
    if (frozen.get("source_metadata_sha256") != metadata_sha
            or frozen.get("prepare_script_sha256") != sha256(Path(__file__))):
        raise ValueError("cell-e freeze does not pin source metadata and extraction code")


def fetch_object(entry: dict) -> str:
    """Download one generation-pinned object atomically, or recheck its local copy."""
    entries, metadata_sha = metadata_preflight()
    require_pre_download_freeze(metadata_sha)
    if entry not in entries:
        raise ValueError("cell-e object differs from pinned source metadata")
    name = entry["name"]
    destination = SOURCE / name
    expected_size = entry["bytes"]
    expected_md5 = entry["md5_base64"]
    if destination.exists():
        size, md5, local_sha = inspect(destination)
        if size != expected_size or md5 != expected_md5:
            raise ValueError(f"existing cell-e object failed official size/MD5: {name}")
        print(f"verified existing {name} {size} {local_sha}", flush=True)
        return local_sha

    quoted = urllib.parse.quote(name, safe="")
    url = (f"https://storage.googleapis.com/storage/v1/b/clusterdata_2019_e/o/{quoted}"
           f"?alt=media&generation={entry['generation']}")
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=SOURCE, prefix=f".{name}.",
                                         suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(request, timeout=120) as response:
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > expected_size:
                        raise ValueError(f"GCS object exceeds pinned size: {name}")
                    output.write(block)
        size, md5, local_sha = inspect(temporary)
        if size != expected_size or md5 != expected_md5:
            raise ValueError(f"downloaded cell-e object failed official size/MD5: {name}")
        os.replace(temporary, destination)
        temporary = None
        print(f"verified download {name} {size} {local_sha}", flush=True)
        return local_sha
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".part",
                                         delete=False) as output:
            temporary = Path(output.name)
            json.dump(payload, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_all() -> dict:
    entries, metadata_sha = metadata_preflight()
    require_pre_download_freeze(metadata_sha)
    hashes = {entry["name"]: fetch_object(entry) for entry in entries}
    verified = {
        "bucket": "clusterdata_2019_e",
        "metadata_manifest_sha256": metadata_sha,
        "objects": [{**entry, "sha256": hashes[entry["name"]]} for entry in entries],
    }
    write_json(VERIFIED, verified)
    return verified


def verify_inputs() -> tuple[list[Path], str]:
    """Fail closed unless the single compressed objects match their pinned hashes."""
    entries, metadata_sha = metadata_preflight()
    require_pre_download_freeze(metadata_sha)
    if not VERIFIED.exists():
        raise ValueError("the single cell-e object has not been verified")
    verified = json.loads(VERIFIED.read_text())
    observed = verified.get("objects")
    if (verified.get("bucket") != "clusterdata_2019_e"
            or verified.get("metadata_manifest_sha256") != metadata_sha
            or not isinstance(observed, list) or len(observed) != len(entries)):
        raise ValueError("cell-e verified manifest disagrees with pinned metadata")
    paths = []
    for entry, local in zip(entries, observed, strict=True):
        for key in ("name", "bytes", "generation", "md5_base64", "crc32c_base64"):
            if local.get(key) != entry[key]:
                raise ValueError(f"cell-e verified manifest field differs: {key}")
        if not isinstance(local.get("sha256"), str) or not SHA256_RE.fullmatch(local["sha256"]):
            raise ValueError("cell-e verified manifest is missing local SHA-256")
        path = SOURCE / entry["name"]
        size, md5, local_sha = inspect(path)
        if size != entry["bytes"] or md5 != entry["md5_base64"]:
            raise ValueError(f"cell-e compressed size or GCS MD5 mismatch: {path.name}")
        if local_sha != local["sha256"]:
            raise ValueError(f"cell-e local SHA-256 mismatch: {path.name}")
        paths.append(path)
    return paths, sha256(VERIFIED)


def valid_int(value: object, field: str) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= INT64_MAX:
        raise ValueError(f"missing, negative, out-of-range, or non-integer {field}")
    return value


def extract_event_rows(paths: list[Path], scratch: Path) -> dict:
    """Stream the complete object and count earliest timed valid JOB SUBMIT per ID.

    ``scratch`` contains a short-lived SQLite deduplication table. It is
    deleted on success and failure, leaving IDs out of ``series.json``.
    """
    if len(paths) != 1 or len(set(paths)) != 1:
        raise ValueError("exactly one verified cell-e object is required")
    _, metadata_sha = metadata_preflight()
    require_pre_download_freeze(metadata_sha)
    schema = json.loads(SCHEMA.read_text())
    columns = {column["name"] for column in schema}
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_e_dedup_", dir=scratch) as temporary:
        con = sqlite3.connect(Path(temporary) / "first_submits.sqlite3")
        try:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("CREATE TABLE first_submit (collection_id INTEGER PRIMARY KEY, "
                        "first_time_us INTEGER NOT NULL, event_rows INTEGER NOT NULL, "
                        "shard_mask INTEGER NOT NULL)")
            upsert = ("INSERT INTO first_submit VALUES (?, ?, 1, ?) "
                      "ON CONFLICT(collection_id) DO UPDATE SET "
                      "first_time_us=MIN(first_time_us, excluded.first_time_us), "
                      "event_rows=event_rows+1, shard_mask=shard_mask|excluded.shard_mask")
            audit = Counter()
            min_time, max_time = None, None
            for shard, path in enumerate(paths):
                batch = []
                with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
                    for line in stream:
                        row = json.loads(line)
                        if not isinstance(row, dict) or not set(row) <= columns:
                            raise ValueError("unexpected collection-event row schema")
                        audit["all_collection_event_rows"] += 1
                        timestamp = valid_int(row.get("time"), "time")
                        event_type = valid_int(row.get("type"), "type")
                        collection_type = valid_int(row.get("collection_type"), "collection_type")
                        collection_id = valid_int(row.get("collection_id"), "collection_id")
                        if row.get("missing_type") is not None:
                            valid_int(row["missing_type"], "missing_type")
                        job_submit = collection_type == 0 and event_type == 0
                        if timestamp in (0, INT64_MAX):
                            key = ("non_timed_pretrace_rows" if timestamp == 0
                                   else "non_timed_posttrace_rows")
                            audit[key] += 1
                            if job_submit:
                                audit["non_timed_job_submit_rows"] += 1
                            continue
                        audit["timed_collection_event_rows"] += 1
                        min_time = timestamp if min_time is None else min(min_time, timestamp)
                        max_time = timestamp if max_time is None else max(max_time, timestamp)
                        if not job_submit:
                            continue
                        audit["raw_job_submit_rows"] += 1
                        if row.get("missing_type") is not None:
                            audit["excluded_job_submit_missing_type_rows"] += 1
                            continue
                        audit["valid_job_submit_rows"] += 1
                        batch.append((collection_id, timestamp, 1 << shard))
                        if len(batch) >= 10_000:
                            con.executemany(upsert, batch)
                            con.commit()
                            batch.clear()
                if batch:
                    con.executemany(upsert, batch)
                    con.commit()
            if min_time is None or max_time is None:
                raise ValueError("no timed collection events")
            first_bin = min_time // FIVE_MIN_US + 1
            last_bin = max_time // FIVE_MIN_US - 1
            if last_bin < first_bin:
                raise ValueError("no complete five-minute bins")
            if last_bin - first_bin + 1 > 1_000_000:
                raise ValueError("collection-event time span is implausibly large")
            counts = [0] * (last_bin - first_bin + 1)
            for bin_id, count in con.execute(
                "SELECT first_time_us / ?, COUNT(*) FROM first_submit GROUP BY first_time_us / ?",
                (FIVE_MIN_US, FIVE_MIN_US),
            ):
                if first_bin <= bin_id <= last_bin:
                    counts[bin_id - first_bin] = count
            unique, duplicate_ids, extra_submit_rows, cross_shard_ids = con.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN event_rows>1 THEN 1 ELSE 0 END), "
                "SUM(event_rows-1), "
                "SUM(CASE WHEN (shard_mask & (shard_mask-1)) != 0 THEN 1 ELSE 0 END) "
                "FROM first_submit"
            ).fetchone()
            unique = int(unique or 0)
            duplicate_ids = int(duplicate_ids or 0)
            extra_submit_rows = int(extra_submit_rows or 0)
            cross_shard_ids = int(cross_shard_ids or 0)
            if audit["valid_job_submit_rows"] != unique + extra_submit_rows:
                raise AssertionError("cell-e SUBMIT deduplication totals disagree")
            for name in ("all_collection_event_rows", "timed_collection_event_rows",
                         "non_timed_pretrace_rows", "non_timed_posttrace_rows",
                         "non_timed_job_submit_rows", "raw_job_submit_rows",
                         "excluded_job_submit_missing_type_rows", "valid_job_submit_rows"):
                audit.setdefault(name, 0)
            if (audit["all_collection_event_rows"] != audit["timed_collection_event_rows"]
                    + audit["non_timed_pretrace_rows"] + audit["non_timed_posttrace_rows"]):
                raise AssertionError("cell-e timed and sentinel row counts disagree")
            return {
                "version": 1,
                "target": "first_valid_JOB_collection_SUBMIT_count_per_complete_5m_bin",
                "time_min_us": min_time,
                "time_max_us": max_time,
                "first_full_bin": first_bin,
                "last_full_bin": last_bin,
                "counts": counts,
                "audit": {**dict(sorted(audit.items())),
                          "unique_job_submit_collection_ids": unique,
                          "duplicate_job_submit_collection_ids": duplicate_ids,
                          "extra_valid_submit_rows_after_dedup": extra_submit_rows,
                          "collection_ids_with_submit_in_multiple_shards": cross_shard_ids,
                          "complete_5m_bins": len(counts),
                          "zero_job_submit_bins": sum(value == 0 for value in counts)},
            }
        finally:
            con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "fetch", "verify", "extract"))
    args = parser.parse_args()
    if args.command == "preflight":
        entries, metadata_sha = metadata_preflight()
        print(json.dumps({"objects": len(entries), "compressed_bytes": sum(item["bytes"] for item in entries),
                          "source_metadata_sha256": metadata_sha}, sort_keys=True))
        return
    if args.command == "fetch":
        result = fetch_all()
        print(json.dumps({"verified_manifest": str(VERIFIED), "objects": len(result["objects"]),
                          "verified_manifest_sha256": sha256(VERIFIED)}, sort_keys=True))
        return
    paths, manifest_sha = verify_inputs()
    if args.command == "verify":
        print(json.dumps({"objects": len(paths), "verified_manifest_sha256": manifest_sha},
                         sort_keys=True))
        return
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_e_extract_", dir=ARTIFACT) as temporary:
        series = extract_event_rows(paths, Path(temporary))
    series["source_manifest_sha256"] = manifest_sha
    write_json(SERIES, series)
    print(json.dumps({"series": str(SERIES), "series_sha256": sha256(SERIES),
                      "audit": series["audit"]}, sort_keys=True))


if __name__ == "__main__":
    main()
