# geon ControlRun Manifest Pipeline Design

## 목적

geon Agent Control의 핵심 산출물을 **Agent Registry 정책에 따라 생성되고 Go Guard로 검증된 `DeploymentManifest`**로 명확히 한다.

현재 Deployment Planner, Agents & Guard, Autonomous Loop, Feedback 화면은 각각 기능을 제공하지만 동일한 실행 건을 공유하지 않는다. 이번 변경은 자연어 요청부터 Manifest 승인까지를 하나의 `ControlRun`으로 연결하고, 선택적으로 AppDeploy 배포 결과와 배포 후 운영 기록을 같은 Run에 연결한다.

geon은 AppDeploy의 VM Target 선택과 Runtime Adapter 선택, 실제 배포 실행을 대신하지 않는다. AppDeploy 저장소와 코드는 변경하지 않는다.

## geon의 역할

geon은 다음 역할을 담당하는 AI 운영 Control Plane이다.

1. 자연어 AI 응용 배포 요청을 받는다.
2. 요청을 Go Request Guard로 검증한다.
3. Agent Registry에서 Manifest 생성 권한을 가진 내부 Planner Agent를 선택한다.
4. Qwen Planner를 실행해 AppDeploy 계약에 맞는 `DeploymentManifest`를 생성한다.
5. 생성 결과를 Go Manifest Guard로 검증한다.
6. 검증된 Manifest를 최종 산출물로 반환한다.
7. 사용자가 요청한 경우에만 Manifest를 AppDeploy로 전달하고 배포 상태를 추적한다.
8. 배포 이후 Autonomous Loop와 Feedback을 동일한 Run에 선택적으로 연결한다.

`AIApplicationAutomationAgent`는 시스템 자체가 아니라 Agent Registry에 등록된 기본 내부 Agent 프로필이다. 다른 내부 Agent가 동일한 capability와 bounded action을 제공하면 동일한 Manifest 생성 파이프라인에 선택될 수 있어야 한다.

## 범위

### 포함

- 동시성 안전 인메모리 `ControlRunStore`
- 자연어 요청부터 Manifest 승인까지의 단계별 상태 기록
- Agent Registry 기반 내부 Planner Agent 선택 및 권한 검증
- AppDeploy 없이 실행 가능한 Manifest 생성 API
- 기존 AppDeploy 배포 API와의 호환 연결
- 선택적 AppDeploy 제출 및 배포 결과 연결
- 배포된 Run과 Autonomous Loop 설정의 연결
- Action Proposal과 Feedback의 `run_id` 연결
- 백엔드 ControlRun을 사용하는 Overview와 Planner 화면
- geon이 생성한 ControlRun 개별 삭제 및 전체 삭제
- API, 서비스, 저장소, 웹 UI 테스트
- README와 Swagger/OpenAPI 문서

### 제외

- 외부 Agent Endpoint 실제 HTTP 호출
- 여러 외부 Agent의 순서, 재시도, 보상 처리를 수행하는 워크플로 엔진
- AppDeploy 코드 변경
- CB-Tumblebug 또는 CSP 자원 직접 제어
- Kubernetes와 Container 배포
- 영구 데이터베이스
- Autonomous Loop를 Manifest 생성의 필수 단계로 만드는 변경

## 전체 흐름

### 핵심 Manifest 생성 흐름

```text
사용자 요청
  |
  v
ControlRun 생성
  |
  v
Go Request Guard
  |
  v
Agent Registry Resolver
  - enabled
  - capability: deployment_manifest_planning
  - bounded action: generate_deployment_manifest
  |
  v
Qwen Planner
  |
  v
DeploymentManifest 초안
  |
  v
Go Manifest Guard
  |
  v
MANIFEST_APPROVED
  |
  +----> 최종 Manifest 반환
  |
  +----> 선택적 AppDeploy 제출
```

### 선택적 배포 후 흐름

```text
MANIFEST_APPROVED
  |
  v
submit_deployment_manifest 권한 재검증
  |
  v
AppDeploy
  |
  v
deployment_id를 ControlRun에 연결
  |
  +----> 선택적 Autonomous Loop
  |       AppDeploy metrics -> SLO -> Qwen Action
  |       -> Agent Registry -> Go Guard -> AppDeploy control
  |
  +----> Action Proposal / Feedback를 같은 run_id에 기록
```

## 컴포넌트

### ControlRunStore

새 `internal/controlrun` 패키지가 Run 모델과 저장소를 소유한다.

저장소는 `sync.RWMutex`로 보호하고 현재 runtime Agent, Feedback, Autonomous Event 저장 방식과 동일하게 서버 프로세스 메모리에 보관한다. 브라우저 새로고침에는 유지되지만 서버 재시작 시 초기화된다. 영구 저장은 이번 범위에 포함하지 않는다.

