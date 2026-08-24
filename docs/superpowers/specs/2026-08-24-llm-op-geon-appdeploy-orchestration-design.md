# LLM_Op - geon - AppDeploy 통합 오케스트레이션 설계

## 1. 목적

`geon`을 사용자 요청의 안전 검토 결과와 인프라 추천 결과를 결합해 배포를 판단하고, 배포 이후 Feedback을 반영해 운영 최적화 Revision을 생성하는 중앙 Automation Agent Orchestrator로 정의한다.

이 설계는 다음 세 모듈의 책임을 분리한 상태로 연결한다.

- `LLM_Op`: 비신뢰 자연어 요청의 최초 Safeguard와 AppDeploy prepare-only 요청 투영
- `geon`: 요구 분석, 자원 추천 결합, Agent 배포 판단, Guard, canonical Flow와 Manifest Revision 소유
- `AppDeploy`: Target/Runtime 선택, 실제 배포, 상태와 로그 제공

`geon`은 CB-Tumblebug VM 생성·삭제와 AppDeploy 실행 로직을 내장하지 않는다.

## 2. 현재 상태

현재 `geon`은 다음 독립 PoC 흐름을 제공한다.

```text
자연어 요청 또는 구조화 App Spec
  -> Requirement Analyzer
  -> ApplicationProfile
  -> Mock Resource Recommender
  -> ResourceRecommendation
  -> AIApplicationAutomationAgent
  -> DEPLOY | REJECT | RETRY
  -> Go Guards
  -> DesiredDeploymentSpec
  -> Manifest Revision 1
  -> Mock/Handoff Adapter
```

배포 상태와 성능 Feedback이 접수되면 다음 운영 흐름이 이어진다.

```text
deployment.status.changed
  + optimization.feedback.created
  -> OperationOptimizationAgent
  -> KEEP | SCALE_OUT | SCALE_IN
  -> Scaling Guard
  -> Manifest Revision 2
```

현재 부족한 부분은 최초 자연어 Safeguard, 실제 팀 ResourceRecommendation 입력, AppDeploy prepare-only 투영, 외부 배포 상태와 Feedback의 자동 연결이다.

## 3. 목표 처리 흐름

```text
1. 사용자 자연어 요청 또는 구조화 App Spec
2. 서버 Registry가 app_id/app_version/app_version_id 결합
3. LLM_Op deterministic preflight, normalization, redaction
4. LLM_Op Safeguard
   - allow_request
   - request_clarification
   - reject_request
5. allow_request일 때만 geon 실행
6. Requirement Analyzer -> ApplicationProfile
7. Resource Adapter -> ResourceRecommendation
8. Agent Registry에서 배포 판단 Agent 권한 확인
9. AIApplicationAutomationAgent -> DEPLOY | REJECT | RETRY
10. Request/Result/Domain Guard
11. geon DesiredDeploymentSpec + INITIAL Revision 1
12. approved Flow binding 재검증
13. LLM_Op bounded Proposal + semantic/AppDeploy Guard
14. AppDeploy DeploymentCreateRequest prepare-only 생성
15. 승인된 Deployment Adapter가 AppDeploy로 전달
16. AppDeploy deployment.status.changed 반환
17. Monitoring optimization.feedback.created 반환
18. OperationOptimizationAgent -> KEEP | SCALE_OUT | SCALE_IN
19. Scaling Guard
20. geon OPTIMIZED Revision 2
```

모든 단계는 동일한 `correlation_id`, `trace_id`, `profile_id`, `decision_id` 연결을 유지한다.

## 4. 핵심 소유권

### 4.1 LLM_Op

소유한다.

- 최초 자연어 Safeguard
- 관측 정보 정규화, freshness 검사와 비밀정보 제거
- `allow_request`, `request_clarification`, `reject_request`
- bounded Manifest Proposal
- geon이 승인한 INITIAL Revision의 AppDeploy prepare-only 투영

소유하지 않는다.

- canonical geon Flow와 Revision
- 실제 Target/Runtime 선택
- AppDeploy POST와 실제 배포
- 운영 최적화와 Revision 2

### 4.2 geon

소유한다.

- ApplicationProfile
- ResourceRecommendation 결합과 검증
- Agent Registry와 Agent Dispatcher
- `DEPLOY`, `REJECT`, `RETRY`
- 외부 Go Guards
- DesiredDeploymentSpec
- canonical Flow
- Manifest Revision 1과 Revision 2
- Feedback 기반 운영 최적화 판단

