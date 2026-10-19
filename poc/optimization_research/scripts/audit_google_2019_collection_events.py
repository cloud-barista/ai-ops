"""Read-only streaming audit of one public Google 2019 collection-event shard.

The GCS file is not time ordered and is not a complete cell trace. This script
does not create workload labels, infer a scale action, or inspect other shards.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "data" / "external" / "google_cluster_2019_a"
OBJECT = "collection_events-000000000000.json.gz"
SCHEMA = "collection_events.schema.json"
EXPECTED = {
    OBJECT: {
        "size": 133462892,
        "md5_base64": "XWYyLf0GfH73SLAquvx1jQ==",
        "sha256": "2288aa6bb4ef5157a68ca73719685b5bf438edcc85ef5ea5eb472c294cb271d4",
        "generation": "1596841425790024",
    },
    SCHEMA: {
        "size": 1440,
        "md5_base64": "kL3yk+8pEQqKkOQ6h6q+Mg==",
        "sha256": "63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3",
        "generation": "1596854498220506",
    },
}
FIVE_MIN_US = 300_000_000


def verify_file(name: str) -> Path:
    path = ROOT / name
    expected = EXPECTED[name]
    if path.stat().st_size != expected["size"]:
        raise ValueError(f"GCS object size mismatch: {name}")
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(block)
            sha256.update(block)
    if base64.b64encode(md5.digest()).decode() != expected["md5_base64"]:
        raise ValueError(f"GCS object MD5 mismatch: {name}")
    if sha256.hexdigest() != expected["sha256"]:
        raise ValueError(f"pinned SHA-256 mismatch: {name}")
    return path


def main() -> None:
    schema_path = verify_file(SCHEMA)
    path = verify_file(OBJECT)
    schema = json.loads(schema_path.read_text())
    columns = {item["name"] for item in schema}
    expected_keys = {"time", "type", "collection_id", "collection_type"}
    if not expected_keys <= columns:
        raise ValueError("essential collection event schema fields absent")

    rows = 0
    min_time = 10**30
    max_time = 0
    preceding_time = None
    adjacent_time_decreases = 0
    missing_required = Counter()
    field_presence = Counter()
    event_type = Counter()
    collection_type = Counter()
    missing_type = Counter()
    vertical_scaling = Counter()
    five_min_bins = Counter()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for line in stream:
            row = json.loads(line)
            rows += 1
            for key in row:
                if key not in columns:
                    raise ValueError(f"unrecognized collection event field: {key}")
                field_presence[key] += 1
            for key in expected_keys:
                if key not in row or row[key] is None:
                    missing_required[key] += 1
            if "time" not in row or row["time"] is None:
                continue
            timestamp = int(row["time"])
            if timestamp < 0:
                raise ValueError("negative trace timestamp")
            min_time = min(min_time, timestamp)
            max_time = max(max_time, timestamp)
            if preceding_time is not None and timestamp < preceding_time:
                adjacent_time_decreases += 1
            preceding_time = timestamp
            five_min_bins[timestamp // FIVE_MIN_US] += 1
            event_type[str(row.get("type", "ABSENT"))] += 1
            collection_type[str(row.get("collection_type", "ABSENT"))] += 1
            missing_type[str(row.get("missing_type", "ABSENT"))] += 1
            vertical_scaling[str(row.get("vertical_scaling", "ABSENT"))] += 1

    print(json.dumps({
        "source": {
            "bucket": "clusterdata_2019_a",
            "object": OBJECT,
            "generation": EXPECTED[OBJECT]["generation"],
            "compressed_bytes": EXPECTED[OBJECT]["size"],
            "compressed_sha256": EXPECTED[OBJECT]["sha256"],
            "schema_bucket": "clusterdata_2019_schema",
            "schema_object": SCHEMA,
            "schema_generation": EXPECTED[SCHEMA]["generation"],
            "schema_sha256": EXPECTED[SCHEMA]["sha256"],
        },
        "rows": rows,
        "time_us": [min_time, max_time],
        "adjacent_time_decreases_in_file_order": adjacent_time_decreases,
        "distinct_5m_bins_with_at_least_one_row": len(five_min_bins),
        "five_min_bin_observed_row_count": {
            "min": min(five_min_bins.values()),
            "max": max(five_min_bins.values()),
        },
        "missing_essential_field_rows": dict(sorted(missing_required.items())),
        "field_presence_rows": dict(sorted(field_presence.items())),
        "event_type_counts": dict(sorted(event_type.items())),
        "collection_type_counts": dict(sorted(collection_type.items())),
        "missing_type_counts": dict(sorted(missing_type.items())),
        "vertical_scaling_setting_counts": dict(sorted(vertical_scaling.items())),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
