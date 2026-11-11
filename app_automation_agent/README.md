# App Automation Agent

`ApplicationProfile`과 `ResourceRecommendation`을 받아 배포 및 운영 변경을 판단합니다.

## 핵심 기능

- 자원 제약과 Agent 판단에 대한 Guard 검증
- 승인된 `DeploymentPlan`과 `Revision` 관리
- 실행 후 관측에 따른 유지, 확장, 축소 판단
- 현행 AI-App 배포 요청 준비
- 운영자가 승인한 초기 추론 요청의 AI-App MCP dry-run과 실행 기록 연결
- Innogrid 엔진 카드 등록·복구와 권한을 적용한 MCP 조회

Revision은 승인된 실행 구성의 버전입니다. 판단 승인과 실제 변경 적용은 별도 동작입니다.

## 실행

Go 1.25 이상이 필요합니다.

```bash
cd app_automation_agent/go/service-control-api
go run ./cmd/service-control-api
```

API 문서는 플랫폼의 [통합 Swagger UI](http://127.0.0.1:18100/swagger)에서 확인하고 요청을 실행할 수 있습니다. 통합 문서에는 기본 모드의 정식 배포 판단 API를 포함합니다. 이 서비스의 원본 명세는 `/openapi.yaml`에서 내려받습니다.

## AI-App 요청 준비

`POST /api/v1/deployment-plans`의 승인된 초기 계획을 `POST /api/v1/agent-control/flows/{correlation_id}/aiapp/prepare`에서 AI-App의 `inference`·`app` 요청 본문으로 변환합니다. 첫 연동은 노드 하나와 복제본 하나를 지원합니다.

고정된 AI-App 런타임이 있으면 계획 요청에 `runtime_id`를 지정합니다. `vllm`과 `ollama`를 지원하며 기본값은 `vllm`입니다. Open WebUI의 Ollama 조합은 `runtime_id: ollama`로 계획을 만들고, 준비 요청에 `model_id: qwen3-vl-8b`와 `artifact_id: ollama-gguf`를 보냅니다. 승인한 런타임을 바꿀 때는 새 계획 ID를 사용합니다.

- 추천된 자원의 `resource_id`에는 CB-Tumblebug 노드의 `nsId/infraId/nodeId`를 사용합니다.
- `ApplicationProfile`의 `app_id`에는 AI-App 카탈로그 이름을, `app_version`에는 버전 번호를 문자열로 넣습니다.
- 준비 요청에는 `model_id`와 `artifact_id`를 넣습니다. 응답의 `deployments`는 AI-App `POST /aiapp/deployments`의 본문 형식입니다. 제출 전에 카탈로그·노드와 응용의 모델 접속 설정을 확인합니다.

### 승인된 추론 요청의 dry-run

`AIOPS_AUTONOMY_ADMIN_TOKEN`은 운영자 승인에 사용합니다. `AIOPS_AIAPP_ENGINE_APPROVAL_TOKEN`은 AI-App·엔진의 `AIAPP_ENGINE_APPROVAL_TOKEN`과 맞춥니다. 8090, 엔진 등록, OpsLLM에 쓰는 토큰과 각각 구분합니다. 먼저 엔진 카드를 등록하고 AI-App의 dry-run을 켭니다.

운영자가 `prepare` 결과에서 `revision`과 `deployments[0]`을 확인하고 `POST /api/v1/agent-control/flows/{correlation_id}/aiapp/stage`로 보냅니다. 요청 본문은 `{"revision":1,"deployment":{...}}`이며 `Authorization: Bearer <운영자 토큰>`이 필요합니다. 준비한 대상과 런타임이 승인된 계획과 일치할 때 전용 MCP 도구를 호출합니다.

`blocked`와 `engineRunId`·`deploymentId`가 돌아오면 파일 준비가 끝난 dry-run입니다. 요청과 응답은 `runs/aiapp-stage/{correlation_id}.json`에서 확인합니다. 응답을 잃으면 같은 요청을 다시 보내 이전 기록을 확인합니다. [AI-App MCP 계약](../../ai-app/docs/api/approved-deployment.md)

실제 적용을 승인할 때는 운영자 bearer로 `POST /api/v1/agent-control/flows/{correlation_id}/aiapp/apply`를 호출합니다. `revision`과 `previousRevision`, 추론·응용 요청을 함께 보냅니다. AI-App은 먼저 추론 서비스의 준비를 확인한 뒤 응용에 연결합니다. `GET /api/v1/agent-control/flows/{correlation_id}/aiapp/apply/{revision}`에서 단계별 Run과 배포 ID를 확인합니다. [적용·업데이트 입력](../../ai-app/docs/api/approved-deployment.md#승인된-revision-적용)

구형 AppDeploy를 사용하는 PoC는 `AIOPS_APPDEPLOY_POC_ENABLED=true`와 `AIOPS_APPDEPLOY_BASE_URL`을 지정해 별도 실행합니다. 이 모드의 `AIOPS_AIAPP_BASE_URL`은 비워 둡니다. 실제 제출을 켜는 설정은 `AIOPS_APPDEPLOY_SUBMIT_ENABLED`입니다. [PoC 연동 계약](docs/coordination/geon-appdeploy-integration.md)

## AI-App 자연어 연동

AI-App 8090의 `AIAPP_MOCK_NL_URL`을 이 서비스의 주소로 지정합니다. AI-App과 `llmop`은 서버 간에 접근 가능한 주소로 연결합니다.

| 설정 | 값 |
| --- | --- |
| `AIOPS_AIAPP_BASE_URL` | 경로를 뺀 AI-App 주소. 예를 들어 `http://127.0.0.1:8090` |
| `AIOPS_AIAPP_TOKEN` | 8090이 전달하는 `AIAPP_AUTH_TOKEN`과 동일한 값 |
| `AIOPS_AIAPP_NAMESPACE` | 운영자가 허용한 CB-Tumblebug 네임스페이스 한 개 |
| `AIOPS_AIAPP_ENGINE_REGISTRY_TOKEN` | Innogrid 엔진의 `AIAPP_ENGINE_REGISTRY_TOKEN`과 동일한 등록 전용 값 |
| `AIOPS_LLMOP_BASE_URL` | 경로를 뺀 `llmop` 주소 |
| `AIOPS_OPS_LLM_TOKEN` | `llmop`의 동명 서버 간 토큰 |

콘솔에서 “구축형 LLM으로 배포 가능한 응용을 보여줘”를 보내면 `llmop`이 유형에 맞는 모델을 호출하고, 에이전트가 `list_applications`를 실행합니다. 배포 상태를 조회하려면 배포 ID를 지시에 포함합니다. 실행 결과와 OpsLLM·엔진 ID는 `runs/aiapp-intents`와 기존 `/aiapp/partner/nl/runs`에서 확인할 수 있습니다. 변경 지시는 승인에 필요한 제안으로 기록됩니다.

LLMop의 해석 응답을 받지 못하면 에이전트는 요청 시간이 남아 있는 동안 같은 OpsLLM 실행 ID로 결과를 한 번 조회합니다. 조회한 결과는 일반 해석 응답과 같은 권한 및 승인 검사를 거칩니다. 결과를 확인할 수 없으면 자연어 실행 기록의 `opsLlm.run_id`와 `completion_unconfirmed`로 확인할 수 있습니다. [LLMop 실행 기록 조회](../llmop/go/service-control-api/README.md#opsllm-실행-기록-조회)

### Innogrid 엔진 카드 등록

엔진에서 접속 가능한 Deployment Agent 주소에 `/api/v1/integrations/ai-app`을 붙여 `AIAPP_ENGINE_REGISTRY_URL`로 설정합니다. 엔진은 시작할 때 이 주소의 `/agents`에 카드를 전송합니다. 등록에는 위의 전용 토큰을 쓰고, 자연어 요청과 MCP 조회에는 AI-App 게이트웨이 토큰을 씁니다.

`GET /api/v1/agents/ai-mcmp-app-automation-engine`에서 등록 결과를 확인합니다. `bounded_actions`에는 카드와 정책이 모두 허용하는 읽기 도구가 표시됩니다. 호출은 운영자가 설정한 AI-App 주소와 네임스페이스에 연결되며, 카드에 적힌 내부 MCP 서버 URL은 등록 정보로 보관합니다.

등록은 Deployment Agent의 `runs/aiapp-engine-registry.json`에 저장됩니다. 같은 주소와 네임스페이스에서 재시작하면 복구되고, 둘 중 하나를 바꾸면 엔진이 카드를 다시 전송해야 합니다. `GET /aiapp/partner/nl/readyz`는 설정과 등록 저장본의 준비 상태를 확인합니다. MCP 연결과 도구 권한은 실제 조회 시 확인합니다. 원격에서 일반 에이전트를 등록할 때는 관리자 토큰 `AIOPS_AUTONOMY_ADMIN_TOKEN`을 사용합니다.

### 자연어 에이전트 MCP

MCP 클라이언트를 Streamable HTTP로 `http://127.0.0.1:18130/aiapp/partner/nl/mcp`에 연결합니다. AI-App 프록시를 구성했다면 8090에서도 같은 경로를 사용할 수 있습니다. `GET /aiapp/partner/nl/card`의 `mcpEndpoint`에서 호출 경로를 확인하고, `tools/list`로 도구를 찾습니다.

요청마다 `Authorization: Bearer <AIOPS_AIAPP_TOKEN>`과 `X-AIMCMP-Namespace: <AIOPS_AIAPP_NAMESPACE>`를 보냅니다. HTTP를 직접 호출할 때는 `Content-Type: application/json`과 `Accept: application/json, text/event-stream`을 지정합니다.

| 도구 | 입력 | 동작 |
| --- | --- | --- |
| `run_application_intent` | `intent`, 선택 필드 `llmType` | OpsLLM으로 지시를 해석하고 허용된 조회를 수행합니다. 배포·제어 지시는 제안으로 기록합니다. |
| `get_application_intent_run` | `runId` | 권한이 있는 네임스페이스에서 저장된 실행 기록을 읽습니다. |

도구의 `structuredContent`에는 기존 REST와 같은 실행 기록이 들어갑니다. MCP에서 받은 `runId`를 REST의 `/aiapp/partner/nl/runs/{runId}`로 읽거나, REST에서 만든 기록을 MCP로 읽을 수 있습니다.

자연어 변경 제안은 호출 주체의 권한을 확인하고 검토한 계획의 해시를 승인한 뒤 적용합니다. 같은 승인 결과는 REST와 MCP에서 조회할 수 있습니다. [정책 설정과 승인 절차](docs/aiapp-intent-approval.md)

## 검증

서비스 모듈과 `go/aiops-guard`에서 각각 `go test ./...`와 `go vet ./...`를 실행합니다.

[연동 흐름](docs/integration_pipeline.md) / [API 계약](docs/openapi_deployment_agent.yaml) / [상세 가이드](docs/usage.md)
