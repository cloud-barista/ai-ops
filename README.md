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

The two core Agents and their bounded outputs are:

```text
Request -> AIApplicationAutomationAgent -> Guards -> DesiredDeploymentSpec
Deployment Feedback -> OperationOptimizationAgent -> Guards -> scaling recommendation
```

Guards are outside both Agents. `DesiredDeploymentSpec` is the approved deployment-planning output; `KEEP`, `SCALE_OUT`, and `SCALE_IN` are recommendations only and never execute VM control or AppDeploy.

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

웹 실험은 **배포 판단 실험**과 **배포 후 운영 최적화 실험**을 하나의 Flow로 연결합니다.

```text
자연어 요청 또는 App Spec
→ 요구사항 분석
→ 인프라 추천
→ 배포 판단 Agent + Go Guard
→ Manifest Revision 1
→ 배포 상태 전송
→ 성능 Feedback 전송
→ OperationOptimizationAgent + Scaling Guard
→ Manifest Revision 2
```

### 1. 배포 판단과 Manifest Revision 1

1. `자동화 에이전트` 화면에서 **자연어 요청** 또는 **구조화 App Spec**을 선택합니다.
2. 배포 판단 Agent를 선택합니다. 기본값은 `AIApplicationAutomationAgent (Internal)`입니다.
3. 자연어 요청 또는 App Spec을 입력하고 **자동 분석 및 판단**을 누릅니다.
4. 다음 단계가 서버에서 자동으로 실행됩니다.
   - Requirement Analyzer → `ApplicationProfile`
   - Mock Resource Recommender → `ResourceRecommendation`
   - Agent Registry 권한 확인
   - 선택 Agent의 `DEPLOY / REJECT / RETRY` 판단
   - Request Guard와 Result Guard 검증
5. 실행이 끝나면 `실험 결과` 화면으로 이동합니다.
6. `Manifest Revision 1`에서 최초 배포용 `DesiredDeploymentSpec`을 확인합니다.

Revision 1은 **최초 배포 판단 결과**입니다. 이 단계에서는 아직 배포 상태나 성능 Feedback을 보내지 않습니다.

### 2. 배포 상태 전송

1. `실험 결과`에서 방금 생성된 Flow를 선택합니다.
2. `배포 상태 전송` 영역의 `deployment.status.changed JSON`을 확인합니다.
3. 실제 외부 시스템에서 받은 메시지를 넣거나 **SLO 위반 샘플** 버튼으로 시험 데이터를 불러옵니다.
4. **2. 배포 상태 전송**을 누릅니다.

이 단계는 배포 실행 자체가 아니라, 공통 JSON 형식의 배포 결과를 geon에 전달하는 단계입니다. 상태가 저장되면 다음 Feedback 단계가 활성화됩니다.

### 3. 성능 Feedback 전송과 운영 최적화

1. `optimization.feedback.created JSON`을 확인합니다.
2. 필요하면 SLO 위반 샘플을 사용합니다.
3. **3. 성능 Feedback 전송**을 누릅니다.

Feedback이 접수되면 다음 처리가 자동으로 이어집니다.

```text
OperationOptimizationAgent
→ Request Guard
→ Result Guard
→ Scaling Guard
→ KEEP / SCALE_OUT / SCALE_IN
```

별도의 운영 최적화 실행 버튼을 다시 누르는 방식이 아닙니다. Feedback 전송이 운영 최적화 Agent 실행의 입력이 됩니다.

### 4. Manifest Revision 2 확인

운영 최적화 판단이 승인되면 `Manifest Revision 2`가 생성됩니다.

- `KEEP`: 기존 배포 요구 스펙 유지
- `SCALE_OUT`: Replica 증가를 반영한 최적화 Manifest
- `SCALE_IN`: Replica 감소를 반영한 최적화 Manifest

따라서 최종 시연 결과는 다음 두 개입니다.

| 결과 | 의미 |
| --- | --- |
| `Manifest Revision 1` | 최초 배포를 위한 승인된 `DesiredDeploymentSpec` |
| `Manifest Revision 2` | Feedback과 Scaling Guard 결과를 반영한 최적화 `DesiredDeploymentSpec` |

`deployment.status.changed`와 `optimization.feedback.created`는 Manifest가 아닙니다. 두 JSON은 배포 결과와 성능 관측을 전달하는 통신 메시지입니다. `전체 Flow 실행 기록`도 감사용 기록이며 Manifest가 아닙니다.

### 5. Agent 및 정책

기본 `AIApplicationAutomationAgent (Internal)`은 별도 Agent 서버 없이 실행됩니다.

배포 판단에 사용할 Agent의 상태, capability와 bounded action을 관리합니다.

```text
required capability: ai_application_automation
required action:     generate_deployment_decision
```

Runtime Agent는 Registry에 등록하는 것만으로 실행되지 않습니다. 선택하려면 등록한 `endpoint + invocation_path`에서 응답하는 별도 HTTP Agent 서버가 실행 중이어야 합니다.

### 6. 실험 결과

동일한 Flow에서 다음 증거를 확인합니다.

- 선택 Agent와 실행 상태
- `DEPLOY`, `REJECT`, `RETRY` 판단
- Request, Result, Domain Guard 결과
- `DesiredDeploymentSpec`
- Adapter 전달 상태
- 배포 상태와 성능 Feedback
- `OperationOptimizationAgent`와 Scaling Guard 결과
- `Manifest Revision 1`과 `Manifest Revision 2`

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

## 선택적 추론 방식 비교

`추론 방식 비교`는 핵심 배포 Flow에 필요한 단계가 아니라, 선택적으로 실행하는 평가 기능입니다. 선택한 Flow에서 비교 실행을 누르면 다음 세 경로를 비교합니다.

```text
규칙 기반 판단
Qwen 원시 제안
Qwen 제안 + Go Guard 검증
```

비교 결과에는 실행 여부, Action, 후보 자원, Guard 상태, 지연시간과 판단 일치 여부가 기록됩니다. 기본 설정의 Qwen 후보는 비활성화되어 있으므로, Ollama를 연결하지 않으면 Qwen 경로가 `provider_unavailable` 또는 `skipped`로 기록됩니다. 이 경우 핵심 배포 판단은 정상적으로 실행되지만, Qwen 비교 실험은 완료된 것으로 해석하면 안 됩니다.

```bash
ollama pull qwen3.5:4b
ollama list
```

Git Bash에서 Qwen 비교를 실제 실행하려면 Ollama를 켠 뒤 다음 설정으로 서버를 다시 시작합니다.

```bash
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
./run-agent-control.sh
```

서버를 다시 시작한 뒤 새 Flow를 생성하고 `실험 결과 → 추론 방식 비교 → 비교 실행`을 누릅니다.

## 고급 프로토콜 검증

`고급 프로토콜 검증`은 외부 팀과 합의한 **Common JSON v1.0 통신 형식을 수동으로 시험하는 개발자용 기능**입니다. Application Context와 Resource Recommendation 메시지를 직접 입력해 계약 형식과 Flow 연결을 확인할 때 사용합니다.

일반적인 교수님 시연에서는 `자동화 에이전트`의 자연어 요청부터 시작하는 기본 Flow만 사용하면 됩니다. 고급 프로토콜 검증은 통신 계약 증거가 필요할 때 별도로 보여줍니다.

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
