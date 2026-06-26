# AI Application Deployment-Control Inference Optimization Strategy

## Purpose

This document corresponds to the 1st-year deliverable:

```text
AI application deployment/control inference optimization strategy for CPU/GPU VM environments
```

The current implementation is a Go prototype that decides where an AI workload
should be placed and how the selected result should be represented as a
Kubernetes deployment/control plan. It does not create cloud VMs directly.

## Input Data

```text
config/inference_optimization.json
```

| Section | Meaning |
| --- | --- |
| `resources` | CPU/GPU VM candidate performance, cost, capacity, node selector, and resource limits |
| `workloads` | AI workload type, VRAM requirement, latency SLO, throughput SLO, service name, namespace, and container image |

## Strategy

1. Check workload accelerator requirements.
2. Filter VM candidates by supported model type.
3. Validate GPU memory requirements.
4. Validate latency and throughput SLOs.
5. Score eligible candidates by latency, throughput, cost, and capacity.
6. Select the highest-scoring VM resource.
7. Convert the result into a Kubernetes deployment/control plan.
8. Generate a manifest and run mock/server dry-run validation.

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

## Deployment Plan Fields

| Field | Meaning |
| --- | --- |
| `selected_resource` | Selected CPU/GPU VM resource candidate |
| `deployment_plan.kubernetes.namespace` | Namespace for the AI application workload |
| `deployment_plan.kubernetes.deployment` | Deployment name for the AI application workload |
| `deployment_plan.kubernetes.node_selector` | CPU/GPU VM placement condition |
| `deployment_plan.kubernetes.resources` | CPU, memory, GPU, and VRAM request/limit hints |
| `deployment_plan.control_actions` | Deploy, scale, monitor, and rollback control actions |

## Relation To Agents

- `AIApplicationManagementAgent` evaluates the AI application deployment/control plan.
- `AISemiconductorInfraOpsAgent` evaluates CPU/GPU VM feasibility.
- `CostOptimizationAgent` evaluates cost efficiency.
- `AIServiceHASupportAgent` can be connected to recovery workflows when alert inputs are supplied.

## Deliverable Mapping

| Deliverable | File |
| --- | --- |
| AI application deployment/control inference optimization strategy | `docs/design/ai_application_deployment_strategy.md` |
| CPU/GPU VM and workload configuration | `config/inference_optimization.json` |
| Placement recommendation CLI | `go run ./cmd/aiops-service-control recommend-inference-placement` |
| Deployment/control plan CLI | `go run ./cmd/aiops-service-control plan-inference-deployment` |
