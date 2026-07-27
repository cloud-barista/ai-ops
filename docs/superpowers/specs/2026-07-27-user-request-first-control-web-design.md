# geon 사용자 요청 중심 Control Web 및 자동 Feedback 설계

## 목적

geon Agent Control 웹의 화면 순서를 실제 핵심 처리 순서와 일치시킨다.

현재 백엔드의 핵심 Manifest 흐름은 다음과 같다.

```text
사용자 요청
  -> ControlRun 생성
  -> Go Request Guard
  -> Agent Registry
  -> Agent Dispatcher
  -> AIApplicationAutomationAgent
  -> Qwen Planner
  -> DeploymentManifest 초안
  -> Go Manifest Guard
  -> 승인된 DeploymentManifest
  -> 선택적 AppDeploy 제출
```

그러나 현재 웹은 `Overview -> Agents & Guard -> Deployment Planner -> Autonomous Loop -> Feedback` 순서로 구성되어 있다. 이 구성은 Agent Registry가 사용자 요청보다 먼저 실행되는 것처럼 보이게 하고, Manifest 생성과 배포 후 운영 기능의 경계를 흐리게 한다.

이번 변경은 다음 원칙을 적용한다.

1. 웹 접속 시 사용자 요청을 입력하는 Manifest Workflow를 첫 화면으로 표시한다.
2. 하나의 `run_id`로 요청, Guard, Agent 선택, Qwen, Manifest, AppDeploy, 운영 결과를 연결한다.
3. Feedback은 별도의 수동 입력을 요구하지 않고 기존 ControlRun 실행 근거에서 자동으로 구성한다.
4. Agent Registry는 핵심 흐름의 내부 단계이자 관리 화면으로 유지한다.
5. Autonomous Loop는 Manifest 생성 이후의 선택적 배포 후 실험으로 명확히 분리한다.
6. AppDeploy 코드와 책임 범위는 변경하지 않는다.

## 범위

### 포함

- Manifest Workflow를 기본 진입 화면으로 변경
- 메뉴와 Overview 바로가기 순서 재구성
- 사용자 요청부터 Manifest Guard까지의 단계형 진행 표시
- `ControlRun` 선택과 결과 화면의 자동 동기화
- 승인된 Manifest에만 AppDeploy 제출 버튼 활성화
- 같은 `run_id`에 연결된 실행 결과를 Feedback 화면에서 자동 집계
- Agent Registry 화면에서 현재 Run이 선택한 Agent 강조
- 배포되지 않은 Run에 대한 Autonomous Loop 실행 방지와 이유 표시
- 기존 외부 실행기 Feedback 입력을 고급 시험 기능으로 재분류
- 데스크톱과 모바일 웹 검증
- 웹 정적 계약 테스트와 관련 API 회귀 테스트
- README의 실행 및 실험 순서 갱신

### 제외

- AppDeploy 코드 변경
- 새로운 AppDeploy 배포 API 개발
- 외부 Agent를 여러 단계로 연결하는 범용 워크플로 엔진
- Qwen 온라인 재학습 또는 자동 파인튜닝
- 영구 데이터베이스
- Feedback 데이터의 장기 보존
- Autonomous Loop를 Manifest 생성의 필수 단계로 변경

## 사용자 경험 구조

### 기본 진입 화면

웹 루트 `/`는 `Manifest Workflow` 화면을 기본 활성 화면으로 표시한다.

화면은 다음 세 영역으로 구성한다.

1. **사용자 요청**
   - 자연어 배포 요구
   - `app_version_id`
   - LLM Candidate
   - 선택적 Target Hint
   - `Generate Manifest` 명령
2. **ControlRun 진행 상태**
   - Request Guard
   - Agent Registry
   - Agent Dispatcher
   - Qwen Planner
   - Manifest Guard
3. **결과**
   - Run 상태
   - 선택 Agent와 실제 모델
   - Guard 승인·거절 이유
   - 승인된 `DeploymentManifest`
   - 선택적 `Submit to AppDeploy` 명령

사용자는 첫 화면에서 요청을 입력하고, 같은 화면에서 모든 내부 처리 단계와 최종 Manifest를 확인한다.

### 메뉴 순서

주 메뉴는 사용자 작업 순서를 기준으로 다음과 같이 재배치한다.

```text
1. Manifest Workflow
2. Agents & Guard
3. Post-deployment
4. Feedback
5. Guide
```

- `Manifest Workflow`는 핵심 실험 시작점이다.
- `Agents & Guard`는 Agent 등록, capability, bounded action, 실행 계약을 관리한다.
- `Post-deployment`는 AppDeploy 배포가 완료된 Run에만 사용하는 선택 실험이다.
- `Feedback`은 모든 Run의 자동 실행 결과와 외부 실행기 콜백을 확인한다.
- `Guide`는 구성요소 설명과 전체 실험 순서를 제공한다.

