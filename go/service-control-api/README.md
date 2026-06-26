# Service Control API

Go implementation of the AI service-control prototype. This module performs
LLM selection, agent registry validation, CPU/GPU placement recommendation,
deployment-plan generation, manifest dry-run, and readiness reporting.

## Run Tests

```bash
go test ./...
```

## Run API

```bash
go run ./cmd/service-control-api
```

## Run CLI

```bash
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first

go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace aiops-demo \
  --recovery-deployment aiops-service \
  --mode mock \
  --guard-backend go
```

## API Endpoints

| Method | Path |
| --- | --- |
| `GET` | `/healthz` |
| `GET` | `/openapi.yaml` |
| `GET` | `/api/v1/agents` |
| `POST` | `/api/v1/ops-llm/select` |
| `POST` | `/api/v1/apps/placement` |
| `POST` | `/api/v1/apps/deployment-plan` |
| `POST` | `/api/v1/service-operations/run` |

## Response Signals

The integrated pipeline returns:

```text
selected_llm
runtime_model
selected_resource
deployment_plan
deployment_manifest
deployment_dry_run
agent_reviews
recovery_pipeline_ready
guard_backend
guard_validation
```
