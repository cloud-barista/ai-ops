from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from khu_policy_agent.learning import train_policy
from khu_policy_agent.live_collector import DEFAULT_PROFILES, LiveCollectionStore
from khu_policy_agent.live_dataset import build_live_placement_dataset


def _add_measurement(store: LiveCollectionStore, *, run_id: str, profile, ordinal: int, device: str, replica_count: int = 1) -> None:
    execution_id = f"{run_id}-{profile.name}-{device}-{replica_count}x"
    is_gpu = device == "cuda"
    store.add_execution(
        execution_id=execution_id,
        run_id=run_id,
        ordinal=ordinal,
        profile=profile,
        device=device,
        started_at="2026-01-01T00:00:00Z",
        result={
            "success": True,
            "finished_at": "2026-01-01T00:00:01Z",
            "batch_latency_p95_ms": 10.0 if is_gpu else 300.0,
            "inference_examples_per_second": 600.0 if is_gpu else 20.0,
            "max_rss_kib": 512 * 1024,
            "model_parameter_bytes": 128 * 1024**2,
            "gpu_index": 0,
            "replica_gpu_indices": list(range(replica_count)) if is_gpu else [],
        },
        error=None,
        node_summary={"cpu_percent_mean": 45.0, "gpu_utilization_percent_mean": 70.0},
        replica_count=replica_count,
    )
    store.add_samples(
        execution_id,
        [
            {
                "observed_at": "2026-01-01T00:00:00Z",
                "phase": "before",
                "cpu_percent": 25.0,
                "memory_total_bytes": 64 * 1024**3,
                "memory_available_bytes": 48 * 1024**3,
                "load_average_1m": 1.0,
                "gpus": [
                    {
                        "index": 0,
                        "utilization_percent": 70.0,
                        "memory_used_mib": 1024.0,
                        "memory_total_mib": 24 * 1024.0,
                    },
                    {
                        "index": 1,
                        "utilization_percent": 65.0,
                        "memory_used_mib": 1024.0,
                        "memory_total_mib": 24 * 1024.0,
                    },
                ],
            }
        ],
    )


def test_live_measurements_become_deploy_only_grouped_placement_training_rows(tmp_path: Path) -> None:
    raw = tmp_path / "live.sqlite3"
    # Ten workload configurations are necessary for the policy learner's
    # source-profile group holdout. They are raw measurement groups, not
    # synthetic outcome labels.
    profiles = DEFAULT_PROFILES[:10]
    with LiveCollectionStore(raw) as store:
        store.begin_run("round-1", {"source": "actual_huggingface_inference"})
        for ordinal, profile in enumerate(profiles):
            _add_measurement(store, run_id="round-1", profile=profile, ordinal=ordinal * 2, device="cpu")
            _add_measurement(store, run_id="round-1", profile=profile, ordinal=ordinal * 2 + 1, device="cuda")
        _add_measurement(store, run_id="round-1", profile=profiles[0], ordinal=99, device="cuda", replica_count=2)
        store.finish_run("round-1", "complete")
    dataset = tmp_path / "placement.sqlite3"
    result = build_live_placement_dataset(source_db=raw, destination_db=dataset, csv_dir=tmp_path / "csv", run_id="etl")
    assert result["summary"] == {"scenarios": 10, "outcomes": 21, "oracle_rows": 10, "feasible_rows": 21}
    connection = sqlite3.connect(dataset)
    actions = {item[0] for item in connection.execute("SELECT DISTINCT action FROM action_outcomes")}
    sources = connection.execute("SELECT COUNT(DISTINCT source_scenario_id) FROM action_outcomes").fetchone()[0]
    outcomes = [json.loads(item[0]) for item in connection.execute("SELECT outcome_json FROM action_outcomes")]
    replica_row = connection.execute(
        "SELECT desired_replicas, feature_json FROM action_outcomes "
        "WHERE candidate_id LIKE 'observed-%-cuda-2x-cpu2'"
    ).fetchone()
    connection.close()
    assert actions == {"DEPLOY"}
    assert sources == 10
    assert all(item["outcome_source"] == "actual_huggingface_inference" for item in outcomes)
    assert replica_row is not None
    assert replica_row[0] == 2
    assert json.loads(replica_row[1])["candidate_gpu_count"] == 2.0
    trained = train_policy(dataset_db=dataset, artifact_dir=tmp_path / "artifact", seed=42, trees=8)
    assert trained["sample_count"] == 21
    assert trained["test_metrics"]["groups"] >= 1
