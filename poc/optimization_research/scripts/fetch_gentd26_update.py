"""Fetch pinned GenTD26 pipeline-update and pre-observation resource traces.

The existing GenTD26 downloader covers other files. This small, separate
collector keeps the update experiment's source hashes independent.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path


COMMIT = "8c132fb7717fbd44cf434cf81f2cd865a80072d5"
BASE = (
    "https://raw.githubusercontent.com/alibaba/clusterdata/"
    f"{COMMIT}/cluster-trace-v2026-GenAI/"
)
ARCHIVES = {
    "pipeline_update_latency_anon.tar.gz": "464bfc59ba7d35c68e30c2cb77303ed303e9832761f8d12a45f80dda1c8dbb79",
    "basemodel_update_latency_anon.tar.gz": "4a0cfd5537dc0e7e19340cc8cfc49c26d6aef0ffd85f3ed68db1f4e157811e9d",
    "lora_update_latency_anon.tar.gz": "15fb8a51adad7d05861fdd73174642027b3d9fed821d7e762f330cf2d81e9c38",
    "controlnet_latency_data_anon.tar.gz": "3d6fe3248084069835ee50753029800455b93e7c9037fca2068d19db096e61dd",
    "model_predict_data_anon.tar.gz": "9d97e16c2f2e2ef009edc8e67a4947bb708432123873e417a69089e4c864e87d",
    "pod_memory_util_anon.tar.gz": "0ccfb52821432dd6eed546ae772299c9c9fa9bbed952eb44953c41564afcfa7c",
}


def fetch(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in ARCHIVES.items():
        path = output / name
        if path.exists():
            content = path.read_bytes()
        else:
            with urllib.request.urlopen(BASE + name, timeout=60) as response:
                content = response.read()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ValueError(f"{name}: SHA256 mismatch: {actual}")
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            members = archive.getmembers()
            if (len(members) != 1 or not members[0].isfile()
                    or not members[0].name.endswith(".csv")
                    or Path(members[0].name).name != members[0].name):
                raise ValueError(f"{name}: unexpected archive members")
            handle = archive.extractfile(members[0])
            if handle is None:
                raise ValueError(f"{name}: missing CSV")
            csv = handle.read()
            csv_path = output / members[0].name
            if csv_path.exists() and csv_path.read_bytes() != csv:
                raise ValueError(f"{csv_path}: existing content differs")
            if not csv_path.exists():
                csv_path.write_bytes(csv)
        if not path.exists():
            path.write_bytes(content)
        print(f"{name}: {len(content)} bytes, SHA256 {actual}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/gentd26",
    )
    fetch(parser.parse_args().output)
