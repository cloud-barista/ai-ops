"""Stream the untouched Philly [25,35) machine bucket after method freeze.

Reuse the already-audited archive parser and parquet writer without changing
their files. The source's global selection and disk guards are overridden only
inside this isolated process and recorded in the new collection audit.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import requests

import fetch_philly_gpu_sample as source
import fetch_philly_machine_calibration as prior_collector


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data/external/msr_philly_2017_day_ahead_holdout"
OUTPUT_NAME = "gpu_util_machine_hash25to34pct.parquet"
AUDIT_NAME = "gpu_util_day_ahead_holdout_audit.json"
FREEZE = ROOT / "artifacts/philly_day_ahead_calibration_holdout_v1/freeze.json"
LOWER_RESIDUE = 25
UPPER_RESIDUE = 35
MIN_FREE_BYTES = 3_000_000_000
MAX_SAMPLE_BYTES = 300_000_000
SELECTION_RULE = "25 <= int.from_bytes(SHA256(machineId UTF-8), 'big') % 100 < 35"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected(machine_id: str) -> bool:
    bucket = prior_collector.machine_bucket(machine_id)
    return LOWER_RESIDUE <= bucket < UPPER_RESIDUE


def require_room(path: Path) -> None:
    fs = os.statvfs(path)
    if fs.f_bavail * fs.f_frsize < MIN_FREE_BYTES:
        raise RuntimeError("Less than 3 GB free before/while streaming compact sample")


def verified_freeze() -> dict:
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    required = {
        "collector_sha256": sha256(Path(__file__)),
        "inner_collector_sha256": sha256(Path(prior_collector.__file__)),
        "source_parser_sha256": sha256(Path(source.__file__)),
    }
    if frozen.get("status") != "FROZEN_BEFORE_BUCKET_25_35_COLLECTION":
        raise ValueError("new machine protocol is not frozen")
    for key, digest in required.items():
        if frozen.get(key) != digest:
            raise ValueError(f"frozen {key} changed")
    if frozen.get("source_archive_sha256") != source.EXPECTED_SHA256:
        raise ValueError("archive identity changed")
    return frozen


def collect() -> dict:
    frozen = verified_freeze()  # Never make an HTTP request before this.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    require_room(OUTPUT_DIR)
    output = OUTPUT_DIR / OUTPUT_NAME
    audit_path = OUTPUT_DIR / AUDIT_NAME
    machine_path = OUTPUT_DIR / "machine_list.json"
    if any(path.exists() for path in (output, audit_path, machine_path)):
        raise FileExistsError("new machine sample already exists")
    with requests.Session() as session:
        source.validate_http_head(session)
        with session.get(source.URL, stream=True, timeout=(30, 180),
                         headers={"Accept-Encoding": "identity",
                                  "User-Agent": "ai-mcmp-research/philly-day-ahead-holdout"}) as response:
            if response.status_code != 200:
                raise ValueError(f"Pinned Philly GET returned {response.status_code}")
            if (int(response.headers.get("Content-Length", "-1")) != source.EXPECTED_SIZE
                    or response.headers.get("ETag") != source.EXPECTED_ETAG
                    or response.headers.get("Content-Encoding", "identity") != "identity"):
                raise ValueError("Pinned Philly GET headers differ from source contract")
            response.raw.decode_content = False
            with ExitStack() as overrides:
                overrides.enter_context(patch.object(prior_collector, "machine_selected", selected))
                overrides.enter_context(patch.object(prior_collector, "LOWER_RESIDUE", LOWER_RESIDUE))
                overrides.enter_context(patch.object(prior_collector, "UPPER_RESIDUE", UPPER_RESIDUE))
                overrides.enter_context(patch.object(prior_collector, "SELECTION_RULE", SELECTION_RULE))
                overrides.enter_context(patch.object(source, "MIN_FREE_BYTES", MIN_FREE_BYTES))
                audit = prior_collector.scan_archive(
                    response.raw, output=output,
                    expected_size=source.EXPECTED_SIZE,
                    expected_sha256=source.EXPECTED_SHA256,
                    max_sample_bytes=MAX_SAMPLE_BYTES)
    selected_ids = audit["gpu_csv"]["selected_machine_ids"]
    if (not selected_ids or any(not selected(mid) for mid in selected_ids)
            or audit["sample_rule"] != SELECTION_RULE
            or audit["sample_bucket_lower_inclusive"] != LOWER_RESIDUE
            or audit["sample_bucket_upper_exclusive"] != UPPER_RESIDUE
            or audit["gpu_csv"]["rows"] != 44_750_640):
        raise ValueError("new bucket selection or full-source audit failed")
    old_ids = set()
    for relative in (
        "data/external/msr_philly_2017/gpu_util_audit.json",
        "data/external/msr_philly_2017_holdout/gpu_util_holdout_audit.json",
        "data/external/msr_philly_2017_calibration_holdout/gpu_util_calibration_holdout_audit.json",
    ):
        old_ids.update(json.loads((ROOT/relative).read_text(encoding="utf-8"))
                       ["gpu_csv"]["selected_machine_ids"])
    if set(selected_ids) & old_ids:
        raise ValueError("new machine bucket overlaps earlier work")
    machine_records = audit["machine_list"].pop("records")
    machine_temp = machine_path.with_name(machine_path.name + ".tmp")
    machine_temp.write_text(json.dumps(machine_records, indent=2, sort_keys=True)+"\n")
    os.replace(machine_temp, machine_path)
    audit["machine_list"]["path"] = str(machine_path)
    audit["machine_list"]["sha256"] = sha256(machine_path)
    audit.update({"http_content_length": source.EXPECTED_SIZE,
                  "http_etag": source.EXPECTED_ETAG,
                  "pre_fetch_freeze_sha256": sha256(FREEZE),
                  "wrapper_collector_sha256": frozen["collector_sha256"],
                  "inner_collector_sha256": frozen["inner_collector_sha256"],
                  "minimum_free_bytes": MIN_FREE_BYTES,
                  "maximum_compact_sample_bytes": MAX_SAMPLE_BYTES})
    temp = audit_path.with_name(audit_path.name + ".tmp")
    temp.write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False)+"\n")
    os.replace(temp, audit_path)
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = collect()
    print(json.dumps({"complete": result["complete"],
                      "source_rows": result["gpu_csv"]["rows"],
                      "new_rows": result["gpu_csv"]["selected_rows"],
                      "new_machines": result["gpu_csv"]["selected_machine_count"],
                      "audit": str(OUTPUT_DIR/AUDIT_NAME)}, sort_keys=True))
