#!/usr/bin/env python3
"""Audit HAS-GPU request/GPU clock joins using ZIP member HTTP ranges only.

This reads every HAS request/usage CSV pair and the request writer from the
Zenodo archive. It does not download the archive or fit a model.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile


RECORD_URL = "https://zenodo.org/api/records/15596511"
EXPECTED_ZIP_SIZE = 62_262_205
EXPECTED_ZIP_MD5 = "md5:b8da5cea33e59b9b21dab8163633118a"
RESULT_PREFIX = "HAS-GPU/validation/result/HAS/"
SOURCE_MEMBER = "HAS-GPU/validation/client/test_batch.py"
README_MEMBER = "HAS-GPU/README.md"
ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "has_gpu_clock_ranges_v1.json"


def get_json(url: str) -> dict:
    with urlopen(Request(url, headers={"User-Agent": "KHU-AI-MCMP-research-audit"}), timeout=30) as response:
        return json.load(response)


class HTTPRangeReader(io.RawIOBase):
    def __init__(self, url: str, size: int):
        self.url = url
        self.size = size
        self.position = 0
        self.requests = 0
        self.bytes_received = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.position = offset
        elif whence == 1:
            self.position += offset
        elif whence == 2:
            self.position = self.size + offset
        else:
            raise ValueError(f"unsupported whence: {whence}")
        if not 0 <= self.position <= self.size:
            raise ValueError("seek outside ZIP")
        return self.position

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = self.size - self.position
        amount = min(amount, self.size - self.position)
        if amount == 0:
            return b""
        start, end = self.position, self.position + amount - 1
        headers = {
            "User-Agent": "KHU-AI-MCMP-research-audit",
            "Range": f"bytes={start}-{end}",
        }
        with urlopen(Request(self.url, headers=headers), timeout=30) as response:
            body = response.read()
            claimed = response.headers.get("Content-Range")
            if response.status != 206 or claimed != f"bytes {start}-{end}/{self.size}":
                raise ValueError(f"unexpected range response: {response.status} {claimed}")
        if len(body) != amount:
            raise ValueError(f"short range response: {len(body)} != {amount}")
        self.position += amount
        self.requests += 1
        self.bytes_received += amount
        return body


def parse_naive_local_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None:
        raise ValueError(f"expected naive request timestamp: {value}")
    # Hypothesis for overlap audit only; the producer does not record timezone.
    return result.replace(tzinfo=timezone.utc)


def parse_gpu_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"expected timezone-bearing GPU timestamp: {value}")
    return result.astimezone(timezone.utc)


def read_csv(zip_file: ZipFile, path: str) -> tuple[list[dict], str]:
    data = zip_file.read(path)  # ZipFile verifies each member CRC.
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")))), hashlib.sha256(data).hexdigest()


def audit() -> dict:
    record = get_json(RECORD_URL)
    files = {item["key"]: item for item in record["files"]}
    source = files["HAS-GPU.zip"]
    if source["size"] != EXPECTED_ZIP_SIZE or source["checksum"] != EXPECTED_ZIP_MD5:
        raise ValueError("Zenodo archive identity changed")

    remote = HTTPRangeReader(source["links"]["self"], source["size"])
    runs = []
    with ZipFile(remote) as zip_file:
        names = set(zip_file.namelist())
        source_bytes = zip_file.read(SOURCE_MEMBER)
        readme_bytes = zip_file.read(README_MEMBER)
        if b'"timestamp": datetime.now().isoformat()' not in source_bytes or b'elapsed = time.time() - start_time' not in source_bytes:
            raise ValueError("request timestamp/duration writer changed")
        folders = sorted({
            "/".join(path.split("/")[:-1]) + "/"
            for path in names
            if path.startswith(RESULT_PREFIX) and path.endswith(".csv")
        })
        for folder in folders:
            csv_names = sorted(path for path in names if path.startswith(folder) and path.endswith(".csv"))
            request_names = [path for path in csv_names if path.rsplit("/", 1)[1].startswith("has-") and not path.rsplit("/", 1)[1].startswith("has-usage-")]
            usage_names = [path for path in csv_names if path.rsplit("/", 1)[1].startswith("has-usage-")]
            if len(request_names) != 1 or len(usage_names) != 1:
                raise ValueError(f"unpaired HAS CSVs in {folder}: {csv_names}")
            req_path, gpu_path = request_names[0], usage_names[0]
            req_rows, req_sha = read_csv(zip_file, req_path)
            gpu_rows, gpu_sha = read_csv(zip_file, gpu_path)
            if not req_rows or not gpu_rows:
                raise ValueError(f"empty HAS CSV in {folder}")
            request_ends = [parse_naive_local_time(row["timestamp"]) for row in req_rows]
            durations = [float(row["duration"]) for row in req_rows]
            if not all(math.isfinite(value) for value in durations):
                raise ValueError(f"nonfinite request duration in {req_path}")
            request_starts = [end - timedelta(seconds=duration) for end, duration in zip(request_ends, durations)]
            gpu_times = [parse_gpu_time(row["timestamp"]) for row in gpu_rows]
            gpu_start, gpu_end = min(gpu_times), max(gpu_times)
            started_in_gpu_span = sum(gpu_start <= time <= gpu_end for time in request_starts)
            ended_in_gpu_span = sum(gpu_start <= time <= gpu_end for time in request_ends)
            # Counts only. No latency percentiles or model scores are computed here.
            future_start_counts = {}
            for horizon in (10, 30):
                eligible = []
                for anchor in gpu_times:
                    if anchor + timedelta(seconds=horizon) > gpu_end:
                        continue
                    count = sum(anchor < start <= anchor + timedelta(seconds=horizon) for start in request_starts)
                    eligible.append(count)
                nonoverlap = []
                anchor = gpu_start
                while anchor + timedelta(seconds=horizon) <= gpu_end:
                    nonoverlap.append(sum(anchor < start <= anchor + timedelta(seconds=horizon) for start in request_starts))
                    anchor += timedelta(seconds=horizon)
                future_start_counts[str(horizon)] = {
                    "candidate_anchors": len(eligible),
                    "anchors_with_at_least_20_starts": sum(n >= 20 for n in eligible),
                    "nonoverlapping_windows": len(nonoverlap),
                    "nonoverlapping_windows_with_at_least_20_starts": sum(n >= 20 for n in nonoverlap),
                }
            gpu_numeric_values = {}
            for key in ("sm_usage", "quota_usage", "pod_count", "sm_partition", "quota"):
                if key in gpu_rows[0]:
                    values = sorted({float(row[key]) for row in gpu_rows if row[key]})
                    gpu_numeric_values[key] = values
            runs.append({
                "run": folder[len(RESULT_PREFIX):].rstrip("/"),
                "model_family": folder[len(RESULT_PREFIX):].rstrip("/").split("-", 1)[1].removesuffix("-stress"),
                "request_member": req_path,
                "request_sha256": req_sha,
                "request_rows": len(req_rows),
                "request_duration_seconds_min": min(durations),
                "request_duration_seconds_max": max(durations),
                "request_duration_negative_or_zero_rows": sum(value <= 0 for value in durations),
                "request_completion_row_order_regressions": sum(a > b for a, b in zip(request_ends, request_ends[1:])),
                "request_first_completion_as_utc": min(request_ends).isoformat(),
                "request_last_completion_as_utc": max(request_ends).isoformat(),
                "request_starts_in_gpu_span_as_utc": started_in_gpu_span,
                "request_completions_in_gpu_span_as_utc": ended_in_gpu_span,
                "gpu_member": gpu_path,
                "gpu_sha256": gpu_sha,
                "gpu_rows": len(gpu_rows),
                "gpu_first_utc": gpu_start.isoformat(),
                "gpu_last_utc": gpu_end.isoformat(),
                "gpu_row_order_regressions": sum(a > b for a, b in zip(gpu_times, gpu_times[1:])),
                "gpu_columns": list(gpu_rows[0]),
                "gpu_numeric_values": gpu_numeric_values,
                "future_start_counts": future_start_counts,
            })
    if remote.bytes_received >= source["size"]:
        raise ValueError("range audit unexpectedly downloaded the whole ZIP")
    return {
        "record_url": "https://zenodo.org/records/15596511",
        "record_api": RECORD_URL,
        "zip_size": source["size"],
        "zip_published_md5_unverified": source["checksum"],
        "license_api_id": record["metadata"]["license"]["id"],
        "source_member": SOURCE_MEMBER,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "readme_member": README_MEMBER,
        "readme_sha256": hashlib.sha256(readme_bytes).hexdigest(),
        "zip_readme_mentions_apache_2_0": b"Apache License" in readme_bytes or b"Apache License, Version 2.0" in readme_bytes,
        "zip_member_count": len(names),
        "selected_runs": len(runs),
        "selected_request_rows": sum(run["request_rows"] for run in runs),
        "selected_gpu_rows": sum(run["gpu_rows"] for run in runs),
        "selected_request_completions_in_gpu_span_as_utc": sum(run["request_completions_in_gpu_span_as_utc"] for run in runs),
        "request_duration_seconds_min": min(run["request_duration_seconds_min"] for run in runs),
        "request_duration_seconds_max": max(run["request_duration_seconds_max"] for run in runs),
        "request_duration_negative_or_zero_rows": sum(run["request_duration_negative_or_zero_rows"] for run in runs),
        "request_completion_row_order_regressions": sum(run["request_completion_row_order_regressions"] for run in runs),
        "gpu_row_order_regressions": sum(run["gpu_row_order_regressions"] for run in runs),
        "http_range_requests": remote.requests,
        "http_range_bytes_received": remote.bytes_received,
        "runs": runs,
        "interpretation": "The join assumes naive client-local request times can be interpreted as UTC; overlap alone does not prove clock synchronization.",
    }


if __name__ == "__main__":
    output = audit()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "runs"}, indent=2))
    print(f"wrote {ARTIFACT}")
