# geon Agent Dispatcher and Runtime Agent Execution Design

## 목적

geon Agent Registry에 등록한 Agent가 단순히 목록과 호출 계획에만 남지 않고, 검증된 공통 계약을 통해 실제 실행될 수 있도록 `Agent Dispatcher`를 추가한다.

이 변경은 다음 세 역할을 명확히 분리한다.

1. `geon Agent Control`은 요청, Registry 조회, Guard, 실행 추적을 담당하는 공통 프레임워크다.
2. `AIApplicationAutomationAgent`는 AI 응용 요구를 분석하고 `DeploymentManifest`를 생성하는 기본 내부 Agent다.
3. 외부 Runtime Agent는 Registry에 등록된 HTTP endpoint를 통해 자신의 고유 기능을 실행한다.

AppDeploy는 계속해서 최종 VM Target 및 Runtime Adapter 선택과 실제 AI 응용 배포를 담당한다. AppDeploy 저장소와 코드는 변경하지 않는다.

## 현재 문제

현재 Registry는 구성 파일의 내부 Agent와 실행 중 등록한 외부 Agent를 관리한다. 외부 Agent에 대해서는 capability, bounded action, endpoint, invocation path를 검증하고 `AgentInvocationPlan`을 생성하지만 실제 HTTP 요청은 보내지 않는다.

따라서 현재 상태는 다음과 같다.

```text
외부 Agent 등록
  -> capability / bounded action 검증
  -> target URL 생성
  -> execution_status: not_executed
```

또한 Manifest Planner는 `source=configuration`인 내부 Agent만 선택한다. 이는 기본 Planner의 안전 경계에는 적합하지만, Registry가 실제 Agent 실행 진입점으로 보이기에는 부족하다.

## 설계 원칙

- Registry 등록과 Agent 실행은 분리한다.
- 모든 실행은 Dispatcher를 통과한다.
- 호출 전에는 Agent 권한을 검증하고 호출 후에는 결과 계약을 검증한다.
- 내부 Agent와 외부 Agent는 같은 요청 및 결과 envelope를 사용한다.
- Agent 고유 출력은 `result` 내부에 유지하며 모든 Agent에 `DeploymentManifest`를 강제하지 않는다.
- `AIApplicationAutomationAgent`의 최종 고유 출력은 계속 `DeploymentManifest`다.
- 외부 Agent 한 건을 호출하는 기능만 제공하며 다중 Agent 워크플로 엔진은 만들지 않는다.
- 실패를 성공으로 바꾸는 mock fallback을 두지 않는다.
- Credential, token, private key는 Registry 또는 ControlRun에 저장하지 않는다.

## 범위

### 포함

- Agent 실행 공통 요청 및 결과 계약
- 내부 및 외부 Agent를 선택하는 `Agent Dispatcher`
- 기존 Qwen Manifest Planner를 호출하는 내부 Agent Executor
- 등록된 HTTP endpoint를 호출하는 외부 Agent Executor
- 실행 전 capability 및 bounded action 검증
- 실행 후 Agent 이름, action, 상태 및 결과 envelope 검증
- Agent 실행 상태와 결과를 `ControlRun`에 기록
- Agent Control 웹에서 실행 가능한 Agent와 실행 결과 표시
- timeout, payload size, HTTP status, invalid JSON 오류 처리
- 서비스, Dispatcher, Executor, API, 웹 UI 테스트
- README 및 Swagger/OpenAPI 문서

### 제외

- 여러 Agent를 순서대로 연결하는 DAG 또는 워크플로 엔진
- Agent 간 자동 협상, delegation, retry orchestration
- Registry가 외부 Agent 프로세스를 설치하거나 시작하는 기능
- 외부 Agent별 비즈니스 로직 구현
- Agent가 임의의 AppDeploy 또는 클라우드 권한을 획득하는 기능
- AppDeploy 코드 변경
- CB-Tumblebug 또는 CSP 자원 직접 제어
- Kubernetes 및 Container 배포
- 영구 Registry 또는 ControlRun 데이터베이스

## 목표 구조

