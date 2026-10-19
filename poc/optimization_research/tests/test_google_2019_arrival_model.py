"""Focused checks for the ten-shard Google 2019 arrival experiment."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_google_2019_arrival_model.py"
spec = importlib.util.spec_from_file_location("google2019_arrival_model", SCRIPT)
arrival = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(arrival)


def write_gzip(path: Path, events: list[dict]) -> None:
    with gzip.open(path, "wt") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")


def event(timestamp: int, collection_id: int, kind: int = 0,
          collection_type: int = 0, **extra: object) -> dict:
    return {"time": timestamp, "type": kind, "collection_type": collection_type,
            "collection_id": collection_id, **extra}


def test_unordered_shards_use_earliest_submit_and_zero_fill(tmp_path: Path) -> None:
    width = arrival.FIVE_MIN_US
    rows = [
        [event(5 * width + 1, 90, kind=6), event(3 * width + 2, 7),
         event(0, 1, kind=6), event(2 * width + 1, 8, collection_type=1)],
        [event(str(2 * width + 10), "7", kind="0", collection_type="0"),
         event(2 * width + 20, 9, missing_type=2)],
        [event(4 * width + 2, 10), event(4 * width + 1, 11)],
    ] + [[] for _ in range(7)]
    paths = []
    for index, events in enumerate(rows):
        path = tmp_path / f"shard{index}.gz"
        write_gzip(path, events)
        paths.append(path)
    result = arrival.extract_event_rows(paths, tmp_path)
    assert result["first_full_bin"] == 1
    assert result["last_full_bin"] == 4
    assert result["counts"] == [0, 1, 0, 2]
    assert result["audit"]["raw_job_submit_rows"] == 5
    assert result["audit"]["excluded_job_submit_missing_type_rows"] == 1
    assert result["audit"]["unique_job_submit_collection_ids"] == 3
    assert result["audit"]["duplicate_job_submit_collection_ids"] == 1
    assert result["audit"]["collection_ids_with_submit_in_multiple_shards"] == 1


def test_numeric_string_parser_rejects_malformed_values() -> None:
    assert arrival.valid_int("0", "time") == 0
    assert arrival.valid_int("123", "collection_id") == 123
    for bad in ("", "-1", "+1", "1.0", " 1", None, -1, True):
        with pytest.raises(ValueError, match="non-integer"):
            arrival.valid_int(bad, "time")


def test_future_or_target_counts_cannot_change_features_or_online_baselines() -> None:
    counts = list(range(600))
    offset = 350
    original = arrival.features_before(counts, offset, offset + 1)
    baseline = arrival.baseline_predictions(counts, offset)
    changed = counts.copy()
    changed[offset:] = [999999] * len(changed[offset:])
    assert arrival.features_before(changed, offset, offset + 1) == original
    assert arrival.baseline_predictions(changed, offset) == baseline
    changed[offset - 1] = 999999
    assert arrival.features_before(changed, offset, offset + 1) != original


def test_temporal_split_has_fixed_chronological_blocks() -> None:
    series = {"counts": [0] * 8928, "first_full_bin": 1, "last_full_bin": 8928}
    train, validation, test = arrival.split_offsets(series)
    assert len(train) == 5184
    assert len(validation) == 1728
    assert len(test) == 1728
    assert train[0] == 288
    assert train[-1] + 1 == validation[0]
    assert validation[-1] + 1 == test[0]
    assert test[-1] == 8927


def test_missing_verified_ten_shard_manifest_blocks_extraction(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    object_list = tmp_path / "objects.json"
    object_list.write_text(json.dumps({"objects": []}))
    schema = tmp_path / "schema.json"
    schema.write_text("[]")
    monkeypatch.setattr(arrival, "OBJECT_LIST", object_list)
    monkeypatch.setattr(arrival, "SCHEMA", schema)
    monkeypatch.setattr(arrival, "VERIFIED", tmp_path / "not_downloaded.json")
    monkeypatch.setattr(arrival, "EXPECTED_OBJECT_LIST_SHA256", arrival.sha256(object_list))
    monkeypatch.setattr(arrival, "EXPECTED_SCHEMA_SHA256", arrival.sha256(schema))
    with pytest.raises(ValueError, match="all ten shards"):
        arrival.verify_inputs()


def test_changed_series_is_rejected_by_freeze(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    protocol = tmp_path / "protocol.md"
    script = tmp_path / "script.py"
    series = tmp_path / "series.json"
    freeze = tmp_path / "freeze.json"
    protocol.write_text("frozen protocol")
    script.write_text("frozen script")
    series.write_text("frozen series")
    monkeypatch.setattr(arrival, "PROTOCOL", protocol)
    monkeypatch.setattr(arrival, "SERIES", series)
    monkeypatch.setattr(arrival, "FREEZE", freeze)
    monkeypatch.setattr(arrival, "__file__", str(script))
    record = {"source_manifest_sha256": "source", "protocol_sha256": arrival.sha256(protocol),
              "script_sha256": arrival.sha256(script), "series_sha256": arrival.sha256(series)}
    freeze.write_text(json.dumps(record))
    assert arrival.read_freeze("source") == record
    series.write_text("changed series")
    with pytest.raises(ValueError, match="freeze file differs"):
        arrival.read_freeze("source")
