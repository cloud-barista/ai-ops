"""Synthetic-only tests for the disjoint Philly machine holdout collector."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pyarrow.parquet as pq
import pytest

from scripts import fetch_philly_gpu_sample as source
from scripts import fetch_philly_gpu_holdout as holdout
from scripts.fetch_philly_gpu_holdout import (
    LOWER_RESIDUE, UPPER_RESIDUE, machine_bucket, machine_selected,
    scan_archive,
)
from scripts.normalize_philly_gpu_sample import normalize_sample


def _machine_in_range(low: int, high: int) -> str:
    return next(f"machine-{index}" for index in range(100_000)
                if low <= machine_bucket(f"machine-{index}") < high)


def _archive(gpu_csv: str, machine_csv: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        directory = tarfile.TarInfo("trace-data")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for name in sorted(source.EXPECTED_FILES):
            payload = (gpu_csv if name == source.GPU_FILE else
                       machine_csv if name == source.MACHINE_FILE else
                       "placeholder\n").encode()
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def _scan(data: bytes, output, *, expected_sha256: str | None = None):
    return scan_archive(io.BytesIO(data), output=output, expected_size=len(data),
                        expected_sha256=expected_sha256 or hashlib.sha256(data).hexdigest())


def test_holdout_hash_buckets_do_not_overlap_frozen_first_five_percent():
    old = _machine_in_range(0, LOWER_RESIDUE)
    holdout_low = _machine_in_range(LOWER_RESIDUE, LOWER_RESIDUE + 1)
    holdout_high = _machine_in_range(UPPER_RESIDUE - 1, UPPER_RESIDUE)
    outside = _machine_in_range(UPPER_RESIDUE, 100)
    assert source.machine_selected(old)
    assert not machine_selected(old)
    assert not source.machine_selected(holdout_low)
    assert machine_selected(holdout_low)
    assert machine_selected(holdout_high)
    assert not machine_selected(outside)


def test_full_archive_preserves_holdout_rows_values_order_and_irregular_fields(tmp_path):
    old = _machine_in_range(0, 5)
    chosen = _machine_in_range(5, 15)
    other = _machine_in_range(15, 100)
    rows = [
        ["2017-10-03 00:08:00 PDT", old, "99", "0", "0", "0", "0", "0", "0", "0"],
        ["2017-10-03 00:08:00 PDT", chosen, "NA", "85.5", "0", "0", "0", "0", "0", "0"],
        ["2017-10-03 00:09:00 PDT", other, "77", "0", "0", "0", "0", "0", "0", "0"],
        ["2017-10-03 00:08:00 PDT", chosen, "4.5", "1", "2", "3", "4", "5", "6", "7"],
        ["2017-11-05 01:30:00 PDT", chosen, "20", "NA", ""],
        ["2017-11-05 01:30:00 PST", chosen, "40", "0", ""],
    ]
    gpu_csv = "time,machineId,gpu0_util\n" + "\n".join(",".join(row) for row in rows) + "\n"
    machine_csv = "machineId,numGPUs,gpuMem\n" + "".join(
        f"{machine},8,24GB\n" for machine in [old, chosen, other]
    )
    data = _archive(gpu_csv, machine_csv)
    output = tmp_path / "holdout.parquet"
    audit = _scan(data, output)
    saved = pq.read_table(output).to_pydict()
    assert audit["complete"] and audit["integrity_pass"]
    assert audit["compressed_sha256"] == hashlib.sha256(data).hexdigest()
    assert audit["collector_script_sha256"] == hashlib.sha256(
        Path(holdout.__file__).read_bytes()).hexdigest()
    assert audit["source_parser_script_sha256"] == hashlib.sha256(
        Path(source.__file__).read_bytes()).hexdigest()
    assert (audit["sample_bucket_lower_inclusive"],
            audit["sample_bucket_upper_exclusive"]) == (5, 15)
    assert audit["gpu_csv"]["rows"] == 6
    assert audit["gpu_csv"]["selected_rows"] == 4
    assert audit["gpu_csv"]["selected_machine_ids"] == [chosen]
    assert audit["gpu_csv"]["selected_duplicate_machine_utc_minute_rows"] == 1
    assert audit["gpu_csv"]["selected_machines_missing_from_machine_list"] == []
    assert saved["source_row_index"] == [2, 4, 5, 6]
    assert saved["machine_id"] == [chosen] * 4
    assert saved["gpu0_util"] == [None, 4.5, None, None]
    assert saved["gpu1_util"] == [85.5, 1.0, None, None]
    assert saved["source_gpu_field_count"] == [8, 8, 3, 3]
    assert saved["raw_irregular_fields_json"][2:] == [
        json.dumps(row, separators=(",", ":")) for row in rows[4:]
    ]
    assert saved["time_utc_s"][3] - saved["time_utc_s"][2] == 3600
    assert audit["sample_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert len(audit["tar_members"]) == 6
    assert audit["tar_members"][0]["is_file"] is False

    audit_path = tmp_path / "holdout_audit.json"
    audit_path.write_text(json.dumps(audit))
    normalized = tmp_path / "normalized.parquet"
    normalized_audit = tmp_path / "normalized_audit.json"
    lineage = normalize_sample(output, audit_path, normalized, normalized_audit)
    assert lineage["rows"] == 4
    assert lineage["usable_rows"] == 2
    assert lineage["invalid_rows"] == 2
    assert pq.read_table(normalized, columns=["source_row_index"]).column(0).to_pylist() == [2, 4, 5, 6]


@pytest.mark.parametrize("failure", ["hash", "size", "truncated"])
def test_archive_integrity_failure_cannot_promote_holdout(tmp_path, failure):
    chosen = _machine_in_range(5, 15)
    data = _archive(f"2017-10-03 00:08:00 PDT,{chosen},1,2,3,4,5,6,7,8\n",
                    f"{chosen},8,24GB\n")
    output = tmp_path / "holdout.parquet"
    if failure == "hash":
        kwargs = {"expected_size": len(data), "expected_sha256": "0" * 64}
        supplied = data
    elif failure == "size":
        kwargs = {"expected_size": len(data) + 1,
                  "expected_sha256": hashlib.sha256(data).hexdigest()}
        supplied = data
    else:
        kwargs = {"expected_size": len(data),
                  "expected_sha256": hashlib.sha256(data).hexdigest()}
        supplied = data[:-8]
    with pytest.raises((EOFError, OSError, ValueError)):
        scan_archive(io.BytesIO(supplied), output=output, **kwargs)
    assert not output.exists()
    assert not (tmp_path / "holdout.parquet.incomplete").exists()


def test_collector_refuses_to_replace_existing_holdout(tmp_path):
    output = tmp_path / "holdout.parquet"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        scan_archive(io.BytesIO(b"anything"), output=output,
                     expected_size=8, expected_sha256="0" * 64)
    assert output.read_bytes() == b"existing"


def test_direct_script_import_mode_works_without_repository_on_python_path():
    script = Path(holdout.__file__).resolve()
    program = """
