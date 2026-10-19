"""Stream and audit Microsoft's pinned Philly GPU trace without saving its tarball.

Every GPU CSV row is parsed. All rows from a deterministic 5% sample of
machine IDs are retained at original one-minute granularity, including NA,
duplicate timestamps, and source row order. No outcomes or risk labels are
constructed here.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import requests


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d"
URL = (
    "https://media.githubusercontent.com/media/msr-fiddle/philly-traces/"
    f"{SOURCE_COMMIT}/trace-data.tar.gz"
)
EXPECTED_SIZE = 1_055_988_361
EXPECTED_SHA256 = "2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c"
EXPECTED_ETAG = f'"{EXPECTED_SHA256}"'
SAMPLE_MODULUS = 100
SAMPLE_RESIDUES = 5
MAX_SAMPLE_BYTES = 1_900_000_000
MIN_FREE_BYTES = 5_000_000_000
ROW_GROUP_SIZE = 50_000
EXPECTED_FILES = {
    "trace-data/cluster_cpu_util",
    "trace-data/cluster_gpu_util",
    "trace-data/cluster_mem_util",
    "trace-data/cluster_job_log",
    "trace-data/cluster_machine_list",
}
GPU_FILE = "trace-data/cluster_gpu_util"
MACHINE_FILE = "trace-data/cluster_machine_list"
OUTPUT_SCHEMA = pa.schema([
    ("source_row_index", pa.int64()),
    ("time_text", pa.string()),
    ("time_utc_s", pa.int64()),
    ("machine_id", pa.string()),
    ("source_gpu_field_count", pa.int16()),
    ("raw_irregular_fields_json", pa.string()),
    *[(f"gpu{i}_util", pa.float64()) for i in range(8)],
])


def machine_selected(machine_id: str) -> bool:
    digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % SAMPLE_MODULUS < SAMPLE_RESIDUES


@lru_cache(maxsize=65_536)
def parse_source_time(value: str) -> tuple[int, str]:
    """Respect the PDT/PST suffix, including the repeated autumn clock hour."""
    try:
        clock, suffix = value.rsplit(" ", 1)
        offset = {"PDT": -7, "PST": -8}[suffix]
        naive = datetime.fromisoformat(clock)
        if naive.tzinfo is not None:
            raise ValueError("timestamp already has a timezone offset")
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid Philly timestamp: {value!r}") from exc
    aware = naive.replace(tzinfo=timezone(timedelta(hours=offset)))
    return int(aware.timestamp()), suffix


def parse_gpu_row(row: list[str], row_index: int
                  ) -> tuple[str, int, str, str, list[float | None], Counter, int, str | None]:
    original_row = row
    if len(row) > 10 and row[-1] == "":
        row = row[:-1]
    if not 2 <= len(row) <= 66:
        raise ValueError(f"GPU CSV row {row_index} has implausible field count {len(row)}")
    time_text, machine_id = row[:2]
    if not machine_id or machine_id.strip() != machine_id:
        raise ValueError(f"GPU CSV row {row_index} has invalid machineId")
    epoch, tz = parse_source_time(time_text)
    status = Counter()
    values: list[float | None] = []
    for token in row[2:]:
        if token == "NA":
            values.append(None)
            status["na"] += 1
            continue
        if token == "":
            values.append(None)
            status["empty"] += 1
            continue
        try:
            value = float(token)
        except ValueError as exc:
            raise ValueError(f"GPU CSV row {row_index} has unexpected utilization {token!r}") from exc
        if not math.isfinite(value):
            raise ValueError(f"GPU CSV row {row_index} has nonfinite utilization {token!r}")
        status["below_zero" if value < 0 else "above_100" if value > 100 else "valid"] += 1
        values.append(value)
    gpu_field_count = len(values)
    if gpu_field_count != 8:
        raw_json = json.dumps(original_row, separators=(",", ":"), ensure_ascii=False)
        return time_text, epoch, tz, machine_id, [None] * 8, status, gpu_field_count, raw_json
    return time_text, epoch, tz, machine_id, values, status, gpu_field_count, None


def iter_text_lines(binary_file):
    """Feed csv.reader from tar's non-seekable member file."""
    pending = b""
    while block := binary_file.read(1 << 20):
        parts = (pending + block).split(b"\n")
        pending = parts.pop()
        if len(pending) > 1 << 20:
            raise ValueError("CSV line exceeds 1 MiB")
        for part in parts:
            yield (part + b"\n").decode("utf-8")
    if pending:
        yield pending.decode("utf-8")


class HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, n: int = -1) -> bytes:
        block = self.source.read(n)
        self.digest.update(block)
        self.count += len(block)
        return block


class SampleWriter:
    def __init__(self, path: Path, max_bytes: int = MAX_SAMPLE_BYTES):
        self.path = path
        self.max_bytes = max_bytes
        require_free_space(path.parent)
        self.writer = pq.ParquetWriter(path, OUTPUT_SCHEMA, compression="zstd")
        self.batch = {name: [] for name in OUTPUT_SCHEMA.names}
        self.rows = 0

    def add(self, row_index: int, time_text: str, epoch: int,
            machine_id: str, values: list[float | None],
            gpu_field_count: int, raw_irregular_fields_json: str | None) -> None:
        self.batch["source_row_index"].append(row_index)
        self.batch["time_text"].append(time_text)
        self.batch["time_utc_s"].append(epoch)
        self.batch["machine_id"].append(machine_id)
        self.batch["source_gpu_field_count"].append(gpu_field_count)
        self.batch["raw_irregular_fields_json"].append(raw_irregular_fields_json)
        for index, value in enumerate(values):
            self.batch[f"gpu{index}_util"].append(value)
        if len(self.batch["source_row_index"]) >= ROW_GROUP_SIZE:
            self.flush()

    def flush(self) -> None:
        require_free_space(self.path.parent)
        n = len(self.batch["source_row_index"])
        if not n:
            return
        table = pa.Table.from_pydict(self.batch, schema=OUTPUT_SCHEMA)
        self.writer.write_table(table, row_group_size=n)
        self.rows += n
        self.batch = {name: [] for name in OUTPUT_SCHEMA.names}
        if self.path.stat().st_size > self.max_bytes:
            raise RuntimeError("Compact sample exceeded the 1.9 GB disk guard")
        require_free_space(self.path.parent)

    def close(self) -> None:
        self.flush()
        self.writer.close()
        if self.path.stat().st_size > self.max_bytes:
            raise RuntimeError("Compact sample exceeded the 1.9 GB disk guard")
        require_free_space(self.path.parent)


def require_free_space(path: Path) -> None:
    usage = os.statvfs(path)
    free = usage.f_bavail * usage.f_frsize
    if free < MIN_FREE_BYTES:
        raise RuntimeError(f"Free disk fell below 5 GB guard: {free} bytes")


