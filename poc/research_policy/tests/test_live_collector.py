from __future__ import annotations

import sqlite3

from khu_policy_agent.live_collector import (
    DEFAULT_PROFILES,
    CPU_SCALING_PROFILES,
    DIVERSE_PROFILES,
    LiveCollectionStore,
    _plans_for,
    _summarize_samples,
)


def test_live_store_persists_actual_measurement_shape_and_exports_csv(tmp_path) -> None:
    database = tmp_path / "executions.sqlite3"
    profile = DEFAULT_PROFILES[0]
    with LiveCollectionStore(database) as store:
        store.begin_run("run-1", {"source": "actual_huggingface_inference"})
        store.add_execution(
            execution_id="execution-1",
            run_id="run-1",
            ordinal=0,
            profile=profile,
            device="cuda",
            started_at="2026-01-01T00:00:00Z",
            result={"success": True, "finished_at": "2026-01-01T00:00:01Z", "wall_seconds": 1.0},
            error=None,
            node_summary={"sample_count": 2},
        )
        store.add_samples(
            "execution-1",
            [
                {
                    "observed_at": "2026-01-01T00:00:00Z",
                    "phase": "before",
                    "cpu_percent": None,
                    "memory_total_bytes": 1,
                    "memory_available_bytes": 1,
                    "load_average_1m": 0,
                    "gpus": [],
                }
            ],
        )
        store.finish_run("run-1", "complete")
        summary = store.summary("run-1")
        exports = store.export_csv(tmp_path / "csv")
    assert summary == {"executions": 1, "successes": 1, "samples": 1}
    assert all((tmp_path / "csv" / name).is_file() for name in ("collection_runs.csv", "execution_measurements.csv", "node_samples.csv"))
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM execution_measurements").fetchone()[0] == 1
    connection.close()
    assert set(exports) == {"runs", "executions", "samples"}


def test_summary_uses_running_samples() -> None:
    summary = _summarize_samples(
        [
            {"phase": "before", "cpu_percent": 1, "memory_available_bytes": 30, "gpus": []},
            {
                "phase": "running",
                "cpu_percent": 50,
                "memory_available_bytes": 20,
                "gpus": [{"utilization_percent": 90, "memory_used_mib": 1000}],
            },
        ]
    )
    assert summary["running_sample_count"] == 1
    assert summary["cpu_percent_mean"] == 50.0
    assert summary["gpu_utilization_percent_max"] == 90.0


def test_diverse_matrix_varies_load_shape_and_uses_two_physical_gpu_replicas() -> None:
    plans, skipped = _plans_for(DIVERSE_PROFILES, ("cpu", "cuda"), (0, 1))
    assert not skipped
    assert any(plan.device == "cpu" and plan.profile.max_length == 384 for plan in plans)
    assert any(plan.profile.request_concurrency == 4 for plan in plans)
    assert any(plan.device == "cuda" and plan.replica_count == 2 for plan in plans)
    assert {plan.profile.load_condition for plan in plans} >= {
        "nominal_interactive", "moderate_closed_loop", "closed_loop_burst", "closed_loop_sustained"
    }


def test_cpu_core_matrix_expands_cpu_candidates_and_records_the_enforced_limit() -> None:
    plans, skipped = _plans_for(
        DIVERSE_PROFILES[:1],
        ("cpu", "cuda"),
        (0, 1),
        available_cpu_indices=tuple(range(16)),
        cpu_core_counts=(2, 4, 8, 16),
    )
    assert not skipped
    cpu_plans = [plan for plan in plans if plan.device == "cpu"]
    assert [plan.cpu_core_limit for plan in cpu_plans] == [2, 4, 8, 16]
    assert [plan.profile.cpu_threads for plan in cpu_plans] == [2, 4, 8, 16]
    gpu_plans = [plan for plan in plans if plan.device == "cuda"]
    assert len(gpu_plans) == 1
    assert gpu_plans[0].cpu_core_limit == 2


def test_cpu_scaling_profiles_request_four_gpu_replicas_but_remain_host_bounded() -> None:
    plans, skipped = _plans_for(
        CPU_SCALING_PROFILES,
        ("cpu", "cuda"),
        (0, 1),
        available_cpu_indices=tuple(range(16)),
        cpu_core_counts=(2, 4, 8, 16),
        apply_cpu_core_counts_to_gpu=True,
    )
    assert {plan.cpu_core_limit for plan in plans} == {2, 4, 8, 16}
    assert {plan.replica_count for plan in plans if plan.device == "cuda"} == {1, 2}
    assert all(item["requested_gpu_replicas"] == 4 for item in skipped)
