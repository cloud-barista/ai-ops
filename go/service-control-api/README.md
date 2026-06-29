# Service Control API

AI service-control prototype의 Go 구현 모듈입니다. 이 모듈은 LLM selection, agent registry validation, CPU/GPU placement recommendation, deployment-plan generation, manifest dry-run, readiness reporting을 수행합니다.

## 테스트 실행

```bash
go test ./...
```

## API 실행

```bash
go run ./cmd/service-control-api
```

## CLI 실행

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

## API Endpoint

| Method | Path |
| --- | --- |
| `GET` | `/healthz` |
| `GET` | `/openapi.yaml` |
| `GET` | `/api/v1/agents` |
| `POST` | `/api/v1/ops-llm/select` |
| `POST` | `/api/v1/apps/placement` |
| `POST` | `/api/v1/apps/deployment-plan` |
| `POST` | `/api/v1/service-operations/run` |

## 응답 신호

통합 pipeline은 다음 값을 반환합니다.

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
