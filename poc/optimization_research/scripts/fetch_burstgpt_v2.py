"""Fetch the pinned BurstGPT v2.0 request trace with observed elapsed time."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path


TAG_COMMIT = "7eb2c4f8350f8a6985272386f5c14af1f678b299"
FILENAME = "BurstGPT_3.csv"
URL = f"https://github.com/HPMLL/BurstGPT/releases/download/v2.0/{FILENAME}"
EXPECTED_SIZE = 231_682_327
EXPECTED_SHA256 = "2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f"


def digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def fetch(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    target = output / FILENAME
    if target.exists():
        size, sha256 = digest_file(target)
        if size != EXPECTED_SIZE or sha256 != EXPECTED_SHA256:
            raise ValueError(f"Existing {target} differs from pinned v2.0 asset")
        print(f"Verified {target}: {size} bytes, SHA256 {sha256}")
        return target

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, prefix=f".{FILENAME}.", delete=False) as temp:
            tmp_path = Path(temp.name)
            request = urllib.request.Request(URL, headers={"User-Agent": "ai-mcmp-research"})
            with urllib.request.urlopen(request, timeout=90) as response:
                while chunk := response.read(1024 * 1024):
                    temp.write(chunk)
            temp.flush()
            os.fsync(temp.fileno())
        size, sha256 = digest_file(tmp_path)
        if size != EXPECTED_SIZE or sha256 != EXPECTED_SHA256:
            raise ValueError(f"Downloaded asset mismatch: {size} bytes, SHA256 {sha256}")
        os.replace(tmp_path, target)
        print(f"Downloaded {target}: {size} bytes, SHA256 {sha256}")
        return target
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/burstgpt_v2",
    )
    fetch(parser.parse_args().output)