```text
사용자 또는 외부 Application
  |
  v
geon Agent Control API
  |
  v
Agent Registry Resolver
  - enabled
  - capability
  - bounded action
  |
  v
Go Agent Request Guard
  |
  v
Agent Dispatcher
  |
  +---- source=configuration
  |       |
  |       v
  |     Internal Agent Executor
  |       |
  |       v
  |     AIApplicationAutomationAgent
  |       -> Qwen Planner
  |       -> DeploymentManifest
  |
  +---- source=runtime
          |
          v
        HTTP Agent Executor
          |
          v
        Registered External Agent Endpoint
  |
  v
Go Agent Result Guard
  |
  v
ControlRun 기록 및 결과 반환
```

`DeploymentManifest`를 생성한 경우에는 기존 Go Manifest Guard를 추가로 적용한다. 승인된 Manifest는 사용자가 명시적으로 요청한 경우에만 AppDeploy로 제출한다.

## 컴포넌트

### Agent Registry Resolver

Resolver는 Agent 이름 또는 capability와 action으로 실행 대상을 찾는다.

검증 조건은 다음과 같다.

- Agent가 Registry에 존재한다.
- `enabled == true`다.
- 요청 capability가 Agent의 capabilities에 포함된다.
- 요청 action이 Agent의 bounded actions에 포함된다.
- Runtime Agent는 endpoint와 invocation path를 가진다.
- Configuration Agent는 등록된 내부 Executor 이름과 매핑된다.

같은 capability를 가진 Agent가 여러 개이고 요청에 Agent 이름이 없다면 자동 선택하지 않고 `ambiguous_agent` 오류를 반환한다. 이는 Registry 선언 순서에 따라 다른 Agent가 실행되는 비결정성을 방지한다.

### Agent Dispatcher

Dispatcher는 Agent의 `source`에 따라 Executor를 선택한다.

```go
type AgentExecutor interface {
    Execute(context.Context, AgentExecutionRequest) (AgentExecutionResult, error)
}

type AgentDispatcher interface {
    Dispatch(context.Context, AgentProfile, AgentExecutionRequest) (AgentExecutionResult, error)
}
```

Dispatcher는 Agent의 capability 또는 비즈니스 로직을 직접 구현하지 않는다. Registry 검증을 통과한 Agent를 올바른 Executor에 전달하고 실행 시간을 기록한다.

### Internal Agent Executor

내부 Executor는 configuration Agent 이름과 Go 구현을 명시적으로 매핑한다.

첫 번째 매핑은 다음과 같다.

```text
AIApplicationAutomationAgent
  -> deployment_manifest_planning
  -> existing Qwen deploymentplanner.Generator
  -> existing appdeploy.ValidateManifest
```

기존 ControlRun Manifest 생성 API는 내부적으로 Dispatcher를 사용한다. 따라서 Registry에서 Agent를 선택한 뒤 Qwen을 실행한다는 구조가 코드에서도 명확해진다.

지원하지 않는 configuration Agent 이름은 `internal_executor_not_registered`로 거부한다. Registry에 JSON 항목을 추가하는 것만으로 임의의 내부 코드가 실행되지는 않는다.

### HTTP Agent Executor

외부 Runtime Agent는 Registry의 `endpoint + invocation_path`로 HTTP POST 요청을 받는다.

기본 정책은 다음과 같다.

- 요청 timeout 기본값 30초, 설정 가능한 최댓값 120초
- JSON 요청과 응답만 허용
- 응답 본문 최대 크기 1 MiB
- 2xx 이외의 HTTP 상태는 실행 실패
- redirect 자동 추적 금지
- URL에 user info, query, fragment 금지
- endpoint 또는 invocation path에 Credential 저장 금지
- 환경변수 기반 선택적 bearer token reference만 허용하고 값은 응답과 로그에 기록하지 않음

localhost 및 사설망 endpoint는 연구용 실행에서 필요하므로 일괄 차단하지 않는다. 대신 endpoint 등록 권한을 신뢰된 운영자로 제한하고, 등록된 URL 이외의 동적 URL을 실행 요청에서 받지 않는다.

### Agent Request Guard

실행 전 Guard는 다음을 검증한다.

- `run_id`, agent, capability, action 필수값
- Agent Registry 권한
- input JSON 최대 크기
- Credential 형태의 금지 필드
- action과 capability 문자열 일치
- 실행 요청에서 endpoint override 금지