def _scan_gpu(member_file, *, writer: SampleWriter | None,
              preview_rows: int | None) -> dict:
    reader = csv.reader(iter_text_lines(member_file))
    rows = 0
    header_present = False
    machines: dict[str, int] = {}
    selected_flags: dict[str, bool] = {}
    selected_machines = set()
    selected_seen_keys: set[int] = set()
    selected_duplicate_rows = 0
    non_minute_rows = 0
    status = Counter()
    field_count_histogram = Counter()
    irregular_examples = []
    irregular_rows = 0
    timezones = Counter()
    min_epoch = None
    max_epoch = None
    first_rows = []
    for raw in reader:
        if rows == 0 and raw and raw[0] == "time":
            header_present = True
            continue
        if not raw or (len(raw) == 1 and raw[0] == ""):
            raise ValueError("GPU CSV contains an empty row")
        rows += 1
        (time_text, epoch, tz, machine_id, values, local_status,
         gpu_field_count, raw_irregular_json) = parse_gpu_row(raw, rows)
        field_count_histogram[gpu_field_count] += 1
        if gpu_field_count != 8:
            irregular_rows += 1
            if len(irregular_examples) < 10:
                irregular_examples.append({
                    "source_row_index": rows, "time_text": time_text,
                    "machine_id": machine_id, "gpu_field_count": gpu_field_count,
                    "raw_fields": raw,
                })
        if len(first_rows) < 3:
            first_rows.append({
                "time_text": time_text, "machine_id": machine_id,
                "gpu_values": values,
            })
        min_epoch = epoch if min_epoch is None else min(min_epoch, epoch)
        max_epoch = epoch if max_epoch is None else max(max_epoch, epoch)
        if epoch % 60 != 0:
            non_minute_rows += 1
        timezones[tz] += 1
        status.update(local_status)
        if machine_id not in machines:
            machines[machine_id] = len(machines)
            selected_flags[machine_id] = machine_selected(machine_id)
        if not 0 <= epoch < 2**32:
            raise ValueError(f"GPU CSV row {rows} timestamp outside packed-key range")
        key = (machines[machine_id] << 32) | epoch
        if selected_flags[machine_id]:
            selected_machines.add(machine_id)
            if key in selected_seen_keys:
                selected_duplicate_rows += 1
            else:
                selected_seen_keys.add(key)
            if writer is not None:
                writer.add(rows, time_text, epoch, machine_id, values,
                           gpu_field_count, raw_irregular_json)
        if preview_rows is not None and rows >= preview_rows:
            break
        if rows % 50_000 == 0 and writer is not None:
            require_free_space(writer.path.parent)
        if rows % 1_000_000 == 0:
            print(f"gpu rows scanned: {rows:,}; selected: {writer.rows if writer else 0:,}", flush=True)
    return {
        "rows": rows,
        "header_present": header_present,
        "machine_count": len(machines),
        "selected_machine_count": len(selected_machines),
        "selected_machine_ids": sorted(selected_machines),
        "selected_rows": writer.rows + len(writer.batch["source_row_index"]) if writer else None,
        "selected_duplicate_machine_utc_minute_rows": selected_duplicate_rows,
        "duplicate_audit_scope": "selected 5% machines only; exact packed machineId+UTC-second keys",
        "non_minute_timestamp_rows": non_minute_rows,
        "gpu_cell_status": dict(status),
        "gpu_field_count_histogram": dict(field_count_histogram),
        "irregular_rows": irregular_rows,
        "irregular_examples": irregular_examples,
        "timezone_rows": dict(timezones),
        "min_time_utc_s": min_epoch,
        "max_time_utc_s": max_epoch,
        "first_rows": first_rows,
    }


def _scan_machine_list(member_file) -> dict:
    records = []
    by_machine = {}
    duplicate_ids = 0
    header_present = False
    for row_index, raw in enumerate(csv.reader(iter_text_lines(member_file)), start=1):
        if row_index == 1 and raw and raw[0].strip().lower() in {"machineid", "machine_id"}:
            header_present = True
            continue
        if len(raw) == 4 and raw[-1] == "":
            raw = raw[:-1]
        if len(raw) != 3:
            raise ValueError(f"Machine-list row {row_index} has {len(raw)} fields, expected 3")
        machine_id, count_text, memory_text = (value.strip() for value in raw)
        if not machine_id or not count_text or not memory_text:
            raise ValueError(f"Machine-list row {row_index} has an empty field")
        count_numeric = float(count_text)
        if not count_numeric.is_integer() or not 0 <= count_numeric <= 64:
            raise ValueError(f"Machine-list row {row_index} has invalid GPU count {count_text!r}")
        record = {
            "machine_id": machine_id, "gpu_count": int(count_numeric),
            "gpu_memory_text": memory_text,
        }
        if machine_id in by_machine:
            duplicate_ids += 1
            if by_machine[machine_id] != record:
                raise ValueError(f"Conflicting machine-list entries for {machine_id}")
        else:
            by_machine[machine_id] = record
        records.append(record)
    return {
        "rows": len(records), "unique_machines": len(by_machine),
        "duplicate_machine_ids": duplicate_ids, "header_present": header_present,
        "records": records,
    }


