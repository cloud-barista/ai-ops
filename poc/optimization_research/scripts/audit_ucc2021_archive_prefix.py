"""Read only the first MiB of the official UCC2021 archive for join evidence.

The archive contains concatenated bzip2 streams, so BZ2File wraps the prefix
before tarfile reads it. This script stops at the first large client log and
never writes or extracts archive contents.
"""

from __future__ import annotations

import bz2
import hashlib
import io
import json
import re
import tarfile
import urllib.request


URL = "https://zenodo.org/api/records/5618882/files/UCC2021-data.tar.bz2/content"
PREFIX_BYTES = 1 << 20
EXPECTED_TOTAL_BYTES = 948_445_354
EXPECTED_PREFIX_SHA256 = (
    "dbc84777afc1f26e32dd706798c68d3f42e66d5aa611b7e33f6af800b72f6f91"
)


def audit() -> dict[str, object]:
    request = urllib.request.Request(
        URL,
        headers={"Range": f"bytes=0-{PREFIX_BYTES - 1}",
                 "User-Agent": "ai-mcmp-ucc2021-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        expected_range = f"bytes 0-{PREFIX_BYTES - 1}/{EXPECTED_TOTAL_BYTES}"
        if response.status != 206 or response.headers.get("Content-Range") != expected_range:
            raise ValueError("The server did not honor the pinned one-MiB range")
        prefix = response.read(PREFIX_BYTES + 1)
    if len(prefix) != PREFIX_BYTES:
        raise ValueError(f"Expected {PREFIX_BYTES} bytes; received {len(prefix)}")
    digest = hashlib.sha256(prefix).hexdigest()
    if digest != EXPECTED_PREFIX_SHA256:
        raise ValueError("Official archive prefix changed; review before using results")

    findings: dict[str, object] = {
        "source_url": URL,
        "prefix_bytes": len(prefix),
        "archive_total_bytes": EXPECTED_TOTAL_BYTES,
        "prefix_sha256": digest,
        "server_start_logs": {},
    }
    with bz2.BZ2File(io.BytesIO(prefix)) as decompressed:
        with tarfile.open(fileobj=decompressed, mode="r|") as archive:
            for entry in archive:
                if not entry.isfile():
                    continue
                if entry.name == "data/distwalk-lin-04.log":
                    with archive.extractfile(entry) as handle:
                        head = handle.read(8192).decode("utf-8", "replace")
                    request_line = next(
                        (line for line in head.splitlines() if line.startswith("t: ")),
                        None,
                    )
                    findings["first_client_log"] = {
                        "member": entry.name,
                        "uncompressed_bytes": entry.size,
                        "first_request_line": request_line,
                    }
                    break
                if re.fullmatch(r"data/distwalk-\w+-\d+-instance-\d+\.log", entry.name):
                    with archive.extractfile(entry) as handle:
                        line = handle.read().decode("utf-8", "replace").strip()
                    if not re.fullmatch(r"Server started at: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", line):
                        raise ValueError(f"Unexpected server log format: {entry.name}")
                    findings["server_start_logs"][entry.name] = line
                if entry.name == "data/distwalk-lin-04-pred.json":
                    with archive.extractfile(entry) as handle:
                        records = json.load(handle)
                    stamps = [m[0] for record in records for m in record["measurements"]]
                    findings["lin_prediction"] = {
                        "member": entry.name,
                        "metric_names": sorted({record["name"] for record in records}),
                        "measurement_count": len(stamps),
                        "first_timestamp": min(stamps) if stamps else None,
                        "last_timestamp": max(stamps) if stamps else None,
                    }
    if "first_client_log" not in findings or not findings["server_start_logs"]:
        raise ValueError("Required log evidence was not present in the archive prefix")
    return findings


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