거부된 요청은 내부 Executor, Qwen, 외부 HTTP endpoint를 호출하지 않는다.

### Agent Result Guard

공통 응답 envelope에 대해 다음을 검증한다.

- request의 `run_id`와 response의 `run_id` 일치
- 선택 Agent와 response Agent 일치
- status가 허용된 enum인지 확인
- proposal action이 Agent bounded actions에 포함되는지 확인
- result와 evidence가 유효한 JSON 값인지 확인
- 응답 크기 제한

`DeploymentManifest` 결과에는 기존 Manifest Guard를 추가로 실행한다. 다른 Agent 고유 결과에는 각 capability에 등록된 결과 Validator가 있으면 적용하고, Validator가 없으면 공통 envelope 검증까지만 통과시킨 뒤 `domain_validation=not_registered`를 명시한다.

## 공통 계약

### 실행 요청

```json
{
  "run_id": "run-001",
  "agent": "JobSchedulingAgent",
  "capability": "job_scheduling",
  "action": "schedule_job",
  "input": {
    "jobs": [],
    "available_resources": {}
  },
  "context": {
    "requested_by": "geon-control"
  }
}
```

### 실행 응답

```json
{
  "run_id": "run-001",
  "agent": "JobSchedulingAgent",
  "status": "completed",
  "proposal": {
    "action": "schedule_job",
    "parameters": {}
  },
  "result": {
    "schedule": []
  },
  "evidence": {},
  "message": "Job schedule generated"
}
```

허용 status는 다음과 같다.

- `completed`
- `rejected`
- `failed`

HTTP 호출이 성공해도 response status가 `rejected` 또는 `failed`면 Dispatcher 결과는 성공으로 승격하지 않는다.

## ControlRun 연결

모든 Agent 실행은 ControlRun에 다음 단계를 기록한다.

```text
AGENT_RESOLVING
AGENT_AUTHORIZED | AGENT_REJECTED
AGENT_DISPATCHING
AGENT_COMPLETED | AGENT_FAILED
RESULT_APPROVED | RESULT_REJECTED
```

ControlRun에는 다음 정보만 보존한다.

- Agent 이름과 source
- capability와 action
- 실행 상태
- 시작 및 종료 시각
- latency
- 안전하게 정제한 input 요약
- Guard 결과
- Agent result envelope

Authorization header, token, endpoint Credential과 민감한 원문은 저장하지 않는다.

기존 Manifest ControlRun은 다음과 같이 확장된다.

```text
REQUEST_APPROVED
  -> AGENT_AUTHORIZED
  -> AGENT_DISPATCHING
  -> AIApplicationAutomationAgent
  -> MANIFEST_GENERATED
  -> MANIFEST_APPROVED
  -> optional AppDeploy submit
```

## API

새 API는 다음과 같다.

```text
POST /api/v1/agents/{name}/execute
```

요청 body는 capability, action, input, context를 포함한다. Agent 이름은 path에서만 받으며 body의 Agent 이름 중복 입력은 허용하지 않는다.

응답은 다음 정보를 포함한다.

```json
{
  "request_id": "req-...",
  "run_id": "run-...",
  "selected_agent": {},
  "request_guard": {},
  "execution": {},
  "result_guard": {},
  "result": {}
}
```

기존 `POST /api/v1/agents/{name}/invocation-plan` API는 호환성과 사전 검토를 위해 유지한다. 실제 호출 여부가 명확하도록 UI와 문서에서는 `호출 계획`과 `Agent 실행`을 구분한다.

기존 ControlRun Manifest API는 URL과 응답 호환성을 유지하면서 내부 Dispatcher를 사용한다.

## 웹 UI

Agents & Guard 화면에 다음을 추가한다.

- Agent별 source 표시: `internal` 또는 `external`
- 실행 가능한 Agent에 `실행` 명령
- capability와 bounded action 선택
- JSON input 편집 영역
- 실행 전 Guard 결과
- 실제 실행 상태: `executing`, `completed`, `rejected`, `failed`
- latency와 result envelope
- 생성된 `run_id`로 ControlRun 상세 이동

Configuration Agent 삭제 기능은 제공하지 않는다. Runtime Agent는 기존처럼 삭제할 수 있다.

Deployment Planner 화면은 사용자 흐름을 유지하되 결과에 다음을 명확히 표시한다.