메뉴 순서는 내부 함수 호출 순서를 그대로 복제하지 않는다. 내부 단계는 Manifest Workflow의 단계 표시에서 정확히 표현하고, 메뉴는 사용자가 수행하는 주요 작업을 기준으로 구성한다.

## ControlRun 연결

### 식별자

사용자 요청을 제출하면 백엔드가 `run_id`를 생성한다. 이후 모든 화면은 이 값을 공통 상관관계 식별자로 사용한다.

```text
run_id
  -> request
  -> request_guard
  -> selected_agent
  -> agent execution
  -> qwen generation
  -> manifest_guard
  -> manifest
  -> deployment
  -> logs
  -> correlation_ids
  -> autonomy events
  -> execution feedback
```

### 화면 간 동기화

- Manifest Workflow에서 Run을 생성하거나 선택하면 전역 `activeRunID`를 갱신한다.
- Agents & Guard는 선택된 Run의 `selected_agent.name`과 권한을 강조한다.
- Post-deployment는 선택된 Run의 `deployment_id`를 자동 사용한다.
- Feedback은 선택된 Run의 단계, 상태, 로그, 실행 Feedback을 자동 표시한다.
- 사용자가 별도 화면에서 `run_id`를 다시 입력하지 않아도 된다.
- 페이지 새로고침 후에는 최신 Run을 선택하되, 사용자가 명시적으로 선택한 Run이 있으면 해당 선택을 우선한다.

## 자동 Feedback

### 원칙

자동 Feedback은 별도의 중복 저장소를 새로 만들지 않는다. 다음 데이터를 실행 근거의 원본으로 사용한다.

- `ControlRun.stages`
- `ControlRun.request_guard`
- `ControlRun.selected_agent`
- `ControlRun.execution`
- `ControlRun.generation`
- `ControlRun.manifest`
- `ControlRun.deployment`
- `ControlRun.polling`
- `ControlRun.logs`
- `ControlRun.correlation_ids`
- 같은 Run에 연결된 Automation Feedback
- 같은 Run에 연결된 Autonomous Loop 이벤트

Feedback 화면은 이 데이터를 `run_id`로 집계한 읽기 모델이다. 이 방식은 동일한 상태를 두 저장소에 중복 기록해 서로 달라지는 문제를 피한다.

### 자동 수집 시점

다음 단계가 끝날 때 ControlRun 자체가 자동 Feedback 근거를 가진다.

| 시점 | 자동으로 표시할 내용 |
|---|---|
| Request Guard 완료 | 승인·거절, 정책 버전, 이유 |
| Agent Registry 완료 | 선택 Agent, capability, bounded action, 선택 이유 |
| Agent Dispatcher 완료 | 실행 상태, 지연시간, Agent 메시지 |
| Qwen Planner 완료 | Candidate, 실제 모델, 추론 지연시간 |
| Manifest Guard 완료 | 승인·거절, 검증 이유, Manifest |
| AppDeploy 제출 완료 | deployment_id, 상태, polling, 로그 |
| Action Proposal 완료 | correlation_id, 제안 Action, Guard 상태 |
| 실행기 Feedback 도착 | 실행 상태, 외부 실행 ID, 성능 값 |
| Autonomous Loop 동작 | SLO 판단, 제안 Action, Guard, 실행 결과 |

Manifest가 승인되거나 거절되는 즉시 Feedback 화면에서 결과가 조회되어야 하며 사용자가 별도의 Feedback 등록 버튼을 누를 필요가 없다.

### 수동 입력의 역할

기존 Feedback 입력 폼은 일반 사용자 Feedback이 아니다. 승인된 `correlation_id`를 받은 외부 실행기가 실행 결과를 반환하는 API 시험 도구다.

따라서 화면에서 다음과 같이 분리한다.

- 기본 영역: `Automatic Run Feedback`
- 고급 접힘 영역: `External Executor Callback Test`

연구자의 주관 평가가 필요하면 별도 메모 필드를 후속 기능으로 추가할 수 있지만, 이번 범위에서는 자동 실행 근거와 외부 실행기 콜백을 우선한다.

자동 Feedback은 Qwen의 온라인 재학습을 의미하지 않는다. 1차년도 범위에서는 감사, 성능 평가, 실패 분석을 위한 실행 데이터로 사용한다.

## Agent Registry 화면

Agent Registry는 프로젝트 전체의 첫 화면이 아니라 Control Plane의 관리 화면이다.

이 화면은 다음을 제공한다.

- 등록 Agent 목록
- enabled 상태
- capability
- bounded actions
- internal/runtime source
- runtime endpoint 정보
- 선택 Agent 실행
- Run에서 선택된 Agent 강조
- Agent Request Guard 및 Result Guard 결과

Manifest Workflow에서 Registry 단계가 완료되면 해당 Run의 선택 Agent가 Agents & Guard 화면에 자동 반영된다. Registry에서 Agent를 등록했다고 해서 자동으로 Manifest를 생성하지는 않는다.

