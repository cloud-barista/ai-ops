"""Synthetic source and integrity checks for held-out Google 2019 cell-b."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_google_2019_cell_b.py"
spec = importlib.util.spec_from_file_location("prepare_google_2019_cell_b", SCRIPT)
cell_b = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cell_b)


def event(timestamp: int | str, collection_id: int | str, kind: int | str = 0,
          collection_type: int | str = 0, **extra: object) -> dict:
    return {"time": timestamp, "type": kind, "collection_type": collection_type,
            "collection_id": collection_id, **extra}


def write_gzip(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def pinned_synthetic_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            rows: list[list[dict]]) -> list[Path]:
    source = tmp_path / "source"
    source.mkdir()
    entries = []
    paths = []
    for index, shard_rows in enumerate(rows):
        path = source / cell_b.EXPECTED_NAMES[index]
        write_gzip(path, shard_rows)
        paths.append(path)
        content = path.read_bytes()
        entries.append({"name": path.name, "bytes": len(content),
                        "generation": str(100 + index),
                        "md5_base64": base64.b64encode(hashlib.md5(content).digest()).decode(),
                        "crc32c_base64": "test", "sha256": hashlib.sha256(content).hexdigest()})
    manifest = source / "collection_events_object_manifest.json"
    manifest.write_text(json.dumps({"bucket": "clusterdata_2019_b", "objects": [
        {key: value for key, value in entry.items() if key != "sha256"} for entry in entries]}))
    schema = source / "collection_events.schema.json"
    schema.write_text(json.dumps([{"name": name} for name in (
        "time", "type", "collection_type", "collection_id", "missing_type")]))
    verified = source / "verified_collection_events_manifest.json"
    verified.write_text(json.dumps({"bucket": "clusterdata_2019_b",
                                    "metadata_manifest_sha256": cell_b.sha256(manifest),
                                    "objects": entries}))
    monkeypatch.setattr(cell_b, "SOURCE", source)
    monkeypatch.setattr(cell_b, "OBJECT_LIST", manifest)
    monkeypatch.setattr(cell_b, "SCHEMA", schema)
    monkeypatch.setattr(cell_b, "VERIFIED", verified)
    monkeypatch.setattr(cell_b, "EXPECTED_OBJECT_LIST_SHA256", cell_b.sha256(manifest))
    monkeypatch.setattr(cell_b, "EXPECTED_SCHEMA_SHA256", cell_b.sha256(schema))
    monkeypatch.setattr(cell_b, "EXPECTED_COMPRESSED_BYTES", sum(item["bytes"] for item in entries))
    return paths


def test_real_metadata_preflight_needs_no_event_download() -> None:
    entries, metadata_sha = cell_b.metadata_preflight()
    assert [entry["name"] for entry in entries] == cell_b.EXPECTED_NAMES
    assert metadata_sha == cell_b.EXPECTED_OBJECT_LIST_SHA256


def test_unordered_shards_deduplicate_earliest_timed_submit_and_exclude_sentinels(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    width = cell_b.FIVE_MIN_US
    rows = [
        [event(0, 20), event(str(cell_b.INT64_MAX), 21),
         event(width + 1, 100, kind=6), event(4 * width + 1, 20)],
        [event(str(2 * width + 20), "20", kind="0", collection_type="0"),
         event(3 * width + 1, 30, missing_type=2),
         event(5 * width + 1, 101, kind=6)],
        [event(4 * width + 30, 21), event(3 * width + 30, 22)],
        [event(3 * width + 10, 22), event(4 * width + 40, 102, kind=1)],
    ]
    paths = pinned_synthetic_source(tmp_path, monkeypatch, rows)
    verified, manifest_sha = cell_b.verify_inputs()
    assert verified == paths
    assert manifest_sha == cell_b.sha256(cell_b.VERIFIED)
    scratch = tmp_path / "scratch"
    series = cell_b.extract_event_rows(paths, scratch)
    assert series["target"] == "first_valid_JOB_collection_SUBMIT_count_per_complete_5m_bin"
    assert series["time_min_us"] == width + 1
    assert series["time_max_us"] == 5 * width + 1
    assert (series["first_full_bin"], series["last_full_bin"]) == (2, 4)
    assert series["counts"] == [1, 1, 1]
    audit = series["audit"]
    assert audit["all_collection_event_rows"] == 11
    assert audit["non_timed_pretrace_rows"] == 1
    assert audit["non_timed_posttrace_rows"] == 1
    assert audit["non_timed_job_submit_rows"] == 2
    assert audit["raw_job_submit_rows"] == 6
    assert audit["excluded_job_submit_missing_type_rows"] == 1
    assert audit["valid_job_submit_rows"] == 5
    assert audit["unique_job_submit_collection_ids"] == 3
    assert audit["duplicate_job_submit_collection_ids"] == 2
    assert audit["collection_ids_with_submit_in_multiple_shards"] == 2
    assert not any(scratch.iterdir())  # deduplication DB with raw IDs is removed
    assert not any(key in series for key in ("collection_id", "user", "collection_name"))


def test_all_four_verified_shards_are_required(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = pinned_synthetic_source(tmp_path, monkeypatch, [[], [], [], []])
    cell_b.VERIFIED.unlink()
    with pytest.raises(ValueError, match="all four"):
        cell_b.verify_inputs()
    with pytest.raises(ValueError, match="exactly four"):
        cell_b.extract_event_rows(paths[:3], tmp_path / "scratch")


def test_manifest_and_compressed_content_integrity_are_checked(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = pinned_synthetic_source(tmp_path, monkeypatch, [[], [], [], []])
    verified = json.loads(cell_b.VERIFIED.read_text())
    verified["objects"][0]["sha256"] = "0" * 64
    cell_b.VERIFIED.write_text(json.dumps(verified))
    with pytest.raises(ValueError, match="local SHA-256 mismatch"):
        cell_b.verify_inputs()
    verified["objects"][0]["sha256"] = cell_b.sha256(paths[0])
    cell_b.VERIFIED.write_text(json.dumps(verified))
    paths[1].write_bytes(paths[1].read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="size or GCS MD5 mismatch"):
        cell_b.verify_inputs()


@pytest.mark.parametrize("invalid", [None, "", "-1", "+1", "1.0", " 1", -1,
                                         True, cell_b.INT64_MAX + 1])
def test_invalid_int64_fields_fail_closed(invalid: object) -> None:
    with pytest.raises(ValueError, match="non-integer"):
        cell_b.valid_int(invalid, "time")


def test_unexpected_row_field_is_rejected_and_scratch_removed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    width = cell_b.FIVE_MIN_US
    paths = pinned_synthetic_source(tmp_path, monkeypatch,
                                    [[event(width + 1, 1, secret="unexpected")],
                                     [event(4 * width + 1, 2)], [], []])
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError, match="unexpected collection-event row schema"):
        cell_b.extract_event_rows(paths, scratch)
    assert not any(scratch.iterdir())


def test_fetch_requires_hash_pinned_pre_download_freeze(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze = tmp_path / "freeze.json"
    monkeypatch.setattr(cell_b, "FREEZE", freeze)
    with pytest.raises(ValueError, match="pre-download"):
        cell_b.require_pre_download_freeze("metadata")
    freeze.write_text(json.dumps({"source_metadata_sha256": "metadata",
                                  "prepare_script_sha256": "incorrect"}))
    with pytest.raises(ValueError, match="does not pin"):
        cell_b.require_pre_download_freeze("metadata")
    freeze.write_text(json.dumps({"source_metadata_sha256": "metadata",
                                  "prepare_script_sha256": cell_b.sha256(SCRIPT)}))
    cell_b.require_pre_download_freeze("metadata")


def test_generation_pinned_fetch_checks_bytes_before_publish(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(cell_b, "SOURCE", source)
    compressed = gzip.compress(b'{"time":123}\n')
    entry = {"name": cell_b.EXPECTED_NAMES[0], "bytes": len(compressed),
             "md5_base64": base64.b64encode(hashlib.md5(compressed).digest()).decode(),
             "generation": "1596844191969755"}
    seen = []

    def fake_urlopen(request: object, timeout: int) -> io.BytesIO:
        seen.append(request.full_url)
        assert timeout == 120
        return io.BytesIO(compressed)

    monkeypatch.setattr(cell_b.urllib.request, "urlopen", fake_urlopen)
    assert cell_b.fetch_object(entry) == hashlib.sha256(compressed).hexdigest()
    assert "generation=1596844191969755" in seen[0]
    assert (source / entry["name"]).read_bytes() == compressed
    assert not list(source.glob("*.part"))
    (source / entry["name"]).unlink()
    entry["md5_base64"] = base64.b64encode(b"bad hash bad hash").decode()
    with pytest.raises(ValueError, match="official size/MD5"):
        cell_b.fetch_object(entry)
    assert not (source / entry["name"]).exists()
