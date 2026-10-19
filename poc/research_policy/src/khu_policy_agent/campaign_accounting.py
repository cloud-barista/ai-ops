"""Validation and byte accounting for CB-Tumblebug collection evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable


class RunValidationError(ValueError):
    """Raised when a collected run cannot safely contribute to a campaign."""


@dataclass(frozen=True)
class RunEvidence:
    run_id: str
    infra_id: str
    spec_id: str
    image_id: str
    device: str
    profile_set: str
    seed: int
    data_bytes: int
    execution_count: int
    failure_count: int
    node_sample_count: int
    scenario_count: int
    outcome_count: int


@dataclass(frozen=True)
class CampaignTotals:
    data_bytes: int
    cpu_bytes: int
    gpu_bytes: int
    execution_count: int
    node_sample_count: int
    scenario_count: int
    outcome_count: int


_DURABLE_FILENAMES = frozenset(
    {
        "executions.sqlite3",
        "observed-lifecycle.sqlite3",
        "collection-summary.json",
        "lifecycle-summary.json",
        "prefetch-plan.json",
        "provenance.json",
    }
)
_DURABLE_DIRECTORIES = frozenset({"raw-csv", "lifecycle-csv"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_checksum(archive: Path, checksum: Path) -> None:
    if not archive.is_file():
        raise RunValidationError(f"archive is missing: {archive}")
    if not checksum.is_file():
        raise RunValidationError(f"checksum file is missing: {checksum}")
    fields = checksum.read_text(encoding="utf-8").strip().split()
    if not fields or len(fields[0]) != 64:
        raise RunValidationError(f"checksum file is invalid: {checksum}")
    if _sha256(archive).lower() != fields[0].lower():
        raise RunValidationError(f"archive checksum mismatch: {archive}")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RunValidationError(f"required database is missing: {path.name}")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise RunValidationError(f"cannot open database {path.name}: {error}") from error


def _durable_bytes(run_dir: Path) -> int:
    total = 0
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir)
        if path.name in _DURABLE_FILENAMES or any(
            part in _DURABLE_DIRECTORIES for part in relative.parts[:-1]
        ):
            total += path.stat().st_size
    return total


def _load_provenance(run_dir: Path) -> tuple[str, str, str, str, str, int]:
    path = run_dir / "provenance.json"
    if not path.is_file():
        raise RunValidationError("required provenance.json is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        configuration = payload["configuration"]
        run_id = str(configuration["KHU_RUN_ID"])
        infra_id = str(configuration["KHU_TB_INFRA_ID"])
        spec_id = str(configuration["KHU_TB_SPEC_ID"])
        image_id = str(configuration["KHU_TB_IMAGE_ID"])
        device = str(configuration["KHU_DEVICE_SCOPE"])
        profile_set = str(configuration["KHU_PROFILE_SET"])
        seed = int(configuration["KHU_SEED"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunValidationError(f"provenance is incomplete: {error}") from error
    if payload.get("collection_status") != "complete":
        raise RunValidationError("provenance collection_status is not complete")
    if device not in {"cpu", "gpu"}:
        raise RunValidationError(f"campaign run device must be cpu or gpu: {device}")
    if not all((run_id, infra_id, spec_id, image_id, profile_set)):
        raise RunValidationError("provenance contains an empty required value")
    return run_id, infra_id, spec_id, image_id, device, profile_set, seed


def inspect_run(run_dir: Path, archive: Path, checksum: Path) -> RunEvidence:
    """Validate one downloaded run and return its countable evidence."""

    run_dir = Path(run_dir)
    archive = Path(archive)
    checksum = Path(checksum)
    if not run_dir.is_dir():
        raise RunValidationError(f"run directory is missing: {run_dir}")
    _verify_checksum(archive, checksum)
    run_id, infra_id, spec_id, image_id, device, profile_set, seed = (
        _load_provenance(run_dir)
    )

    try:
        with _connect_read_only(run_dir / "executions.sqlite3") as connection:
            stored = connection.execute(
                "SELECT status, execution_count, success_count FROM collection_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if stored is None:
                raise RunValidationError(f"collection run is missing: {run_id}")
            status, stored_count, stored_successes = stored
            execution_count, success_count = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(success), 0) FROM execution_measurements WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            failure_count = execution_count - success_count
            node_sample_count = connection.execute(
                """
                SELECT COUNT(*)
                  FROM node_samples AS samples
                  JOIN execution_measurements AS measurements
                    ON measurements.execution_id = samples.execution_id
                 WHERE measurements.run_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
    except sqlite3.Error as error:
        raise RunValidationError(f"invalid executions database: {error}") from error

    if status != "complete":
        raise RunValidationError(f"collection status is not complete: {status}")
    if execution_count <= 0:
        raise RunValidationError("collection contains no executions")
    if failure_count or success_count != execution_count:
        raise RunValidationError(f"run contains {failure_count} failed execution(s)")
    if stored_count != execution_count or stored_successes != success_count:
        raise RunValidationError("collection summary counts do not match stored executions")

    try:
        with _connect_read_only(run_dir / "observed-lifecycle.sqlite3") as connection:
            scenario_count = connection.execute(
                "SELECT COUNT(*) FROM deployment_scenarios"
            ).fetchone()[0]
            outcome_count = connection.execute(
                "SELECT COUNT(*) FROM action_outcomes"
            ).fetchone()[0]
    except sqlite3.Error as error:
        raise RunValidationError(f"invalid lifecycle database: {error}") from error
    if scenario_count <= 0 or outcome_count <= 0:
        raise RunValidationError("lifecycle evidence is empty")

    return RunEvidence(
        run_id=run_id,
        infra_id=infra_id,
        spec_id=spec_id,
        image_id=image_id,
        device=device,
        profile_set=profile_set,
        seed=seed,
        data_bytes=_durable_bytes(run_dir),
        execution_count=execution_count,
        failure_count=failure_count,
        node_sample_count=node_sample_count,
        scenario_count=scenario_count,
        outcome_count=outcome_count,
    )


def summarize_runs(items: Iterable[RunEvidence]) -> CampaignTotals:
    """Aggregate validated evidence while retaining device byte totals."""

    evidence = tuple(items)
    unexpected = sorted({item.device for item in evidence} - {"cpu", "gpu"})
    if unexpected:
        raise RunValidationError(f"unsupported campaign devices: {', '.join(unexpected)}")
    return CampaignTotals(
        data_bytes=sum(item.data_bytes for item in evidence),
        cpu_bytes=sum(item.data_bytes for item in evidence if item.device == "cpu"),
        gpu_bytes=sum(item.data_bytes for item in evidence if item.device == "gpu"),
        execution_count=sum(item.execution_count for item in evidence),
        node_sample_count=sum(item.node_sample_count for item in evidence),
        scenario_count=sum(item.scenario_count for item in evidence),
        outcome_count=sum(item.outcome_count for item in evidence),
    )
