from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from khu_policy_agent.generator import generate_dataset
from khu_policy_agent.learning import load_policy, load_rows, train_policy
from khu_policy_agent.service import evaluate_request


def _source_db(path: Path, count: int = 12) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE scenarios (scenario_id TEXT PRIMARY KEY, ordinal INTEGER, input_json TEXT, selected_mode TEXT);
        CREATE TABLE scenario_nodes (
          scenario_id TEXT, client_id TEXT, cpu_cores INTEGER, cpu_utilization_ratio REAL,
          memory_total_bytes INTEGER, memory_allocatable_bytes INTEGER,
          storage_total_bytes INTEGER, storage_available_bytes INTEGER,
          gpu_count INTEGER, gpu_memory_total_bytes INTEGER, gpu_memory_allocatable_bytes INTEGER,
          gpu_utilization_ratio REAL, availability_probability REAL, status TEXT, blocked INTEGER
        );
        """
    )
    for ordinal in range(count):
        payload = {
            "request": {"task_type": "distributed_inference", "inference_config": {"target_throughput_rps": 30 + ordinal}},
            "model_capability": {"full_model_memory_estimate": 2 * 1024**3},
        }
        scenario_id = f"source-{ordinal:03d}"
        connection.execute(
            "INSERT INTO scenarios VALUES (?, ?, ?, ?)",
            (scenario_id, ordinal, json.dumps(payload), "FL"),
        )
        for node in range(4):
            connection.execute(
                "INSERT INTO scenario_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scenario_id, f"node-{node}", 16 + node * 4, 0.25,
                    32 * 1024**3, 24 * 1024**3, 512 * 1024**3, 400 * 1024**3,
                    1 if node else 0, 16 * 1024**3, 12 * 1024**3, 0.2,
                    0.98, "online", 0,
                ),
            )
    # This source has no usable observed node. Generation must retain
    # infeasible outcomes for evaluation but select only the provisionable
    # Mock/Handoff candidate as the oracle.
    connection.execute("UPDATE scenario_nodes SET status = 'offline' WHERE scenario_id = ?", ("source-000",))
    connection.commit()
    connection.close()
    return path


def test_generation_uses_static_inputs_and_creates_balanced_lifecycle_rows(tmp_path: Path) -> None:
    source = _source_db(tmp_path / "source.sqlite3")
    result = generate_dataset(
        source_db=source,
        destination_db=tmp_path / "dataset.sqlite3",
        csv_dir=tmp_path / "csv",
        count=12,
        seed=42,
        run_id="test",
    )
    assert result["summary"]["scenarios"] == 60
    assert result["summary"]["oracle_rows"] == 60
    connection = sqlite3.connect(tmp_path / "dataset.sqlite3")
    states = dict(connection.execute("SELECT lifecycle_state, COUNT(*) FROM deployment_scenarios GROUP BY lifecycle_state"))
    actions = dict(connection.execute("SELECT action, COUNT(*) FROM action_outcomes GROUP BY action"))
    invalid_rewards = connection.execute("SELECT COUNT(*) FROM action_outcomes WHERE feasible = 0 AND reward != 0").fetchone()[0]
    invalid_oracles = connection.execute("SELECT COUNT(*) FROM action_outcomes WHERE feasible = 0 AND is_oracle = 1").fetchone()[0]
    feature_rows = [json.loads(value) for (value,) in connection.execute("SELECT feature_json FROM action_outcomes")]
    connection.close()
    assert set(states) == {"NEW", "SPEC_DRIFT", "SLO_PRESSURE", "UNDERUTILIZED", "HEALTHY"}
    assert all(value == 12 for value in states.values())
    assert {"DEPLOY", "UPDATE", "SCALE_OUT", "SCALE_IN", "KEEP"} <= set(actions)
    assert invalid_rewards == 0
    assert invalid_oracles == 0
    assert any(float(item["candidate_gpu_count"]) == 0 for item in feature_rows)
    assert any(float(item["candidate_gpu_count"]) > 0 for item in feature_rows)
    assert set(result["csv"]) == {"runs", "scenarios", "outcomes"}
    assert all(Path(path).read_text(encoding="utf-8").splitlines() for path in result["csv"].values())


def test_generation_accumulates_independent_seed_runs(tmp_path: Path) -> None:
    source = _source_db(tmp_path / "source.sqlite3")
    dataset = tmp_path / "dataset.sqlite3"
    generate_dataset(source_db=source, destination_db=dataset, count=12, seed=42, run_id="seed-42")
    generate_dataset(source_db=source, destination_db=dataset, count=12, seed=43, run_id="seed-43")
    connection = sqlite3.connect(dataset)
    run_count = connection.execute("SELECT COUNT(*) FROM dataset_runs").fetchone()[0]
    scenario_count = connection.execute("SELECT COUNT(*) FROM deployment_scenarios").fetchone()[0]
    outcome_count = connection.execute("SELECT COUNT(*) FROM action_outcomes").fetchone()[0]
    duplicate_scenarios = connection.execute(
        "SELECT COUNT(*) FROM (SELECT scenario_id FROM deployment_scenarios GROUP BY scenario_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    connection.close()
    assert run_count == 2
    assert scenario_count == 120
    assert outcome_count > 0
    assert duplicate_scenarios == 0


def test_training_keeps_source_scenarios_grouped_and_service_abstains_on_ood(tmp_path: Path) -> None:
    source = _source_db(tmp_path / "source.sqlite3", count=20)
    dataset = tmp_path / "dataset.sqlite3"
    generate_dataset(source_db=source, destination_db=dataset, count=20, seed=42, run_id="test")
    trained = train_policy(dataset_db=dataset, artifact_dir=tmp_path / "artifact", seed=42, trees=12)
    assert trained["sample_count"] > 0
    assert trained["test_metrics"]["groups"] > 0
    model = load_policy(tmp_path / "artifact")
    rows = load_rows(dataset)
    sample = rows[0]
    response = evaluate_request(
        model,
        {
            "application_profile": {
                "workload": {"task_type": "generic", "expected_rps": sample.features["expected_rps"]},
                "requirements": {
                    "compute": {"cpu_cores_min": 1, "memory_mib_min": 256, "storage_gib_min": 1},
                    "accelerator": {"required": False},
                    "deployment": {"replicas_min": 1, "replicas_max": 3},
                    "slo": {"latency_p95_ms_max": 250, "throughput_rps_min": 1},
                },
            },
            "resource_recommendation": {"candidates": [{"candidate_id": "ood", "cpu_cores": 99999, "memory_mib": 999999, "storage_gib": 99999, "availability": 1.0, "healthy": True}]},
            "phase": "planning",
        },
    )
    assert response["proposal"]["action"] == "ABSTAIN"
    assert response["proposal"]["ood_ratio"] > 0.20
    assert response["proposal"]["abstain_reason"] in {"out_of_distribution", "confidence_below_threshold"}


def test_service_exposes_each_policy_action_and_rejects_tampered_artifact(tmp_path: Path) -> None:
    source = _source_db(tmp_path / "source.sqlite3", count=20)
    dataset = tmp_path / "dataset.sqlite3"
    artifact = tmp_path / "artifact"
    generate_dataset(source_db=source, destination_db=dataset, count=20, seed=42, run_id="test")
    train_policy(dataset_db=dataset, artifact_dir=artifact, seed=42, trees=12)
    model = load_policy(artifact)
    base = {
        "application_profile": {
            "workload": {"task_type": "generic", "expected_rps": 12},
            "requirements": {
                "compute": {"cpu_cores_min": 2, "memory_mib_min": 512, "storage_gib_min": 20},
                "accelerator": {"required": False},
                "deployment": {"replicas_min": 1, "replicas_max": 3},
                "slo": {"latency_p95_ms_max": 250, "throughput_rps_min": 12},
            },
        },
        "resource_recommendation": {
            "candidates": [
                {
                    "candidate_id": "candidate-a", "node_count": 1, "cpu_cores": 16,
                    "cpu_available_cores": 12, "cpu_utilization_percent": 30,
                    "memory_mib": 16384, "memory_available_mib": 12000,
                    "storage_gib": 512, "storage_available_gib": 400,
                    "availability": 0.99, "cost_per_hour": 4,
                    "provider": "mock-csp-a", "region": "kr-central", "healthy": True,
                }
            ]
        },
    }
    cases = (
        ("planning", {}, {}, "DEPLOY"),
        ("planning", {"deployment_ref": "logical-a", "current_replicas": 1, "spec_drift": True}, {}, "UPDATE"),
        (
            "operation", {"current_replicas": 1},
            {"metrics": {"resource": {"cpu_average_percent": 90, "accelerator_average_percent": 0}, "inference": {"latency_p95_ms": 500, "throughput_rps": 2, "error_rate_percent": 10}}},
            "SCALE_OUT",
        ),
        (
            "operation", {"current_replicas": 3},
            {"metrics": {"resource": {"cpu_average_percent": 10, "accelerator_average_percent": 0}, "inference": {"latency_p95_ms": 100, "throughput_rps": 20, "error_rate_percent": 0}}},
            "SCALE_IN",
        ),
        (
            "operation", {"current_replicas": 1},
            {"metrics": {"resource": {"cpu_average_percent": 50, "accelerator_average_percent": 0}, "inference": {"latency_p95_ms": 100, "throughput_rps": 20, "error_rate_percent": 0}}},
            "KEEP",
        ),
    )
    for phase, deployment_context, feedback, expected in cases:
        response = evaluate_request(
            model,
            {**base, "phase": phase, "deployment_context": deployment_context, "optimization_feedback": feedback},
        )
        assert expected in {item["action"] for item in response["candidate_evaluations"]}

    (artifact / "khu-policy.joblib").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        load_policy(artifact)