소유하지 않는다.

- LLM_Op 거부 결과 우회
- 실제 cloud provider, Target, Runtime Adapter와 credential 선택
- AppDeploy 실행 내부 구현
- CB-Tumblebug VM lifecycle

### 4.3 AppDeploy

소유한다.

- App/AppVersion 등록
- Runtime/Target Profile
- 실제 Target과 Runtime Adapter 선택
- 배포, 중지, 상태, 로그와 모니터링

자연어 안전 판단이나 geon Agent 판단을 다시 구현하지 않는다.

## 5. geon 구성요소 변경

### 5.1 Trusted Orchestrator

기존 `application.analysis.request` 진입점 앞에 LLM_Op Safeguard를 배치한다. Safeguard와 approved-flow bridge는 같은 Go 프로세스의 trusted orchestration 경로에서 실행한다.

외부 caller가 continuation, Safeguard 내부 결과 또는 PlanningConstraints를 직접 제출하는 resume API는 만들지 않는다. 프로세스 경계를 넘겨야 할 경우 opaque server record, 만료, one-time consume, idempotency와 HMAC/서명 계약을 먼저 추가한다.

### 5.2 Requirement Analyzer

다음을 수정한다.

- 명시적 `GPU 0`은 CPU-only로 해석한다.
- 각 필드에 `explicit`, `defaulted`, `inferred` provenance를 기록한다.
- 필수 배포 필드가 추정되었으면 무조건 승인하지 않고 clarification 또는 RETRY로 전환한다.
- GPU device memory처럼 AppDeploy 계약으로 손실 없이 전달할 수 없는 필드는 조용히 제거하지 않고 fail-closed한다.

### 5.3 Resource Adapter

자원 추천 입력을 Adapter로 분리한다.

- `mock`: 현재 로컬 카탈로그 기반 추천
- `external`: 팀 Resource Recommender가 제공한 Common JSON v1.0 수신

통합 모드에서 external 추천 실패를 mock 추천 성공으로 바꾸지 않는다.

### 5.4 Deployment Adapter

다음 모드를 정의한다.

- `mock`: `SIMULATED` 증거만 생성
- `handoff`: AppDeploy 요청을 `READY/not_submitted`로 기록
- `appdeploy`: 명시적 승인과 idempotency 조건을 통과한 요청만 AppDeploy로 전달

초기 통합은 `handoff`를 기본으로 사용한다. 실제 POST를 활성화할 때는 `correlation_id + revision + app_version_id`를 idempotency key로 사용하고 동일 Revision의 중복 제출을 차단한다.

### 5.5 Feedback Adapter

AppDeploy와 모니터링 시스템이 다음 Common JSON 메시지를 geon에 전달한다.

- `deployment.status.changed`
- `optimization.feedback.created`

두 메시지는 Manifest가 아니다. 같은 Flow에 대한 관측 증거이며, 둘이 검증된 뒤 OperationOptimizationAgent가 실행된다.

### 5.6 Operation Optimization

Revision 2는 LLM_Op을 다시 거치지 않는다.

```text
Revision 1
  -> deployment status
  -> performance/SLO/cost feedback
  -> OperationOptimizationAgent
  -> Scaling Guard
  -> Revision 2
```

실제 scale 실행은 AppDeploy 또는 외부 실행 계층의 책임이다.

## 6. 상태와 실패 처리

### 6.1 Safeguard 상태

- `SAFEGUARD_APPROVED`: geon 분석 진행
- `CLARIFICATION_REQUIRED`: 사용자 보완 요청 후 종료
- `REQUEST_REJECTED`: 종료
- `SAFEGUARD_FAILED`: 종료

allow 이외 결과를 legacy Planner나 full-Manifest 생성기로 우회하지 않는다.

### 6.2 geon 판단 상태

- `DEPLOY_APPROVED`: Revision 1 생성 가능
- `RETRY_REQUIRED`: 요구 또는 추천 수정 필요
- `REJECTED`: 종료
- `AGENT_EXECUTION_FAILED`: 선택 Agent 실행 실패
- `GUARD_REJECTED`: Agent 결과 검증 실패

### 6.3 전달 상태

- `SIMULATED`: Mock 전달
- `READY`: prepare-only 인계 준비
- `SUBMITTED`: AppDeploy가 요청을 수락하고 deployment ID를 반환
- `FAILED`: 제출 또는 downstream 처리 실패

`SIMULATED`와 `READY`는 실제 VM 배포 성공이 아니다.