저장소 인터페이스는 다음 동작을 제공한다.

```go
type Store interface {
    Create(input CreateInput) Run
    Get(runID string) (Run, bool)
    List() []Run
    Update(runID string, mutate func(*Run) error) (Run, error)
    Delete(runID string) (Run, bool)
    Clear() int
}
```

반환값은 호출자가 내부 slice 또는 map을 변경할 수 없도록 복사한다.

### Agent Registry Resolver

Manifest Planner는 특정 Agent 이름을 하드코딩하지 않는다. Resolver는 구성 파일에 등록된 내부 Agent 중 다음 조건을 모두 만족하는 Agent를 선택한다.

- `enabled == true`
- capability에 `deployment_manifest_planning` 포함
- bounded action에 `generate_deployment_manifest` 포함
- 외부 runtime endpoint 호출이 필요하지 않은 configuration Agent

요청에 `agent_name`이 있으면 해당 Agent만 검증한다. 없으면 Registry의 선언 순서에서 첫 번째 적합한 Agent를 선택한다. 선택 결과와 선택 이유를 ControlRun에 기록한다.

AppDeploy 제출 전에는 같은 Agent가 `submit_deployment_manifest` bounded action을 가지는지 다시 검증한다.

### Request Guard

기존 `internal/plannerguard`를 그대로 사용한다.

Request Guard는 LLM 호출 전에 다음을 검증한다.

- 필수 필드
- 요청자 allowlist
- 요청 길이
- 1차년도 VM-only 범위
- Credential 형태의 민감한 파라미터

거부된 요청은 Qwen과 AppDeploy를 호출하지 않는다. Run에는 원문 Credential이나 민감한 parameters를 저장하지 않고 Guard 결과와 안전한 식별자만 저장한다.

### Qwen Manifest Generator

기존 `deploymentplanner.Generator.Generate`를 Manifest 생성 전용 핵심으로 유지한다.

Generator는 다음을 수행한다.

- 자연어 요구에서 CPU, memory, GPU, storage, accelerator 요구량 결정
- 신뢰 필드인 `app_version_id`, `target_profile_id`, `requested_by` 보존
- Target, Runtime Adapter, Credential, Endpoint, Command를 생성하지 않음
- JSON `DeploymentManifest` 한 개만 출력

### Manifest Guard

기존 `appdeploy.ValidateManifest`를 사용한다.

다음 조건을 검증한다.

- schema version과 kind
- 신뢰 필드 일치
- CPU, memory, GPU, storage 형식
- accelerator와 GPU 값의 일관성
- 허용되지 않은 필드
- Credential 형태의 parameters

승인된 Manifest만 `MANIFEST_APPROVED` 상태로 반환한다.

### AppDeploy Submitter

Manifest 생성과 AppDeploy 제출을 분리한다.

- Manifest 생성은 AppDeploy가 실행 중이지 않아도 성공할 수 있다.
- 제출은 명시적인 submit 요청에서만 실행한다.
- 제출 전 Registry의 `submit_deployment_manifest` 권한을 확인한다.
- AppDeploy가 반환한 `deployment_id`, 상태, polling 결과, 로그를 ControlRun에 기록한다.
- AppDeploy 오류는 승인된 Manifest를 삭제하거나 무효화하지 않는다. Run 상태는 `APPDEPLOY_FAILED`가 되며 Manifest 승인 기록을 유지한다.

기존 `POST /api/v1/planner/deployments`는 호환성을 위해 유지한다. 내부적으로 ControlRun 생성과 Manifest 생성 후 AppDeploy 제출을 연속 호출하고 기존 응답 필드에 `run_id`를 추가한다.

### Autonomous Loop 연결

Autonomous Loop는 선택적 배포 후 기능이다.

- `deployment_id`가 있는 ControlRun만 연결 가능하다.
- Planner 성공 시 Loop를 자동 시작하지 않는다.
- UI의 `이 배포 감시` 명령이 Run의 `deployment_id`를 Autonomy 설정에 복사한다.
- Autonomy Event에는 가능한 경우 `run_id`를 기록한다.
- Loop의 Qwen Action과 Registry/Guard 결과는 Manifest 생성 상태를 변경하지 않는다.

### Feedback 연결

Action Proposal 요청은 선택적으로 `run_id`를 받는다.

- 존재하는 Run이면 Action Proposal correlation ID를 Run에 추가한다.
- Feedback은 correlation ID를 통해 Run에 연결한다.
- Feedback은 이번 범위에서 기록과 추적에 사용한다.
- Feedback을 Qwen 재학습 또는 모델 파라미터 업데이트에 사용하지 않는다.

