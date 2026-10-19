"""Audit and sample every stored member of Alibaba's pinned MaaS 5-minute ZIP.

The 4.8 GB ZIP is never saved. One Parquet member (at most 300 MB) is fetched
with resumable byte ranges, verified against the ZIP directory, scanned, and
discarded before the next member. The 1% sample keeps every selected row,
including missing or invalid telemetry; no original instance ID is written.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import zlib

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests


ROOT = Path(__file__).resolve().parents[1]
URL = (
    "https://tre-clusterdata.oss-cn-hangzhou.aliyuncs.com/"
    "cluster-trace-v2026-maas/data/instance_week_5min.zip"
)
EXPECTED_SIZE = 4_812_189_789
EXPECTED_ETAG = "434E78D81E2A9F9F1FDEF3163DD0FCEC"
EXPECTED_MEMBERS = 21
EXPECTED_ROWS = 140_906_827
TAIL_BYTES = 128 * 1024
RANGE_CHUNK = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 300_000_000
MEMBER_NAME = re.compile(r"instance_week_5min/part_[0-9]+\.parquet\Z")
REQUIRED = (
    "instance_id", "time_s", "cluster_id", "gpu_count", "sm_util",
    "gpu_mem_util", "samples",
)
OUTPUT_SCHEMA = pa.schema([
    ("instance_hash", pa.string()),
    ("time_s", pa.int64()),
    ("cluster_id", pa.string()),
    ("gpu_count", pa.float64()),
    ("sm_util", pa.float64()),
    ("gpu_mem_util", pa.float64()),
    ("samples", pa.float64()),
    ("alarm_tie_hash", pa.string()),
])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def clean_etag(value: str | None) -> str:
    if not value or value.startswith("W/"):
        raise ValueError("Missing or weak ETag")
    return value.strip('"')


class RangeClient:
    """Strict, pinned HTTP byte-range reader; injectable session for tests."""

    def __init__(self, url: str = URL, size: int = EXPECTED_SIZE,
                 etag: str = EXPECTED_ETAG, session: requests.Session | None = None):
        self.url, self.size, self.etag = url, size, etag
        self.session = session or requests.Session()
        self.raw_etag = f'"{etag}"'
        self.response_headers: list[dict[str, str | int]] = []

    def head(self) -> None:
        response = self.session.head(self.url, timeout=60, allow_redirects=True,
                                     headers={"Accept-Encoding": "identity"})
        try:
            if response.status_code != 200:
                raise ValueError(f"HEAD returned HTTP {response.status_code}")
            if int(response.headers.get("Content-Length", "-1")) != self.size:
                raise ValueError("Source ZIP length differs from pinned value")
            if clean_etag(response.headers.get("ETag")) != self.etag:
                raise ValueError("Source ZIP ETag differs from pinned value")
            self.raw_etag = response.headers["ETag"]
            if "bytes" not in response.headers.get("Accept-Ranges", "").lower():
                raise ValueError("Source does not advertise byte ranges")
        finally:
            response.close()

    def get(self, start: int, end: int) -> bytes:
        if start < 0 or end < start or end >= self.size:
            raise ValueError("Invalid byte range")
        headers = {
            "Accept-Encoding": "identity",
            "Range": f"bytes={start}-{end}",
            "If-Range": self.raw_etag,
            "User-Agent": "ai-mcmp-research/maas-range-audit",
        }
        response = self.session.get(self.url, headers=headers, timeout=120,
                                    allow_redirects=True, stream=True)
        try:
            expected = f"bytes {start}-{end}/{self.size}"
            if response.status_code != 206:
                raise ValueError(f"Range returned HTTP {response.status_code}, expected 206")
            if response.headers.get("Content-Range") != expected:
                raise ValueError("Range Content-Range mismatch")
            if int(response.headers.get("Content-Length", "-1")) != end - start + 1:
                raise ValueError("Range Content-Length mismatch")
            if clean_etag(response.headers.get("ETag")) != self.etag:
                raise ValueError("Range ETag differs from pinned value")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Range body was content-encoded")
            blocks = []
            received = 0
            for block in response.iter_content(chunk_size=1024 * 1024):
                if not block:
                    continue
                received += len(block)
                if received > end - start + 1:
                    raise ValueError("Range body exceeded requested length")
                blocks.append(block)
            if received != end - start + 1:
                raise ValueError("Range body was truncated")
            self.response_headers.append({
                "range": headers["Range"], "status": response.status_code,
                "content_range": response.headers["Content-Range"],
                "etag": response.headers["ETag"],
                "content_length": received,
            })
            return b"".join(blocks)
        finally:
            response.close()


@dataclass(frozen=True)
class ZipMember:
    name: str
    name_bytes: bytes
    local_offset: int
    size: int
    crc32: int
    flags: int
    data_offset: int = -1


def _zip64_value(extra: bytes, raw_size: int, raw_compressed: int,
                 raw_offset: int, raw_disk: int) -> tuple[int, int, int, int]:
    value = None
    cursor = 0
    while cursor + 4 <= len(extra):
        kind, length = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        if cursor + length > len(extra):
            raise ValueError("Malformed ZIP extra field")
        if kind == 1:
            value = extra[cursor:cursor + length]
        cursor += length
    if cursor != len(extra):
        raise ValueError("Malformed ZIP extra field tail")
    need = (raw_size == 0xffffffff or raw_compressed == 0xffffffff or
            raw_offset == 0xffffffff or raw_disk == 0xffff)
    if need and value is None:
        raise ValueError("ZIP64 extra field missing")
    value = value or b""
    pos = 0

    def read(fmt: str, previous: int, sentinel: int) -> int:
        nonlocal pos
        if previous != sentinel:
            return previous
        width = struct.calcsize(fmt)
        if pos + width > len(value):
            raise ValueError("Truncated ZIP64 extra field")
        result = struct.unpack_from(fmt, value, pos)[0]
        pos += width
        return result

    return (read("<Q", raw_size, 0xffffffff),
            read("<Q", raw_compressed, 0xffffffff),
            read("<Q", raw_offset, 0xffffffff),
            read("<I", raw_disk, 0xffff))


def read_directory(client: RangeClient, expected_members: int = EXPECTED_MEMBERS
                   ) -> list[ZipMember]:
    tail_start = max(0, client.size - TAIL_BYTES)
    tail = client.get(tail_start, client.size - 1)
    eocd_pos = -1
    for pos in range(len(tail) - 22, -1, -1):
        if tail[pos:pos + 4] != b"PK\x05\x06":
            continue
        comment_len = struct.unpack_from("<H", tail, pos + 20)[0]
        if pos + 22 + comment_len == len(tail):
            eocd_pos = pos
            break
    if eocd_pos < 0:
        raise ValueError("ZIP end-of-central-directory record not found")
    (_, disk, central_disk, entries_disk, entries, central_size,
     central_offset, _) = struct.unpack_from("<IHHHHIIH", tail, eocd_pos)
    if disk or central_disk:
        raise ValueError("Multidisk ZIP is unsupported")
    if (entries == 0xffff or entries_disk == 0xffff or central_size == 0xffffffff
            or central_offset == 0xffffffff):
        locator_offset = tail_start + eocd_pos - 20
        if locator_offset < 0:
            raise ValueError("ZIP64 locator is missing")
        locator = client.get(locator_offset, locator_offset + 19)
        signature, zip64_disk, zip64_offset, disk_count = struct.unpack("<IIQI", locator)
        if signature != 0x07064b50 or zip64_disk or disk_count != 1:
            raise ValueError("Invalid ZIP64 locator")
        record = client.get(zip64_offset, zip64_offset + 55)
        (signature, record_length, _, _, disk, central_disk, entries_disk,
         entries, central_size, central_offset) = struct.unpack("<IQHHIIQQQQ", record)
        if signature != 0x06064b50 or record_length < 44 or disk or central_disk:
            raise ValueError("Invalid ZIP64 end record")
    if entries != entries_disk or entries != expected_members:
        raise ValueError(f"Expected {expected_members} ZIP members, found {entries}")
    if central_offset < 0 or central_size <= 0 or central_offset + central_size > client.size:
        raise ValueError("ZIP central directory offset/length is invalid")
    central = client.get(central_offset, central_offset + central_size - 1)
    members = []
    cursor = 0
    names = set()
    for _ in range(entries):
        if cursor + 46 > len(central):
            raise ValueError("Truncated central directory")
        fields = struct.unpack_from("<IHHHHHHIIIHHHHHII", central, cursor)
        (signature, _, _, flags, method, _, _, crc, compressed, size,
         name_length, extra_length, comment_length, disk_start, _, _,
         local_offset) = fields
        if signature != 0x02014b50:
            raise ValueError("Invalid central directory signature")
        cursor += 46
        finish = cursor + name_length + extra_length + comment_length
        if finish > len(central):
            raise ValueError("Truncated central directory entry")
        name_bytes = central[cursor:cursor + name_length]
        extra = central[cursor + name_length:cursor + name_length + extra_length]
        cursor = finish
        encoding = "utf-8" if flags & 0x800 else "cp437"
        name = name_bytes.decode(encoding, errors="strict")
        if not MEMBER_NAME.fullmatch(name) or name in names:
            raise ValueError("Unexpected or duplicate ZIP member name")
        names.add(name)
        size, compressed, local_offset, disk_start = _zip64_value(
            extra, size, compressed, local_offset, disk_start)
        if method != 0 or flags & 0x41 or disk_start:
            raise ValueError("Member is compressed, encrypted, or multidisk")
        if size != compressed or size <= 0 or size > MAX_MEMBER_BYTES:
            raise ValueError("Unexpected stored Parquet member size")
        members.append(ZipMember(name, name_bytes, local_offset, size, crc, flags))
    if cursor != len(central):
        raise ValueError("Central directory contains unexpected bytes")
    if len({int(member.name.rsplit("part_", 1)[1].split(".")[0])
            for member in members}) != expected_members:
        raise ValueError("Duplicate Parquet part number")
    return sorted(members, key=lambda member: member.name)


def locate_member(client: RangeClient, member: ZipMember) -> ZipMember:
    header = client.get(member.local_offset, member.local_offset + 29)
    (signature, _, flags, method, _, _, crc, compressed, size,
     name_length, extra_length) = struct.unpack("<IHHHHHIIIHH", header)
    if signature != 0x04034b50 or flags != member.flags or method != 0:
        raise ValueError("Local ZIP header differs from central directory")
    name = client.get(member.local_offset + 30,
                      member.local_offset + 30 + name_length + extra_length - 1)
    if name[:name_length] != member.name_bytes:
        raise ValueError("Local ZIP member name differs from central directory")
    if not flags & 0x8:
        if crc != member.crc32:
            raise ValueError("Local ZIP CRC differs from central directory")
        if compressed not in (member.size, 0xffffffff) or size not in (member.size, 0xffffffff):
            raise ValueError("Local ZIP size differs from central directory")
    data_offset = member.local_offset + 30 + name_length + extra_length
    if data_offset + member.size > client.size:
        raise ValueError("Member data exceeds ZIP source")
    return ZipMember(member.name, member.name_bytes, member.local_offset,
                     member.size, member.crc32, member.flags, data_offset)


def fetch_member(client: RangeClient, member: ZipMember, work: Path,
                 chunk_size: int = RANGE_CHUNK) -> tuple[Path, str, list[dict]]:
    """Resume only an fsynced, hash-checked prefix of the current member."""
    partial = work / "current_member.part"
    checkpoint = work / "current_member.progress.json"
    identity = {
        "source_size": client.size, "source_etag": client.etag,
        "name": member.name, "offset": member.data_offset,
        "size": member.size, "crc32": member.crc32,
        "chunk_size": chunk_size,
    }
    if partial.exists() != checkpoint.exists():
        raise ValueError("Current member and checkpoint must exist together")
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if any(state.get(key) != value for key, value in identity.items()):
            raise ValueError("Current member checkpoint belongs to another source/member")
        offset = int(state["downloaded_bytes"])
        if offset < 0 or offset > member.size or partial.stat().st_size < offset:
            raise ValueError("Invalid current member checkpoint length")
        with partial.open("r+b") as stream:
            stream.truncate(offset)
        if sha256_file(partial) != state["sha256"] or crc32_file(partial) != state["crc32_prefix"]:
            raise ValueError("Current member prefix differs from checkpoint")
    else:
        offset = 0
        partial.touch(exist_ok=False)
        state = {**identity, "downloaded_bytes": 0,
                 "sha256": hashlib.sha256(b"").hexdigest(),
                 "crc32_prefix": 0, "range_headers": []}
        atomic_json(checkpoint, state)
    digest = hashlib.sha256()
    crc = 0
    if offset:
        with partial.open("rb") as stream:
            while block := stream.read(4 * 1024 * 1024):
                digest.update(block)
                crc = zlib.crc32(block, crc)
    while offset < member.size:
        length = min(chunk_size, member.size - offset)
        start = member.data_offset + offset
        block = client.get(start, start + length - 1)
        with partial.open("ab") as stream:
            stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        digest.update(block)
        crc = zlib.crc32(block, crc)
        offset += length
        state.update(downloaded_bytes=offset, sha256=digest.hexdigest(),
                     crc32_prefix=crc,
                     range_headers=state["range_headers"] + [client.response_headers[-1]])
        atomic_json(checkpoint, state)
    if crc != member.crc32:
        raise ValueError(f"ZIP CRC32 mismatch for {member.name}")
    return partial, digest.hexdigest(), state["range_headers"]


def crc32_file(path: Path) -> int:
    crc = 0
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            crc = zlib.crc32(block, crc)
    return crc


def normalize_id(value: object) -> str:
    """ID bytes decode strictly as UTF-8; integers use canonical decimal text."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise ValueError(f"Unsupported instance_id scalar type: {type(value).__name__}")


