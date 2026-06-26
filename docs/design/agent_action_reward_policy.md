# Agent Action And Reward Policy

## Purpose

This document defines how the four prototype agents expose action approvals and
reward signals in the AI-based service-control framework.

The current reward values are prototype review signals. They are not produced by
reinforcement learning. The Go implementation surfaces them in the
`service-operations` readiness report so that reviewers can inspect the agent
decision boundary.

## Principles

- Each agent returns an `action`, `approved`, `reward`, and `reason` field.
- `approved=true` means the action is allowed within that agent boundary.
- `approved=false` blocks the integrated readiness path.
- Final readiness is true only when all required agent reviews, manifest
  dry-run, recovery context, and guard-readiness checks are valid.
- `go/aiops-guard` remains the standalone bounded-action validator for
  namespace, deployment, and replica constraints.

## Agent Policy Table

| Agent | Example approved action | Reward signal meaning |
| --- | --- | --- |
| `AIServiceHASupportAgent` | `ha_scale_out_required`, `ha_no_action` | Service health, availability, and recovery need |
| `AIApplicationManagementAgent` | `app_scale_deployment`, `app_select_inference_vm` | AI application deployment/control suitability |
| `AISemiconductorInfraOpsAgent` | `infra_capacity_approved`, `infra_select_cpu_gpu_vm` | CPU/GPU VM feasibility |
| `CostOptimizationAgent` | `cost_budget_approved`, `cost_budget_rejected` | Cost and resource-efficiency boundary |

## Readiness Example

Input context:

```text
recovery_namespace=online-boutique
recovery_deployment=paymentservice
workload=llm-chat-inference
mode=mock
guard_backend=go
```

Expected final result:

```text
valid = true
recovery_pipeline_ready = true
guard_backend = go
guard_validation.valid = true
```

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace online-boutique \
  --recovery-deployment paymentservice \
  --mode mock \
  --guard-backend go
```

## Future Extension

- Replace prototype reward values with measured operation outcomes.
- Add latency, availability, and cost deltas as reward calibration inputs.
- Split the reward table into a separate versioned config if policy tuning
  becomes part of the final evaluation.
- Add AI semiconductor/NPU accelerator policies to the infrastructure agent.