## 데이터 모델

```go
type Status string

const (
    StatusReceived          Status = "RECEIVED"
    StatusRequestRejected   Status = "REQUEST_REJECTED"
    StatusAgentRejected     Status = "AGENT_REJECTED"
    StatusPlanning          Status = "PLANNING"
    StatusManifestRejected  Status = "MANIFEST_REJECTED"
    StatusManifestApproved  Status = "MANIFEST_APPROVED"
    StatusSubmitting        Status = "SUBMITTING"
    StatusAppDeployFailed   Status = "APPDEPLOY_FAILED"
    StatusDeployed          Status = "DEPLOYED"
)

type Stage struct {
    Name      string         `json:"name"`
    Status    string         `json:"status"`
    Reason    string         `json:"reason,omitempty"`
    StartedAt time.Time      `json:"started_at"`
    EndedAt   *time.Time     `json:"ended_at,omitempty"`
    Details   map[string]any `json:"details,omitempty"`
}

type Run struct {
    RunID             string                         `json:"run_id"`
    Status            Status                         `json:"status"`
    CreatedAt         time.Time                      `json:"created_at"`
    UpdatedAt         time.Time                      `json:"updated_at"`
    Request           SafeRequest                    `json:"request"`
    RequestGuard      plannerguard.Decision          `json:"request_guard"`
    SelectedAgent     AgentSelection                 `json:"selected_agent"`
    Generation        deploymentplanner.GenerateResult `json:"generation"`
    Manifest          appdeploy.DeploymentManifest   `json:"manifest"`
    Deployment        *appdeploy.DeploymentResponse  `json:"deployment,omitempty"`
    Polling           *deploymentplanner.PollingResult `json:"polling,omitempty"`
    CorrelationIDs    []string                       `json:"correlation_ids,omitempty"`
    Stages            []Stage                        `json:"stages"`
}
```

`SafeRequest`는 자연어 요청, app version ID, target hint, candidate ID, requester를 포함한다. 민감한 parameters는 저장하지 않는다.

## REST API

### Manifest 생성

```http
POST /api/v1/control-runs
Content-Type: application/json
```

```json
{
  "natural_language_request": "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요.",
  "app_version_id": "appver-...",
  "candidate_id": "qwen3.5-ops-planner",
  "requested_by": "ai-ops-geon-control-app",
  "agent_name": "AIApplicationAutomationAgent",
  "target_profile_id": ""
}
```

성공 응답은 `201 Created`이다.

```json
{
  "run_id": "run-...",
  "status": "MANIFEST_APPROVED",
  "request_guard": {
    "valid": true,
    "status": "Approved"
  },
  "selected_agent": {
    "name": "AIApplicationAutomationAgent",
    "capability": "deployment_manifest_planning",
    "action": "generate_deployment_manifest"
  },
  "manifest": {
    "schema_version": "deployment.khu.ai/v1alpha1",
    "kind": "DeploymentManifest",
    "spec": {
      "app_version_id": "appver-...",
      "accelerator": "nvidia",
      "resources": {
        "cpu": "4",
        "memory": "16Gi",
        "gpu": "1",
        "storage": "20Gi"
      }
    }
  },
  "stages": []
}
```

Guard 거부도 Run을 반환한다. HTTP 상태는 요청 거부 `400`, Agent 권한 거부 `403`, Manifest 거부 `422`를 사용한다.

### AppDeploy 제출

```http
POST /api/v1/control-runs/{run_id}/submit
```

`MANIFEST_APPROVED` 또는 재시도 가능한 `APPDEPLOY_FAILED` Run만 제출할 수 있다.

### 조회와 삭제

```text
GET    /api/v1/control-runs
GET    /api/v1/control-runs/{run_id}
DELETE /api/v1/control-runs/{run_id}
DELETE /api/v1/control-runs
```

삭제는 geon 메모리의 ControlRun만 제거한다. AppDeploy deployment, App, Target, VM은 삭제하지 않는다.

## Web UI

### Overview

- 브라우저 `localStorage`가 아닌 `GET /api/v1/control-runs`를 사용한다.
- 최신 Run의 상태, 선택 Agent, Guard 결과, Manifest 상태, deployment ID를 표시한다.
- 각 Run을 열어 전체 단계 Timeline을 확인한다.

### Deployment Planner

