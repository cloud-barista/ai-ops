# Registry-Selected Deployment Decision Agent Design

## 1. 목적

현재 geon의 메인 자동화 흐름은 `AIApplicationAutomationAgent`를 코드에서 고정하여
`DEPLOY`, `REJECT`, `RETRY`를 판단한다. Agent Registry 웹에서 Runtime Agent를
등록할 수 있지만, 등록한 Agent는 메인 자동화 판단에 참여하지 않는다.

이번 변경의 목적은 다음과 같다.

- `AIApplicationAutomationAgent`를 코드에 고정된 필수 Agent가 아니라 Registry에서
  선택 가능한 기본 Internal Agent로 취급한다.
- 같은 capability와 bounded action을 가진 Runtime Agent를 메인 화면에서 선택한다.
- 선택된 Agent가 `DEPLOY`, `REJECT`, `RETRY` 판단을 생성한다.
- 어떤 Agent를 사용하더라도 geon의 Go Guard가 결과를 최종 검증한다.
- 승인된 판단만 `DesiredDeploymentSpec`과 `deployment.create.request` 생성으로 이어진다.

## 2. 범위

### 포함

- Registry의 Internal Agent와 Runtime Agent를 하나의 선택 목록으로 제공
- 자동화 실행 요청에 `decision_agent` 추가
- 선택 Agent의 capability, bounded action, enabled 상태 검증
- Internal Agent는 기존 Go 판단 로직으로 실행
- Runtime Agent는 기존 Agent Dispatcher와 HTTP Executor로 실행
- 공통 배포 판단 결과 계약 정의
- Request Guard, Result Guard, Deployment Decision Guard 적용
- 선택 Agent와 실행 증거를 AutomationRun에 기록
- 메인 웹에 배포 판단 Agent 선택 메뉴 추가
- Registry 등록·삭제가 선택 메뉴에 반영

### 제외

- 여러 Agent의 동시 투표 또는 합의
- Runtime Agent 영구 저장
- Runtime Agent 프로세스의 설치·시작·중지
- Runtime Agent 실패 시 다른 Agent로 자동 전환
- 실제 INNO/ETRI 배포 API 호출
- 실제 VM 생성 또는 Scale-out 실행

## 3. 핵심 개념

### 3.1 Agent 역할

배포 판단 Agent는 다음 입력을 받는다.

- `ApplicationProfile`
- `ResourceRecommendation`
- 동일한 `run_id`, `correlation_id`, `trace_id`

Agent는 다음 결과를 생성한다.

- `DEPLOY`, `REJECT`, `RETRY` 중 하나
- 선택 후보 ID
- 판단 이유
- 신뢰도
- 필요 시 수정 요청

### 3.2 실행 방식

| Source | 실행 방식 | 예시 |
| --- | --- | --- |
| `configuration` | geon 내부 Go Executor | `AIApplicationAutomationAgent` |
| `runtime` | 등록된 Endpoint로 HTTP POST | `RuntimeDeploymentAgent` |

두 Source는 동일한 Registry 권한과 Agent 결과 계약을 사용한다. 차이는 Dispatcher가
선택하는 Executor뿐이다.

## 4. Agent 자격 조건

메인 화면의 배포 판단 Agent 목록에는 다음 조건을 모두 만족하는 Agent만 표시한다.

```text
enabled = true
capability = ai_application_automation
bounded_action = generate_deployment_decision
```

백엔드는 웹의 필터 결과를 신뢰하지 않고 실행 시 같은 조건을 다시 검증한다.

`AIApplicationAutomationAgent`는 Registry 설정에서 기본 Agent로 지정하되 Go 코드에
Agent 이름을 고정하지 않는다. API 클라이언트가 `decision_agent`를 생략하면 Registry의
기본 Agent를 선택한다. 기본 Agent가 없거나 자격 조건을 만족하지 않으면 요청을 거부한다.

Registry 설정에는 capability별 기본 Agent를 다음처럼 명시한다.

```json
{
  "version": "3",
  "defaults": {
    "ai_application_automation": "AIApplicationAutomationAgent"
  },
  "agents": []
}
```

`defaults`는 Agent 이름을 실행 코드에 다시 고정하기 위한 필드가 아니다. 요청이 Agent를
지정하지 않았을 때 Registry가 선택할 정책값이며, 선택 후에도 enabled, capability,
bounded action을 동일하게 검증한다.