def scan_archive(raw_stream, *, output: Path | None, expected_size: int,
                 expected_sha256: str, preview_rows: int | None = None,
                 max_sample_bytes: int = MAX_SAMPLE_BYTES) -> dict:
    """Scan a gzip tar stream. Probe mode intentionally stops before integrity audit."""
    source = HashingReader(raw_stream)
    sample_temp = output.with_name(output.name + ".incomplete") if output else None
    writer = SampleWriter(sample_temp, max_sample_bytes) if sample_temp else None
    members = []
    gpu_audit = None
    machine_audit = None
    complete = False
    try:
        with gzip.GzipFile(fileobj=source, mode="rb") as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="r|", bufsize=1 << 20) as archive:
                for member in archive:
                    members.append({
                        "name": member.name, "size": member.size,
                        "type": member.type.decode(), "is_file": member.isfile(),
                    })
                    if member.name == MACHINE_FILE:
                        if not member.isfile():
                            raise ValueError("Machine-list tar member is not a regular file")
                        with archive.extractfile(member) as machine_file:
                            machine_audit = _scan_machine_list(machine_file)
                        continue
                    if member.name != GPU_FILE:
                        continue
                    if not member.isfile():
                        raise ValueError("GPU tar member is not a regular file")
                    with archive.extractfile(member) as gpu_file:
                        gpu_audit = _scan_gpu(gpu_file, writer=writer, preview_rows=preview_rows)
                    if preview_rows is not None:
                        break
            if preview_rows is None:
                while gzip_stream.read(4 * 1024 * 1024):
                    pass
                complete = True
        if gpu_audit is None:
            raise ValueError("GPU CSV member is missing")
        if complete:
            file_names = {entry["name"] for entry in members if entry["is_file"]}
            if file_names != EXPECTED_FILES:
                raise ValueError(f"Tar file set differs from documented five files: {file_names!r}")
            if source.count != expected_size:
                raise ValueError(f"Compressed byte count {source.count} != pinned {expected_size}")
            if source.digest.hexdigest() != expected_sha256:
                raise ValueError("Compressed SHA256 differs from pinned LFS OID")
            if machine_audit is None:
                raise ValueError("Machine-list CSV member is missing")
            known = {record["machine_id"] for record in machine_audit["records"]}
            gpu_audit["selected_machines_missing_from_machine_list"] = sorted(
                set(gpu_audit["selected_machine_ids"]) - known
            )
        if writer is not None:
            writer.close()
            writer = None
            os.replace(sample_temp, output)
        result = {
            "complete": complete,
            "source_commit": SOURCE_COMMIT,
            "source_url": URL,
            "compressed_bytes_read": source.count,
            "compressed_sha256": source.digest.hexdigest() if complete else None,
            "tar_members": members,
            "gpu_csv": gpu_audit,
            "machine_list": machine_audit,
            "sample_rule": "int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 5",
            "sample_parquet": str(output) if output else None,
        }
        if output is not None:
            result["sample_bytes"] = output.stat().st_size
            digest = hashlib.sha256()
            with output.open("rb") as sample:
                while block := sample.read(4 * 1024 * 1024):
                    digest.update(block)
            result["sample_sha256"] = digest.hexdigest()
        return result
    except BaseException:
        if writer is not None:
            writer.writer.close()
        if sample_temp is not None:
            sample_temp.unlink(missing_ok=True)
        raise