def selected_id(value: object) -> tuple[str, bool]:
    raw = normalize_id(value).encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return digest.hex(), int.from_bytes(digest[:8], "big") % 100 == 0


def _count_true(mask: pa.Array) -> int:
    value = pc.sum(pc.cast(mask, pa.int64())).as_py()
    return int(value or 0)


def _numeric_summary(array: pa.Array, *, lower: float | None = None,
                     upper: float | None = None) -> dict:
    nonnull = pc.drop_null(pc.cast(array, pa.float64()))
    finite_mask = pc.is_finite(nonnull)
    finite = pc.filter(nonnull, finite_mask)
    result = {
        "nonfinite": len(nonnull) - len(finite),
        "minimum": pc.min(finite).as_py() if len(finite) else None,
        "maximum": pc.max(finite).as_py() if len(finite) else None,
    }
    if lower is not None:
        result["below_lower"] = _count_true(pc.less(finite, lower))
    if upper is not None:
        result["above_upper"] = _count_true(pc.greater(finite, upper))
    result["equal_zero"] = _count_true(pc.equal(finite, 0.0))
    return result


def _merge_numeric(target: dict, source: dict) -> None:
    for key, value in source.items():
        if key == "minimum":
            if value is not None:
                target[key] = value if target.get(key) is None else min(target[key], value)
        elif key == "maximum":
            if value is not None:
                target[key] = value if target.get(key) is None else max(target[key], value)
        else:
            target[key] = target.get(key, 0) + value