import importlib.util
import sys
from pathlib import Path
script = Path(sys.argv[1])
sys.path.insert(0, str(script.parent))
spec = importlib.util.spec_from_file_location('holdout_direct', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.machine_bucket('synthetic-machine-0') in range(100)
"""
    result = subprocess.run([sys.executable, "-I", "-c", program, str(script)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _freeze_fixture(tmp_path):
    spec_path = tmp_path / "model_spec.json"
    spec_path.write_text(json.dumps({
        "kind": holdout.ORIGINAL_MODEL_KIND,
        "original_normalized_source_sha256": holdout.ORIGINAL_NORMALIZED_SHA256,
        "original_score_sha256": holdout.ORIGINAL_SCORE_SHA256,
        "original_dry_run_sha256": holdout.ORIGINAL_DRY_RUN_SHA256,
    }))
    spec_sha = holdout.sha256_file(spec_path)
    protocol_path = tmp_path / "philly_machine_holdout_protocol_frozen.md"
    protocol_path.write_text(
        "**상태: FROZEN**\n"
        f"- `model_spec.json` SHA256 `{spec_sha}`\n"
        f"- `scripts/fetch_philly_gpu_holdout.py` SHA256 "
        f"`{holdout.sha256_file(Path(holdout.__file__))}`\n"
    )
    return protocol_path, holdout.sha256_file(protocol_path), spec_path, spec_sha


@pytest.mark.parametrize("change", [
    "missing_hash", "bad_protocol_hash", "bad_spec_hash", "draft_protocol",
    "missing_model_pin", "missing_collector_pin", "wrong_model_kind",
])
def test_missing_or_mismatched_freeze_rejects_before_any_http(tmp_path, monkeypatch, change):
    protocol, protocol_sha, spec, spec_sha = _freeze_fixture(tmp_path)
    if change == "missing_hash":
        protocol_sha = ""
    elif change == "bad_protocol_hash":
        protocol_sha = "0" * 64
    elif change == "bad_spec_hash":
        spec_sha = "0" * 64
    elif change == "draft_protocol":
        protocol.write_text(protocol.read_text().replace("FROZEN", "DRAFT"))
        protocol_sha = holdout.sha256_file(protocol)
    elif change == "missing_model_pin":
        protocol.write_text("\n".join(line for line in protocol.read_text().splitlines()
                                      if "model_spec.json" not in line))
        protocol_sha = holdout.sha256_file(protocol)
    elif change == "missing_collector_pin":
        protocol.write_text("\n".join(line for line in protocol.read_text().splitlines()
                                      if "fetch_philly_gpu_holdout.py" not in line))
        protocol_sha = holdout.sha256_file(protocol)
    else:
        spec.write_text(json.dumps({"kind": "new-machine-trained-model",
                                    "original_normalized_source_sha256":
                                    holdout.ORIGINAL_NORMALIZED_SHA256,
                                    "original_score_sha256": holdout.ORIGINAL_SCORE_SHA256,
                                    "original_dry_run_sha256":
                                    holdout.ORIGINAL_DRY_RUN_SHA256}))
        spec_sha = holdout.sha256_file(spec)
        protocol.write_text("**상태: FROZEN**\n"
                            f"- `model_spec.json` SHA256 `{spec_sha}`\n"
                            f"- `scripts/fetch_philly_gpu_holdout.py` SHA256 "
                            f"`{holdout.sha256_file(Path(holdout.__file__))}`\n")
        protocol_sha = holdout.sha256_file(protocol)
    monkeypatch.setattr(holdout.requests, "Session", lambda: (_ for _ in ()).throw(
        AssertionError("HTTP session opened before freeze check")))
    monkeypatch.setattr(holdout, "OUTPUT_DIR", tmp_path / "output")
    with pytest.raises(ValueError):
        holdout.run(expected_protocol_sha256=protocol_sha,
                    expected_model_spec_sha256=spec_sha,
                    protocol=protocol, model_spec=spec)
    assert not (tmp_path / "output").exists()


def test_valid_freeze_guard_records_both_explicit_hashes(tmp_path):
    protocol, protocol_sha, spec, spec_sha = _freeze_fixture(tmp_path)
    audit = holdout.verify_pre_fetch_freeze(protocol, protocol_sha, spec, spec_sha)
    assert audit == {
        "frozen_holdout_protocol_sha256": protocol_sha,
        "original_only_model_spec_sha256": spec_sha,
        "holdout_collector_script_sha256": holdout.sha256_file(Path(holdout.__file__)),
    }


@pytest.mark.parametrize("missing", ["protocol", "model_spec"])
def test_missing_freeze_file_rejects_before_any_http(tmp_path, monkeypatch, missing):
    protocol, protocol_sha, spec, spec_sha = _freeze_fixture(tmp_path)
    (protocol if missing == "protocol" else spec).unlink()
    monkeypatch.setattr(holdout.requests, "Session", lambda: (_ for _ in ()).throw(
        AssertionError("HTTP session opened before freeze check")))
    monkeypatch.setattr(holdout, "OUTPUT_DIR", tmp_path / "output")
    with pytest.raises(FileNotFoundError):
        holdout.run(expected_protocol_sha256=protocol_sha,
                    expected_model_spec_sha256=spec_sha,
                    protocol=protocol, model_spec=spec)
    assert not (tmp_path / "output").exists()
