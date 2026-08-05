# Kyung Hee AIOps: geon

> AI 응용의 요구사항 분석, 인프라 추천, 배포 판단과 안전 검증을 연결하는 Go 기반 자동화 Agent PoC

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 프로젝트 역할

`geon`은 자연어 요청 또는 구조화된 App Spec을 받아 다음 과정을 한 번에 실행합니다.

```text
사용자 요청
→ Requirement Analyzer
→ ApplicationProfile
→ Mock Resource Recommender
→ ResourceRecommendation
→ Registry에서 배포 판단 Agent 선택
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ DesiredDeploymentSpec
→ Mock simulation 또는 External handoff ready
```

핵심 산출물은 실제 VM 배포 명령이 아니라 **검증 근거가 포함된 배포 결정과 플랫폼 중립적인 `DesiredDeploymentSpec`**입니다.

## 실행

[Go 1.25 이상](https://go.dev/dl/)을 설치하고 저장소를 받습니다.

```bash
git clone --branch geon --single-branch https://github.com/cloud-barista/ai-ops.git
cd ai-ops
```

Windows PowerShell 또는 VS Code 터미널:

```powershell
.\run-agent-control.cmd
```

Git Bash, Linux 또는 macOS:

```bash
./run-agent-control.sh
```

실행 후 [http://127.0.0.1:18080/](http://127.0.0.1:18080/)을 엽니다. 종료는 `Ctrl+C`입니다.

## 웹 실험 순서

### 1. 자동화 실행

1. **자연어 요청** 또는 **구조화 App Spec**을 선택합니다.
2. **배포 판단 Agent**를 선택합니다.
3. 요청을 입력하고 **자동 분석 및 판단**을 누릅니다.
4. `ApplicationProfile`, `ResourceRecommendation`, Agent 판단과 Guard 결과를 확인합니다.
5. 승인된 경우 생성된 `DesiredDeploymentSpec`을 확인합니다.

기본 `AIApplicationAutomationAgent (Internal)`은 별도 Agent 서버 없이 실행됩니다.

### 2. Agent 및 정책

배포 판단에 사용할 Agent의 상태, capability와 bounded action을 관리합니다.

```text
required capability: ai_application_automation
required action:     generate_deployment_decision
```

Runtime Agent는 Registry에 등록하는 것만으로 실행되지 않습니다. 선택하려면 등록한 `endpoint + invocation_path`에서 응답하는 별도 HTTP Agent 서버가 실행 중이어야 합니다.

### 3. 실험 결과

동일한 Flow에서 다음 증거를 확인합니다.

- 선택 Agent와 실행 상태
- `DEPLOY`, `REJECT`, `RETRY` 판단
- Request, Result, Domain Guard 결과
- `DesiredDeploymentSpec`
- Adapter 전달 상태
- 선택적 Feedback과 스케일링 판단

## 결과 해석

| 결과 | 의미 |
| --- | --- |
| `DEPLOY_APPROVED` | Agent 판단과 Guard 검증을 통과해 Desired Spec 생성 |
| `REJECTED` | 입력 요구사항 또는 Agent 판단이 유효하지 않음 |
| `RETRY_REQUIRED` | 요구사항을 만족하는 자원 재추천이 필요함 |
| `AGENT_EXECUTION_FAILED` | 선택한 Runtime Agent endpoint 호출 실패 |
| `SIMULATED` | Mock Adapter가 모의 전달 증거를 생성함 |
| `READY` | 외부 Adapter로 전달할 Common JSON이 준비됨 |

`SIMULATED`와 `READY`는 실제 VM 배포 성공을 의미하지 않습니다.

## 선택적 Qwen 비교

핵심 자동화 실행에는 Ollama가 필요하지 않습니다. Qwen은 **실험 결과 → 추론 방식 비교**에서 다음 방식을 비교할 때만 사용합니다.

```text
규칙 기반
Qwen 단순 추론
Qwen 제안 + Go Guard
```

```bash
ollama pull qwen3.5:4b
ollama list
```

Ollama가 꺼져 있어도 핵심 배포 판단은 실행되며, Qwen 비교만 `provider_unavailable`로 기록됩니다.

## CLI 실행

Windows Git Bash 기준입니다.

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

포트 충돌은 PowerShell에서 확인합니다.

```powershell
Get-NetTCPConnection -LocalPort 18080 -State Listen
```

## 책임 경계

| geon | 외부 시스템 |
| --- | --- |
| 요구 분석과 ApplicationProfile 생성 | 운영 플랫폼의 App Registry |
| Mock 카탈로그 기반 자원 추천 | 실시간 클라우드 자원 수집·추천 |
| Agent 권한과 배포 판단 검증 | 외부 실행기와 플랫폼 권한 관리 |
| DesiredDeploymentSpec 생성 | 플랫폼 전용 Manifest 변환 |
| Mock/Handoff 전달 증거 생성 | 실제 VM 생성과 배포 실행 |
| Feedback 기반 스케일링 판단 | 실제 Scale-out·Scale-in 실행 |

AppDeploy와 실제 인프라는 수정하거나 내장하지 않습니다. 향후 외부 연동은 Adapter 경계에서 교체합니다.

## 개발

```bash
cd go/service-control-api
go test ./... -count=1
go vet ./...
go build ./...
```

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | Go API, Agent Control과 Web UI |
| [`config/agent_registry.json`](config/agent_registry.json) | Agent capability와 action 정책 |
| [`config/mock_resource_catalog.json`](config/mock_resource_catalog.json) | CPU/GPU Mock 자원 후보 |
| [`docs/submission/openapi_service_control.yaml`](docs/submission/openapi_service_control.yaml) | OpenAPI 통신 계약 |
| [`go/service-control-api/README.md`](go/service-control-api/README.md) | 상세 API·Runtime Agent 실행 규약 |

## 공식 산출물

| 산출물 | 문서 |
| --- | --- |
| LLM 운영 관리 구조 설계서 | [Markdown](docs/deliverables/01_llm_operation_management_design.md) |
| 에이전트 등록 관리 프로토타입 | [Markdown](docs/deliverables/02_agent_registration_management_prototype.md) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [Markdown](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) |

## License

이 프로젝트는 [Apache License 2.0](LICENSE)에 따라 배포됩니다.
