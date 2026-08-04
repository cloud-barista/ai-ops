# Kyung Hee AIOps

> AI 어플리케이션 자동화 에이전트와 배포·스케일링 판단 메커니즘을 검증하는 1차년도 Go PoC

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 연구 목표

`geon`은 경희대학교 담당 범위인 다음 연구 기능을 독립적으로 검증합니다.

1. 자연어 요청 또는 구조화 App Spec 하나를 받습니다.
2. Requirement Analyzer가 `ApplicationProfile`을 생성합니다.
3. Mock Resource Recommender가 로컬 자원 카탈로그를 비교해 `ResourceRecommendation`을 생성합니다.
4. Agent Registry에서 자동화 Agent의 capability와 bounded action을 검증합니다.
5. `DEPLOY`, `REJECT`, `RETRY` 중 하나를 결정하고 Go Guard로 검증합니다.
6. 플랫폼 독립적인 `DesiredDeploymentSpec`을 출력합니다.
7. Mock 또는 외부 handoff Adapter가 전달 증거를 기록합니다.
8. 배포 상태와 성능 Feedback을 받으면 원인과 최소 스케일링 동작을 판단합니다.

핵심 산출물은 특정 배포 플랫폼의 실행 명령이 아니라 **검증 근거가 포함된 배포 결정과 Desired Deployment Spec**입니다.

```text
자연어 요청 또는 구조화 App Spec
→ Requirement Analyzer
→ ApplicationProfile
→ Mock Resource Recommender
→ ResourceRecommendation
→ Agent Registry 권한 확인
→ Registry에서 선택한 배포 판단 Agent
  (기본 Internal 또는 등록 Runtime)
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ DesiredDeploymentSpec
→ Mock simulation 또는 External handoff ready
→ 선택적 Feedback
→ NO_ACTION / SCALE_OUT / SCALE_IN
```

## 책임 경계

| geon이 담당하는 기능 | 외부 시스템이 담당하는 기능 |
| --- | --- |
| 자연어·App Spec 요구 분석과 ApplicationProfile 생성 | 운영 플랫폼의 최종 App Registry |
| 로컬 Mock 카탈로그 기반 자원 추천 | 실시간 클라우드 자원 수집·추천 |
| Agent 권한과 허용 Action 검증 | 외부 실행기와 플랫폼 권한 관리 |
| 배포 가능 여부와 최소 동작 결정 | VM 생성과 실제 배포 실행 |
| 플랫폼 독립적 배포 요구 스펙 생성 | 플랫폼 전용 Manifest 변환 |
| Mock/Handoff Adapter 전달 증거 생성 | 실제 배포 요청 처리와 실행 |
| Feedback 원인·SLO·스케일링 판단 | 실제 Scale-out·Scale-in 실행 |

기존 AppDeploy, ControlRun, Autonomous Loop 호환 API는 백엔드에 유지하지만, 핵심 연구 웹의 기본 흐름에는 포함하지 않습니다.

## 빠른 실행

### 1. 사전 준비

- Go 1.25 이상
- `geon` 브랜치 저장소
- Qwen 추론 비교를 실행할 때만 Ollama와 `qwen3.5:4b`

Windows Git Bash:

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
go version
```

Qwen 비교 실험을 함께 실행할 경우:

```bash
ollama list
ollama pull qwen3.5:4b  # 없을 때만 실행
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
```

### 2. geon 실행

저장소 내부에서 실행합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export AIOPS_REPO_ROOT="$(git rev-parse --show-toplevel)"
export AIOPS_BIND_ADDRESS="127.0.0.1"
export AIOPS_DEPLOYMENT_ADAPTER="mock"
export PORT=18080

cd "$AIOPS_REPO_ROOT/go/service-control-api"
go mod download
go run ./cmd/service-control-api
```

다른 터미널에서 확인합니다.

```bash
curl http://127.0.0.1:18080/healthz
```