## 7. Manifest 불변식

- geon `DeploymentManifest`는 canonical Common JSON Revision이다.
- LLM_Op/AppDeploy `DeploymentManifest`는 AppDeploy HTTP body다.
- 두 타입을 직접 캐스팅하거나 병렬 제출하지 않는다.
- AppDeploy prepare-only 투영은 `Revision == 1`, `Phase == INITIAL`, `TriggerAction == DEPLOY`만 허용한다.
- Revision 2는 최초 배포 요청으로 제출하지 않는다.
- LLM은 credential, endpoint, provider, Target, Runtime Adapter 또는 shell command를 생성할 수 없다.

## 8. 웹 UX

기본 화면은 하나의 Flow를 다음 순서로 보여준다.

```text
요청
-> 최초 Safeguard
-> 요구 분석
-> 인프라 추천
-> Agent 배포 판단
-> Go Guard
-> Revision 1
-> AppDeploy 전달/상태
-> 성능 Feedback
-> 운영 최적화
-> Revision 2
```

각 단계에는 동일한 Flow ID와 입력 출처를 표시한다.

- `MOCK`: 로컬 fixture 또는 카탈로그
- `EXTERNAL`: 팀 API
- `LIVE`: 실제 AppDeploy 실행 결과

`/llm-op-demo`는 고급 Safeguard/Proposal 계약 검증 화면으로 유지하며 기본 교수 시연 흐름의 시작 화면으로 사용하지 않는다.

## 9. API 경계

기존 Common JSON v1.0 endpoint를 유지한다.

- `POST /api/v1/agent-control/application-analysis-requests`
- `POST /api/v1/agent-control/application-contexts`
- `POST /api/v1/agent-control/resource-recommendations`
- `POST /api/v1/agent-control/deployment-status`
- `POST /api/v1/agent-control/optimization-feedback`
- `GET /api/v1/agent-control/flows/{correlation_id}`

Safeguard 내부 continuation을 공개하는 endpoint는 추가하지 않는다.

## 10. 시험 전략

### 10.1 단위 시험

- GPU 0 CPU-only 해석
- provenance와 clarification 판단
- Safeguard allow/clarify/reject
- approved INITIAL revision bridge 불변식
- AppDeploy prepare-only 변환
- Feedback join과 Scaling Guard

### 10.2 계약 시험

- Common JSON v1.0 schema
- `correlation_id`, `trace_id`, `profile_id`, `decision_id` join
- AppDeploy request/response fixture
- 중복 message와 중복 deployment 제출 차단

### 10.3 통합 시험

1. Mock 전체 Flow
2. LLM_Op fixture + geon + AppDeploy prepare-only
3. VM에서 geon과 AppDeploy 실행
4. Revision 1 실제 제출
5. deployment status와 metrics 반환
6. Revision 2 생성

외부 연동이 실패한 경우 테스트는 실패해야 하며 mock fallback으로 성공 처리하지 않는다.

## 11. 구현 단계

### 단계 1: Guard-first 통합

- `LLM_Op` PR 전체를 geon 기준으로 검토
- isolated `internal/llmop`, `internal/llmopbridge` 패키지 병합
- trusted orchestrator에서 Safeguard -> geon -> approved-flow bridge 연결
- analyzer provenance와 GPU 0 수정

### 단계 2: Prepare-only 인계

- Revision 1 -> AppDeploy request 투영
- `handoff` 증거와 UI 상태 표시
- 동일 Flow ID와 AppVersion binding 검증

### 단계 3: 실제 AppDeploy 제출

- 승인 정책, idempotency와 timeout 추가
- AppDeploy client로 제출
- deployment ID와 상태를 Flow에 연결

### 단계 4: Feedback 폐루프

- 실제 status/metrics callback
- OperationOptimizationAgent와 Scaling Guard
- Revision 2와 제어 인계 계약 검증

## 12. 완료 기준

- 비신뢰 자연어가 live Analyzer보다 먼저 Safeguard를 통과한다.
- 거부·명확화·모델 오류에 fallback이 없다.
- geon이 canonical Flow와 Revision 1·2의 유일한 소유자다.
- AppDeploy 요청은 승인된 INITIAL Revision에서만 파생된다.
- AppDeploy가 Target/Runtime과 실제 실행을 소유한다.
- 동일 Flow ID로 요청, 판단, 배포, Feedback, 최적화 증거가 연결된다.
- Mock와 실제 통합 결과가 UI와 API에서 명확히 구분된다.
