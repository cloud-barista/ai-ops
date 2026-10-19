"""Generate a non-FL/SL, platform-neutral KHU deployment benchmark."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .contracts import (
    LIFECYCLE_STATES,
    ApplicationView,
    CandidateView,
    application_view,
    candidate_feasible,
    candidate_view,
    feature_row,
)
from .store import KHUScenarioStore


MIB = 1024**2
GENERATOR_VERSION = "khu-deployment-scenario-generator/v1"
ACTION_ORDER = {"DEPLOY": 0, "UPDATE": 1, "SCALE_OUT": 2, "SCALE_IN": 3, "KEEP": 4}


def generate_dataset(
    *,
    source_db: str | Path,
    destination_db: str | Path,
    csv_dir: str | Path | None = None,
    count: int = 1000,
    seed: int = 42,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Expand static FCA *inputs* into KHU CPU/GPU lifecycle scenarios.

    ``selected_mode`` and all live FCA databases are intentionally never read.
    This generator does not execute an application, provision a VM, or invoke a
    remote service. The stored outcome is a deterministic research simulator.
    """
    source_path = Path(source_db).expanduser().resolve()
    if count <= 0:
        raise ValueError("count must be positive")
    rows = _read_source_rows(source_path, count)
    if not rows:
        raise ValueError("source SQLite contains no static FCA scenarios")
    selected_run = run_id or f"khu-{seed}-{uuid4().hex[:8]}"
    configuration = {
        "generator_version": GENERATOR_VERSION,
        "source_rows": len(rows),
        "lifecycle_states": list(LIFECYCLE_STATES),
        "candidates_per_scenario": 4,
        "source_label_policy": "input_json_and_scenario_nodes_only",
        "excluded_fields": ["selected_mode", "decision_json", "fca_hf_live_runs"],
    }
    with KHUScenarioStore(destination_db) as store:
        store.begin_run(selected_run, seed, str(source_path), configuration)
        for source in rows:
            payload = _json_object(source["input_json"])
            node_rows = _read_source_nodes(source_path, str(source["scenario_id"]))
            for state_index, state in enumerate(LIFECYCLE_STATES):
                scenario_rng = random.Random(_seed(seed, str(source["scenario_id"]), state))
                application = _application_from_source(payload, node_rows, scenario_rng)
                operation = _operation_for_state(application, state, scenario_rng)
                candidates = _candidates_from_source(node_rows, application, scenario_rng)
                # A scenario is uniquely identified by its deterministic
                # augmentation run, original input, and lifecycle state. This
                # permits independent seed runs to be accumulated in one
                # training SQLite database without mixing their lineage.
                scenario_id = f"khu-{selected_run}-{source['scenario_id']}-{state.lower()}"
                outcomes = _outcomes_for_scenario(
                    scenario_id=scenario_id,
                    source_scenario_id=str(source["scenario_id"]),
                    application=application,
                    operation=operation,
                    candidates=candidates,
                    rng=scenario_rng,
                )
                oracle = _oracle(outcomes)
                store.add_scenario(
                    {
                        "scenario_id": scenario_id,
                        "run_id": selected_run,
                        "source_scenario_id": str(source["scenario_id"]),
                        "source_ordinal": int(source["ordinal"]),
                        "lifecycle_state": state,
                        "application": application,
                        "operation": operation,
                        "oracle_action": oracle["action"],
                        "oracle_candidate_id": oracle["candidate_id"],
                        "oracle_reward": oracle["reward"],
                    }
                )
                for item in outcomes:
                    item["is_oracle"] = item["outcome_id"] == oracle["outcome_id"]
                store.add_outcomes(outcomes)
        store.finish_run(selected_run)
        summary = store.summary(selected_run)
        exports = store.export_csv(csv_dir) if csv_dir else {}
    return {"run_id": selected_run, "db": str(Path(destination_db).resolve()), "csv": exports, "summary": summary}


def _read_source_rows(path: Path, count: int) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT scenario_id, ordinal, input_json FROM scenarios ORDER BY ordinal, scenario_id LIMIT ?", (count,)
        ).fetchall()
    finally:
        connection.close()


