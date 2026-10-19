"""Fetch the small, pinned GenTD26 trace files used in this research."""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path


COMMIT = "8c132fb7717fbd44cf434cf81f2cd865a80072d5"
BASE_URL = (
    "https://raw.githubusercontent.com/alibaba/clusterdata/"
    f"{COMMIT}/cluster-trace-v2026-GenAI/"
)
SHA256 = {
    "README.md": "389a0cb2da5212bdccbf3fc00bffd9850b6c0142a5855167dcef350083e526fe",
    "qps.tar.gz": "6bd1a21e3f916740df1e768fc43346148f1598b15fcc68c658a8faf1f87461c6",
    "queue_size_raw_anon.tar.gz": "585499916ed94900921e72c01213a618cad52443fc3f20f1e0e7ad4c4bb11798",
    "queue_rt_raw_anon.tar.gz": "419deaccd1cc33ef3050b676f75a49af726c169de62ae29344ea2fdf1d4f3819",
    "pod_gpu_duty_cycle_anon.tar.gz": "c4070218189b2811995c30dd9fefb8a793a83a54dccebe7b41cab2d123916855",
    "pod_gpu_memory_used_bytes_anon.tar.gz": "662be431f2d24a003c3a0b28383155fccceefb7e83dea14f912166edc9129132",
    "pipeline_inference_data_anon.tar.gz": "4899bd06c7089764046ae8b1ba5dfd243e47062c96d9ff34f7d344558602bbd5",
    "lora_request_trace.tar.gz": "3205a42d9b11e027434448a21e20125e382e0caab4bcda9cd56aca7cc6c0d57f",
}


def extract_one_csv(archive: bytes) -> tuple[str, bytes]:
    """Only accept a single, plain CSV entry inside each archive."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as handle:
        members = handle.getmembers()
        if (
            len(members) != 1
            or not members[0].isfile()
            or not members[0].name.endswith(".csv")
            or Path(members[0].name).name != members[0].name
        ):
            raise ValueError("Unexpected GenTD26 archive contents")
        stream = handle.extractfile(members[0])
        if stream is None:
            raise ValueError("Missing GenTD26 CSV content")
        return members[0].name, stream.read()


def fetch(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, expected in SHA256.items():
        target = output / name
        if target.exists():
            content = target.read_bytes()
        else:
            with urllib.request.urlopen(BASE_URL + name, timeout=60) as response:
                content = response.read()
        observed = hashlib.sha256(content).hexdigest()
        if observed != expected:
            raise ValueError(f"{name}: SHA256 mismatch: {observed}")
        if not target.exists():
            target.write_bytes(content)
        print(f"{name}: {len(content)} bytes, SHA256 {observed}")
        if name.endswith(".tar.gz"):
            csv_name, csv_content = extract_one_csv(content)
            csv_path = output / csv_name
            if csv_path.exists() and csv_path.read_bytes() != csv_content:
                raise ValueError(f"{csv_path}: existing extracted file differs")
            if not csv_path.exists():
                csv_path.write_bytes(csv_content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/gentd26",
    )
    fetch(parser.parse_args().output)
