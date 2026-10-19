"""Fetch the ten pinned official Google 2019 cell-a collection-event objects.

Only gzip files are stored. Each download is sequential, generation-pinned,
and checked against the public GCS size and MD5 in the local object manifest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "data" / "external" / "google_cluster_2019_a"
MANIFEST = ROOT / "collection_events_object_manifest.json"


def inspect(path: Path) -> tuple[int, str, str]:
    size = 0
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            md5.update(block)
            sha256.update(block)
    return size, base64.b64encode(md5.digest()).decode(), sha256.hexdigest()


def fetch_object(entry: dict[str, object]) -> str:
    name = str(entry["name"])
    destination = ROOT / name
    expected_size = int(entry["bytes"])
    expected_md5 = str(entry["md5_base64"])
    if destination.exists():
        size, md5, sha256 = inspect(destination)
        if size == expected_size and md5 == expected_md5:
            print(f"verified existing {name} {size} {sha256}", flush=True)
            return sha256
        raise ValueError(f"existing object disagrees with pinned GCS metadata: {name}")

    url = ("https://storage.googleapis.com/clusterdata_2019_a/"
           + urllib.parse.quote(name)
           + "?generation=" + urllib.parse.quote(str(entry["generation"])))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=ROOT, prefix=f".{name}.", suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(url, timeout=120) as response:
                while block := response.read(1024 * 1024):
                    output.write(block)
        size, md5, sha256 = inspect(temporary)
        if size != expected_size or md5 != expected_md5:
            raise ValueError(f"downloaded object failed GCS size or MD5: {name}")
        os.replace(temporary, destination)
        temporary = None
        print(f"verified download {name} {size} {sha256}", flush=True)
        return sha256
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    entries = manifest["objects"]
    names = [entry["name"] for entry in entries]
    expected_names = [f"collection_events-{index:012d}.json.gz" for index in range(10)]
    if manifest["bucket"] != "clusterdata_2019_a" or names != expected_names:
        raise ValueError("object manifest is not the fixed ten-shard cell-a collection-event set")
    hashes = {entry["name"]: fetch_object(entry) for entry in entries}
    verified = {
        "bucket": manifest["bucket"],
        "metadata_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "objects": [{**entry, "sha256": hashes[entry["name"]]} for entry in entries],
    }
    (ROOT / "verified_collection_events_manifest.json").write_text(
        json.dumps(verified, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