def _validate_schema(schema: pa.Schema) -> None:
    if len(set(schema.names)) != len(schema.names) or any(name not in schema.names for name in REQUIRED):
        raise ValueError("Required weekly Parquet columns missing/duplicated")
    if not (pa.types.is_string(schema.field("instance_id").type)
            or pa.types.is_large_string(schema.field("instance_id").type)
            or pa.types.is_binary(schema.field("instance_id").type)
            or pa.types.is_large_binary(schema.field("instance_id").type)
            or pa.types.is_integer(schema.field("instance_id").type)):
        raise ValueError("Unsupported instance_id Parquet type")
    if not pa.types.is_integer(schema.field("time_s").type):
        raise ValueError("time_s must be integer seconds")
    if not (pa.types.is_string(schema.field("cluster_id").type)
            or pa.types.is_large_string(schema.field("cluster_id").type)
            or pa.types.is_binary(schema.field("cluster_id").type)
            or pa.types.is_large_binary(schema.field("cluster_id").type)):
        raise ValueError("cluster_id must be string/binary")
    for name in ("gpu_count", "sm_util", "gpu_mem_util", "samples"):
        field_type = schema.field(name).type
        if not (pa.types.is_integer(field_type) or pa.types.is_floating(field_type)
                or pa.types.is_decimal(field_type)):
            raise ValueError(f"{name} must be numeric")