## 5. 전체 실행 흐름

```text
사용자 자연어 요청 또는 구조화 App Spec
  -> Requirement Analyzer
  -> ApplicationProfile
  -> Resource Recommender
  -> ResourceRecommendation
  -> Registry에서 decision_agent 조회
  -> Agent Request Guard
  -> Agent Dispatcher
       configuration -> Internal Decision Executor
       runtime       -> HTTP Agent Executor
  -> Agent Result Guard
  -> Deployment Decision Guard
  -> DEPLOY / REJECT / RETRY
       DEPLOY -> DesiredDeploymentSpec -> deployment.create.request -> Adapter
       REJECT -> 수정 이유 기록 후 종료
       RETRY  -> correction_request 기록 후 종료
```

Adapter는 Agent 실행 이후에도 기존처럼 자동 수행하며 별도 버튼을 추가하지 않는다.

`Internal Decision Executor`는 현재 `evaluateFlow`에 들어 있는 규칙 기반 요구사항 검증,
후보 선택, `DEPLOY/REJECT/RETRY` 결정을 Dispatcher가 호출할 수 있는 Executor로 분리한
구성요소다. 이 Executor도 Runtime Agent와 동일한 요청과 결과 계약을 사용한다.

## 6. API 계약

### 6.1 자동화 실행 요청

`POST /api/v1/agent-control/automation-runs`

```json
{
  "input_type": "natural_language",
  "request": "GPU 1개로 추론 서비스를 배포해 주세요.",
  "requested_by": "geon-web",
  "decision_agent": "RuntimeDeploymentAgent"
}
```

`decision_agent`는 선택 필드다. 생략 시 Registry 기본 Agent를 사용한다.

### 6.2 Dispatcher 요청

```json
{
  "run_id": "run-001",
  "agent": "RuntimeDeploymentAgent",
  "capability": "ai_application_automation",
  "action": "generate_deployment_decision",
  "input": {
    "application_profile": {},
    "resource_recommendation": {}
  },
  "context": {
    "correlation_id": "flow-001",
    "trace_id": "trace-001"
  }
}
```

### 6.3 공통 Agent 결과

bounded action과 실제 배포 결정은 서로 다른 필드로 구분한다.

```json
{
  "run_id": "run-001",
  "agent": "RuntimeDeploymentAgent",
  "status": "completed",
  "proposal": {
    "action": "generate_deployment_decision",
    "parameters": {
      "decision": "DEPLOY",
      "selected_candidate_id": "candidate-gpu-01",
      "reason": "GPU와 메모리 요구조건을 만족합니다.",
      "confidence": 0.91
    }
  },
  "domain_validation": "deployment_decision"
}
```

`proposal.action`은 Registry에서 허용된 동작이고, `parameters.decision`은 Agent가 생성한
도메인 판단이다.

## 7. Guard 구조

### 7.1 Agent Request Guard

- 선택 Agent가 Registry에 존재하는지 확인
- Agent가 enabled인지 확인
- capability와 bounded action 확인
- 입력에서 credential, token, endpoint 등 금지 필드 차단

### 7.2 Agent Result Guard

- `run_id`와 Agent 신원 일치 확인
- 상태 값 검증
- 응답 크기와 JSON Content-Type 검증
- 요청 bounded action과 결과 `proposal.action` 일치 확인

### 7.3 Deployment Decision Guard

- 결정이 `DEPLOY`, `REJECT`, `RETRY` 중 하나인지 확인
- 선택 후보가 ResourceRecommendation에 포함되는지 확인
- 선택 후보가 ApplicationProfile 최소 자원을 만족하는지 확인
- 신뢰도가 0 이상 1 이하인지 확인
- `DEPLOY`에 선택 후보가 반드시 존재하는지 확인
- `RETRY`와 `REJECT`에 수정 이유가 존재하는지 확인

Go Guard 승인 전에는 `DesiredDeploymentSpec`, `deployment.create.request`, Adapter 증거를
생성하지 않는다.

## 8. 실패 처리

| 상황 | 결과 |
| --- | --- |
| Agent 미등록·비활성·권한 없음 | `AGENT_AUTHORIZATION_REJECTED` |
| Runtime Endpoint 연결 실패·Timeout | `AGENT_EXECUTION_FAILED` |
| Agent 응답 계약 오류 | `AGENT_RESULT_REJECTED` |
| 자원 후보 불일치 | `RETRY_REQUIRED` |
| 유효한 `REJECT` | 수정 이유를 저장하고 종료 |