def validate_http_head(session: requests.Session) -> None:
    response = session.head(URL, allow_redirects=True, timeout=60,
                            headers={"Accept-Encoding": "identity"})
    try:
        if response.status_code != 200:
            raise ValueError(f"Pinned Philly HEAD returned {response.status_code}")
        if int(response.headers.get("Content-Length", "-1")) != EXPECTED_SIZE:
            raise ValueError("Pinned Philly Content-Length changed")
        if response.headers.get("ETag") != EXPECTED_ETAG:
            raise ValueError("Pinned Philly ETag changed")
        if "bytes" not in response.headers.get("Accept-Ranges", ""):
            raise ValueError("Pinned Philly archive no longer supports ranges")
    finally:
        response.close()


def run(*, probe_rows: int | None = None) -> dict:
    output_dir = ROOT / "data" / "external" / "msr_philly_2017"
    output_dir.mkdir(parents=True, exist_ok=True)
    require_free_space(output_dir)
    output = None if probe_rows is not None else output_dir / "gpu_util_machine_hash5pct.parquet"
    if output is not None and output.exists():
        raise FileExistsError(f"Refusing to overwrite existing sample: {output}")
    try:
        with requests.Session() as session:
            validate_http_head(session)
            with session.get(URL, stream=True, timeout=(30, 180),
                             headers={"Accept-Encoding": "identity",
                                      "User-Agent": "ai-mcmp-research/philly-stream-audit"}) as response:
                if response.status_code != 200:
                    raise ValueError(f"Pinned Philly GET returned {response.status_code}")
                if int(response.headers.get("Content-Length", "-1")) != EXPECTED_SIZE:
                    raise ValueError("Pinned Philly GET Content-Length changed")
                if response.headers.get("ETag") != EXPECTED_ETAG:
                    raise ValueError("Pinned Philly GET ETag changed")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("Pinned Philly GET has unexpected Content-Encoding")
                response.raw.decode_content = False
                audit = scan_archive(response.raw, output=output, expected_size=EXPECTED_SIZE,
                                     expected_sha256=EXPECTED_SHA256, preview_rows=probe_rows)
    except BaseException as exc:
        failure = {
            "integrity_pass": False,
            "source_commit": SOURCE_COMMIT,
            "source_url": URL,
            "reason": f"{type(exc).__name__}: {exc}",
            "sample_path_promoted": False,
        }
        path = output_dir / "gpu_util_failure.json"
        path.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n")
        raise
    audit["integrity_pass"] = probe_rows is None
    if audit["machine_list"] is not None:
        machine_records = audit["machine_list"].pop("records")
        machine_path = output_dir / "machine_list.json"
        machine_temp = machine_path.with_name(machine_path.name + ".tmp")
        machine_temp.write_text(json.dumps(machine_records, indent=2, sort_keys=True) + "\n")
        os.replace(machine_temp, machine_path)
        audit["machine_list"]["path"] = str(machine_path)
        audit["machine_list"]["sha256"] = hashlib.sha256(machine_path.read_bytes()).hexdigest()
    audit["http_content_length"] = EXPECTED_SIZE
    audit["http_etag"] = EXPECTED_ETAG
    path = output_dir / ("gpu_util_probe.json" if probe_rows is not None else "gpu_util_audit.json")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temp, path)
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-rows", type=int, default=None,
                        help="Parse only this many GPU rows without writing a sample")
    args = parser.parse_args()
    if args.probe_rows is not None and args.probe_rows <= 0:
        parser.error("--probe-rows must be positive")
    result = run(probe_rows=args.probe_rows)
    print(json.dumps({
        "complete": result["complete"],
        "gpu_rows": result["gpu_csv"]["rows"],
        "sample_rows": result["gpu_csv"]["selected_rows"],
        "sample_bytes": result.get("sample_bytes"),
        "audit": "gpu_util_probe.json" if args.probe_rows else "gpu_util_audit.json",
    }, sort_keys=True), flush=True)
