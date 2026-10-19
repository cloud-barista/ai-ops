"""Lossless access helpers for the pinned SwissAI JSONL source."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
from typing import BinaryIO


RAW_FILENAME = "trace.jsonl"
GZIP_FILENAME = RAW_FILENAME + ".gz"


def local_source_path(directory: Path) -> Path:
    """Prefer the original file, otherwise use its lossless gzip archive."""
    raw = directory / RAW_FILENAME
    compressed = directory / GZIP_FILENAME
    if raw.exists():
        return raw
    if compressed.exists():
        return compressed
    raise FileNotFoundError(f"Neither {raw} nor {compressed} exists")


def open_source(path: Path) -> BinaryIO:
    """Open the source as decompressed bytes when it is stored as gzip."""
    path = Path(path)
    if path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    return path.open("rb")


def source_digest(path: Path, chunk_size: int = 4 * 1024 * 1024) -> tuple[int, str]:
    """Return the original uncompressed byte count and SHA-256."""
    size = 0
    digest = hashlib.sha256()
    with open_source(path) as stream:
        while block := stream.read(chunk_size):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()
