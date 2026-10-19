"""Synthetic integrity and temporal-label tests for the cell-a correction audit."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_google_2019_cell_a_sentinels.py"
spec = importlib.util.spec_from_file_location("google_2019_cell_a_sentinels", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def event(timestamp: int | str, collection_id: int | str, kind: int | str = 0,
          collection_type: int | str = 0, **extra: object) -> dict:
    return {"time": timestamp, "type": kind, "collection_type": collection_type,
            "collection_id": collection_id, **extra}


def write_gzip(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def synthetic_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                     shard_rows: list[list[dict]]) -> list[Path]:
    assert len(shard_rows) == 10
    source = tmp_path / "source"
    source.mkdir()
    paths = []
    entries = []
    for index, rows in enumerate(shard_rows):
        path = source / audit.EXPECTED_NAMES[index]
        write_gzip(path, rows)
        raw = path.read_bytes()
        entries.append({"name": path.name, "bytes": len(raw),
                        "generation": str(index + 1),
                        "md5_base64": base64.b64encode(hashlib.md5(raw).digest()).decode(),
                        "crc32c_base64": "synthetic",
                        "sha256": hashlib.sha256(raw).hexdigest()})
        paths.append(path)
    metadata_path = source / "collection_events_object_manifest.json"
    metadata_path.write_text(json.dumps({"bucket": "clusterdata_2019_a", "objects": [
        {key: value for key, value in row.items() if key != "sha256"} for row in entries]}))
    verified_path = source / "verified_collection_events_manifest.json"
    verified_path.write_text(json.dumps({"bucket": "clusterdata_2019_a",
                                         "metadata_manifest_sha256": audit.sha256(metadata_path),
                                         "objects": entries}))
    schema_path = source / "collection_events.schema.json"
    schema_path.write_text(json.dumps([{"name": field} for field in (
        "time", "type", "collection_type", "collection_id", "missing_type")]))
    old = tmp_path / "old"
    old.mkdir()
    protocol = old / "protocol.md"
    script = old / "script.py"
    series = old / "series.json"
    freeze = old / "freeze.json"
    protocol.write_text("frozen protocol")
    script.write_text("frozen script")
    series.write_text(json.dumps({"first_full_bin": 1, "last_full_bin": 4,
                                  "counts": [0, 0, 1, 1]}))
    freeze.write_text(json.dumps({"source_manifest_sha256": audit.sha256(verified_path),
                                  "protocol_sha256": audit.sha256(protocol),
                                  "script_sha256": audit.sha256(script),
                                  "series_sha256": audit.sha256(series)}))
    for name, value in (("SOURCE", source), ("OBJECT_LIST", metadata_path),
                        ("VERIFIED", verified_path), ("SCHEMA", schema_path),
                        ("OLD_FREEZE", freeze), ("OLD_SERIES", series),
                        ("OLD_PROTOCOL", protocol), ("OLD_SCRIPT", script),
                        ("EXPECTED_OBJECT_LIST_SHA256", audit.sha256(metadata_path)),
                        ("EXPECTED_VERIFIED_SHA256", audit.sha256(verified_path)),
                        ("EXPECTED_SCHEMA_SHA256", audit.sha256(schema_path)),
                        ("EXPECTED_OLD_FREEZE_SHA256", audit.sha256(freeze))):
        monkeypatch.setattr(audit, name, value)
    return paths


def test_unordered_shards_special_times_and_v1_impact(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    width = audit.FIVE_MIN_US
    paths = synthetic_source(tmp_path, monkeypatch, [
        [event(0, 20), event(str(audit.INT64_MAX), 21),
         event(width + 1, 90, kind=6), event(4 * width + 1, 20), event(0, 91)],
        [event(str(2 * width + 20), "20", kind="0", collection_type="0"),
         event(3 * width + 1, 30, missing_type=2), event(5 * width + 1, 90, kind=6)],
        [event(4 * width + 30, 21), event(3 * width + 30, 22)],
        [event(3 * width + 10, 22), event(4 * width + 40, 102, kind=1)],
        [], [], [], [], [], [],
    ])
    verified_paths, frozen_sha, columns = audit.verify_frozen_sources()
    assert verified_paths == paths
    assert frozen_sha == audit.sha256(audit.OLD_FREEZE)
    scratch = tmp_path / "scratch"
    clean = audit.extract_event_rows(paths, scratch, columns)
    assert clean["time_min_us"] == width + 1
    assert clean["time_max_us"] == 5 * width + 1
    assert (clean["first_full_bin"], clean["last_full_bin"]) == (2, 4)
    assert clean["counts"] == [1, 1, 1]
    stats = clean["audit"]
    assert stats["all_collection_event_rows"] == 12
    assert stats["non_timed_pretrace_rows"] == 2
    assert stats["non_timed_posttrace_rows"] == 1
    assert stats["non_timed_pretrace_valid_job_submit_rows"] == 2
    assert stats["raw_job_submit_rows"] == 6
    assert stats["excluded_job_submit_missing_type_rows"] == 1
    assert stats["valid_job_submit_rows"] == 5
    assert stats["unique_job_submit_collection_ids"] == 3
    assert stats["duplicate_job_submit_collection_ids"] == 2
    assert stats["collection_ids_with_submit_in_multiple_shards"] == 2
    assert stats["unique_pretrace_valid_job_submit_collection_ids"] == 2
    assert stats["pretrace_collection_ids_with_timed_submit"] == 1
    assert stats["recovered_first_timed_submit_bins"] == [
        {"first_timed_submit_bin": 2, "collection_count": 1}]
    assert list(scratch.iterdir()) == []
    assert not any(key in clean for key in ("collection_id", "user", "collection_name"))

    effect = audit.compare_with_frozen_v1(clean, json.loads(audit.OLD_SERIES.read_text()))
    assert effect["removed_v1_bins"] == [{"bin": 1, "v1_count": 0}]
    assert effect["changed_shared_bins"] == [
        {"bin": 2, "v1_count": 0, "corrected_count": 1}]
    assert effect["changed_shared_bin_count"] == 1


def test_frozen_inputs_and_shard_tampering_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = synthetic_source(tmp_path, monkeypatch, [[] for _ in range(10)])
    audit.verify_frozen_sources()
    original = paths[3].read_bytes()
    paths[3].write_bytes(paths[3].read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="compressed object size or GCS MD5 mismatch"):
        audit.verify_frozen_sources()
    paths[3].write_bytes(original)
    audit.verify_frozen_sources()
    audit.OLD_SERIES.write_text("changed after freeze")
    with pytest.raises(ValueError, match="frozen v1 series_sha256"):
        audit.verify_frozen_sources()


def test_incomplete_or_bad_shards_cleanup_scratch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    width = audit.FIVE_MIN_US
    paths = synthetic_source(tmp_path, monkeypatch,
                             [[event(width + 1, 1, unexpected="field")],
                              [event(4 * width + 1, 2)], [], [], [], [], [], [], [], []])
    _, _, columns = audit.verify_frozen_sources()
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError, match="exactly ten distinct"):
        audit.extract_event_rows(paths[:9], scratch, columns)
    with pytest.raises(ValueError, match="unexpected collection-event row schema"):
        audit.extract_event_rows(paths, scratch, columns)
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("invalid", [None, "", "-1", "+1", "1.0", " 1", -1,
                                         True, audit.INT64_MAX + 1])
def test_bad_int64_rejected(invalid: object) -> None:
    with pytest.raises(ValueError, match="non-integer"):
        audit.valid_int(invalid, "time")
