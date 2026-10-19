"""Shared contracts and feature extraction for the KHU policy PoC."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping


POLICY_SCHEMA_VERSION = "khu-policy/v1"
DATASET_SCHEMA_VERSION = "khu-deployment-scenarios/v1"
LIFECYCLE_STATES = ("NEW", "SPEC_DRIFT", "SLO_PRESSURE", "UNDERUTILIZED", "HEALTHY")
POLICY_ACTIONS = ("DEPLOY", "UPDATE", "SCALE_OUT", "SCALE_IN", "KEEP")


def number(value: Any, default: float = 0.0) -> float:
    """Return one finite number; malformed optional telemetry becomes zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    result = float(value)
    return result if isfinite(result) else float(default)


def integer(value: Any, default: int = 0) -> int:
    return int(round(number(value, default)))


def mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def nested(value: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    current: Mapping[str, Any] = value
    for key in keys:
        current = mapping(current.get(key))
    return current


def first_number(value: Mapping[str, Any], names: Iterable[str], default: float = 0.0) -> float:
    for name in names:
        if name in value:
            return number(value[name], default)
    return float(default)


@dataclass(frozen=True)
class ApplicationView:
    cpu_cores: float
    memory_mib: float
    storage_gib: float
    accelerator_required: bool
    accelerator_count: float
    accelerator_memory_mib: float
    replicas_min: float
    replicas_max: float
    expected_rps: float
    latency_slo_ms: float
    throughput_slo_rps: float
    workload_type: str
    request_concurrency: float
    max_sequence_length: float
    load_condition: str


@dataclass(frozen=True)
class CandidateView:
    candidate_id: str
    node_count: float
    cpu_cores: float
    cpu_available: float
    cpu_utilization: float
    memory_mib: float
    memory_available: float
    storage_gib: float
    storage_available: float
    gpu_count: float
    gpu_memory_mib: float
    gpu_available_mib: float
    gpu_utilization: float
    availability: float
    cost_per_hour: float
    healthy: bool
    provider: str
    region: str


def application_view(profile: Mapping[str, Any]) -> ApplicationView:
    requirements = nested(profile, "requirements")
    compute = nested(requirements, "compute")
    accelerator = nested(requirements, "accelerator")
    deployment = nested(requirements, "deployment")
    slo = nested(requirements, "slo")
    workload = nested(profile, "workload")
    return ApplicationView(
        cpu_cores=max(1.0, first_number(compute, ("cpu_cores_min", "cpu_cores"), 1.0)),
        memory_mib=max(128.0, first_number(compute, ("memory_mib_min", "memory_mib"), 128.0)),
        storage_gib=max(1.0, first_number(compute, ("storage_gib_min", "storage_gib"), 1.0)),
        accelerator_required=bool(accelerator.get("required", False)),
        accelerator_count=max(0.0, first_number(accelerator, ("count_min", "count"))),
        accelerator_memory_mib=max(
            0.0, first_number(accelerator, ("memory_mib_min_per_device", "memory_mib"))
        ),
        replicas_min=max(1.0, first_number(deployment, ("replicas_min",), 1.0)),
        replicas_max=max(1.0, first_number(deployment, ("replicas_max",), 1.0)),
        expected_rps=max(0.0, first_number(workload, ("expected_rps",), 0.0)),
        latency_slo_ms=max(1.0, first_number(slo, ("latency_p95_ms_max",), 250.0)),
        throughput_slo_rps=max(0.1, first_number(slo, ("throughput_rps_min",), 1.0)),
        workload_type=str(workload.get("task_type") or "generic").lower(),
        request_concurrency=max(1.0, first_number(workload, ("request_concurrency", "concurrency"), 1.0)),
        max_sequence_length=max(1.0, first_number(workload, ("max_sequence_length", "sequence_length", "max_length"), 64.0)),
        load_condition=str(workload.get("load_condition") or "nominal").lower(),
    )


def candidate_view(value: Mapping[str, Any], index: int = 0) -> CandidateView:
    """Adapt either ResourceOps raw resources or normalized KHU candidates."""
    resource = mapping(value.get("resource")) or value
    cpu = nested(resource, "cpu")
    memory = nested(resource, "memory")
    storage = nested(resource, "storage")
    accelerators = resource.get("accelerators")
    accelerator_items = accelerators if isinstance(accelerators, list) else []
    gpu_count = sum(1 for item in accelerator_items if mapping(item).get("healthy", True))
    gpu_memory = sum(number(mapping(item).get("memory_total_mib")) for item in accelerator_items)
    gpu_available = sum(number(mapping(item).get("memory_available_mib")) for item in accelerator_items)
    gpu_utilizations = [number(mapping(item).get("utilization_percent")) for item in accelerator_items]
    if not accelerator_items:
        gpu_count = integer(value.get("gpu_count"))
        gpu_memory = first_number(value, ("gpu_memory_mib", "gpu_memory_total_mib"))
        gpu_available = first_number(value, ("gpu_memory_available_mib", "gpu_available_mib"), gpu_memory)
        gpu_utilizations = [first_number(value, ("gpu_utilization_percent", "gpu_utilization"))]
    candidate_id = str(
        value.get("candidate_id") or resource.get("resource_id") or resource.get("hostname") or f"candidate-{index + 1}"
    )
    return CandidateView(
        candidate_id=candidate_id,
        node_count=max(1.0, first_number(value, ("node_count",), 1.0)),
        cpu_cores=max(0.0, first_number(cpu, ("logical_cores", "cores"), first_number(value, ("cpu_cores",)))),
        cpu_available=max(0.0, first_number(cpu, ("available_cores",), first_number(value, ("cpu_available_cores",)))),
        cpu_utilization=max(0.0, first_number(cpu, ("utilization_percent",), first_number(value, ("cpu_utilization_percent", "cpu_utilization")))),
        memory_mib=max(0.0, first_number(memory, ("total_mib",), first_number(value, ("memory_mib",)))),
        memory_available=max(0.0, first_number(memory, ("available_mib",), first_number(value, ("memory_available_mib",)))),
        storage_gib=max(0.0, first_number(storage, ("total_gib",), first_number(value, ("storage_gib",)))),
        storage_available=max(0.0, first_number(storage, ("available_gib",), first_number(value, ("storage_available_gib",)))),
        gpu_count=float(gpu_count),
        gpu_memory_mib=max(0.0, gpu_memory),
        gpu_available_mib=max(0.0, gpu_available),
        gpu_utilization=(sum(gpu_utilizations) / len(gpu_utilizations)) if gpu_utilizations else 0.0,
        availability=max(0.0, min(1.0, first_number(value, ("availability", "availability_score"), 1.0))),
        cost_per_hour=max(0.0, first_number(value, ("cost_per_hour", "estimated_cost_per_hour"), 0.0)),
        healthy=bool(resource.get("healthy", value.get("healthy", True))),
        provider=str(resource.get("provider") or value.get("provider") or "unknown").lower(),
        region=str(resource.get("region") or value.get("region") or "unknown").lower(),
    )


def candidate_feasible(application: ApplicationView, candidate: CandidateView) -> bool:
    if not candidate.healthy:
        return False
    if candidate.cpu_cores < application.cpu_cores:
        return False
    if candidate.memory_mib < application.memory_mib:
        return False
    if candidate.storage_gib < application.storage_gib:
        return False
    if application.accelerator_required:
        return (
            candidate.gpu_count >= application.accelerator_count
            and candidate.gpu_memory_mib >= application.accelerator_memory_mib
        )
    return True


def operational_view(value: Mapping[str, Any]) -> dict[str, float | str]:
    feedback = mapping(value.get("optimization_feedback")) or value
    metrics = nested(feedback, "metrics")
    resource = nested(metrics, "resource")
    inference = nested(metrics, "inference")
    return {
        "lifecycle_state": str(value.get("lifecycle_state") or "NEW").upper(),
        "current_replicas": max(0.0, first_number(value, ("current_replicas",), 0.0)),
        "spec_drift": 1.0 if bool(value.get("spec_drift", False)) else 0.0,
        "cooldown_active": 1.0 if bool(value.get("cooldown_active", False)) else 0.0,
        "cpu_average_percent": max(0.0, first_number(resource, ("cpu_average_percent",), first_number(value, ("cpu_average_percent",)))),
        "memory_peak_mib": max(0.0, first_number(resource, ("memory_peak_mib",), first_number(value, ("memory_peak_mib",)))),
        "gpu_average_percent": max(0.0, first_number(resource, ("accelerator_average_percent",), first_number(value, ("gpu_average_percent",)))),
        "latency_p95_ms": max(0.0, first_number(inference, ("latency_p95_ms",), first_number(value, ("latency_p95_ms",)))),
        "throughput_rps": max(0.0, first_number(inference, ("throughput_rps",), first_number(value, ("throughput_rps",)))),
        "error_rate_percent": max(0.0, first_number(inference, ("error_rate_percent",), first_number(value, ("error_rate_percent",)))),
    }


def feature_row(
    application: ApplicationView,
    candidate: CandidateView,
    operation: Mapping[str, Any],
    action: str,
    desired_replicas: int,
) -> dict[str, float | str]:
    """Feature schema shared by generation, training, and online evaluation."""
    values: dict[str, float | str] = {
        "workload_type": application.workload_type,
        "accelerator_required": float(application.accelerator_required),
        "app_cpu_cores": application.cpu_cores,
        "app_memory_mib": application.memory_mib,
        "app_storage_gib": application.storage_gib,
        "app_gpu_count": application.accelerator_count,
        "app_gpu_memory_mib": application.accelerator_memory_mib,
        "replicas_min": application.replicas_min,
        "replicas_max": application.replicas_max,
        "expected_rps": application.expected_rps,
        "latency_slo_ms": application.latency_slo_ms,
        "throughput_slo_rps": application.throughput_slo_rps,
        "workload_request_concurrency": application.request_concurrency,
        "workload_sequence_length": application.max_sequence_length,
        "workload_load_condition": application.load_condition,
        "candidate_provider": candidate.provider,
        "candidate_region": candidate.region,
        "candidate_nodes": candidate.node_count,
        "candidate_cpu_cores": candidate.cpu_cores,
        "candidate_cpu_available": candidate.cpu_available,
        "candidate_cpu_utilization": candidate.cpu_utilization,
        "candidate_memory_mib": candidate.memory_mib,
        "candidate_memory_available": candidate.memory_available,
        "candidate_storage_gib": candidate.storage_gib,
        "candidate_storage_available": candidate.storage_available,
        "candidate_gpu_count": candidate.gpu_count,
        "candidate_gpu_memory_mib": candidate.gpu_memory_mib,
        "candidate_gpu_available_mib": candidate.gpu_available_mib,
        "candidate_gpu_utilization": candidate.gpu_utilization,
        "candidate_availability": candidate.availability,
        "candidate_cost_per_hour": candidate.cost_per_hour,
        "lifecycle_state": str(operation.get("lifecycle_state", "NEW")),
        "current_replicas": number(operation.get("current_replicas")),
        "spec_drift": number(operation.get("spec_drift")),
        "cooldown_active": number(operation.get("cooldown_active")),
        "observed_cpu_percent": number(operation.get("cpu_average_percent")),
        "observed_memory_mib": number(operation.get("memory_peak_mib")),
        "observed_gpu_percent": number(operation.get("gpu_average_percent")),
        "observed_latency_p95_ms": number(operation.get("latency_p95_ms")),
        "observed_throughput_rps": number(operation.get("throughput_rps")),
        "observed_error_rate_percent": number(operation.get("error_rate_percent")),
        "action": action,
        "desired_replicas": float(desired_replicas),
        "replica_delta": float(desired_replicas) - number(operation.get("current_replicas")),
    }
    return values