## Post-deployment 화면

Post-deployment 기능은 `ControlRun.status == DEPLOYED`이고 `deployment_id`가 존재할 때만 실행할 수 있다.

그 이전에는 실행 버튼을 비활성화하고 다음 이유를 표시한다.

```text
먼저 승인된 Manifest를 AppDeploy에 제출하고 배포 완료 상태를 확인하세요.
```

Autonomous Loop는 다음 경로를 따른다.

```text
배포 결과
  -> AppDeploy metrics
  -> SLO 평가
  -> Qwen Action 제안
  -> Agent Registry
  -> Go Guard
  -> 승인된 제어 실행
  -> 결과를 동일 run_id에 연결
```

이는 Manifest 생성 경로와 분리된 선택적 후속 실험이다.

## 상태와 오류 표시

- Guard 거절은 일반적인 `FAILED`가 아니라 거절한 단계와 이유를 표시한다.
- `Agent result was rejected`와 같은 일반 메시지만 표시하지 않는다.
- 오류 응답에 `run_id`가 있으면 해당 ControlRun을 자동 선택한다.
- Qwen 또는 외부 Agent 오류가 발생해도 완료된 이전 단계는 타임라인에 유지한다.
- AppDeploy가 연결되지 않아도 Manifest 생성과 자동 Feedback 조회는 가능하다.
- 새로고침 후에도 서버 인메모리 저장소에 Run이 남아 있으면 최신 상태를 복원한다.
- 서버 재시작으로 Run이 사라진 경우 UI는 이전 localStorage 식별자를 폐기하고 명확한 안내를 표시한다.

## 구현 경계

### 백엔드

- 기존 `ControlRun`을 단일 실행 기록의 권위 있는 원본으로 유지한다.
- 기존 단계 추가 로직을 재사용한다.
- 기존 Automation Feedback의 executor 검증과 credential 검사 규칙을 유지한다.
- 필요하면 조회용 조합 함수만 추가하고 원본 데이터를 복제하지 않는다.
- AppDeploy 제출과 Autonomous Loop는 기존 선택적 API를 사용한다.

### 웹

- 초기 `activeView`를 Manifest Workflow로 변경한다.
- 메뉴, Guide 단계, Overview 명령의 순서를 일치시킨다.
- Run 선택 변경을 모든 연결 화면에 반영한다.
- Feedback 화면은 ControlRun과 기존 Automation Feedback을 자동 조합한다.
- 외부 실행기 Callback 폼은 기본 화면에서 접는다.

## 테스트

### 정적 웹 계약

- Manifest Workflow가 첫 번째 메뉴이고 기본 활성 화면인지 검증
- 메뉴 순서가 Workflow, Agents, Post-deployment, Feedback, Guide인지 검증
- 단계 표시가 Request Guard부터 Manifest Guard까지 올바른 순서인지 검증
- Feedback 화면에 Automatic Run Feedback 영역이 존재하는지 검증
- 외부 실행기 Callback 폼이 고급 영역으로 분리됐는지 검증

### 서비스 및 API 회귀

- Manifest 승인 Run에 모든 핵심 단계가 기록되는지 검증
- Guard 거절 Run에서도 거절 단계와 이유가 유지되는지 검증
- AppDeploy 제출 결과가 동일 Run에 연결되는지 검증
- Action Proposal과 실행 Feedback이 동일 `run_id`에 연결되는지 검증
- 기존 Agent Registry, Dispatcher, Automation Feedback 테스트 유지

### 브라우저 검증

- 첫 화면에서 요청 제출 후 Manifest 승인까지 진행
- 선택 Run 변경 시 Agents, Post-deployment, Feedback 화면 동기화
- AppDeploy 미연결 상태에서 Manifest 생성 성공
- 오류 발생 시 `run_id`, 단계, 이유 표시
- 데스크톱과 모바일에서 버튼, 단계, JSON 결과가 겹치지 않음
- 콘솔 오류와 가로 오버플로 없음

## 완료 조건

다음 조건을 모두 만족하면 변경을 완료한 것으로 본다.

1. 웹 접속 시 Manifest Workflow가 첫 화면이다.
2. 사용자 요청 한 번으로 Request Guard부터 Manifest Guard까지 실행된다.
3. 모든 단계와 결과가 동일한 `run_id`로 연결된다.
4. 승인된 `DeploymentManifest`가 최종 결과로 표시된다.
5. Feedback 화면이 ControlRun 실행 결과를 자동 표시한다.
6. 외부 실행기 Callback을 제외하고 사용자가 Feedback을 수동 등록할 필요가 없다.
7. 배포되지 않은 Run은 Post-deployment 기능을 실행할 수 없다.
8. AppDeploy가 없어도 핵심 Manifest 실험과 Feedback 조회가 가능하다.
9. AppDeploy가 있으면 같은 Run에 배포 상태와 로그가 연결된다.
10. AppDeploy 저장소와 코드는 변경하지 않는다.
