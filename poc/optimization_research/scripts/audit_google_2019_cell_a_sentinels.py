"""Re-extract cell-a JOB arrivals with Google v3 special times excluded.

This is a post-hoc data correction audit, not a new model evaluation. The
frozen v1 source, extraction code, protocol, and series are checked but never
modified. Anonymous collection IDs live only in a disposable SQLite database.
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
from collections import Counter
from pathlib import Path


RESEARCH = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH / "data" / "external" / "google_cluster_2019_a"
OLD = RESEARCH / "artifacts" / "google_2019_arrival_model"
OUTPUT = RESEARCH / "artifacts" / "google_2019_cell_a_sentinel_v2"
OBJECT_LIST = SOURCE / "collection_events_object_manifest.json"
VERIFIED = SOURCE / "verified_collection_events_manifest.json"
SCHEMA = SOURCE / "collection_events.schema.json"
OLD_FREEZE = OLD / "freeze.json"
OLD_SERIES = OLD / "series.json"
OLD_PROTOCOL = RESEARCH / "reports" / "google_2019_arrival_model_protocol_v1.md"
OLD_SCRIPT = RESEARCH / "scripts" / "evaluate_google_2019_arrival_model.py"
EXPECTED_OBJECT_LIST_SHA256 = "452c60360ff98db27d10417494361a664c7ef34da229ef3093e9df9149f12f89"
EXPECTED_SCHEMA_SHA256 = "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3"
EXPECTED_VERIFIED_SHA256 = "b01dcc58bfbfea500942c7812b0add7242446fcb5c8fa75546d89635447d3e34"
EXPECTED_OLD_FREEZE_SHA256 = "5b260980c471e2282856e6ef8d5a675e8f62fd17c044816f45b5e1bc4143fe07"
EXPECTED_NAMES = [f"collection_events-{index:012d}.json.gz" for index in range(10)]
REQUIRED_FIELDS = frozenset(("time", "type", "collection_id", "collection_type"))
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FIVE_MIN_US = 300_000_000
INT64_MAX = 2**63 - 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_compressed(path: Path) -> tuple[int, str, str]:
    size = 0
    md5 = hashlib.md5()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            md5.update(block)
            digest.update(block)
    return size, base64.b64encode(md5.digest()).decode(), digest.hexdigest()


def verify_frozen_sources() -> tuple[list[Path], str, set[str]]:
    """Verify all ten GCS objects and every referenced v1 freeze input."""
    for path, expected in ((OBJECT_LIST, EXPECTED_OBJECT_LIST_SHA256),
                           (SCHEMA, EXPECTED_SCHEMA_SHA256),
                           (VERIFIED, EXPECTED_VERIFIED_SHA256),
                           (OLD_FREEZE, EXPECTED_OLD_FREEZE_SHA256)):
        if sha256(path) != expected:
            raise ValueError(f"pinned input checksum mismatch: {path.name}")
    freeze = json.loads(OLD_FREEZE.read_text())
    old_inputs = (("source_manifest_sha256", VERIFIED),
                  ("protocol_sha256", OLD_PROTOCOL),
                  ("script_sha256", OLD_SCRIPT),
                  ("series_sha256", OLD_SERIES))
    for key, path in old_inputs:
        if freeze.get(key) != sha256(path):
            raise ValueError(f"frozen v1 {key} no longer matches: {path.name}")

    metadata = json.loads(OBJECT_LIST.read_text())
    verified = json.loads(VERIFIED.read_text())
    if metadata.get("bucket") != "clusterdata_2019_a":
        raise ValueError("unexpected source bucket")
    if (verified.get("bucket") != metadata["bucket"]
            or verified.get("metadata_manifest_sha256") != EXPECTED_OBJECT_LIST_SHA256):
        raise ValueError("verified manifest does not match source metadata")
    entries = metadata.get("objects")
    observed = verified.get("objects")
    if (not isinstance(entries, list) or not isinstance(observed, list)
            or [row.get("name") for row in entries] != EXPECTED_NAMES
            or [row.get("name") for row in observed] != EXPECTED_NAMES):
        raise ValueError("exactly ten pinned cell-a JSON shards are required")
    schema = json.loads(SCHEMA.read_text())
    columns = {column["name"] for column in schema}
    if not REQUIRED_FIELDS <= columns:
        raise ValueError("essential collection-event schema fields absent")
    paths = []
    for expected, local in zip(entries, observed, strict=True):
        for key in ("name", "bytes", "generation", "md5_base64", "crc32c_base64"):
            if local.get(key) != expected.get(key):
                raise ValueError(f"verified object metadata differs: {key}")
        local_sha = local.get("sha256")
        if not isinstance(local_sha, str) or not SHA256_RE.fullmatch(local_sha):
            raise ValueError("missing verified object SHA-256")
        path = SOURCE / expected["name"]
        size, md5, digest = inspect_compressed(path)
        if size != expected["bytes"] or md5 != expected["md5_base64"]:
            raise ValueError(f"compressed object size or GCS MD5 mismatch: {path.name}")
        if digest != local_sha:
            raise ValueError(f"compressed object SHA-256 mismatch: {path.name}")
        paths.append(path)
    return paths, sha256(OLD_FREEZE), columns


def valid_int(value: object, field: str) -> int:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= INT64_MAX:
        raise ValueError(f"missing, negative, out-of-range, or non-integer {field}")
    return value


def extract_event_rows(paths: list[Path], scratch: Path, columns: set[str]) -> dict:
    """Count first *timed* valid JOB SUBMIT across unordered verified shards."""
    if len(paths) != 10 or len(set(paths)) != 10:
        raise ValueError("exactly ten distinct cell-a shards are required")
    if not REQUIRED_FIELDS <= columns:
        raise ValueError("essential collection-event fields absent")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_a_sentinel_dedup_", dir=scratch) as temporary:
        con = sqlite3.connect(Path(temporary) / "first_submits.sqlite3")
        try:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("CREATE TABLE first_submit (collection_id INTEGER PRIMARY KEY, "
                        "first_time_us INTEGER NOT NULL, event_rows INTEGER NOT NULL, "
                        "shard_mask INTEGER NOT NULL)")
            con.execute("CREATE TABLE pretrace_job_submit (collection_id INTEGER PRIMARY KEY)")
            upsert = ("INSERT INTO first_submit VALUES (?, ?, 1, ?) "
                      "ON CONFLICT(collection_id) DO UPDATE SET "
                      "first_time_us=MIN(first_time_us, excluded.first_time_us), "
                      "event_rows=event_rows+1, shard_mask=shard_mask|excluded.shard_mask")
            audit = Counter()
            min_time, max_time = None, None
            for shard, path in enumerate(paths):
                submit_batch = []
                pretrace_batch = []
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
                        if job_submit:
                            audit["all_job_submit_rows"] += 1
                        if timestamp in (0, INT64_MAX):
                            kind = "pretrace" if timestamp == 0 else "posttrace"
                            audit[f"non_timed_{kind}_rows"] += 1
                            if job_submit:
                                audit[f"non_timed_{kind}_job_submit_rows"] += 1
                                if row.get("missing_type") is None:
                                    audit[f"non_timed_{kind}_valid_job_submit_rows"] += 1
                                    if timestamp == 0:
                                        pretrace_batch.append((collection_id,))
                                        if len(pretrace_batch) >= 10_000:
                                            con.executemany("INSERT OR IGNORE INTO pretrace_job_submit VALUES (?)",
                                                            pretrace_batch)
                                            con.commit()
                                            pretrace_batch.clear()
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
                        submit_batch.append((collection_id, timestamp, 1 << shard))
                        if len(submit_batch) >= 10_000:
                            con.executemany(upsert, submit_batch)
                            con.commit()
                            submit_batch.clear()
                if submit_batch:
                    con.executemany(upsert, submit_batch)
                if pretrace_batch:
                    con.executemany("INSERT OR IGNORE INTO pretrace_job_submit VALUES (?)",
                                    pretrace_batch)
                con.commit()
            if min_time is None or max_time is None:
                raise ValueError("no timed collection events")
            first_bin = min_time // FIVE_MIN_US + 1
            last_bin = max_time // FIVE_MIN_US - 1
            if last_bin < first_bin or last_bin - first_bin + 1 > 1_000_000:
                raise ValueError("no plausible complete five-minute bin interval")
            counts = [0] * (last_bin - first_bin + 1)
            for bin_id, count in con.execute(
                "SELECT first_time_us / ?, COUNT(*) FROM first_submit GROUP BY first_time_us / ?",
                (FIVE_MIN_US, FIVE_MIN_US),
            ):
                if first_bin <= bin_id <= last_bin:
                    counts[bin_id - first_bin] = count
            unique, duplicate_ids, extra_rows, cross_shard_ids = con.execute(
                "SELECT COUNT(*), SUM(event_rows>1), SUM(event_rows-1), "
                "SUM((shard_mask & (shard_mask-1)) != 0) FROM first_submit"
            ).fetchone()
            pretrace_unique = con.execute("SELECT COUNT(*) FROM pretrace_job_submit").fetchone()[0]
            recovered = [
                {"first_timed_submit_bin": bin_id, "collection_count": count}
                for bin_id, count in con.execute(
                    "SELECT first_time_us / ?, COUNT(*) FROM pretrace_job_submit "
                    "JOIN first_submit USING(collection_id) GROUP BY first_time_us / ? "
                    "ORDER BY first_time_us / ?", (FIVE_MIN_US,) * 3)
            ]
            for name in ("all_collection_event_rows", "timed_collection_event_rows",
                         "non_timed_pretrace_rows", "non_timed_posttrace_rows",
                         "all_job_submit_rows", "non_timed_pretrace_job_submit_rows",
                         "non_timed_posttrace_job_submit_rows",
                         "non_timed_pretrace_valid_job_submit_rows",
                         "non_timed_posttrace_valid_job_submit_rows",
                         "raw_job_submit_rows", "excluded_job_submit_missing_type_rows",
                         "valid_job_submit_rows"):
                audit.setdefault(name, 0)
            if audit["all_collection_event_rows"] != (
                audit["timed_collection_event_rows"]
                + audit["non_timed_pretrace_rows"] + audit["non_timed_posttrace_rows"]
            ) or audit["all_job_submit_rows"] != (
                audit["raw_job_submit_rows"]
                + audit["non_timed_pretrace_job_submit_rows"]
                + audit["non_timed_posttrace_job_submit_rows"]
            ) or audit["valid_job_submit_rows"] != unique + extra_rows:
                raise AssertionError("source, special-time, and dedup totals disagree")
            return {
                "version": 2,
                "target": "first_timed_valid_JOB_collection_SUBMIT_count_per_complete_5m_bin",
                "time_min_us": min_time,
                "time_max_us": max_time,
                "first_full_bin": first_bin,
                "last_full_bin": last_bin,
                "counts": counts,
                "audit": {**dict(sorted(audit.items())),
                          "unique_job_submit_collection_ids": unique,
                          "duplicate_job_submit_collection_ids": duplicate_ids or 0,
                          "extra_valid_submit_rows_after_dedup": extra_rows or 0,
                          "collection_ids_with_submit_in_multiple_shards": cross_shard_ids or 0,
                          "unique_pretrace_valid_job_submit_collection_ids": pretrace_unique,
                          "pretrace_collection_ids_with_timed_submit": sum(
                              item["collection_count"] for item in recovered),
                          "recovered_first_timed_submit_bins": recovered,
                          "complete_5m_bins": len(counts),
                          "zero_job_submit_bins": sum(value == 0 for value in counts)},
            }
        finally:
            con.close()


def compare_with_frozen_v1(series: dict, old_series: dict) -> dict:
    """Quantify changed eligibility and labels without releasing IDs."""
    old_first, old_last = old_series["first_full_bin"], old_series["last_full_bin"]
    new_first, new_last = series["first_full_bin"], series["last_full_bin"]
    old_counts, new_counts = old_series["counts"], series["counts"]
    if len(old_counts) != old_last - old_first + 1:
        raise ValueError("frozen v1 counts have inconsistent boundaries")
    if len(new_counts) != new_last - new_first + 1:
        raise ValueError("corrected counts have inconsistent boundaries")
    shared_first = max(old_first, new_first)
    shared_last = min(old_last, new_last)
    if shared_first > shared_last:
        raise ValueError("v1 and v2 have no comparable complete bins")
    changed = [
        {"bin": bin_id, "v1_count": old_counts[bin_id - old_first],
         "corrected_count": new_counts[bin_id - new_first]}
        for bin_id in range(shared_first, shared_last + 1)
        if old_counts[bin_id - old_first] != new_counts[bin_id - new_first]
    ]
    removed = [
        {"bin": bin_id, "v1_count": old_counts[bin_id - old_first]}
        for bin_id in range(old_first, old_last + 1)
        if not new_first <= bin_id <= new_last
    ]
    added = [
        {"bin": bin_id, "corrected_count": new_counts[bin_id - new_first]}
        for bin_id in range(new_first, new_last + 1)
        if not old_first <= bin_id <= old_last
    ]
    return {
        "version": 2,
        "old_v1_series_sha256": sha256(OLD_SERIES),
        "old_first_full_bin": old_first,
        "old_last_full_bin": old_last,
        "corrected_first_full_bin": new_first,
        "corrected_last_full_bin": new_last,
        "old_total_in_complete_bins": sum(old_counts),
        "corrected_total_in_complete_bins": sum(new_counts),
        "removed_v1_bins": removed,
        "added_corrected_bins": added,
        "shared_complete_bins": shared_last - shared_first + 1,
        "changed_shared_bins": changed,
        "changed_shared_bin_count": len(changed),
    }


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "extract"))
    args = parser.parse_args()
    paths, freeze_sha, columns = verify_frozen_sources()
    if args.command == "verify":
        print(json.dumps({"verified_objects": len(paths), "v1_freeze_sha256": freeze_sha},
                         sort_keys=True))
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cell_a_sentinel_", dir=OUTPUT) as temporary:
        series = extract_event_rows(paths, Path(temporary), columns)
    series["source_manifest_sha256"] = sha256(VERIFIED)
    series["v1_freeze_sha256"] = freeze_sha
    impact = compare_with_frozen_v1(series, json.loads(OLD_SERIES.read_text()))
    write_json(OUTPUT / "series.json", series)
    impact["corrected_series_sha256"] = sha256(OUTPUT / "series.json")
    write_json(OUTPUT / "impact.json", impact)
    print(json.dumps({"series_sha256": sha256(OUTPUT / "series.json"),
                      "impact_sha256": sha256(OUTPUT / "impact.json"),
                      "audit": series["audit"],
                      "changed_shared_bins": impact["changed_shared_bins"]}, sort_keys=True))


if __name__ == "__main__":
    main()