브라우저에서 [http://127.0.0.1:18080/](http://127.0.0.1:18080/)을 엽니다.

`AIOPS_DEPLOYMENT_ADAPTER=mock`은 실제 VM을 생성하지 않고 `SIMULATED` 증거를 기록합니다. `handoff`로 바꾸면 Common JSON 배포 요청을 외부 전달 준비 상태인 `READY`로 기록하지만, 실제 네트워크 전송이나 배포 성공을 의미하지 않습니다.

## 웹 사용 순서

웹은 연구 흐름에 맞춰 3개 화면만 제공합니다.

### 1. 자동화 실행

1. 기본 입력 방식인 **자연어 요청**을 선택합니다.
2. **배포 판단 Agent**에서 기본 Internal Agent 또는 등록한 Runtime Agent를 선택합니다.
3. 배포·운영 요구사항을 한 번 입력하고 **자동 분석 및 판단**을 누릅니다.
4. `요구사항 분석 → 인프라 추천 → Agent 배포 판단 → Adapter 전달` 4단계 상태를 확인합니다.
5. 선택 Agent·출처·Dispatch 상태와 Request/Result/Domain Guard를 확인합니다.
6. 결과 JSON의 `desired_deployment_spec`과 `deployment_submission`을 확인합니다.
7. `mock / SIMULATED`는 독립 PoC 모의실험이며 실제 VM 배포가 아님을 확인합니다.
8. 필요하면 **자동 생성된 중간 결과**에서 `ApplicationProfile`과 `ResourceRecommendation`을 확인합니다.

구조화 입력 시험에서는 **구조화 App Spec**을 선택합니다. 기존 Common JSON v1.0 메시지를 직접 시험하는 화면은 **고급 프로토콜 검증**에 보존되어 있습니다.

### 2. Agent 및 정책

- 기본 Internal Agent `AIApplicationAutomationAgent`를 확인합니다.
- 핵심 capability `ai_application_automation`을 확인합니다.
- 허용 Action `generate_deployment_decision`을 확인합니다.
- 필요하면 같은 capability와 Action을 가진 Runtime Agent를 등록하거나 삭제합니다.

등록된 Runtime Agent는 **자동화 실행** 화면의 선택 목록에 나타나며, 선택하면 `DEPLOY`, `REJECT`, `RETRY` 판단을 실제 HTTP endpoint에 요청합니다. Runtime Agent 오류나 결과 거부 시 Internal Agent로 자동 대체하지 않으며 Adapter도 호출하지 않습니다. Runtime 등록 정보는 프로세스 메모리에만 저장되므로 서버 재시작 시 사라집니다.

### 3. 실험 결과

- 저장된 Flow별 선택 Agent·출처·Guard·판단·Desired Deployment Spec을 확인합니다.
- 선택적으로 규칙 기반, Qwen, Qwen+Guard 추론 결과를 비교합니다.
- SLO 위반 Feedback 샘플로 `SCALE_OUT` 판단을 검증합니다.
- 개별 기록 또는 전체 기록을 삭제합니다.

Qwen 서버가 꺼져 있어도 핵심 결정적 배포 판단은 실행됩니다. Qwen 비교 결과만 `provider_unavailable`로 기록됩니다.

## 핵심 API

| Method | Endpoint | 역할 |
| --- | --- | --- |
| `POST` | `/api/v1/agent-control/application-analysis-requests` | 통신 규약의 `application.analysis.request` 수신 및 중복 없는 자동 실행 |
| `POST` | `/api/v1/agent-control/automation-runs` | 입력 한 번으로 요구 분석·추천·Agent 판단 자동 실행 |
| `GET` | `/api/v1/agent-control/automation-runs/{run_id}` | 자동 실행 전체 증거 조회 |
| `POST` | `/api/v1/agent-control/application-contexts` | 요구 분석 결과 수신 |
| `POST` | `/api/v1/agent-control/resource-recommendations` | 추천 결과 수신 및 자동화 판단 |
| `GET` | `/api/v1/agent-control/flows` | 실험 Flow 목록 |
| `GET` | `/api/v1/agent-control/flows/{correlation_id}` | 단일 Flow 조회 |
| `DELETE` | `/api/v1/agent-control/flows/{correlation_id}` | 단일 Flow 삭제 |
| `DELETE` | `/api/v1/agent-control/flows` | 전체 Flow 삭제 |
| `POST` | `/api/v1/agent-control/deployment-status` | 배포 상태 연결 |
| `POST` | `/api/v1/agent-control/optimization-feedback` | 성능 Feedback과 스케일링 판단 |
| `POST` | `/api/v1/agent-control/flows/{correlation_id}/reasoning-comparisons` | 추론 방식 비교 |
| `GET/POST` | `/api/v1/agents` | Agent 조회·등록 |
| `DELETE` | `/api/v1/agents/{name}` | Runtime Agent 삭제 |

OpenAPI 계약은 [docs/submission/openapi_service_control.yaml](docs/submission/openapi_service_control.yaml)에서 확인합니다.

외부 시스템은 Common JSON v1.0 Envelope의 `message_id`를 요청 고유 키로 사용합니다. 동일한 메시지를 다시 보내면 `Idempotent-Replayed: true` 헤더와 기존 `run_id`가 반환됩니다. 같은 `message_id`에 다른 내용을 보내면 `409 Conflict`로 거부합니다. 실제 배포 시스템은 geon이 생성한 `deployment.create.request.data.deployment_request.request_id`를 멱등성 키로 사용합니다.

## 검증

```bash
cd go/service-control-api
go test ./... -count=1
go vet ./...
```

## 코드 구조

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/internal/agentcontrol/`](go/service-control-api/internal/agentcontrol/) | 입력 결합, 배포 결정, Guard, Feedback, 스케일링 판단 |
| [`config/mock_resource_catalog.json`](config/mock_resource_catalog.json) | 독립 PoC용 CPU/GPU Mock 자원 후보 |
| [`go/service-control-api/internal/api/`](go/service-control-api/internal/api/) | Agent Registry와 REST API |
| [`go/service-control-api/internal/webui/`](go/service-control-api/internal/webui/) | 3개 화면 연구용 Control Web |
| [`config/agent_registry.json`](config/agent_registry.json) | Agent capability와 bounded action 정책 |
| [`docs/submission/openapi_service_control.yaml`](docs/submission/openapi_service_control.yaml) | API 통신 계약 |
| [`docs/evidence/`](docs/evidence/) | 실험 결과와 검증 증적 |

## 공식 산출물

| 산출물 | 문서 |
| --- | --- |
| LLM 운영 관리 구조 설계서 | [Markdown](docs/deliverables/01_llm_operation_management_design.md) |
| 에이전트 등록 관리 프로토타입 | [Markdown](docs/deliverables/02_agent_registration_management_prototype.md) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [Markdown](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) |

## License

ai-ops는 [Apache License 2.0](LICENSE)에 따라 배포됩니다.