- 기본 명령은 `Generate Manifest`이다.
- Manifest 승인 후 `Submit to AppDeploy` 명령을 활성화한다.
- 기존 `Plan & Deploy` 흐름은 호환 명령으로 유지할 수 있지만 내부적으로 두 API를 순서대로 호출한다.
- 단계별 상태는 `Request Guard`, `Agent Registry`, `Qwen`, `Manifest Guard`, `AppDeploy` 순서로 표시한다.
- 최종 Manifest JSON 복사 기능을 제공한다.

### Agents & Guard

- Registry 목록에서 Manifest Planner 자격 여부를 표시한다.
- 현재 Run에서 선택된 Agent와 capability, bounded action 검증 결과를 표시한다.
- Action Proposal 실험은 Manifest 생성과 구분된 배포 후 운영 경로로 유지한다.

### Autonomous Loop

- 제목과 설명을 `배포 후 자율 운영 실험`으로 명확히 한다.
- deployment ID가 있는 Run을 선택할 수 있다.
- Run 선택은 설정값을 채우지만 Loop를 자동 시작하지 않는다.

### Feedback

- `run_id`, correlation ID, executor, 상태를 함께 표시한다.
- Run 상세 화면에서도 연결된 Feedback을 확인할 수 있다.

## 오류 처리

- Request Guard 거부: Run 상태 `REQUEST_REJECTED`, Qwen 미호출
- Agent 미등록 또는 권한 부족: `AGENT_REJECTED`, Qwen 미호출
- Qwen 오류 또는 JSON 파싱 오류: `MANIFEST_REJECTED`
- Manifest Guard 거부: `MANIFEST_REJECTED`, AppDeploy 미호출
- AppDeploy 미설정: Manifest 생성은 성공, 제출만 실패
- AppDeploy 일시 오류: `APPDEPLOY_FAILED`, 재시도 정보 보존
- 존재하지 않는 Run: `404`
- 올바르지 않은 상태 전이: `409`

오류 응답과 Stage에는 Credential, private key, token 원문을 포함하지 않는다.

## 상태 전이

```text
RECEIVED
  -> REQUEST_REJECTED
  -> AGENT_REJECTED
  -> PLANNING
       -> MANIFEST_REJECTED
       -> MANIFEST_APPROVED
            -> SUBMITTING
                 -> APPDEPLOY_FAILED
                 -> DEPLOYED
```

`APPDEPLOY_FAILED -> SUBMITTING` 재시도를 허용한다. `REQUEST_REJECTED`, `AGENT_REJECTED`, `MANIFEST_REJECTED` Run은 동일 Run에서 재실행하지 않고 새 Run을 생성한다.

## 테스트 전략

### 단위 테스트

- Store 생성, 조회, 정렬, 복사, 갱신, 삭제, 동시 접근
- Agent 자동 선택과 명시적 선택
- disabled Agent 거부
- capability 누락 거부
- bounded action 누락 거부
- Request Guard 거부 시 Qwen 미호출
- Manifest Guard 거부 시 AppDeploy 미호출
- Manifest 승인 후 AppDeploy 없이 성공
- 제출 권한 검증
- 상태 전이 위반 거부
- Feedback correlation ID와 Run 연결

### API 테스트

- ControlRun 생성, 조회, 목록, 삭제
- Guard별 HTTP 상태 코드
- Manifest 생성 후 제출
- 기존 `/api/v1/planner/deployments` 호환 응답과 `run_id`
- AppDeploy 미설정 상태에서도 Manifest 생성 성공
- Swagger route와 schema 노출

### Web UI 테스트

- 모든 사이드 메뉴가 동일한 backend Run을 표시
- Manifest 생성 후 Run Timeline 갱신
- 승인 전 Submit 비활성화
- 배포된 Run을 Autonomous Loop 양식에 연결
- 삭제 후 Overview와 상세 화면 갱신
- 브라우저 새로고침 후 서버 Run 재조회

## 성공 기준

1. AppDeploy 없이 자연어 요청에서 검증된 DeploymentManifest를 생성할 수 있다.
2. Planner 실행 전에 Request Guard와 Agent Registry 권한 검증이 모두 수행된다.
3. 모든 Manifest 생성 단계가 하나의 `run_id`로 조회된다.
4. 승인되지 않은 Agent 또는 Manifest는 Qwen 이후 단계나 AppDeploy로 전달되지 않는다.
5. 승인된 Manifest를 명시적으로 AppDeploy에 제출할 수 있다.
6. 배포 결과와 선택적 Autonomous Loop, Feedback이 같은 Run에 연결된다.
7. Overview는 브라우저 로컬 기록이 아니라 backend ControlRun을 표시한다.
8. 기존 AppDeploy 배포 API와 CLI 사용자는 호환성을 유지한다.
9. AppDeploy 저장소에는 변경이 없다.
