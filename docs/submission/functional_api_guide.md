# Functional API Guide

## API Server

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

## Endpoints

| Method | Path | Function |
| --- | --- | --- |
| `GET` | `/healthz` | Health check |
| `GET` | `/openapi.yaml` | OpenAPI contract |
| `GET` | `/api/v1/agents` | Agent registry listing |
| `POST` | `/api/v1/ops-llm/select` | Ops LLM selection |
| `POST` | `/api/v1/apps/placement` | CPU/GPU VM placement recommendation |
| `POST` | `/api/v1/apps/deployment-plan` | AI application deployment/control plan |
| `POST` | `/api/v1/service-operations/run` | Integrated readiness pipeline |

## Service Operations Example

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"online-boutique","recovery_deployment":"paymentservice","mode":"mock","guard_backend":"go"}'
```

Key response fields:

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

`recovery_namespace` and `recovery_deployment` are service operation/recovery
context fields. They are separate from the AI application deployment namespace
inside `deployment_plan.kubernetes`.
