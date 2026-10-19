#!/usr/bin/env python3
"""Audit Airflow autoscaling artifacts without restoring the 4.18 GB DB archive.

This examines the official, checksummed summary and software archives plus a
partial HTTP range of db.tar.gz. It cannot count actions or match outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


SELECTED = "PerformanceFeedback_3_0_5__154"
EXPECTED_MD5 = {
    "csv.tar.gz": "612550cbf2721dbb9a5450331766c225",
    "sa.tar.gz": "b2262a4f5d8b08e5a591cb5344c2e721",
}
EXPECTED_SHA256 = {
    "SELECTION.md": "7c0424fcedee2bcba8979608a3bc09f061cab2cd35bc6c4c4fabde6d342b2b19",
    "zenodo_record.json": "38ad461c90f3e2fc78a9a9474a5639cbec159ef9583e1510dc00220c88510406",
    "db_tar_gz_range_0_8388607.bin": "0d48e9b84d4158f917e6f0fb9685ebe1e0680f7eea57e605346be981ba58e162",
}
CSV_IDENTIFIERS = (
    SELECTED,
    "timestamp",
    "event_type",
    "resource_id",
    "dag_id",
    "execution_date",
    "task_id",
    "ResourceAllocated",
    "ResourceDeallocated",
)


def digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(data_dir: Path) -> dict:
    for name, expected in EXPECTED_MD5.items():
        actual = digest(data_dir / name, "md5")
        if actual != expected:
            raise ValueError(f"{name}: official MD5 mismatch: {actual}")
    for name, expected in EXPECTED_SHA256.items():
        actual = digest(data_dir / name, "sha256")
        if actual != expected:
            raise ValueError(f"{name}: pinned SHA-256 mismatch: {actual}")

    selection = data_dir / "SELECTION.md"
    if SELECTED not in selection.read_text(encoding="utf-8"):
        raise ValueError("preselected experiment missing from SELECTION.md")

    csv_hits = {term: [] for term in CSV_IDENTIFIERS}
    csv_names = []
    with tarfile.open(data_dir / "csv.tar.gz", "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".csv"):
                continue
            csv_names.append(member.name)
            payload = archive.extractfile(member).read()
            for term in CSV_IDENTIFIERS:
                if term.encode("ascii") in payload:
                    csv_hits[term].append(member.name)

    source_name = "airflow/airflow/models.py"
    allocator_name = "airflow/airflow/autoscalers/resource_allocator.py"
    jobs_name = "airflow/airflow/jobs.py"
    with tarfile.open(data_dir / "sa.tar.gz", "r:gz") as archive:
        sources = {
            name: archive.extractfile(name).read().decode("utf-8")
            for name in (source_name, allocator_name, jobs_name)
        }
    enum_block = sources[source_name].split("class StatLogEventTypes(enum.Enum):", 1)[1].split("class StatLog(Base):", 1)[0]
    event_values = {
        name: int(value)
        for name, value in re.findall(r"^\s+(\w+)\s*=\s*(\d+)", enum_block, re.MULTILINE)
    }
    required = {
        "WorkflowArrived": 0,
        "TaskStarted": 3,
        "TaskFinished": 4,
        "ResourceAllocated": 5,
        "ResourceIdle": 6,
        "ResourceBusy": 7,
        "ResourceDeallocated": 8,
        "TaskEligible": 11,
    }
    if any(event_values.get(k) != v for k, v in required.items()):
        raise ValueError("official software event definitions differ from audit")
    if "resource.state = State.BOOTING" not in sources[allocator_name]:
        raise ValueError("allocation state transition missing")
    if "idle_res.state = State.DOWN" not in sources[allocator_name]:
        raise ValueError("deallocation state transition missing")
    if "event_type=models.StatLogEventTypes.TaskStarted" not in sources[jobs_name]:
        raise ValueError("task assignment event missing")

    prefix = data_dir / "db_tar_gz_range_0_8388607.bin"
    with prefix.open("rb") as stream:
        gzip_magic = stream.read(2)
    if prefix.stat().st_size != 8_388_608 or gzip_magic != b"\x1f\x8b":
        raise ValueError("DB range is not the expected first 8 MiB of a gzip stream")
    headers_seen = 0
    complete_members = 0
    current_member = None
    experiment_names = set()
    selected_members = []
    log_members = []
    truncated = False
    try:
        with tarfile.open(prefix, "r|gz") as archive:
            for member in archive:
                headers_seen += 1
                current_member = member.name
                if member.isfile():
                    with archive.extractfile(member) as payload:
                        while payload.read(1024 * 1024):
                            pass
                complete_members += 1
                parts = member.name.split("/")
                if len(parts) > 1 and parts[0] == "db":
                    experiment_names.add(parts[1])
                if SELECTED in parts:
                    selected_members.append(member.name)
                if member.name.endswith("airflow-autoscaler.log"):
                    log_members.append(member.name)
    except (tarfile.ReadError, EOFError):
        truncated = True

    return {
        "selected_experiment": SELECTED,
        "official_archives": {
            name: {"bytes": (data_dir / name).stat().st_size, "md5": EXPECTED_MD5[name]}
            for name in EXPECTED_MD5
        },
        "csv_files_checked": len(csv_names),
        "csv_identifier_hits": csv_hits,
        "software_event_values": {k: event_values[k] for k in required},
        "db_prefix": {
            "bytes": prefix.stat().st_size,
            "sha256": digest(prefix, "sha256"),
            "truncated_as_expected": truncated,
            "tar_member_headers_seen": headers_seen,
            "complete_tar_members_seen": complete_members,
            "incomplete_member": (current_member if headers_seen > complete_members else None),
            "experiment_directories_seen": len(experiment_names),
            "selected_members_seen": selected_members,
            "log_members_seen": log_members,
        },
        "action_counts_or_matching_rates_measured": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/external/airflow_action_feasibility",
    )
    args = parser.parse_args()
    print(json.dumps(inspect(args.data_dir), indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