def _read_source_nodes(path: Path, scenario_id: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT client_id, cpu_cores, cpu_utilization_ratio, memory_total_bytes,
                   memory_allocatable_bytes, storage_total_bytes, storage_available_bytes,
                   gpu_count, gpu_memory_total_bytes, gpu_memory_allocatable_bytes,
                   gpu_utilization_ratio, availability_probability, status, blocked
              FROM scenario_nodes WHERE scenario_id = ? ORDER BY client_id
            """,
            (scenario_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _application_from_source(
    payload: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]], rng: random.Random
) -> dict[str, Any]:
    request = _mapping(payload.get("request"))
    model = _mapping(payload.get("model_capability"))
    task_type = str(request.get("task_type") or "generic")
    model_mib = max(256.0, _number(model.get("full_model_memory_estimate")) / MIB)
    max_cpu = max((_number(item.get("cpu_cores")) for item in nodes), default=8.0)
    max_memory = max((_number(item.get("memory_allocatable_bytes")) / MIB for item in nodes), default=8192.0)
    max_gpu = max((_number(item.get("gpu_count")) for item in nodes), default=0.0)
    max_gpu_memory = max((_number(item.get("gpu_memory_allocatable_bytes")) / MIB for item in nodes), default=0.0)
    inference = _mapping(request.get("inference_config"))
    planning = _mapping(request.get("planning_config"))
    expected_rps = _number(inference.get("target_throughput_rps"), 0.0)
    if expected_rps <= 0:
        expected_rps = max(4.0, _number(planning.get("target_active"), 1.0) * rng.uniform(8.0, 24.0))
    gpu_required = max_gpu > 0 and ("inference" in task_type or rng.random() < 0.55)
    accelerator_count = 1 if gpu_required else 0
    accelerator_memory = min(max_gpu_memory, max(1024.0, model_mib * rng.uniform(0.35, 0.75))) if gpu_required else 0.0
    replicas_min = 1
    replicas_max = rng.randint(3, 6)
    app = {
        "profile_id": f"profile-{hashlib.sha256(str(payload).encode()).hexdigest()[:12]}",
        "app_id": "synthetic-khu-ai-app",
        "app_version": "1",
        "workload": {
            "task_type": "gpu_inference" if gpu_required else ("inference" if "inference" in task_type else "batch_ai"),
            "expected_rps": round(expected_rps, 4),
        },
        "requirements": {
            "compute": {
                "cpu_cores_min": max(1, min(int(max_cpu), max(1, round(max_cpu * rng.uniform(0.08, 0.30))))),
                "memory_mib_min": max(256, min(int(max_memory), int(model_mib * rng.uniform(0.6, 1.25)))),
                "storage_gib_min": rng.choice((20, 40, 80, 160)),
            },
            "accelerator": {
                "required": gpu_required,
                "type": "nvidia" if gpu_required else "",
                "count_min": accelerator_count,
                "memory_mib_min_per_device": round(accelerator_memory, 3),
            },
            "deployment": {"replicas_min": replicas_min, "replicas_max": replicas_max, "isolation": "shared"},
            "slo": {
                "latency_p95_ms_max": round(rng.uniform(80.0, 400.0), 3),
                "throughput_rps_min": round(expected_rps, 3),
            },
            "cost": {"currency": "USD", "cost_per_hour_max": round(rng.uniform(2.0, 15.0), 3)},
        },
    }
    return app


def _operation_for_state(application: Mapping[str, Any], state: str, rng: random.Random) -> dict[str, Any]:
    app = application_view(application)
    target_latency = app.latency_slo_ms
    target_throughput = app.throughput_slo_rps
    if state == "NEW":
        return {"lifecycle_state": state, "current_replicas": 0, "spec_drift": False, "cooldown_active": False}
    if state == "SPEC_DRIFT":
        return _operation(state, app.replicas_min, True, 35, 240, 35, target_latency * 0.8, target_throughput * 1.2, 0.4)
    if state == "SLO_PRESSURE":
        current = max(app.replicas_min, app.replicas_max - 1)
        return _operation(state, current, False, 88, 640, 90, target_latency * rng.uniform(1.3, 2.2), target_throughput * rng.uniform(0.35, 0.75), rng.uniform(4, 12))
    if state == "UNDERUTILIZED":
        return _operation(state, app.replicas_max, False, rng.uniform(4, 22), 180, rng.uniform(0, 16), target_latency * 0.55, target_throughput * 1.4, 0.1)
    return _operation(state, app.replicas_min, False, rng.uniform(35, 65), 400, rng.uniform(15, 55), target_latency * 0.75, target_throughput * 1.15, 0.3)


def _operation(state: str, replicas: float, drift: bool, cpu: float, memory: float, gpu: float, latency: float, throughput: float, error: float) -> dict[str, Any]:
    return {
        "lifecycle_state": state,
        "current_replicas": int(replicas),
        "spec_drift": drift,
        "cooldown_active": False,
        "optimization_feedback": {
            "metrics": {
                "resource": {"cpu_average_percent": round(cpu, 3), "memory_peak_mib": round(memory, 3), "accelerator_average_percent": round(gpu, 3)},
                "inference": {"latency_p95_ms": round(latency, 3), "throughput_rps": round(throughput, 3), "error_rate_percent": round(error, 3)},
            }
        },
    }


def _candidates_from_source(nodes: Sequence[Mapping[str, Any]], application: Mapping[str, Any], rng: random.Random) -> list[CandidateView]:
    app = application_view(application)
    base_nodes = list(nodes) or [{}]
    candidates: list[CandidateView] = []
    variants = ((0.72, 0.70, 0.72, 0.75), (1.0, 1.0, 1.0, 1.0), (1.35, 1.25, 1.30, 1.35), (1.05, 1.55, 0.92, 1.12))
    for index, factors in enumerate(variants):
        source = base_nodes[index % len(base_nodes)]
        cpu_factor, memory_factor, gpu_factor, cost_factor = factors
        base_cpu = max(2.0, _number(source.get("cpu_cores"), 8.0))
        base_memory = max(1024.0, _number(source.get("memory_allocatable_bytes"), 8 * 1024 * MIB) / MIB)
        base_storage = max(20.0, _number(source.get("storage_available_bytes"), 200 * 1024 * MIB) / (1024 * MIB))
        base_gpu = max(0.0, _number(source.get("gpu_count")))
        base_gpu_memory = max(0.0, _number(source.get("gpu_memory_allocatable_bytes")) / MIB)
        if app.accelerator_required and base_gpu == 0 and index >= 1:
            base_gpu, base_gpu_memory = 1.0, max(app.accelerator_memory_mib * 1.25, 4096.0)
        node_count = 1 if index < 2 else 2
        # The final candidate is a provisionable Mock/Handoff template. Static
        # source inventories can contain only blocked/offline nodes; retaining
        # a capacity-compatible candidate keeps each synthetic state evaluable
        # without treating an infeasible row as an oracle.
        guaranteed = index == len(variants) - 1
        candidate_cpu = round(base_cpu * cpu_factor)
        candidate_memory = round(base_memory * memory_factor, 3)
        candidate_storage = round(base_storage * memory_factor, 3)
        candidate_gpu = round(base_gpu * gpu_factor)
        candidate_gpu_memory = round(base_gpu_memory * gpu_factor, 3)
        if guaranteed:
            candidate_cpu = max(candidate_cpu, int(app.cpu_cores))
            candidate_memory = max(candidate_memory, app.memory_mib)
            candidate_storage = max(candidate_storage, app.storage_gib)
            candidate_gpu = max(candidate_gpu, int(app.accelerator_count))
            candidate_gpu_memory = max(candidate_gpu_memory, app.accelerator_memory_mib)
        payload = {
            "candidate_id": f"candidate-{index + 1}",
            "node_count": node_count,
            "cpu_cores": candidate_cpu,
            "cpu_available_cores": round(base_cpu * cpu_factor * rng.uniform(0.45, 0.95), 3),
            "cpu_utilization_percent": round(rng.uniform(5, 65), 3),
            "memory_mib": candidate_memory,
            "memory_available_mib": round(base_memory * memory_factor * rng.uniform(0.45, 0.90), 3),
            "storage_gib": candidate_storage,
            "storage_available_gib": round(base_storage * memory_factor * rng.uniform(0.5, 0.9), 3),
            "gpu_count": candidate_gpu,
            "gpu_memory_mib": candidate_gpu_memory,
            "gpu_available_mib": round(base_gpu_memory * gpu_factor * rng.uniform(0.4, 0.92), 3),
            "gpu_utilization_percent": round(rng.uniform(0, 75), 3) if base_gpu else 0.0,
            "availability": round(rng.uniform(0.74, 0.995), 4),
            "cost_per_hour": round((0.12 * base_cpu * cpu_factor + 0.00008 * base_memory * memory_factor + 1.8 * base_gpu * gpu_factor) * node_count * cost_factor, 4),
            "healthy": guaranteed or bool(source.get("status", "online") == "online" and not source.get("blocked", False)),
            "provider": ("mock-csp-a", "mock-csp-b")[index % 2],
            "region": ("kr-central", "kr-south")[index // 2],
        }
        candidates.append(candidate_view(payload, index))
    return candidates


def _outcomes_for_scenario(
    *,
    scenario_id: str,
    source_scenario_id: str,
    application: Mapping[str, Any],
    operation: Mapping[str, Any],
    candidates: Sequence[CandidateView],
    rng: random.Random,
) -> list[dict[str, Any]]:
    app = application_view(application)
    actions = _actions_for_state(str(operation["lifecycle_state"]))
    outcomes: list[dict[str, Any]] = []
    for candidate in candidates:
        for action in actions:
            desired_replicas = _desired_replicas(app, operation, action)
            features = feature_row(app, candidate, _operation_features(operation), action, desired_replicas)
            metrics = _simulate(app, candidate, operation, action, desired_replicas, rng)
            outcome_id = f"{scenario_id}:{candidate.candidate_id}:{action}"
            outcomes.append(
                {
                    "outcome_id": outcome_id,
                    "scenario_id": scenario_id,
                    "source_scenario_id": source_scenario_id,
                    "candidate_id": candidate.candidate_id,
                    "action": action,
                    "desired_replicas": desired_replicas,
                    "features": features,
                    "is_oracle": False,
                    **metrics,
                }
            )
    return outcomes


def _actions_for_state(state: str) -> tuple[str, ...]:
    return {
        "NEW": ("DEPLOY",),
        "SPEC_DRIFT": ("UPDATE", "KEEP"),
        "SLO_PRESSURE": ("SCALE_OUT", "UPDATE", "KEEP"),
        "UNDERUTILIZED": ("SCALE_IN", "KEEP"),
        "HEALTHY": ("KEEP", "UPDATE"),
    }[state]


def _desired_replicas(app: ApplicationView, operation: Mapping[str, Any], action: str) -> int:
    current = int(operation.get("current_replicas", 0))
    if action in {"DEPLOY", "UPDATE"}:
        return max(1, current or int(app.replicas_min))
    if action == "SCALE_OUT":
        return min(int(app.replicas_max), max(1, current) + 1)
    if action == "SCALE_IN":
        return max(int(app.replicas_min), current - 1)
    return max(1, current)


def _simulate(
    app: ApplicationView,
    candidate: CandidateView,
    operation: Mapping[str, Any],
    action: str,
    replicas: int,
    rng: random.Random,
) -> dict[str, Any]:
    feasible = candidate_feasible(app, candidate)
    state = str(operation["lifecycle_state"])
    feedback = _operation_features(operation)
    total_cpu = max(0.1, candidate.cpu_cores * candidate.node_count)
    gpu_factor = 1.0 + (candidate.gpu_count * 2.5 if app.accelerator_required else candidate.gpu_count * 0.25)
    capacity = max(0.1, (total_cpu * 3.2 + candidate.gpu_count * 75.0) * max(1, replicas) * candidate.availability)
    demand = max(app.throughput_slo_rps, app.expected_rps, 1.0)
    action_factor = 1.0
    if action == "SCALE_OUT":
        action_factor = 1.22
    elif action == "SCALE_IN":
        action_factor = 0.82
    elif action == "UPDATE":
        action_factor = 1.20 if state == "SPEC_DRIFT" else 0.95
    elif action == "KEEP" and state == "SLO_PRESSURE":
        action_factor = 0.68
    elif action == "KEEP" and state == "UNDERUTILIZED":
        action_factor = 0.96
    noise = rng.uniform(0.94, 1.06)
    throughput = capacity * action_factor * noise
    pressure = demand / max(throughput, 0.1)
    latency = app.latency_slo_ms * max(0.28, pressure) * (1.0 + max(0.0, candidate.cpu_utilization - 50.0) / 250.0)
    if app.accelerator_required and candidate.gpu_count <= 0:
        latency *= 3.5
    latency /= max(1.0, gpu_factor * 0.65)
    error_rate = max(0.0, (pressure - 0.92) * 22.0 + (1.0 - candidate.availability) * 14.0)
    if action == "SCALE_IN" and state == "SLO_PRESSURE":
        error_rate += 30.0
    cpu_utilization = min(100.0, max(1.0, demand / max(total_cpu * max(1, replicas) * 3.2, 0.1) * 100.0))
    memory_utilization = min(100.0, app.memory_mib * max(1, replicas) / max(candidate.memory_mib * candidate.node_count, 1.0) * 100.0)
    gpu_utilization = min(100.0, demand / max(candidate.gpu_count * max(1, replicas) * 90.0, 1.0) * 100.0) if candidate.gpu_count else 0.0
    success = feasible and error_rate < 35.0
    slo_satisfied = success and latency <= app.latency_slo_ms and throughput >= app.throughput_slo_rps and error_rate < 5.0
    cost = candidate.cost_per_hour * (1.0 + 0.06 * max(0, replicas - 1))
    if not success:
        reward = 0.0
    else:
        slo_headroom = (min(1.0, app.latency_slo_ms / max(latency, 0.1)) + min(1.0, throughput / app.throughput_slo_rps) + min(1.0, max(0.0, 5.0 - error_rate) / 5.0)) / 3.0
        performance = (min(1.0, throughput / (app.throughput_slo_rps * 1.25)) + min(1.0, app.latency_slo_ms / max(latency, 0.1))) / 2.0
        resource_efficiency = max(0.0, 1.0 - abs(((cpu_utilization + memory_utilization + gpu_utilization) / (3 if candidate.gpu_count else 2)) - 65.0) / 65.0)
        # A stable demand-derived budget proxy keeps generated rewards independent
        # of policy labels while still penalizing oversized GPU/CPU candidates.
        cost_efficiency = 1.0 / (1.0 + cost / max(2.0, app.cpu_cores * 0.8 + app.accelerator_count * 2.0))
        reward = 0.55 * slo_headroom + 0.25 * performance + 0.10 * resource_efficiency + 0.10 * cost_efficiency
        if not slo_satisfied:
            reward *= 0.35
    outcome = {
        "feasible": feasible,
        "success": success,
        "slo_satisfied": slo_satisfied,
        "reward": round(max(0.0, min(1.0, reward)), 8),
        "cost_per_hour": round(cost, 6),
        "latency_p95_ms": round(latency, 6),
        "throughput_rps": round(throughput, 6),
        "error_rate_percent": round(error_rate, 6),
        "cpu_utilization_percent": round(cpu_utilization, 6),
        "memory_utilization_percent": round(memory_utilization, 6),
        "gpu_utilization_percent": round(gpu_utilization, 6),
    }
    outcome["outcome"] = {"simulator_version": GENERATOR_VERSION, **outcome}
    return outcome


def _oracle(outcomes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    eligible = [row for row in outcomes if bool(row["feasible"])]
    if not eligible:
        raise ValueError("scenario must include at least one hard-feasible candidate")
    return max(
        eligible,
        key=lambda row: (float(row["reward"]), bool(row["slo_satisfied"]), bool(row["feasible"]), -ACTION_ORDER[str(row["action"])], str(row["candidate_id"])),
    )


def _operation_features(operation: Mapping[str, Any]) -> dict[str, Any]:
    feedback = _mapping(operation.get("optimization_feedback"))
    metrics = _mapping(feedback.get("metrics"))
    return {
        "lifecycle_state": operation.get("lifecycle_state", "NEW"),
        "current_replicas": operation.get("current_replicas", 0),
        "spec_drift": operation.get("spec_drift", False),
        "cooldown_active": operation.get("cooldown_active", False),
        "optimization_feedback": {"metrics": metrics},
    }


def _seed(seed: int, source: str, state: str) -> int:
    digest = hashlib.sha256(f"{GENERATOR_VERSION}|{seed}|{source}|{state}".encode()).hexdigest()
    return int(digest[:15], 16)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(default)


def _json_object(value: str) -> Mapping[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise ValueError("source input_json must contain an object")
    return decoded
