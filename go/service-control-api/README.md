# Service Control API

AI service-control prototype의 Go 구현 모듈입니다. 이 모듈은 LLM 호출 전 Go Request Guard, 실제 LLM 기반 Deployment Manifest 생성, Go Manifest Guard, AppDeploy 요청·상태 추적, agent 등록과 bounded Action 검증을 제공합니다.

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

go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-ai-application-control \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-llm-automation-action \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --candidate-id qwen3.5-ops-planner \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1

go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --vm-requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference \
  --operation-service llm-chat-inference \
  --operation-resource aws-us-west-2-g6-xlarge-l4-20260707 \
  --mode plan_only \
  --guard-backend go
```

## API Endpoint

| Method | Path |
| --- | --- |
| `GET` | `/healthz` |
| `GET` | `/openapi.yaml` |
| `GET` | `/api/v1/agents` |
| `POST` | `/api/v1/agents` |
| `GET` | `/api/v1/agents/:name` |
| `POST` | `/api/v1/agents/:name/actions/:action/validate` |
| `POST` | `/api/v1/agents/:name/invocations/plan` |
| `POST` | `/api/v1/ops-llm/select` |
| `POST` | `/api/v1/apps/vm-suitability` |
| `POST` | `/api/v1/apps/deployment-plan` |
| `POST` | `/api/v1/automation/action-proposals` |
| `POST` | `/api/v1/automation/feedback` |
| `POST` | `/api/v1/planner/deployments` |
| `POST` | `/api/v1/service-operations/run` |

## 응답 신호

통합 pipeline은 다음 값을 반환합니다.

```text
selected_llm
decision_execution_status
llm_automation_action
runtime_model
selected_resource (입력된 실제 VM snapshot ID)
deployment_plan
deployment_validation
agent_reviews
operation_pipeline_ready
guard_backend
guard_validation
```

`deployment_plan.executor_type`은 `registered_external_agent`입니다. 특정 팀 도구를 고정하지 않으며, registry에 등록된 에이전트 중 `ai_application_deployment_control` capability와 요청 Action을 모두 허용한 실행 주체를 선택합니다. 연결된 실행 주체가 없으면 `register_executor_agent`를 precondition으로 남기고 실행하지 않습니다.