def audit_member(source: Path, member: ZipMember, work: Path, source_sha256: str,
                 range_headers: list[dict], client: RangeClient) -> dict:
    parquet = pq.ParquetFile(source)
    schema = parquet.schema_arrow
    _validate_schema(schema)
    stem = Path(member.name).stem
    sample_path = work / f"{stem}.sample.parquet"
    ids_path = work / f"{stem}.ids.txt.gz"
    audit_path = work / f"{stem}.audit.json"
    sample_temp = sample_path.with_name(sample_path.name + ".tmp")
    ids_temp = ids_path.with_name(ids_path.name + ".tmp")
    counters: dict[str, object] = {
        "row_count": 0, "selected_row_count": 0,
        "column_nulls": {name: 0 for name in schema.names},
        "time_mod_300": Counter(), "day_counts": Counter(),
        "numeric": {}, "id_nulls": 0,
    }
    full_ids: set[str] = set()
    selected_ids: set[str] = set()
    id_cache: dict[str, tuple[str, bool]] = {}
    writer = pq.ParquetWriter(sample_temp, OUTPUT_SCHEMA, compression="zstd")
    try:
        for batch in parquet.iter_batches(batch_size=65_536):
            counters["row_count"] += batch.num_rows
            columns = {name: batch.column(batch.schema.get_field_index(name)) for name in schema.names}
            for name, array in columns.items():
                counters["column_nulls"][name] += array.null_count
            for name, bounds in {
                "gpu_count": (0.0, None), "samples": (0.0, None),
                "sm_util": (0.0, 100.0), "gpu_mem_util": (0.0, 100.0),
            }.items():
                data = _numeric_summary(columns[name], lower=bounds[0], upper=bounds[1])
                target = counters["numeric"].setdefault(name, {})
                _merge_numeric(target, data)
            nonnull_times = pc.drop_null(columns["time_s"]).to_numpy(zero_copy_only=False)
            for residue, count in zip(*np.unique(nonnull_times % 300, return_counts=True)):
                counters["time_mod_300"][str(int(residue))] += int(count)
            for day, count in zip(*np.unique(np.floor_divide(nonnull_times, 86400), return_counts=True)):
                counters["day_counts"][str(int(day))] += int(count)
            indices: list[int] = []
            hashes: list[str] = []
            ties: list[str | None] = []
            ids = columns["instance_id"].to_pylist()
            times = columns["time_s"].to_pylist()
            for index, raw_id in enumerate(ids):
                if raw_id is None:
                    counters["id_nulls"] += 1
                    continue
                id_text = normalize_id(raw_id)
                cached = id_cache.get(id_text)
                if cached is None:
                    cached = selected_id(id_text)
                    id_cache[id_text] = cached
                digest, selected = cached
                full_ids.add(digest)
                if not selected:
                    continue
                selected_ids.add(digest)
                indices.append(index)
                hashes.append(digest)
                time_s = times[index]
                tie = hashlib.sha256(f"{id_text}:{int(time_s) + 600}".encode("utf-8")).hexdigest() if time_s is not None else None
                ties.append(tie)
            if indices:
                take = pa.array(indices, type=pa.int32())
                arrays = [
                    pa.array(hashes, type=pa.string()),
                    pc.cast(pc.take(columns["time_s"], take), pa.int64()),
                    pc.cast(pc.take(columns["cluster_id"], take), pa.string()),
                    pc.cast(pc.take(columns["gpu_count"], take), pa.float64()),
                    pc.cast(pc.take(columns["sm_util"], take), pa.float64()),
                    pc.cast(pc.take(columns["gpu_mem_util"], take), pa.float64()),
                    pc.cast(pc.take(columns["samples"], take), pa.float64()),
                    pa.array(ties, type=pa.string()),
                ]
                writer.write_table(pa.Table.from_arrays(arrays, schema=OUTPUT_SCHEMA))
                counters["selected_row_count"] += len(indices)
    finally:
        writer.close()
    if counters["row_count"] != parquet.metadata.num_rows:
        raise ValueError(f"Parquet row count changed during scan for {member.name}")
    with gzip.open(ids_temp, "wt", encoding="ascii", newline="\n") as stream:
        for digest in sorted(full_ids):
            stream.write(digest + "\n")
    audit = {
        "name": member.name, "source_byte_offset": member.data_offset,
        "source_size": member.size, "zip_crc32": f"{member.crc32:08x}",
        "source_sha256": source_sha256,
        "source_url": client.url, "source_archive_size": client.size,
        "source_etag": client.etag,
        "source_schema": [{"name": field.name, "type": str(field.type)} for field in schema],
        "source_id_type": str(schema.field("instance_id").type),
        "id_normalization": "UTF-8 text unchanged; bytes strict UTF-8; integers canonical decimal; no Unicode normalization",
        "full_unique_ids": len(full_ids), "selected_unique_ids": len(selected_ids),
        "sample_sha256": sha256_file(sample_temp),
        "ids_sha256": sha256_file(ids_temp),
        "http_ranges": range_headers,
        **{key: dict(value) if isinstance(value, Counter) else value
           for key, value in counters.items()},
    }
    os.replace(sample_temp, sample_path)
    os.replace(ids_temp, ids_path)
    atomic_json(audit_path, audit)
    return audit


