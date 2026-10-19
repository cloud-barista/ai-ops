"""Fetch the pinned Qwen bucket trace without keeping an uncompressed copy."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/external/swissai_serving_trace/qwen3-32b-buckets.jsonl.gz"
URL = (
    "https://huggingface.co/datasets/eth-easl/swissai-serving-trace/resolve/"
    "c3dd66602b564ce189f7db81115620413e646cd5/"
    "qwen3-32b-buckets.jsonl?download=true"
)
EXPECTED_SHA256 = "58b2265b98a10ec3bb2a0b455c520731c1296967906c7adc7b6d3710a013e402"
EXPECTED_BYTES = 4_617_817_342
EXPECTED_ROWS = 3_994_435
CHUNK = 1024 * 1024


def fetch() -> dict:
    if DEST.exists() or DEST.is_symlink():
        raise FileExistsError(f"refusing_to_replace:{DEST}")
    part = DEST.with_suffix(DEST.suffix + ".part")
    if part.exists() or part.is_symlink():
        raise FileExistsError(f"remove_incomplete_prior_download_first:{part}")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    rows = 0
    size = 0
    final_byte = None
    try:
        with urllib.request.urlopen(URL, timeout=120) as source, part.open("xb") as raw_dest:
            with gzip.GzipFile(filename="qwen3-32b-buckets.jsonl", mode="wb", fileobj=raw_dest, compresslevel=1, mtime=0) as zipped:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
                    rows += chunk.count(b"\n")
                    size += len(chunk)
                    final_byte = chunk[-1]
                    zipped.write(chunk)
                    if size // (256 * CHUNK) != (size - len(chunk)) // (256 * CHUNK):
                        print(f"downloaded {size / 1e9:.2f}/{EXPECTED_BYTES / 1e9:.2f} GB", file=sys.stderr, flush=True)
            raw_dest.flush()
            os.fsync(raw_dest.fileno())
        if size != EXPECTED_BYTES or rows != EXPECTED_ROWS or digest.hexdigest() != EXPECTED_SHA256 or final_byte != 10:
            raise ValueError(f"source_integrity_mismatch bytes={size} rows={rows} sha256={digest.hexdigest()} last_byte={final_byte}")
        os.replace(part, DEST)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"path": str(DEST), "compressed_bytes": DEST.stat().st_size,
            "raw_bytes": size, "rows": rows, "raw_sha256": digest.hexdigest(), "url": URL}


if __name__ == "__main__":
    print(json.dumps(fetch(), sort_keys=True))
