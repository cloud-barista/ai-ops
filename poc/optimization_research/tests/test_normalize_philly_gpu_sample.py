"""Check lossless first-width reconstruction of the verified Philly sample."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.fetch_philly_gpu_sample import OUTPUT_SCHEMA
import scripts.normalize_philly_gpu_sample as normalize_module
from scripts.normalize_philly_gpu_sample import (
    SCHEMA, decode_source_values, normalize_sample, sha256_file,
)


def _source_row(index: int, machine: str, gpu_tokens: list[str]) -> dict:
    time_text = f"2017-10-03 00:{index:02d}:00 PDT"
    count = len(gpu_tokens)
    raw_fields = [time_text, machine, *gpu_tokens, ""]
    row = {
        "source_row_index": index,
        "time_text": time_text,
        "time_utc_s": 1507014000 + index * 60,
        "machine_id": machine,
        "source_gpu_field_count": count + (count in {2, 4}),
        "raw_irregular_fields_json": None if count == 8 else json.dumps(raw_fields),
    }
    row.update({f"gpu{i}_util": (
        None if count != 8 or gpu_tokens[i] == "NA" else float(gpu_tokens[i])
    ) for i in range(8)})
    return row


def test_source_decoder_distinguishes_2_4_8_16_slots_and_terminal_comma():
    for width in (2, 4, 8, 16):
        row = _source_row(1, "m4", ["NA", *[str(i) for i in range(1, width)]])
        observed, values = decode_source_values(row)
        assert observed == width
        assert values[:2] == [None, 1.0]
        assert len(values) == width
    malformed = _source_row(1, "m4", ["1", "2"])
    malformed["source_gpu_field_count"] = 5
    with pytest.raises(ValueError, match="terminal delimiter"):
        decode_source_values(malformed)
    malformed = _source_row(1, "m4", ["1", "2"])
    malformed["raw_irregular_fields_json"] = json.dumps([
        malformed["time_text"], "m4", "1", "2", "", ""])
    with pytest.raises(ValueError, match="terminal delimiter"):
        decode_source_values(malformed)


def test_normalization_keeps_order_recovers_low_gpu_rows_and_masks_width_change(tmp_path):
    rows = [
        _source_row(1, "m4", ["NA", "80"]),
        _source_row(2, "m4", ["1", "2", "3", "4"]),
        _source_row(3, "m4", ["10", "20"]),
        _source_row(4, "m8", ["1", "2", "3", "4"]),
        _source_row(5, "m16", [str(i) for i in range(8)]),
        _source_row(6, "m16", [str(i) for i in range(16)]),
    ]
    source = tmp_path / "source.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA), source)
    source_audit = tmp_path / "source_audit.json"
    source_audit.write_text(json.dumps({
        "integrity_pass": True, "complete": True,
        "sample_sha256": sha256_file(source),
        "compressed_sha256": "a" * 64,
        "gpu_csv": {"selected_rows": len(rows),
                    "selected_machine_ids": ["m16", "m4", "m8"]},
    }))
    output = tmp_path / "derived.parquet"
    audit_path = tmp_path / "derived_audit.json"
    audit = normalize_sample(source, source_audit, output, audit_path)
    saved = pq.read_table(output).to_pydict()
    assert pq.read_schema(output) == SCHEMA
    assert saved["source_row_index"] == list(range(1, 7))
    assert saved["expected_gpu_slots"] == [2, 2, 2, 4, 8, 8]
    assert saved["observed_gpu_slots"] == [2, 4, 2, 4, 8, 16]
    assert saved["width_matches_first"] == [True, False, True, True, True, False]
    assert saved["row_usable"] == [True, False, True, True, True, False]
    assert saved["gpu0_util"] == [None, None, 10.0, 1.0, 0.0, None]
    assert saved["gpu1_util"] == [80.0, None, 20.0, 2.0, 1.0, None]
    assert saved["gpu2_util"] == [None, None, None, 3.0, 2.0, None]
    assert audit["rows"] == 6
    assert audit["usable_rows"] == 4
    assert audit["invalid_rows"] == 2
    assert audit["source_sha256"] == sha256_file(source)
    assert audit["output_sha256"] == sha256_file(output)
    assert audit["source_row_index_sequence_sha256"] == audit["derived_row_index_sequence_sha256"]
    assert audit["normalization_script_sha256"] == sha256_file(
        Path(normalize_module.__file__))
    assert json.loads(audit_path.read_text()) == audit


def test_source_sha_mismatch_does_not_create_final_output(tmp_path):
    source = tmp_path / "source.parquet"
    pq.write_table(pa.Table.from_pylist([_source_row(1, "m4", ["1", "2"])],
                                      schema=OUTPUT_SCHEMA), source)
    source_audit = tmp_path / "source_audit.json"
    source_audit.write_text(json.dumps({"integrity_pass": True, "complete": True,
                                        "sample_sha256": "0" * 64}))
    output = tmp_path / "derived.parquet"
    with pytest.raises(ValueError, match="SHA256"):
        normalize_sample(source, source_audit, output, tmp_path / "audit.json")
    assert not output.exists()