- Selected Agent: `AIApplicationAutomationAgent`
- Agent Source: `internal`
- Dispatcher Status
- Manifest Guard Status
- 최종 `DeploymentManifest`

## 오류 처리

| 오류 | HTTP 상태 | 의미 |
| --- | --- | --- |
| `agent_not_found` | 404 | Registry에 Agent가 없음 |
| `agent_disabled` | 409 | Agent가 비활성 상태 |
| `agent_not_authorized` | 403 | capability 또는 action이 허용되지 않음 |
| `ambiguous_agent` | 409 | Agent 이름 없이 여러 후보가 일치 |
| `internal_executor_not_registered` | 501 | 내부 Agent 구현 매핑이 없음 |
| `agent_timeout` | 504 | 내부 또는 외부 Agent 실행 시간 초과 |
| `agent_http_error` | 502 | 외부 endpoint가 2xx 이외 응답 |
| `invalid_agent_response` | 502 | JSON 또는 공통 결과 계약 오류 |
| `agent_result_rejected` | 422 | Result Guard가 결과를 거부 |

실패한 실행도 ControlRun에 실패 단계와 안전한 오류 메시지를 기록한다.

## 테스트

### 단위 테스트

- Registry Resolver의 enabled, capability, action 검증
- 같은 capability의 다중 Agent 모호성 거부
- configuration 및 runtime Executor 선택
- 내부 Agent 이름과 Executor 매핑
- HTTP timeout, redirect, non-2xx, invalid JSON, oversized response
- 요청 Credential 필드 거부
- response Agent, run ID, action 불일치 거부
- DeploymentManifest 추가 Guard 실행

### 통합 테스트

- `AIApplicationAutomationAgent`가 Dispatcher를 통해 기존 Qwen Generator를 실행
- Manifest-only ControlRun이 AppDeploy 없이 `MANIFEST_APPROVED` 도달
- `httptest.Server` Runtime Agent 등록 및 실제 HTTP 호출
- Runtime Agent 결과와 latency가 ControlRun에 저장
- 외부 Agent 실패가 성공으로 변환되지 않음
- 기존 Invocation Plan API 호환성

### 웹 테스트

- Agent source와 실행 가능 상태 표시
- JSON input 검증
- 실행 중 중복 제출 방지
- completed, rejected, failed 결과 렌더링
- ControlRun 링크와 삭제 기능 회귀 테스트

## 완료 조건

다음 조건을 모두 만족하면 구현을 완료한 것으로 본다.

1. 기존 `AIApplicationAutomationAgent` Manifest 경로가 Agent Dispatcher를 사용한다.
2. Registry 권한이 없는 Agent 또는 action은 실행 전에 거부된다.
3. 등록한 Runtime Agent endpoint가 실제 HTTP 요청을 받고 공통 응답을 반환할 수 있다.
4. 외부 Agent 응답은 Result Guard를 통과해야 사용자에게 승인 결과로 표시된다.
5. 모든 실행은 하나의 `run_id`로 ControlRun에 추적된다.
6. AppDeploy 없이 Manifest 생성이 가능하고, AppDeploy 실행 책임은 변경되지 않는다.
7. 기존 API와 UI 핵심 흐름이 회귀하지 않는다.
8. `go test ./...`와 `go vet ./...`가 통과한다.

## 연구 범위 설명

이 구현 이후 연구 결과는 다음과 같이 설명한다.

> geon은 Agent Registry에 등록된 Agent의 capability와 bounded action을 검증하고, Agent Dispatcher를 통해 내부 AI 응용 자동화 Agent 또는 외부 Runtime Agent를 실행하며, 실행 결과를 Go Guard와 ControlRun으로 검증 및 추적하는 Agent Control 프레임워크다. 기본 AIApplicationAutomationAgent는 자연어 AI 응용 요구를 Qwen으로 분석해 검증된 DeploymentManifest를 생성하고, 실제 배포는 AppDeploy에 위임한다.

이는 에이전트 등록 관리 프로토타입과 AI 응용 자동화 에이전트 프로토타입을 하나의 실행 가능한 구조로 연결한다. 다중 Agent 워크플로와 범용 Agent 플랫폼은 이번 1차년도 범위에 포함하지 않는다.
