from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from khu_policy_agent.campaign_accounting import RunValidationError, inspect_run, summarize_runs


def _write_run(tmp_path: Path, *, success_values: tuple[int, ...] = (1, 1)) -> tuple[Path, Path, Path]:
    run_id = "cpu-run-1"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    executions = run_dir / "executions.sqlite3"
    connection = sqlite3.connect(executions)
    connection.executescript(
        """
        CREATE TABLE collection_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            execution_count INTEGER NOT NULL,
            success_count INTEGER NOT NULL
        );
        CREATE TABLE execution_measurements (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            success INTEGER NOT NULL
        );
        CREATE TABLE node_samples (
            sample_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO collection_runs VALUES (?, ?, ?, ?)",
        (run_id, "complete", len(success_values), sum(success_values)),
    )
    for index, success in enumerate(success_values):
        execution_id = f"execution-{index}"
        connection.execute(
            "INSERT INTO execution_measurements VALUES (?, ?, ?)",
            (execution_id, run_id, success),
        )
        connection.execute(
            "INSERT INTO node_samples VALUES (?, ?)",
            (f"sample-{index}", execution_id),
        )
    connection.commit()
    connection.close()

    lifecycle = run_dir / "observed-lifecycle.sqlite3"
    connection = sqlite3.connect(lifecycle)
    connection.executescript(
        """
        CREATE TABLE deployment_scenarios (scenario_id TEXT PRIMARY KEY);
        CREATE TABLE action_outcomes (outcome_id TEXT PRIMARY KEY);
        INSERT INTO deployment_scenarios VALUES ('scenario-1');
        INSERT INTO action_outcomes VALUES ('outcome-1');
        INSERT INTO action_outcomes VALUES ('outcome-2');
        """
    )
    connection.commit()
    connection.close()

    provenance = {
        "collection_status": "complete",
        "configuration": {
            "KHU_RUN_ID": run_id,
            "KHU_TB_INFRA_ID": run_id,
            "KHU_TB_SPEC_ID": "aws+us-west-2+m6a.large",
            "KHU_TB_IMAGE_ID": "ami-0eb3161272dc9c6eb",
            "KHU_DEVICE_SCOPE": "cpu",
            "KHU_PROFILE_SET": "baseline",
            "KHU_SEED": "42",
        },
    }
    (run_dir / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (run_dir / "collection-summary.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "lifecycle-summary.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "prefetch-plan.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "raw-csv").mkdir()
    (run_dir / "raw-csv" / "measurements.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (run_dir / "lifecycle-csv").mkdir()
    (run_dir / "lifecycle-csv" / "outcomes.csv").write_text("a\n1\n", encoding="utf-8")

    # These SQLite transient files and unrelated logs must not inflate the target.
    (run_dir / "executions.sqlite3-wal").write_bytes(b"w" * 101)
    (run_dir / "executions.sqlite3-shm").write_bytes(b"s" * 103)
    (run_dir / "debug.log").write_bytes(b"d" * 107)

    archive = tmp_path / f"{run_id}.tar.gz"
    archive.write_bytes(b"archive payload")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = tmp_path / f"{run_id}.tar.gz.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return run_dir, archive, checksum


def _durable_size(run_dir: Path) -> int:
    exact = {
        "executions.sqlite3",
        "observed-lifecycle.sqlite3",
        "collection-summary.json",
        "lifecycle-summary.json",
        "prefetch-plan.json",
        "provenance.json",
    }
    return sum(
        path.stat().st_size
        for path in run_dir.rglob("*")
        if path.is_file()
        and (
            path.name in exact
            or "raw-csv" in path.parts
            or "lifecycle-csv" in path.parts
        )
    )


def test_inspect_run_counts_only_verified_durable_evidence(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path)

    evidence = inspect_run(run_dir, archive, checksum)

    assert evidence.run_id == "cpu-run-1"
    assert evidence.infra_id == "cpu-run-1"
    assert evidence.data_bytes == _durable_size(run_dir)
    assert evidence.execution_count == 2
    assert evidence.failure_count == 0
    assert evidence.node_sample_count == 2
    assert evidence.scenario_count == 1
    assert evidence.outcome_count == 2
    assert evidence.device == "cpu"
    assert evidence.spec_id == "aws+us-west-2+m6a.large"
    assert evidence.image_id == "ami-0eb3161272dc9c6eb"


def test_inspect_run_rejects_missing_infra_provenance(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path)
    path = run_dir / "provenance.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["configuration"]["KHU_TB_INFRA_ID"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunValidationError, match="provenance is incomplete"):
        inspect_run(run_dir, archive, checksum)


def test_inspect_run_rejects_failed_execution(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path, success_values=(1, 0))

    with pytest.raises(RunValidationError, match="failed execution"):
        inspect_run(run_dir, archive, checksum)


def test_inspect_run_rejects_checksum_mismatch(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path)
    checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(RunValidationError, match="checksum"):
        inspect_run(run_dir, archive, checksum)


def test_inspect_run_rejects_missing_database(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path)
    (run_dir / "observed-lifecycle.sqlite3").unlink()

    with pytest.raises(RunValidationError, match="observed-lifecycle.sqlite3"):
        inspect_run(run_dir, archive, checksum)


def test_summarize_runs_separates_cpu_and_gpu_bytes(tmp_path: Path) -> None:
    run_dir, archive, checksum = _write_run(tmp_path)
    cpu = inspect_run(run_dir, archive, checksum)
    gpu = type(cpu)(**{**cpu.__dict__, "run_id": "gpu-run-1", "device": "gpu"})

    totals = summarize_runs((cpu, gpu))

    assert totals.data_bytes == cpu.data_bytes * 2
    assert totals.cpu_bytes == cpu.data_bytes
    assert totals.gpu_bytes == gpu.data_bytes
    assert totals.execution_count == 4
