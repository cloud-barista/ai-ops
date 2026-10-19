"""Fetch only Alibaba's small, pinned MaaS application-case release."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
import zipfile

from evaluate_maas_trace import SOURCE, SOURCE_SHA256, sha256, verify_sources


URLS = {
    "app_metrics.csv.zip": (
        "https://tre-clusterdata.oss-cn-hangzhou.aliyuncs.com/"
        "cluster-trace-v2026-maas/data/app_metrics.csv.zip"
    ),
    "app_cases.json": (
        "https://raw.githubusercontent.com/alibaba/clusterdata/"
        "8a6295f9779ddc019add669eeca49c5f9f77b3a7/"
        "cluster-trace-v2026-maas/data/app_cases.json"
    ),
}


def fetch(source: Path = SOURCE) -> dict[str, str]:
    source.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maas_app_", dir=source) as temp:
        temp_dir = Path(temp)
        for name, url in URLS.items():
            destination = source / name
            if destination.exists() and sha256(destination) == SOURCE_SHA256[name]:
                continue
            temporary = temp_dir / name
            with urlopen(url, timeout=60) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if sha256(temporary) != SOURCE_SHA256[name]:
                raise ValueError(f"{name}: public source hash changed")
            temporary.replace(destination)
        csv_path = source / "app_metrics.csv"
        if not csv_path.exists() or sha256(csv_path) != SOURCE_SHA256["app_metrics.csv"]:
            with zipfile.ZipFile(source / "app_metrics.csv.zip") as archive:
                if archive.namelist() != ["app_metrics.csv"]:
                    raise ValueError("Unexpected application-case archive members")
                temporary = temp_dir / "app_metrics.csv"
                temporary.write_bytes(archive.read("app_metrics.csv"))
                if sha256(temporary) != SOURCE_SHA256["app_metrics.csv"]:
                    raise ValueError("Extracted CSV hash mismatch")
                temporary.replace(csv_path)
    return verify_sources(source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    hashes = fetch(args.source)
    for name, digest in hashes.items():
        print(f"{digest}  {args.source / name}")


if __name__ == "__main__":
    main()