def validate_saved_member_source(audit: dict, member: ZipMember,
                                 client: RangeClient) -> None:
    """Check source provenance, including range evidence in older member audits."""
    for key, expected in (("source_url", client.url),
                          ("source_archive_size", client.size),
                          ("source_etag", client.etag)):
        if key in audit and audit[key] != expected:
            raise ValueError(f"Saved member source {key} differs: {member.name}")

    ranges = audit.get("http_ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError(f"Saved member range audit missing: {member.name}")
    next_start = member.data_offset
    member_end = next_start + member.size - 1
    for entry in ranges:
        if not isinstance(entry, dict):
            raise ValueError(f"Saved member range audit malformed: {member.name}")
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", str(entry.get("range", "")))
        if match is None:
            raise ValueError(f"Saved member range audit malformed: {member.name}")
        start, end = map(int, match.groups())
        if (start != next_start or end < start or end > member_end
                or entry.get("status") != 206
                or entry.get("content_range") != f"bytes {start}-{end}/{client.size}"
                or entry.get("content_length") != end - start + 1
                or clean_etag(entry.get("etag")) != client.etag):
            raise ValueError(f"Saved member range audit differs from source: {member.name}")
        next_start = end + 1
    if next_start != member_end + 1:
        raise ValueError(f"Saved member range audit incomplete: {member.name}")


def _summary_selected(work: Path, members: list[ZipMember]) -> dict:
    """Exact selected-key and cadence audit using disk-backed SQLite."""
    database = work / "selected_audit.sqlite3"
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=OFF")  # scratch only, always rebuilt
        connection.execute("CREATE TABLE rows (id TEXT, t INTEGER, cluster TEXT, gpu REAL)")
        total = 0
        for member in members:
            path = work / f"{Path(member.name).stem}.sample.parquet"
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=65_536,
                                              columns=["instance_hash", "time_s", "cluster_id", "gpu_count"]):
                cols = [batch.column(i).to_pylist() for i in range(4)]
                connection.executemany("INSERT INTO rows VALUES (?,?,?,?)", zip(*cols))
                total += batch.num_rows
        connection.commit()
        connection.execute("CREATE INDEX selected_id_time ON rows(id,t)")
        exact_duplicate_rows = connection.execute("""
            SELECT COALESCE(SUM(n-1),0) FROM (
              SELECT COUNT(*) AS n FROM rows GROUP BY id,t,cluster,gpu HAVING n>1
            )
        """).fetchone()[0]
        ambiguous = connection.execute("""
            SELECT COUNT(*), COALESCE(SUM(n),0) FROM (
              SELECT COUNT(*) AS n FROM rows GROUP BY id,t HAVING n>1
            )
        """).fetchone()
        changed_at_same_time = connection.execute("""
            SELECT COUNT(*) FROM (
              SELECT id,t FROM rows GROUP BY id,t HAVING
                COUNT(DISTINCT cluster)>1 OR
                (SUM(cluster IS NULL)>0 AND SUM(cluster IS NOT NULL)>0) OR
                COUNT(DISTINCT gpu)>1 OR
                (SUM(gpu IS NULL)>0 AND SUM(gpu IS NOT NULL)>0)
            )
        """).fetchone()[0]
        cadence = connection.execute("""
            WITH unique_rows AS (
              SELECT id,t,MIN(cluster) AS cluster,MIN(gpu) AS gpu
              FROM rows WHERE t IS NOT NULL GROUP BY id,t HAVING COUNT(*)=1
            ), ordered AS (
              SELECT id,t,cluster,gpu,
                LAG(t) OVER(PARTITION BY id ORDER BY t) AS previous_t,
                LAG(cluster) OVER(PARTITION BY id ORDER BY t) AS previous_cluster,
                LAG(gpu) OVER(PARTITION BY id ORDER BY t) AS previous_gpu
              FROM unique_rows
            )
            SELECT
              COALESCE(SUM(CASE WHEN previous_t IS NOT NULL AND t-previous_t>300 THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN previous_t IS NOT NULL AND t-previous_t>300
                                  AND (t-previous_t)%300=0 THEN (t-previous_t)/300-1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN previous_t IS NOT NULL AND (t-previous_t)%300!=0 THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN previous_t IS NOT NULL AND cluster IS NOT previous_cluster THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN previous_t IS NOT NULL AND gpu IS NOT previous_gpu THEN 1 ELSE 0 END),0)
            FROM ordered
        """).fetchone()
        return {
            "rows": total, "exact_key_duplicate_extra_rows": exact_duplicate_rows,
            "same_id_time_ambiguous_keys": ambiguous[0],
            "same_id_time_ambiguous_rows": ambiguous[1],
            "same_id_time_different_cluster_or_gpu_keys": changed_at_same_time,
            "gap_pairs_over_5_minutes": cadence[0],
            "missing_5min_windows_between_unique_rows": cadence[1],
            "non_5min_spacing_pairs": cadence[2],
            "cluster_changes_between_unique_rows": cadence[3],
            "gpu_allocation_changes_between_unique_rows": cadence[4],
        }
    finally:
        connection.close()
        database.unlink(missing_ok=True)


