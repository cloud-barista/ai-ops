"""Convert direct Hugging Face measurements into observed placement outcomes.

The converter is intentionally narrower than the synthetic lifecycle dataset:
it uses only successful, real *new deployment* measurements and produces
``DEPLOY`` candidate outcomes.  CPU/GPU choice is an observed placement
decision; UPDATE and scaling require paired before/after operational evidence
and are therefore outside this dataset's claim.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .contracts import application_view, candidate_feasible, candidate_view, feature_row, operational_view
from .store import KHUScenarioStore


MIB = 1024**2
LIVE_ETL_VERSION = "khu-live-placement-etl/v1"
LIFECYCLE_PAIR_ETL_VERSION = "khu-observed-lifecycle-pair-etl/v1"
CPU_COST_PROXY_PER_HOUR = 0.45
GPU_COST_PROXY_PER_HOUR = 4.80


@dataclass(frozen=True)
class Measurement:
    execution_id: str
    run_id: str
    host: str
    profile_name: str
    model_id: str
    model_revision: str
    device: str
    batch_size: int
    max_length: int
    repeats: int
    workload_id: str
    request_concurrency: int
    cpu_threads: int
    cpu_core_limit: int
    replica_count: int
    load_condition: str
    target_throughput_rps: float
    latency_slo_ms: float
    gpu_indices: tuple[int, ...]
    result: Mapping[str, Any]
    node_summary: Mapping[str, Any]
    node_snapshot: Mapping[str, Any]
    gpu_samples: tuple[tuple[Mapping[str, Any], ...], ...]


def build_live_placement_dataset(
    *,
    source_db: str | Path,
    destination_db: str | Path,
    csv_dir: str | Path | None = None,
    run_id: str | None = None,
    collection_run_id: str | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Materialize directly-observed CPU/GPU placement outcomes.

    A collection round is usable when it contains at least one successful
    measurement.  Normal collection rounds contain paired CPU and CUDA rows
    for each encoder profile; causal-LM profiles can legitimately have only a
    CUDA candidate.  No simulation or FL/SL label is introduced here.
    """
    raw_path = Path(source_db).expanduser().resolve()
    measurements = _read_measurements(raw_path, collection_run_id=collection_run_id)
    if not measurements:
        raise ValueError("live measurement database has no successful executions")
    grouped = _group_measurements(measurements)
    if not grouped:
        raise ValueError("live measurement database has no usable collection groups")
    source_ids = [_source_profile_id(group) for group in grouped.values()]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError(
            "multiple collection rounds contain the same profile; run this ETL once per "
            "--collection-run-id so profile-level train/test grouping remains valid"
        )
    selected_run = run_id or f"khu-live-placement-{uuid4().hex[:10]}"
    configuration = {
        "etl_version": LIVE_ETL_VERSION,
        "source_kind": "actual_huggingface_inference",
        "source_db": str(raw_path),
        "reward_version": "observed-placement-reward/v1",
        "observed_actions": ["DEPLOY"],
        "unsupported_actions": ["UPDATE", "SCALE_OUT", "SCALE_IN", "KEEP"],
        "cpu_cost_proxy_per_hour": CPU_COST_PROXY_PER_HOUR,
        "gpu_cost_proxy_per_hour": GPU_COST_PROXY_PER_HOUR,
        "groups": len(grouped),
        "raw_executions": len(measurements),
        "collection_run_id": collection_run_id or "all",
    }
    with KHUScenarioStore(destination_db) as store:
        store.begin_run(selected_run, int(seed), str(raw_path), configuration)
        for ordinal, ((collection_run, profile_name), group) in enumerate(sorted(grouped.items())):
            scenario_id = f"{selected_run}:{collection_run}:{profile_name}"
            source_scenario_id = _source_profile_id(group)
            application = _application_for(group)
            operation = {
                "lifecycle_state": "NEW",
                "current_replicas": 0,
                "spec_drift": False,
                "cooldown_active": False,
                "optimization_feedback": {"metrics": {"resource": {}, "inference": {}}},
            }
            outcomes = [
                _outcome_for_measurement(
                    scenario_id=scenario_id,
                    source_scenario_id=source_scenario_id,
                    application=application,
                    operation=operation,
                    measurement=measurement,
                )
                for measurement in sorted(group, key=lambda item: (item.device, item.execution_id))
            ]
            oracle = _oracle(outcomes)
            store.add_scenario(
                {
                    "scenario_id": scenario_id,
                    "run_id": selected_run,
                    "source_scenario_id": source_scenario_id,
                    "source_ordinal": ordinal,
                    "lifecycle_state": "NEW",
                    "application": application,
                    "operation": operation,
                    "oracle_action": oracle["action"],
                    "oracle_candidate_id": oracle["candidate_id"],
                    "oracle_reward": oracle["reward"],
                }
            )
            for outcome in outcomes:
                outcome["is_oracle"] = outcome["outcome_id"] == oracle["outcome_id"]
            store.add_outcomes(outcomes)
        store.finish_run(selected_run)
        summary = store.summary(selected_run)
        exports = store.export_csv(csv_dir) if csv_dir else {}
    return {
        "run_id": selected_run,
        "db": str(Path(destination_db).expanduser().resolve()),
        "csv": exports,
        "summary": summary,
        "lineage": {
            "source": str(raw_path),
            "source_successful_executions": len(measurements),
            "placement_groups": len(grouped),
            "observed_actions": ["DEPLOY"],
        },
    }


