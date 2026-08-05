# 기능/API 가이드

## 1. 개요

Go Echo API는 Ops LLM 선정, 에이전트 등록·Action 검증, LLM Deployment Manifest 생성, Go Guard 검증과 AppDeploy 배포 연계를 제공합니다.

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

## 2. Endpoint

| Method | Path | 기능 |
| --- | --- | --- |
| `GET` | `/healthz` | 상태 확인 |
| `GET` | `/openapi.yaml` | OpenAPI 계약 |
| `GET`, `POST` | `/api/v1/agents` | 에이전트 조회·외부 등록 |
| `POST` | `/api/v1/agents/{name}/actions/{action}/validate` | bounded Action 검증 |
| `POST` | `/api/v1/agents/{name}/invocations/plan` | 비실행 호출 계획 생성 |
| `POST` | `/api/v1/ops-llm/select` | Ops LLM 정책 선정 |
| `POST` | `/api/v1/apps/vm-suitability` | 제공된 실제 VM 적합성 검증 |
| `POST` | `/api/v1/apps/deployment-plan` | 범용 외부 에이전트 handoff 계획 |
| `POST` | `/api/v1/automation/action-proposals` | 실제 LLM Action 제안과 Go Guard 검증 |
| `POST` | `/api/v1/automation/feedback` | 승인된 handoff의 실행 상태 기록 |
| `POST` | `/api/v1/planner/deployments` | 자연어 요구를 Manifest로 생성·검증하고 AppDeploy에 전달 |
| `POST` | `/api/v1/service-operations/run` | 전체 계획 흐름 통합 보고 |

## 3. 외부 에이전트 등록 계약

```json
{
  "name": "ExternalExecutionAgent",
  "version": "0.1.0",
  "role": "Execute validated AI application control requests.",
  "endpoint": "https://agent.example.com",
  "invocation_path": "/v1/actions",
  "capabilities": ["ai_application_deployment_control"],
  "bounded_actions": [
    "deploy_application",
    "observe_status",
    "restart_application",
    "stop_application"
  ]
}
```

특정 프레임워크 이름을 요구하지 않습니다. capability와 Action이 일치하는 enabled agent라면 동일한 방식으로 연결됩니다.

## 4. 실제 VM 적합성 요청

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/vm-suitability \
  -H 'content-type: application/json' \
  --data @../../examples/requests/validate-vm-suitability.json
```

핵심 응답은 `target_vm_id`, `compatibility_status`, `resource_checks_passed`, `performance_status`, `checks`입니다. 성능이 없으면 `not_measured`로 반환합니다. 측정값과 성능 요구사항이 모두 있으면 `checks`에 latency SLO와 최소 throughput 비교 결과가 포함됩니다.

## 5. 제어 handoff 계획

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/deployment-plan \
  -H 'content-type: application/json' \
  --data @../../examples/requests/plan-ai-application-control.json
```

`selected_executor`는 registry에 일치 에이전트가 있을 때만 나타납니다. 응답의 `execution_status=not_executed`는 이 API가 실행기가 아니라 계획·검증 계층임을 나타냅니다.

## 6. LLM 자동화 Action

서버는 `AIOPS_LLM_CANDIDATES_PATH`로 candidate config를 받습니다. 요청자는 등록된 `candidate_id`, workload, 실제 VM snapshot과 운영 관측값만 전달합니다.

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/automation/action-proposals \
  -H 'content-type: application/json' \
  --data @../../examples/requests/plan-llm-automation-action.json
```

`decision_execution_status=executed`와 `guard.status=approved`를 각각 확인해야 합니다. 일치하는 외부 실행 주체가 없으면 `status=pending_executor`이며 실제 배포 완료가 아닙니다.

## 7. AppDeploy Planner 실행

서버 실행 전에 candidate config와 AppDeploy API base URL을 설정합니다. URL은 `/api/v1`까지 포함합니다.

```bash
export AIOPS_LLM_CANDIDATES_PATH=../../config/ops_llm_eval_candidates.local_ollama.json
export AIOPS_PLANNER_GUARD_POLICY_PATH=../../config/planner_guard_policy.json
export AIOPS_APPDEPLOY_BASE_URL=http://127.0.0.1:8081/api/v1

go run ./cmd/service-control-api
```

다른 터미널에서 요청합니다.

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/planner/deployments \
  -H 'content-type: application/json' \
  --data @../../examples/requests/run-appdeploy-planner.json
```

응답의 `request_guard`, `generation.guard_valid`, `manifest`, `deployment.status`, `polling`, `retry_recommended`를 확인합니다. `request_guard.status=rejected`이면 LLM과 AppDeploy는 호출되지 않습니다. `deployment.target_profile_id`는 플래너가 아니라 AppDeploy가 선택한 실제 결과입니다.

CLI에서는 다음 명령을 사용합니다.

```bash
go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1
```

## 8. API 통합 검증

```bash
go run ./cmd/aiops-service-control api-integration-validation \
  --output-dir ../../runs/api-integration-local \
  --port 18080
```

이 검증은 10개 API 흐름과 핵심 JSON field를 확인합니다. 테스트용 OpenAI-compatible endpoint를 실제 호출하고 승인 correlation ID의 feedback까지 검증하지만 외부 실행 주체의 endpoint 자체는 실행하지 않습니다.

## 9. 예제 파일

| 구분 | 파일 |
| --- | --- |
| VM 적합성 요청·응답 | `examples/requests/validate-vm-suitability.json`, `examples/responses/validate-vm-suitability-success.json` |
| 제어 계획 요청·응답 | `examples/requests/plan-ai-application-control.json`, `examples/responses/plan-ai-application-control-success.json` |
| LLM 자동화 요청·응답 | `examples/requests/plan-llm-automation-action.json`, `examples/responses/plan-llm-automation-action-success.json` |
| AppDeploy Planner 요청·응답 | `examples/requests/run-appdeploy-planner.json`, `examples/responses/run-appdeploy-planner-success.json` |
| 통합 운영 요청·응답 | `examples/requests/run-service-operations.json`, `examples/responses/run-service-operations-success.json` |

상세 계약은 `docs/submission/openapi_service_control.yaml`에서 확인합니다.