def _aggregate(members: list[ZipMember], member_audits: list[dict],
               work: Path, expected_rows: int) -> dict:
    schemas = {json.dumps(audit["source_schema"], sort_keys=True) for audit in member_audits}
    totals = {
        "row_count": sum(audit["row_count"] for audit in member_audits),
        "selected_row_count": sum(audit["selected_row_count"] for audit in member_audits),
        "column_nulls": Counter(), "time_mod_300": Counter(),
        "day_counts": Counter(), "numeric": {},
        "id_nulls": sum(audit["id_nulls"] for audit in member_audits),
    }
    full_ids: set[str] = set()
    for member, audit in zip(members, member_audits):
        for field in ("column_nulls", "time_mod_300", "day_counts"):
            totals[field].update(audit[field])
        for name, summary in audit["numeric"].items():
            _merge_numeric(totals["numeric"].setdefault(name, {}), summary)
        ids_path = work / f"{Path(member.name).stem}.ids.txt.gz"
        if sha256_file(ids_path) != audit["ids_sha256"]:
            raise ValueError(f"Hash list differs from saved audit for {member.name}")
        with gzip.open(ids_path, "rt", encoding="ascii") as stream:
            full_ids.update(line.rstrip("\n") for line in stream)
    selected_ids = sum(int.from_bytes(bytes.fromhex(digest)[:8], "big") % 100 == 0
                       for digest in full_ids)
    totals["full_unique_ids"] = len(full_ids)
    totals["selected_unique_ids"] = selected_ids
    totals["selected_key_audit"] = _summary_selected(work, members)
    totals["column_nulls"] = dict(totals["column_nulls"])
    totals["time_mod_300"] = dict(totals["time_mod_300"])
    totals["day_counts"] = dict(totals["day_counts"])
    totals["official_row_count_match"] = totals["row_count"] == expected_rows
    totals["expected_official_rows"] = expected_rows
    totals["schema_consistent_across_members"] = len(schemas) == 1
    totals["time_aligned_5min"] = set(totals["time_mod_300"]) <= {"0"}
    totals["official_days_only"] = set(totals["day_counts"]) <= {
        str(day) for day in range(140, 147)}
    return totals


