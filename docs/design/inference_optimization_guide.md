# CPU/GPU VM Inference Optimization Guide

## Purpose

This document describes the prototype strategy for:

```text
CPU/GPU VM-based AI application deployment/control inference optimization
```

The current implementation is a Go-based recommendation prototype. It does not
replace a real GPU scheduler or cloud VM provisioning system. It selects a
candidate CPU/GPU VM resource for an AI workload using resource constraints,
SLOs, capacity, and cost policy.

## Input Configuration

Configuration file:

```text
config/inference_optimization.json
```

| Section | Meaning |
| --- | --- |
| `resources` | CPU/GPU VM candidates, latency, throughput, cost, capacity, node selector, and resource limits |
| `workloads` | AI workload type, accelerator need, VRAM need, latency SLO, throughput SLO, service name, and container image |

## Current Resource Candidates

| Resource | Accelerator | Intended use |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | Lightweight text classification and embedding workloads |
| `gpu-vm-l4` | GPU | Cost/performance-balanced LLM or vision inference |
| `gpu-vm-a100` | GPU | Higher-performance LLM or vision inference |

## Placement Decision

The placement recommendation performs these checks:

1. Confirm whether the workload requires an accelerator.
2. Confirm that the resource supports the workload model type.
3. Confirm GPU memory requirements when accelerator resources are needed.
4. Confirm latency and throughput SLO satisfaction.
5. Rank eligible resources with a weighted score.

Score:

```text
score =
  latency_weight * latency_score
+ throughput_weight * throughput_score
+ cost_weight * cost_score
+ capacity_weight * capacity_score
```

Default weights:

| Item | Weight |
| --- | --- |
| latency | 0.35 |
| throughput | 0.30 |
| cost | 0.20 |
| capacity | 0.15 |

## Go CLI

Recommend placement:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Generate a deployment/control plan:

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

## Expected Prototype Results

| Workload | Selected resource | Action | Reason |
| --- | --- | --- | --- |
| `llm-chat-inference` | `gpu-vm-l4` | `deploy_on_gpu_vm` | GPU is required and the L4 candidate satisfies the SLO/cost balance |
| `text-classifier` | `cpu-vm-standard` | `deploy_on_cpu_vm` | CPU capacity is sufficient for the configured SLO |

## Future Extension

- Integrate live Kubernetes node labels and device-plugin status.
- Consume GPU memory telemetry from real AI-Infra environments.
- Connect to CB-Tumblebug-managed VM inventory once shared infrastructure is available.
- Add NPU or AI semiconductor accelerator profiles.
