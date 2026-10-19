"""Source-format and integrity checks for the bounded Philly stream reader."""

from __future__ import annotations

import hashlib
import io
import tarfile

import pyarrow.parquet as pq
import pytest

from scripts.fetch_philly_gpu_sample import (
    EXPECTED_FILES, machine_selected, parse_gpu_row, parse_source_time,
    scan_archive,
)


def _fake_archive(gpu_csv: str, machine_csv: str = "machineId,numGPUs,gpuMem\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in sorted(EXPECTED_FILES):
            if name.endswith("cluster_gpu_util"):
                payload = gpu_csv.encode()
            elif name.endswith("cluster_machine_list"):
                payload = machine_csv.encode()
            else:
                payload = b"placeholder\n"
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def _machine(want_selected: bool) -> str:
    return next(f"machine-{i}" for i in range(10_000)
                if machine_selected(f"machine-{i}") == want_selected)


def test_time_zone_suffix_disambiguates_fall_clock_hour():
    pdt, _ = parse_source_time("2017-11-05 01:30:00 PDT")
    pst, _ = parse_source_time("2017-11-05 01:30:00 PST")
    assert pst - pdt == 3600
    with pytest.raises(ValueError, match="Invalid Philly timestamp"):
        parse_source_time("2017-11-05 01:30:00 UTC")


def test_source_row_accepts_trailing_empty_field_and_na():
    row = ["2017-10-03 00:08:00 PDT", "m29", "60.8", "NA",
           "100", "0", "0", "0", "0", "0", ""]
    _, _, _, machine, values, status, field_count, raw_json = parse_gpu_row(row, 1)
    assert machine == "m29"
    assert values[:3] == [60.8, None, 100.0]
    assert status["na"] == 1
    assert status["valid"] == 7
    assert field_count == 8
    assert raw_json is None


def test_irregular_16_gpu_fields_are_retained_as_invalid_placeholder():
    row = ["2017-11-05 01:00:00 PDT", "m29"] + ["0"] * 16 + [""]
    _, _, _, _, values, status, field_count, raw_json = parse_gpu_row(row, 46982)
    assert field_count == 16
    assert values == [None] * 8
    assert status["valid"] == 16
    assert '"m29"' in raw_json
    assert raw_json.endswith(',""]')


def test_full_stream_verifies_hash_and_preserves_selected_duplicates(tmp_path):
    chosen, other = _machine(True), _machine(False)
    header = "time,machineId," + ",".join(f"gpu{i}_util" for i in range(8))
    rows = [
        ["2017-10-03 00:08:00 PDT", chosen, "NA", "90", "0", "0", "0", "0", "0", "0"],
        ["2017-10-03 00:08:00 PDT", other, "25", "0", "0", "0", "0", "0", "0", "0"],
        ["2017-10-03 00:08:00 PDT", chosen, "80", "0", "0", "0", "0", "0", "0", "0"],
        ["2017-11-05 01:30:00 PST", chosen, "50", "0", "0", "0", "0", "0", "0", "0"],
    ]
    csv_text = header + "\n" + "\n".join(",".join(row) for row in rows) + "\n"
    machine_csv = f"machineId,numGPUs,gpuMem\n{chosen},2,24GB\n{other},8,16GB\n"
    data = _fake_archive(csv_text, machine_csv)
    output = tmp_path / "sample.parquet"
    result = scan_archive(io.BytesIO(data), output=output, expected_size=len(data),
                          expected_sha256=hashlib.sha256(data).hexdigest())
    audit = result["gpu_csv"]
    assert result["complete"] is True
    assert audit["rows"] == 4
    assert audit["selected_rows"] == 3
    assert audit["selected_duplicate_machine_utc_minute_rows"] == 1
    assert audit["non_minute_timestamp_rows"] == 0
    assert audit["selected_machines_missing_from_machine_list"] == []
    assert audit["gpu_cell_status"]["na"] == 1
    saved = pq.read_table(output).to_pydict()
    assert saved["source_row_index"] == [1, 3, 4]
    assert saved["gpu0_util"] == [None, 80.0, 50.0]
    assert saved["time_text"][0] == "2017-10-03 00:08:00 PDT"
    assert len(result["tar_members"]) == 5
    assert result["machine_list"]["unique_machines"] == 2


def test_bad_hash_cannot_promote_sample(tmp_path):
    chosen = _machine(True)
    row = f"2017-10-03 00:08:00 PDT,{chosen},0,0,0,0,0,0,0,0\n"
    data = _fake_archive(row, f"{chosen},8,24GB\n")
    output = tmp_path / "sample.parquet"
    with pytest.raises(ValueError, match="SHA256"):
        scan_archive(io.BytesIO(data), output=output, expected_size=len(data),
                     expected_sha256="0" * 64)
    assert not output.exists()
    assert not (tmp_path / "sample.parquet.incomplete").exists()


def test_irregular_sample_row_retains_source_fields_and_nulls_model_slots(tmp_path):
    chosen = _machine(True)
    row = ["2017-11-05 01:00:00 PDT", chosen] + [str(i) for i in range(16)] + [""]
    data = _fake_archive(",".join(row) + "\n", f"{chosen},16,24GB\n")
    output = tmp_path / "sample.parquet"
    result = scan_archive(io.BytesIO(data), output=output, expected_size=len(data),
                          expected_sha256=hashlib.sha256(data).hexdigest())
    audit = result["gpu_csv"]
    assert audit["gpu_field_count_histogram"] == {16: 1}
    assert audit["irregular_rows"] == 1
    saved = pq.read_table(output).to_pydict()
    assert saved["source_gpu_field_count"] == [16]
    assert saved["gpu0_util"] == [None]
    assert saved["gpu7_util"] == [None]
    assert chosen in saved["raw_irregular_fields_json"][0]


def test_truncated_gzip_cannot_promote_sample(tmp_path):
    chosen = _machine(True)
    data = _fake_archive(f"2017-10-03 00:08:00 PDT,{chosen},0,0,0,0,0,0,0,0\n",
                         f"{chosen},8,24GB\n")
    truncated = data[:-8]
    output = tmp_path / "sample.parquet"
    with pytest.raises((EOFError, OSError, ValueError)):
        scan_archive(io.BytesIO(truncated), output=output, expected_size=len(data),
                     expected_sha256=hashlib.sha256(data).hexdigest())
    assert not output.exists()
    assert not (tmp_path / "sample.parquet.incomplete").exists()