def _combine_sample(work: Path, members: list[ZipMember], target: Path) -> str:
    temporary = target.with_name(target.name + ".tmp")
    writer = pq.ParquetWriter(temporary, OUTPUT_SCHEMA, compression="zstd")
    try:
        for member in members:
            path = work / f"{Path(member.name).stem}.sample.parquet"
            parquet = pq.ParquetFile(path)
            if not parquet.schema_arrow.equals(OUTPUT_SCHEMA):
                raise ValueError(f"Compact schema changed for {member.name}")
            for batch in parquet.iter_batches(batch_size=65_536):
                writer.write_table(pa.Table.from_batches([batch], schema=OUTPUT_SCHEMA))
    finally:
        writer.close()
    digest = sha256_file(temporary)
    os.replace(temporary, target)
    return digest


def fetch_sample(output_dir: Path = ROOT / "data/external/alibaba_maas_2026",
                 client: RangeClient | None = None,
                 expected_members: int = EXPECTED_MEMBERS,
                 expected_rows: int = EXPECTED_ROWS,
                 chunk_size: int = RANGE_CHUNK) -> dict:
    client = client or RangeClient()
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / "instance_week_5min_work"
    work.mkdir(exist_ok=True)
    final = output_dir / "instance_week_5min_sample.parquet"
    audit_path = output_dir / "instance_week_5min_audit.json"
    failed_path = output_dir / "instance_week_5min_audit_failed.json"
    failure_recorded = False
    try:
        client.head()
        members = read_directory(client, expected_members)
        member_audits = []
        for index, member in enumerate(members, 1):
            member = locate_member(client, member)
            stem = Path(member.name).stem
            saved = work / f"{stem}.audit.json"
            sample = work / f"{stem}.sample.parquet"
            ids = work / f"{stem}.ids.txt.gz"
            if saved.exists():
                audit = json.loads(saved.read_text(encoding="utf-8"))
                validate_saved_member_source(audit, member, client)
                if (audit["name"] != member.name or audit["source_byte_offset"] != member.data_offset
                        or audit["source_size"] != member.size
                        or audit["zip_crc32"] != f"{member.crc32:08x}"
                        or sha256_file(sample) != audit["sample_sha256"]
                        or sha256_file(ids) != audit["ids_sha256"]):
                    raise ValueError(f"Saved member audit changed: {member.name}")
                progress = work / "current_member.progress.json"
                if progress.exists():
                    state = json.loads(progress.read_text(encoding="utf-8"))
                    if state["name"] == member.name:
                        (work / "current_member.part").unlink(missing_ok=True)
                        progress.unlink()
            else:
                partial, source_sha256, range_headers = fetch_member(
                    client, member, work, chunk_size)
                print(f"[{index}/{len(members)}] verified {member.name} {source_sha256}", flush=True)
                audit = audit_member(partial, member, work, source_sha256,
                                     range_headers, client)
                partial.unlink()
                (work / "current_member.progress.json").unlink()
            member_audits.append(audit)
        totals = _aggregate(members, member_audits, work, expected_rows)
        schema_pass = all((totals[key] for key in (
            "official_row_count_match", "schema_consistent_across_members",
            "time_aligned_5min", "official_days_only")))
        audit = {
            "source_url": client.url, "source_size": client.size,
            "source_etag": client.etag, "expected_member_count": expected_members,
            "member_count": len(members),
            "integrity_pass": True,
            "all_members_verified": len(member_audits) == expected_members,
            "schema_pass": bool(schema_pass),
            "sample_rule": "SHA256(UTF-8 normalized instance_id)[:8] big-endian mod 100 == 0",
            "id_normalization": "UTF-8 text unchanged; bytes strict UTF-8; integers canonical decimal; no Unicode normalization",
            "compact_schema": [{"name": field.name, "type": str(field.type)} for field in OUTPUT_SCHEMA],
            "alarm_tie_hash_rule": "SHA256(UTF-8(normalized instance_id + ':' + str(time_s+600))) full lowercase hex",
            "totals": totals,
            "members": member_audits,
        }
        if not schema_pass:
            atomic_json(failed_path, {**audit, "integrity_pass": True,
                                      "all_members_verified": True,
                                      "error": "Official row count/schema/time domain mismatch"})
            failure_recorded = True
            raise ValueError("Official row count/schema/time domain mismatch")
        digest = _combine_sample(work, members, final)
        audit["compact_sha256"] = digest
        audit["compact_size"] = final.stat().st_size
        atomic_json(audit_path, audit)
        failed_path.unlink(missing_ok=True)
        return audit
    except Exception as error:
        if not failure_recorded:
            atomic_json(failed_path, {
                "source_url": client.url, "source_size": client.size,
                "source_etag": client.etag,
                "integrity_pass": False, "schema_pass": False,
                "all_members_verified": False,
                "error": f"{type(error).__name__}: {error}",
            })
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data/external/alibaba_maas_2026")
    args = parser.parse_args()
    audit = fetch_sample(args.output_dir)
    print(json.dumps({
        "row_count": audit["totals"]["row_count"],
        "selected_rows": audit["totals"]["selected_row_count"],
        "selected_unique_ids": audit["totals"]["selected_unique_ids"],
        "compact_sha256": audit["compact_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
