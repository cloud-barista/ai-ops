"""Fetch the pinned ETH LLM serving trace with resumable HTTP byte ranges."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import threading
import urllib.request
from pathlib import Path

from swissai_trace_io import local_source_path, open_source, source_digest


REVISION = "c3dd66602b564ce189f7db81115620413e646cd5"
FILENAME = "trace.jsonl"
URL = (
    "https://huggingface.co/datasets/eth-easl/swissai-serving-trace/"
    f"resolve/{REVISION}/{FILENAME}"
)
EXPECTED_SIZE = 6_981_478_609
# LFS object SHA256 published in the Hugging Face repository tree API.
EXPECTED_SHA256 = "79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7"
CHUNK_SIZE = 64 * 1024 * 1024


def file_digest(path: Path) -> tuple[int, str]:
    return source_digest(path)


def _write_state(path: Path, completed: dict[int, str]) -> None:
    temporary = path.with_suffix(".tmp")
    state = {
        "revision": REVISION,
        "size": EXPECTED_SIZE,
        "chunk_size": CHUNK_SIZE,
        "completed": {str(index): digest for index, digest in sorted(completed.items())},
    }
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(state, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _hash_chunk(source: Path, index: int) -> str:
    start = index * CHUNK_SIZE
    count = min(CHUNK_SIZE, EXPECTED_SIZE - start)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        handle.seek(start)
        while count:
            block = handle.read(min(count, 4 * 1024 * 1024))
            if not block:
                raise ValueError("Incomplete local chunk")
            digest.update(block)
            count -= len(block)
    return digest.hexdigest()


def _fetch_chunk(fd: int, index: int) -> tuple[int, str]:
    start = index * CHUNK_SIZE
    end = min(EXPECTED_SIZE, start + CHUNK_SIZE) - 1
    request = urllib.request.Request(
        URL,
        headers={"User-Agent": "ai-mcmp-research", "Range": f"bytes={start}-{end}"},
    )
    digest = hashlib.sha256()
    offset = start
    with urllib.request.urlopen(request, timeout=120) as response:
        expected_range = f"bytes {start}-{end}/{EXPECTED_SIZE}"
        if response.status != 206 or response.headers.get("Content-Range") != expected_range:
            raise ValueError(f"Unexpected HTTP response for chunk {index}")
        while block := response.read(4 * 1024 * 1024):
            if offset + len(block) > end + 1:
                raise ValueError(f"Chunk {index} exceeded its byte range")
            digest.update(block)
            remaining = memoryview(block)
            while remaining:
                written = os.pwrite(fd, remaining, offset)
                if written <= 0:
                    raise OSError("Short write")
                offset += written
                remaining = remaining[written:]
    if offset != end + 1:
        raise ValueError(f"Chunk {index} was incomplete")
    os.fsync(fd)
    return index, digest.hexdigest()


def fetch(output: Path, workers: int = 4) -> Path:
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    output.mkdir(parents=True, exist_ok=True)
    target = output / FILENAME
    compressed_target = output / f"{FILENAME}.gz"
    for candidate in (target, compressed_target):
        if not candidate.exists():
            continue
        size, sha256 = file_digest(candidate)
        if size != EXPECTED_SIZE or sha256 != EXPECTED_SHA256:
            raise ValueError(f"Existing {candidate} differs from pinned source")
        print(f"Verified {candidate}: {size} uncompressed bytes, SHA256 {sha256}", flush=True)
        return candidate

    partial = output / f".{FILENAME}.part"
    checkpoint = output / f".{FILENAME}.parts.json"
    completed: dict[int, str] = {}
    if partial.exists() != checkpoint.exists():
        raise ValueError("Partial data and checkpoint must exist together")
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            state.get("revision") != REVISION
            or state.get("size") != EXPECTED_SIZE
            or state.get("chunk_size") != CHUNK_SIZE
            or partial.stat().st_size != EXPECTED_SIZE
        ):
            raise ValueError("Partial download does not match pinned source")
        completed = {int(index): digest for index, digest in state["completed"].items()}
        for index, digest in completed.items():
            if _hash_chunk(partial, index) != digest:
                raise ValueError(f"Local chunk {index} differs from checkpoint")
    else:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.ftruncate(fd, EXPECTED_SIZE)
        finally:
            os.close(fd)
        _write_state(checkpoint, completed)

    chunks = math.ceil(EXPECTED_SIZE / CHUNK_SIZE)
    missing = [index for index in range(chunks) if index not in completed]
    print(f"Downloading {len(missing)}/{chunks} chunks with {workers} workers", flush=True)
    fd = os.open(partial, os.O_WRONLY)
    state_lock = threading.Lock()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_chunk, fd, index): index for index in missing}
            for future in concurrent.futures.as_completed(futures):
                index, digest = future.result()
                with state_lock:
                    completed[index] = digest
                    _write_state(checkpoint, completed)
                    if len(completed) % 10 == 0 or len(completed) == chunks:
                        print(f"Verified downloaded chunks: {len(completed)}/{chunks}", flush=True)
    finally:
        os.close(fd)

    size, sha256 = file_digest(partial)
    if size != EXPECTED_SIZE or sha256 != EXPECTED_SHA256:
        raise ValueError(f"Whole-file SHA256 mismatch: {size} bytes, {sha256}")
    os.replace(partial, target)
    checkpoint.unlink()
    print(f"Downloaded {target}: {size} bytes, SHA256 {sha256}", flush=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/swissai_serving_trace",
    )
    parser.add_argument("--workers", type=int, default=4)
    arguments = parser.parse_args()
    fetch(arguments.output, arguments.workers)