def build_observed_lifecycle_pair_dataset(
    *,
    source_db: str | Path,
    destination_db: str | Path,
    csv_dir: str | Path | None = None,
    run_id: str | None = None,
    collection_run_id: str | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Build a provenance-preserving lifecycle comparison dataset.

    A collector run measures the same workload under multiple *static*
    replica allocations.  This ETL retains those measurements as DEPLOY and
    KEEP rows, and exposes an adjacent 1->2, 2->4, or reverse allocation as
    SCALE_OUT/SCALE_IN evidence.  The target was benchmarked independently;
    it was not a live transition from the baseline.  That distinction is
    stored in every outcome and prevents this dataset from claiming observed
    rollout or autoscaling causality.

    UPDATE is deliberately absent: it requires a real image/model revision
    rollout with before/after telemetry, which the local benchmark collector
    does not execute.
    """
    raw_path = Path(source_db).expanduser().resolve()
    measurements = _read_measurements(raw_path, collection_run_id=collection_run_id)
    if not measurements:
        raise ValueError("live measurement database has no successful executions")
    workload_groups = _group_workload_measurements(measurements)
    replica_lanes = _group_replica_lanes(measurements)
    if not workload_groups:
        raise ValueError("live measurement database has no usable workload groups")
    selected_run = run_id or f"khu-observed-lifecycle-{uuid4().hex[:10]}"
    configuration = {
        "etl_version": LIFECYCLE_PAIR_ETL_VERSION,
        "source_kind": "actual_huggingface_inference",
        "source_db": str(raw_path),
        "collection_run_id": collection_run_id or "all",
        "observed_actions": ["DEPLOY", "KEEP"],
        "paired_static_measurement_actions": ["SCALE_OUT", "SCALE_IN"],
        "unsupported_without_new_measurements": ["UPDATE"],
        "transition_evidence": "paired_static_measurements_not_executed_lifecycle_transitions",
        "workload_groups": len(workload_groups),
        "replica_lanes": len(replica_lanes),
    }
    scenarios = 0
    outcomes_count = 0
    with KHUScenarioStore(destination_db) as store:
        store.begin_run(selected_run, int(seed), str(raw_path), configuration)
        ordinal = 0
        for group_key, group in sorted(workload_groups.items()):
            application = _application_for(group)
            source_scenario_id = _lifecycle_source_id(group)
            scenario_id = f"{selected_run}:deploy:{_safe_id(group_key)}"
            operation = _new_operation()
            outcomes = [
                _outcome_for_measurement(
                    scenario_id=scenario_id,
                    source_scenario_id=source_scenario_id,
                    application=application,
                    operation=operation,
                    measurement=item,
                    action="DEPLOY",
                    evidence="direct_static_measurement",
                    etl_version=LIFECYCLE_PAIR_ETL_VERSION,
                )
                for item in sorted(group, key=_measurement_sort_key)
            ]
            written = _add_lifecycle_scenario(
                store,
                scenario_id=scenario_id,
                run_id=selected_run,
                source_scenario_id=source_scenario_id,
                source_ordinal=ordinal,
                lifecycle_state="NEW",
                application=application,
                operation=operation,
                outcomes=outcomes,
            )
            if written:
                outcomes_count += written
                scenarios += 1
                ordinal += 1

        for lane_key, lane in sorted(replica_lanes.items()):
            application = _application_for(lane)
            source_scenario_id = _lifecycle_source_id(lane)
            by_replicas = {item.replica_count: item for item in sorted(lane, key=_measurement_sort_key)}
            for replicas, baseline in sorted(by_replicas.items()):
                operation = _operation_from_baseline(application, baseline)
                scenario_id = f"{selected_run}:operation:{_safe_id(lane_key)}:r{replicas}"
                outcomes = [
                    _outcome_for_measurement(
                        scenario_id=scenario_id,
                        source_scenario_id=source_scenario_id,
                        application=application,
                        operation=operation,
                        measurement=baseline,
                        action="KEEP",
                        evidence="direct_static_measurement",
                        baseline_execution_id=baseline.execution_id,
                        etl_version=LIFECYCLE_PAIR_ETL_VERSION,
                    )
                ]
                for target_replicas, target in sorted(by_replicas.items()):
                    if target_replicas == replicas:
                        continue
                    action = "SCALE_OUT" if target_replicas > replicas else "SCALE_IN"
                    outcomes.append(
                        _outcome_for_measurement(
                            scenario_id=scenario_id,
                            source_scenario_id=source_scenario_id,
                            application=application,
                            operation=operation,
                            measurement=target,
                            action=action,
                            evidence="paired_static_measurement",
                            baseline_execution_id=baseline.execution_id,
                            etl_version=LIFECYCLE_PAIR_ETL_VERSION,
                        )
                    )
                written = _add_lifecycle_scenario(
                    store,
                    scenario_id=scenario_id,
                    run_id=selected_run,
                    source_scenario_id=source_scenario_id,
                    source_ordinal=ordinal,
                    lifecycle_state=str(operation["lifecycle_state"]),
                    application=application,
                    operation=operation,
                    outcomes=outcomes,
                )
                if written:
                    outcomes_count += written
                    scenarios += 1
                    ordinal += 1
        store.finish_run(selected_run)
        summary = store.summary(selected_run)
        exports = store.export_csv(csv_dir) if csv_dir else {}
    return {
        "run_id": selected_run,
        "db": str(Path(destination_db).expanduser().resolve()),
        "csv": exports,
        "summary": summary,
        "lineage": {
            "source": str(raw_path),
            "source_successful_executions": len(measurements),
            "workload_groups": len(workload_groups),
            "scenarios": scenarios,
            "outcomes": outcomes_count,
            "transition_evidence": configuration["transition_evidence"],
        },
    }


def annotate_live_placement_manifest(artifact_dir: str | Path) -> dict[str, Any]:
    """Mark a generic RF artifact as limited to direct placement evidence."""
    path = Path(artifact_dir).expanduser().resolve() / "khu-policy.manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["training_scope"] = {
        "dataset_kind": "actual_huggingface_observed_placement",
        "supported_decision": "DEPLOY candidate placement only",
        "observed_actions": ["DEPLOY"],
        "unsupported_without_new_measurements": ["UPDATE", "SCALE_OUT", "SCALE_IN", "KEEP"],
        "note": "Do not wire this artifact into lifecycle-action execution; it is a shadow placement model.",
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _read_measurements(path: Path, *, collection_run_id: str | None) -> list[Measurement]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(execution_measurements)")}
        select = lambda name, fallback: name if name in columns else f"{fallback} AS {name}"
        rows = connection.execute(
            """
            SELECT em.execution_id, em.run_id, cr.host, em.profile_name, em.model_id, em.model_revision, em.device,
                   batch_size, max_length, repeats,
                   %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   result_json, node_summary_json
              FROM execution_measurements em
              JOIN collection_runs cr ON cr.run_id = em.run_id
             WHERE em.success = 1 AND cr.status = 'complete' AND em.device IN ('cpu', 'cuda')
               AND (? IS NULL OR em.run_id = ?)
             ORDER BY em.run_id, em.ordinal, em.execution_id
            """
            % (
                select("workload_id", "profile_name"),
                select("request_concurrency", "1"),
                select("cpu_threads", "2"),
                select("cpu_core_limit", "cpu_threads"),
                select("replica_count", "1"),
                select("load_condition", "'nominal'"),
                select("target_throughput_rps", "0"),
                select("latency_slo_ms", "0"),
                select("gpu_indices_json", "'[]'"),
            ),
            (collection_run_id, collection_run_id),
        ).fetchall()
        result: list[Measurement] = []
        for row in rows:
            execution_id = str(row["execution_id"])
            snapshot = connection.execute(
                """
                SELECT snapshot_json FROM node_samples
                 WHERE execution_id = ?
                 ORDER BY CASE phase WHEN 'before' THEN 0 ELSE 1 END, sample_id
                 LIMIT 1
                """,
                (execution_id,),
            ).fetchone()
            gpu_rows = connection.execute(
                """
                SELECT gpus_json FROM node_samples
                 WHERE execution_id = ? AND phase = 'running'
                 ORDER BY sample_id
                """,
                (execution_id,),
            ).fetchall()
            result.append(
                Measurement(
                    execution_id=execution_id,
                    run_id=str(row["run_id"]),
                    host=str(row["host"] or "unknown-host"),
                    profile_name=str(row["profile_name"]),
                    model_id=str(row["model_id"]),
                    model_revision=str(row["model_revision"]),
                    device=str(row["device"]),
                    batch_size=int(row["batch_size"]),
                    max_length=int(row["max_length"]),
                    repeats=int(row["repeats"]),
                    workload_id=str(row["workload_id"] or row["profile_name"]),
                    request_concurrency=max(1, int(row["request_concurrency"] or 1)),
                    cpu_threads=max(1, int(row["cpu_threads"] or 2)),
                    cpu_core_limit=max(1, int(row["cpu_core_limit"] or row["cpu_threads"] or 2)),
                    replica_count=max(1, int(row["replica_count"] or 1)),
                    load_condition=str(row["load_condition"] or "nominal"),
                    target_throughput_rps=max(0.0, _number(row["target_throughput_rps"])),
                    latency_slo_ms=max(0.0, _number(row["latency_slo_ms"])),
                    gpu_indices=tuple(int(_number(item)) for item in _scalar_array(row["gpu_indices_json"])),
                    result=_object(row["result_json"]),
                    node_summary=_object(row["node_summary_json"]),
                    node_snapshot=_object(snapshot["snapshot_json"] if snapshot else "{}"),
                    gpu_samples=tuple(_array(item["gpus_json"]) for item in gpu_rows),
                )
            )
        return _deduplicate_physical_measurements(result)
    finally:
        connection.close()


def _group_measurements(measurements: Sequence[Measurement]) -> dict[tuple[str, str], list[Measurement]]:
    grouped: dict[tuple[str, str], list[Measurement]] = {}
    for item in measurements:
        grouped.setdefault((item.run_id, item.profile_name), []).append(item)
    # A profile/device/replica count/CPU allocation is one placement candidate. Retain the
    # last deterministic execution if an interrupted/manual run contains a
    # duplicate, rather than mixing repeated observations into one candidate.
    normalized: dict[tuple[str, str], list[Measurement]] = {}
    for key, items in grouped.items():
        by_candidate: dict[tuple[str, int, int], Measurement] = {}
        for item in sorted(items, key=lambda value: value.execution_id):
            by_candidate[(item.device, item.replica_count, item.cpu_core_limit)] = item
        selected = list(by_candidate.values())
        devices = {item.device for item in selected}
        causal_only = (
            devices == {"cuda"}
            and str(selected[0].result.get("workload_task") or "") == "causal_lm_forward"
        )
        # Placement evidence needs a direct CPU/CUDA comparison. Causal LMs
        # remain GPU-only when the configured workload has no CPU profile.
        if devices == {"cpu", "cuda"} or causal_only:
            normalized[key] = selected
    return normalized


def _deduplicate_physical_measurements(measurements: Sequence[Measurement]) -> list[Measurement]:
    """Use one completed observation for each host-level physical condition.

    Older campaigns may contain deliberate repeated rounds.  They are useful
    for auditing variance in the raw SQLite evidence, but must not overweight a
    condition in a learning dataset when the collection policy requests diverse
    configurations.  A host remains part of the key: the same shape on KHU and
    icns-216 is heterogeneous infrastructure evidence, not a duplicate.
    """
    selected: dict[tuple[str, str, str, str, int, int, int, int, int], Measurement] = {}
    for item in sorted(measurements, key=_measurement_sort_key):
        key = (
            item.host,
            item.model_id,
            item.model_revision,
            item.device,
            item.batch_size,
            item.max_length,
            item.request_concurrency,
            item.cpu_core_limit,
            item.replica_count,
        )
        selected.setdefault(key, item)
    return list(selected.values())


def _group_workload_measurements(measurements: Sequence[Measurement]) -> dict[tuple[str, ...], list[Measurement]]:
    """Keep all directly measured placement choices for one workload run."""
    grouped: dict[tuple[str, ...], list[Measurement]] = {}
    for item in measurements:
        grouped.setdefault(
            (
                item.run_id,
                item.host,
                item.workload_id,
                item.model_id,
                item.model_revision,
                str(item.batch_size),
                str(item.max_length),
                str(item.request_concurrency),
                item.load_condition,
            ),
            [],
        ).append(item)
    return {
        key: _deduplicate_measurements(items)
        for key, items in grouped.items()
        if _deduplicate_measurements(items)
    }


def _group_replica_lanes(measurements: Sequence[Measurement]) -> dict[tuple[str, ...], list[Measurement]]:
    """Group only configurations that differ by physical replica count."""
    grouped: dict[tuple[str, ...], list[Measurement]] = {}
    for item in measurements:
        grouped.setdefault(
            (
                item.run_id,
                item.host,
                item.workload_id,
                item.model_id,
                item.model_revision,
                str(item.batch_size),
                str(item.max_length),
                str(item.request_concurrency),
                item.load_condition,
                item.device,
                str(item.cpu_core_limit),
            ),
            [],
        ).append(item)
    return {
        key: _deduplicate_measurements(items, replica_only=True)
        for key, items in grouped.items()
        if _deduplicate_measurements(items, replica_only=True)
    }


def _deduplicate_measurements(
    measurements: Sequence[Measurement], *, replica_only: bool = False
) -> list[Measurement]:
    """Use one deterministic observation for each candidate shape."""
    selected: dict[tuple[str, int, int] | int, Measurement] = {}
    for item in sorted(measurements, key=_measurement_sort_key):
        key: tuple[str, int, int] | int
        key = item.replica_count if replica_only else (item.device, item.replica_count, item.cpu_core_limit)
        selected[key] = item
    return list(selected.values())


def _measurement_sort_key(item: Measurement) -> tuple[str, str, int, int, str]:
    return (item.run_id, item.device, item.replica_count, item.cpu_core_limit, item.execution_id)


def _new_operation() -> dict[str, Any]:
    return {
        "lifecycle_state": "NEW",
        "current_replicas": 0,
        "spec_drift": False,
        "cooldown_active": False,
        "optimization_feedback": {"metrics": {"resource": {}, "inference": {}}},
    }


def _operation_from_baseline(application: Mapping[str, Any], baseline: Measurement) -> dict[str, Any]:
    latency = max(0.0, _number(baseline.result.get("batch_latency_p95_ms")))
    throughput = max(0.0, _number(baseline.result.get("inference_examples_per_second")))
    cpu = max(0.0, _number(baseline.node_summary.get("cpu_percent_mean")))
    gpu = max(0.0, _active_gpu_utilization(baseline))
    memory = max(0.0, _number(baseline.result.get("max_rss_kib")) / 1024.0)
    app = application_view(application)
    overloaded = latency > app.latency_slo_ms or (throughput > 0 and throughput < app.throughput_slo_rps)
    underutilized = baseline.replica_count > 1 and cpu < 30.0 and (baseline.device == "cpu" or gpu < 30.0)
    state = "SLO_PRESSURE" if overloaded else ("UNDERUTILIZED" if underutilized else "HEALTHY")
    return {
        "lifecycle_state": state,
        "current_replicas": baseline.replica_count,
        "spec_drift": False,
        "cooldown_active": False,
        "optimization_feedback": {
            "metrics": {
                "resource": {
                    "cpu_average_percent": cpu,
                    "memory_peak_mib": memory,
                    "accelerator_average_percent": gpu,
                },
                "inference": {
                    "latency_p95_ms": latency,
                    "throughput_rps": throughput,
                    "error_rate_percent": 0.0 if baseline.result.get("success", False) else 100.0,
                },
            }
        },
    }


def _add_lifecycle_scenario(
    store: KHUScenarioStore,
    *,
    scenario_id: str,
    run_id: str,
    source_scenario_id: str,
    source_ordinal: int,
    lifecycle_state: str,
    application: Mapping[str, Any],
    operation: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
) -> int:
    if not any(bool(item["feasible"]) for item in outcomes):
        return 0
    oracle = _oracle(outcomes)
    marked = [
        {**item, "is_oracle": item["outcome_id"] == oracle["outcome_id"]}
        for item in outcomes
    ]
    store.add_scenario(
        {
            "scenario_id": scenario_id,
            "run_id": run_id,
            "source_scenario_id": source_scenario_id,
            "source_ordinal": source_ordinal,
            "lifecycle_state": lifecycle_state,
            "application": application,
            "operation": operation,
            "oracle_action": oracle["action"],
            "oracle_candidate_id": oracle["candidate_id"],
            "oracle_reward": oracle["reward"],
        }
    )
    return store.add_outcomes(marked)


def _lifecycle_source_id(group: Sequence[Measurement]) -> str:
    first = group[0]
    return ":".join((first.host, *_source_profile_id(group).split(":")))


def _safe_id(value: Sequence[str]) -> str:
    return "-".join(
        "".join(character if character.isalnum() else "_" for character in item)[:96]
        for item in value
    )


def _application_for(group: Sequence[Measurement]) -> dict[str, Any]:
    first = group[0]
    parameter_mib = max(64.0, _number(first.result.get("model_parameter_bytes")) / MIB)
    causal = str(first.result.get("workload_task") or "") == "causal_lm_forward"
    latency_profile = "latency" in first.profile_name
    if first.target_throughput_rps > 0 and first.latency_slo_ms > 0:
        target_rps, latency_slo = first.target_throughput_rps, first.latency_slo_ms
    elif causal:
        target_rps, latency_slo = 20.0, 100.0
    elif latency_profile:
        target_rps, latency_slo = 30.0, 125.0
    else:
        target_rps, latency_slo = 250.0, 150.0
    return {
        "profile_id": f"live-{first.profile_name}",
        "app_id": first.model_id,
        "app_version": first.model_revision,
        "workload": {
            "task_type": "causal_lm_inference" if causal else "sequence_classification_inference",
            "expected_rps": target_rps,
            "request_concurrency": first.request_concurrency,
            "max_sequence_length": first.max_length,
            "load_condition": first.load_condition,
        },
        "requirements": {
            "compute": {
                "cpu_cores_min": max(1, min(8, int(math.ceil(parameter_mib / 1024.0)))),
                "memory_mib_min": int(math.ceil(max(512.0, parameter_mib * 1.50))),
                "storage_gib_min": max(5, int(math.ceil(parameter_mib / 1024.0 * 2.0))),
            },
            "accelerator": {
                "required": causal,
                "type": "nvidia" if causal else "",
                "count_min": 1 if causal else 0,
                "memory_mib_min_per_device": int(math.ceil(parameter_mib * 1.20)) if causal else 0,
            },
        "deployment": {
                "replicas_min": 1,
                "replicas_max": max(item.replica_count for item in group),
                "isolation": "shared",
            },
            "slo": {"latency_p95_ms_max": latency_slo, "throughput_rps_min": target_rps},
            # This is an experiment budget, not a CSP price assertion.
            "cost": {"currency": "USD", "cost_per_hour_max": GPU_COST_PROXY_PER_HOUR},
        },
    }


def _outcome_for_measurement(
    *,
    scenario_id: str,
    source_scenario_id: str,
    application: Mapping[str, Any],
    operation: Mapping[str, Any],
    measurement: Measurement,
    action: str = "DEPLOY",
    evidence: str = "direct_static_measurement",
    baseline_execution_id: str = "",
    etl_version: str = LIVE_ETL_VERSION,
) -> dict[str, Any]:
    candidate_payload = _candidate_for(measurement)
    app = application_view(application)
    candidate = candidate_view(candidate_payload)
    features = feature_row(app, candidate, operational_view(operation), action, measurement.replica_count)
    latency = max(0.0, _number(measurement.result.get("batch_latency_p95_ms")))
    throughput = max(0.0, _number(measurement.result.get("inference_examples_per_second")))
    cpu_utilization = _number(measurement.node_summary.get("cpu_percent_mean"))
    memory_total = max(1.0, _number(measurement.node_snapshot.get("memory_total_bytes")))
    memory_utilization = min(100.0, _number(measurement.result.get("max_rss_kib")) * 1024.0 / memory_total * 100.0)
    gpu_utilization = _active_gpu_utilization(measurement)
    feasible = candidate_feasible(app, candidate)
    success = bool(measurement.result.get("success", False)) and feasible
    slo_satisfied = (
        success
        and latency <= app.latency_slo_ms
        and throughput >= app.throughput_slo_rps
    )
    reward = _reward(
        success=success,
        latency=latency,
        latency_slo=app.latency_slo_ms,
        throughput=throughput,
        throughput_slo=app.throughput_slo_rps,
        cpu_utilization=cpu_utilization,
        memory_utilization=memory_utilization,
        gpu_utilization=gpu_utilization,
        has_gpu=candidate.gpu_count > 0,
        cost=candidate.cost_per_hour,
        cost_budget=GPU_COST_PROXY_PER_HOUR,
    )
    outcome_id = f"{scenario_id}:{action}:{measurement.device}:{measurement.execution_id}"
    return {
        "outcome_id": outcome_id,
        "scenario_id": scenario_id,
        "source_scenario_id": source_scenario_id,
        "candidate_id": candidate.candidate_id,
        "action": action,
        "desired_replicas": measurement.replica_count,
        "feasible": feasible,
        "success": success,
        "slo_satisfied": slo_satisfied,
        "is_oracle": False,
        "reward": reward,
        "cost_per_hour": candidate.cost_per_hour,
        "latency_p95_ms": latency,
        "throughput_rps": throughput,
        "error_rate_percent": 0.0 if success else 100.0,
        "cpu_utilization_percent": cpu_utilization,
        "memory_utilization_percent": memory_utilization,
        "gpu_utilization_percent": gpu_utilization,
        "features": features,
        "outcome": {
            "outcome_source": "actual_huggingface_inference",
            "etl_version": etl_version,
            "action_evidence": evidence,
            "baseline_execution_id": baseline_execution_id or None,
            "execution_id": measurement.execution_id,
            "collection_run_id": measurement.run_id,
            "profile_name": measurement.profile_name,
            "model_id": measurement.model_id,
            "model_revision": measurement.model_revision,
            "device": measurement.device,
            "workload_id": measurement.workload_id,
            "request_concurrency": measurement.request_concurrency,
            "sequence_length": measurement.max_length,
            "replica_count": measurement.replica_count,
            "cpu_core_limit": measurement.cpu_core_limit,
            "host": measurement.host,
            "load_condition": measurement.load_condition,
            "measured": {
                "latency_p95_ms": latency,
                "throughput_rps": throughput,
                "cpu_utilization_percent": cpu_utilization,
                "memory_utilization_percent": memory_utilization,
                "gpu_utilization_percent": gpu_utilization,
            },
            "reward_policy": {
                "formula": "0.55*slo_headroom + 0.25*performance + 0.10*resource_efficiency + 0.10*cost_efficiency",
                "hard_failure_reward": 0.0,
                "cost_proxy": candidate.cost_per_hour,
            },
        },
    }


def _candidate_for(measurement: Measurement) -> dict[str, Any]:
    snapshot = measurement.node_snapshot
    result = measurement.result
    # This is the resource allocation actually enforced during the benchmark,
    # not the full host CPU count. It is therefore safe to use as a placement
    # candidate feature when comparing 2/4/8/16-core shapes.
    cpu_cores = measurement.cpu_core_limit
    cpu_utilization = _number(snapshot.get("cpu_percent"), _number(measurement.node_summary.get("cpu_percent_mean")))
    memory_mib = max(1.0, _number(snapshot.get("memory_total_bytes")) / MIB)
    memory_available_mib = max(0.0, _number(snapshot.get("memory_available_bytes")) / MIB)
    storage = shutil.disk_usage("/")
    is_gpu = measurement.device == "cuda"
    gpu_indices = measurement.gpu_indices or ((int(_number(result.get("gpu_index"), 0)),) if is_gpu else ())
    gpus = [_active_gpu(snapshot, index) for index in gpu_indices] if is_gpu else []
    gpus = [item for item in gpus if item]
    gpu_total = sum(_number(item.get("memory_total_mib")) for item in gpus)
    gpu_available = sum(
        max(0.0, _number(item.get("memory_total_mib")) - _number(item.get("memory_used_mib")))
        for item in gpus
    )
    return {
        "candidate_id": (
            f"observed-{measurement.host}-{measurement.device}-"
            f"{measurement.replica_count}x-cpu{measurement.cpu_core_limit}"
        ),
        # GPU replicas are physical GPUs on one measured host. They must not
        # be misrepresented as a multi-node deployment candidate.
        "node_count": 1,
        "cpu_cores": cpu_cores,
        "cpu_available_cores": max(0.0, cpu_cores * (1.0 - min(100.0, cpu_utilization) / 100.0)),
        "cpu_utilization_percent": cpu_utilization,
        "memory_mib": memory_mib,
        "memory_available_mib": memory_available_mib,
        "storage_gib": storage.total / 1024**3,
        "storage_available_gib": storage.free / 1024**3,
        "gpu_count": len(gpu_indices) if is_gpu else 0,
        "gpu_memory_mib": gpu_total if is_gpu else 0,
        "gpu_available_mib": gpu_available if is_gpu else 0,
        "gpu_utilization_percent": _active_gpu_utilization(measurement),
        "availability": 1.0,
        "cost_per_hour": GPU_COST_PROXY_PER_HOUR * measurement.replica_count if is_gpu else CPU_COST_PROXY_PER_HOUR,
        "healthy": True,
        "provider": "local-observed",
        "region": f"host-{measurement.host}",
    }


def _active_gpu(snapshot: Mapping[str, Any], gpu_index: int) -> Mapping[str, Any]:
    for item in snapshot.get("gpus", []):
        if isinstance(item, Mapping) and int(_number(item.get("index"), -1)) == gpu_index:
            return item
    return {}


def _active_gpu_utilization(measurement: Measurement) -> float:
    # The raw sampler records every GPU.  Use the execution's active index so
    # an idle neighbour GPU cannot dilute this candidate's measurement.
    if measurement.device != "cuda":
        return 0.0
    indices = measurement.gpu_indices or (int(_number(measurement.result.get("gpu_index"), 0)),)
    running = [
        _number(_active_gpu({"gpus": sample}, index).get("utilization_percent"))
        for sample in measurement.gpu_samples
        for index in indices
        if _active_gpu({"gpus": sample}, index)
    ]
    if running:
        return sum(running) / len(running)
    snapshots = [_active_gpu(measurement.node_snapshot, index) for index in indices]
    values = [_number(item.get("utilization_percent")) for item in snapshots if item.get("utilization_percent") is not None]
    if values:
        return sum(values) / len(values)
    return _number(measurement.node_summary.get("gpu_utilization_percent_mean"))


def _reward(
    *,
    success: bool,
    latency: float,
    latency_slo: float,
    throughput: float,
    throughput_slo: float,
    cpu_utilization: float,
    memory_utilization: float,
    gpu_utilization: float,
    has_gpu: bool,
    cost: float,
    cost_budget: float,
) -> float:
    if not success:
        return 0.0
    latency_score = min(1.0, latency_slo / max(latency, 0.001))
    throughput_score = min(1.0, throughput / max(throughput_slo, 0.001))
    slo_headroom = (latency_score + throughput_score + 1.0) / 3.0
    performance = (latency_score + throughput_score) / 2.0
    utilization = [max(0.0, min(100.0, cpu_utilization)), max(0.0, min(100.0, memory_utilization))]
    if has_gpu:
        utilization.append(max(0.0, min(100.0, gpu_utilization)))
    observed_utilization = sum(utilization) / len(utilization)
    resource_efficiency = max(0.0, 1.0 - abs(observed_utilization - 65.0) / 65.0)
    cost_efficiency = 1.0 / (1.0 + cost / max(cost_budget, 0.001))
    return round(
        max(0.0, min(1.0, 0.55 * slo_headroom + 0.25 * performance + 0.10 * resource_efficiency + 0.10 * cost_efficiency)),
        8,
    )


def _oracle(outcomes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    feasible = [item for item in outcomes if bool(item["feasible"])]
    if not feasible:
        raise ValueError("observed placement group has no feasible candidate")
    return max(
        feasible,
        key=lambda item: (float(item["reward"]), bool(item["slo_satisfied"]), str(item["candidate_id"])),
    )


def _source_profile_id(group: Sequence[Measurement]) -> str:
    first = group[0]
    return ":".join(
        (
            first.workload_id,
            first.model_id,
            first.model_revision,
            str(first.batch_size),
            str(first.max_length),
            str(first.request_concurrency),
            first.load_condition,
        )
    )


def _object(raw: Any) -> Mapping[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _array(raw: Any) -> tuple[Mapping[str, Any], ...]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping)) if isinstance(value, list) else ()


def _scalar_array(raw: Any) -> tuple[Any, ...]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(value) if isinstance(value, list) else ()


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)
