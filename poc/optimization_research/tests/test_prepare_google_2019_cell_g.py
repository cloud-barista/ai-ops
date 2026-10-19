"""Synthetic source, sentinel, and integrity checks for held-out cell-g."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_google_2019_cell_g.py"
spec = importlib.util.spec_from_file_location("prepare_google_2019_cell_g", SCRIPT)
cell_g = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cell_g)


def event(timestamp: int | str, collection_id: int | str, kind: int | str = 0,
          collection_type: int | str = 0, **extra: object) -> dict:
    return {"time": timestamp, "type": kind, "collection_type": collection_type,
            "collection_id": collection_id, **extra}


def write_gzip(path: Path, rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def pinned_synthetic_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            rows: list[dict]) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    path = source / cell_g.EXPECTED_NAMES[0]
    write_gzip(path, rows)
    content = path.read_bytes()
    entry = {"name": path.name, "bytes": len(content), "generation": "100",
             "md5_base64": base64.b64encode(hashlib.md5(content).digest()).decode(),
             "crc32c_base64": "test"}
    manifest = source / "collection_events_object_manifest.json"
    manifest.write_text(json.dumps({"bucket": "clusterdata_2019_g", "objects": [entry]}))
    schema = source / "collection_events.schema.json"
    schema.write_text(json.dumps([{"name": name} for name in (
        "time", "type", "collection_type", "collection_id", "missing_type")]))
    verified = source / "verified_collection_events_manifest.json"
    verified.write_text(json.dumps({"bucket": "clusterdata_2019_g",
                                    "metadata_manifest_sha256": cell_g.sha256(manifest),
                                    "objects": [{**entry, "sha256": cell_g.sha256(path)}]}))
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"source_metadata_sha256": cell_g.sha256(manifest),
                                  "prepare_script_sha256": cell_g.sha256(SCRIPT)}))
    for name, value in (("SOURCE", source), ("OBJECT_LIST", manifest), ("SCHEMA", schema),
                        ("VERIFIED", verified), ("FREEZE", freeze),
                        ("EXPECTED_OBJECT_LIST_SHA256", cell_g.sha256(manifest)),
                        ("EXPECTED_SCHEMA_SHA256", cell_g.sha256(schema)),
                        ("EXPECTED_COMPRESSED_BYTES", len(content))):
        monkeypatch.setattr(cell_g, name, value)
    return path


def test_official_metadata_preflight_needs_no_event_download() -> None:
    entries, metadata_sha = cell_g.metadata_preflight()
    assert [entry["name"] for entry in entries] == cell_g.EXPECTED_NAMES
    assert metadata_sha == cell_g.EXPECTED_OBJECT_LIST_SHA256
    assert sum(entry["bytes"] for entry in entries) == 203_257_147


def test_unordered_submits_deduplicate_earliest_timed_and_exclude_sentinels(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    width = cell_g.FIVE_MIN_US
    rows = [event(0, 20), event(str(cell_g.INT64_MAX), 21),
            event(width + 1, 100, kind=6), event(4 * width + 1, 20),
            event(str(2 * width + 20), "20", kind="0", collection_type="0"),
            event(3 * width + 1, 30, missing_type=2), event(5 * width + 1, 101, kind=6),
            event(4 * width + 30, 21), event(3 * width + 30, 22),
            event(3 * width + 10, 22), event(4 * width + 40, 102, kind=1)]
    path = pinned_synthetic_source(tmp_path, monkeypatch, rows)
    verified, manifest_sha = cell_g.verify_inputs()
    assert verified == [path]
    assert manifest_sha == cell_g.sha256(cell_g.VERIFIED)
    scratch = tmp_path / "scratch"
    series = cell_g.extract_event_rows([path], scratch)
    assert series["target"] == "first_valid_JOB_collection_SUBMIT_count_per_complete_5m_bin"
    assert (series["time_min_us"], series["time_max_us"]) == (width + 1, 5 * width + 1)
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
    assert audit["collection_ids_with_submit_in_multiple_shards"] == 0
    assert not any(scratch.iterdir())
    assert not any(key in series for key in ("collection_id", "user", "collection_name"))


def test_verified_object_and_freeze_are_required(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = pinned_synthetic_source(tmp_path, monkeypatch, [])
    cell_g.FREEZE.unlink()
    with pytest.raises(ValueError, match="pre-download"):
        cell_g.verify_inputs()
    with pytest.raises(ValueError, match="pre-download"):
        cell_g.extract_event_rows([path], tmp_path / "scratch")
    with pytest.raises(ValueError, match="pre-download"):
        cell_g.fetch_all()
    cell_g.FREEZE.write_text(json.dumps({"source_metadata_sha256": cell_g.sha256(cell_g.OBJECT_LIST),
                                         "prepare_script_sha256": cell_g.sha256(SCRIPT)}))
    cell_g.VERIFIED.unlink()
    with pytest.raises(ValueError, match="single cell-g object"):
        cell_g.verify_inputs()
    with pytest.raises(ValueError, match="exactly one"):
        cell_g.extract_event_rows([], tmp_path / "scratch")


def test_manifest_and_compressed_content_integrity_are_checked(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = pinned_synthetic_source(tmp_path, monkeypatch, [])
    verified = json.loads(cell_g.VERIFIED.read_text())
    verified["objects"][0]["sha256"] = "0" * 64
    cell_g.VERIFIED.write_text(json.dumps(verified))
    with pytest.raises(ValueError, match="local SHA-256 mismatch"):
        cell_g.verify_inputs()
    verified["objects"][0]["sha256"] = cell_g.sha256(path)
    cell_g.VERIFIED.write_text(json.dumps(verified))
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="size or GCS MD5 mismatch"):
        cell_g.verify_inputs()


@pytest.mark.parametrize("invalid", [None, "", "-1", "+1", "1.0", " 1", -1,
                                         True, cell_g.INT64_MAX + 1])
def test_invalid_int64_fields_fail_closed(invalid: object) -> None:
    with pytest.raises(ValueError, match="non-integer"):
        cell_g.valid_int(invalid, "time")


def test_unexpected_row_field_is_rejected_and_scratch_removed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = pinned_synthetic_source(tmp_path, monkeypatch,
                                   [event(cell_g.FIVE_MIN_US + 1, 1, secret="unexpected"),
                                    event(4 * cell_g.FIVE_MIN_US + 1, 2)])
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError, match="unexpected collection-event row schema"):
        cell_g.extract_event_rows([path], scratch)
    assert not any(scratch.iterdir())


def test_fetch_requires_hash_pinned_pre_download_freeze(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze = tmp_path / "freeze.json"
    monkeypatch.setattr(cell_g, "FREEZE", freeze)
    with pytest.raises(ValueError, match="pre-download"):
        cell_g.require_pre_download_freeze("metadata")
    freeze.write_text(json.dumps({"source_metadata_sha256": "metadata",
                                  "prepare_script_sha256": "incorrect"}))
    with pytest.raises(ValueError, match="does not pin"):
        cell_g.require_pre_download_freeze("metadata")
    freeze.write_text(json.dumps({"source_metadata_sha256": "metadata",
                                  "prepare_script_sha256": cell_g.sha256(SCRIPT)}))
    cell_g.require_pre_download_freeze("metadata")


def test_generation_pinned_fetch_checks_bytes_before_publish(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(cell_g, "SOURCE", source)
    compressed = gzip.compress(b'{"time":123}\n')
    entry = {"name": cell_g.EXPECTED_NAMES[0], "bytes": len(compressed),
             "md5_base64": base64.b64encode(hashlib.md5(compressed).digest()).decode(),
             "generation": "1596913609702066"}
    freeze = tmp_path / "freeze.json"
    monkeypatch.setattr(cell_g, "FREEZE", freeze)
    monkeypatch.setattr(cell_g, "metadata_preflight", lambda: ([entry], "synthetic_metadata"))
    seen = []

    def fake_urlopen(request: object, timeout: int) -> io.BytesIO:
        seen.append(request.full_url)
        assert timeout == 120
        return io.BytesIO(compressed)

    monkeypatch.setattr(cell_g.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="pre-download"):
        cell_g.fetch_object(entry)
    assert not seen
    freeze.write_text(json.dumps({"source_metadata_sha256": "synthetic_metadata",
                                  "prepare_script_sha256": cell_g.sha256(SCRIPT)}))
    assert cell_g.fetch_object(entry) == hashlib.sha256(compressed).hexdigest()
    assert "clusterdata_2019_g" in seen[0]
    assert "generation=1596913609702066" in seen[0]
    assert (source / entry["name"]).read_bytes() == compressed
    assert not list(source.glob("*.part"))
    (source / entry["name"]).unlink()
    entry["md5_base64"] = base64.b64encode(b"bad hash bad hash").decode()
    with pytest.raises(ValueError, match="official size/MD5"):
        cell_g.fetch_object(entry)
    assert not (source / entry["name"]).exists()