Runtime Agent가 실패해도 Internal Agent로 자동 전환하지 않는다. 실행 결과에는 선택 Agent,
Source, Endpoint 호출 여부, 지연시간, Guard 상태를 기록한다. 인증 토큰과 내부 오류 원문은
응답 및 저장 증거에서 제외한다.

## 9. 웹 UI

메인 메뉴는 세 개로 유지한다.

1. `자동화 실행`
2. `Agent Registry`
3. `실행 기록`

### 9.1 자동화 실행

- 자연어 요청 / 구조화 App Spec 입력
- `배포 판단 Agent` 선택 메뉴
- 선택 Agent의 `internal` 또는 `runtime` Source 표시
- `자동 분석 및 판단` 버튼 하나로 전체 흐름 실행
- 결과에 선택 Agent, Agent 실행 상태, 결정, Guard, Desired Spec, Adapter 표시

### 9.2 Agent Registry

- 설정 Agent와 Runtime Agent 목록 표시
- capability, bounded action, enabled, Source 표시
- Runtime Agent 등록·삭제
- 등록 성공 시 자동화 실행 화면의 선택 목록 갱신
- 삭제된 Runtime Agent는 선택 목록에서 제거

Runtime Agent가 배포 판단 자격 조건을 만족하지 않으면 Registry에는 표시되지만 메인 선택
메뉴에는 나타나지 않는다.

### 9.3 실행 기록

각 AutomationRun에 다음 증거를 표시한다.

- 요청한 `decision_agent`
- 실제 선택 Agent와 Source
- Dispatch 상태와 지연시간
- Agent 원본 결과의 안전한 요약
- Request Guard, Result Guard, Decision Guard
- 최종 결정과 DesiredDeploymentSpec
- Adapter 상태

## 10. 데이터 보존과 재시작

- 설정 Agent는 `config/agent_registry.json`에서 유지한다.
- Runtime Agent는 현재와 같이 프로세스 메모리에만 저장한다.
- 서버 재시작 후 Runtime Agent는 다시 등록해야 한다.
- Runtime Agent 영구 저장은 이번 범위에서 제외한다.

## 11. 호환성

- 기존 API 요청에서 `decision_agent`를 생략할 수 있다.
- Registry 기본 Agent가 기존 `AIApplicationAutomationAgent`와 같은 판단 결과를 생성해야 한다.
- 기존 `run_id`, `correlation_id`, `trace_id` 연결을 유지한다.
- 기존 Mock/Handoff Adapter 계약을 변경하지 않는다.
- AppDeploy 코드는 수정하지 않는다.

## 12. 검증 기준

1. Internal Agent를 선택하면 기존 `DEPLOY/REJECT/RETRY` 결과가 유지된다.
2. 자격을 갖춘 Runtime Agent 등록 후 메인 선택 목록에 나타난다.
3. 자격이 없는 Runtime Agent는 선택 목록에 나타나지 않는다.
4. Runtime Agent 선택 시 등록 Endpoint가 정확히 한 번 호출된다.
5. 선택 Agent의 결정이 Go Guard를 통과해야 DesiredDeploymentSpec이 생성된다.
6. Runtime Agent Timeout 시 자동 fallback 없이 실패 증거가 기록된다.
7. 잘못된 후보 ID와 허용되지 않은 결정은 Result/Decision Guard가 거부한다.
8. Runtime Agent 삭제 후 메인 선택 목록에서 사라진다.
9. 기존 API 클라이언트는 Registry 기본 Agent로 동작한다.
10. 데스크톱과 모바일에서 Agent 선택·결과가 겹치거나 잘리지 않는다.

## 13. 완료 정의

다음 시연이 가능하면 완료다.

```text
RuntimeDeploymentAgent 등록
-> 자동화 실행 화면에서 RuntimeDeploymentAgent 선택
-> 자연어 요청 입력
-> ApplicationProfile과 ResourceRecommendation 자동 생성
-> RuntimeDeploymentAgent Endpoint 실제 호출
-> DEPLOY/REJECT/RETRY 결과 수신
-> Go Guard 검증
-> 승인된 경우 DesiredDeploymentSpec과 Mock Adapter 증거 생성
-> 같은 run_id에서 전체 실행 증거 확인
```
