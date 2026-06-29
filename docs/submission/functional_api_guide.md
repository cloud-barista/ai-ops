# Functional/API Guide

## 1. Purpose

This guide describes the Go Echo HTTP API for the 1st-year service-control
functional prototype. The API exposes the same core functions as the Go CLI:
Ops LLM policy selection, agent registry listing, CPU/GPU VM placement
recommendation, deployment-plan generation, and integrated service-operations
readiness validation.

The Swagger/OpenAPI deliverable is maintained at
`docs/submission/openapi_service_control.yaml`.

## 2. Run the API Server

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

Expected startup signal:

```text
http server started on [::]:8080
```

## 3. Endpoint List

| Method | Path | Function |
| --- | --- | --- |
| `GET` | `/healthz` | Health check |
| `GET` | `/openapi.yaml` | Returns the OpenAPI YAML contract |
| `GET` | `/api/v1/agents` | Lists registered AI agents |
| `POST` | `/api/v1/ops-llm/select` | Runs Ops LLM policy-based candidate selection |
| `POST` | `/api/v1/apps/placement` | Recommends CPU/GPU VM placement for an AI workload |
| `POST` | `/api/v1/apps/deployment-plan` | Generates an AI application deployment/control plan |
| `POST` | `/api/v1/service-operations/run` | Runs the integrated service-operations readiness pipeline |

## 4. Basic Checks

```bash
curl http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"service":"service-control-api","status":"ok"}
```

OpenAPI contract:

```bash
curl http://127.0.0.1:8080/openapi.yaml
```

## 5. Agent Registry API

```bash
curl http://127.0.0.1:8080/api/v1/agents
```

The response contains the registered agent names, roles, responsibilities,
bounded actions, reward-signal descriptions, and enabled status. This endpoint
is the API-facing view of `config/agent_registry.json`.

## 6. Ops LLM Selection API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

Major response fields:

| Field | Meaning |
| --- | --- |
| `selected_model` | Candidate selected by the requested policy |
| `selected_score` | Weighted prototype policy score |
| `ranking` | Ranked candidate list |
| `rationale` | Human-readable explanation of the selection |
| `valid` | Whether the selection request was processed successfully |

The current policy values are manually defined prototype policy baselines, not
final standardized benchmark results.

## 7. Placement API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/placement \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}'
```

Major response fields:

| Field | Meaning |
| --- | --- |
| `selected_resource` | Recommended CPU/GPU VM resource profile |
| `action` | Recommended deployment action |
| `score` | Weighted placement score |
| `slo_satisfied` | Whether the selected resource satisfies configured SLO requirements |
| `ranked_candidates` | Eligible candidates ranked by score |
| `rejected_resources` | Resources rejected by constraints |

## 8. Deployment-Plan API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/deployment-plan \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}'
```

The deployment-plan response includes a Kubernetes-oriented plan containing
service name, container image, target resource, target accelerator, namespace,
deployment name, replicas, node selector, resource requests, resource limits,
control actions, monitoring metrics, and SLO values.

## 9. Service Operations API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

Major response fields:

| Field | Meaning |
| --- | --- |
| `valid` | Integrated readiness result |
| `selected_llm` | Selected LLM policy candidate |
| `runtime_model` | Runtime model label used in the readiness flow |
| `selected_resource` | Selected CPU/GPU VM candidate |
| `deployment_plan` | AI application deployment/control plan |
| `deployment_manifest` | Generated Kubernetes Deployment manifest |
| `deployment_dry_run` | Mock or dry-run deployment validation result |
| `agent_reviews` | Application, infrastructure, and cost review results |
| `recovery_pipeline_ready` | Whether the service operation/recovery context is ready |
| `guard_backend` | Guard validation backend, expected as `go` |
| `guard_validation` | Bounded-action readiness validation result |

## 10. Recovery Context Boundary

`recovery_namespace` and `recovery_deployment` are service operation/recovery
context fields. They identify the service-control target used for readiness and
guard validation. They are different from the AI application deployment
namespace generated inside `deployment_plan.kubernetes.namespace`.

For example:

- `recovery_namespace = aiops-demo`
- `recovery_deployment = aiops-service`
- `deployment_plan.kubernetes.namespace = ai-inference`

## 11. OpenAPI Deliverable

The OpenAPI document is a required submission artifact:

```text
docs/submission/openapi_service_control.yaml
```

It should be reviewed together with this guide and the Go route definitions in
`go/service-control-api/internal/api/server.go`.
