# Two-Agent Application Automation and Optimization Design

## 1. 목적

이 설계는 경희대학교 담당 범위인 AI 응용 요구 기반 배포 판단과 배포 후 운영
최적화를 geon 안에서 명확한 Agent 역할로 분리한다. 전체 AI-App 플랫폼이나 실제
인프라 실행기를 다시 만드는 것이 목적이 아니다.

핵심 목표는 다음과 같다.

- `ApplicationProfile`과 `ResourceRecommendation`을 입력으로 배포 여부를 판단한다.
- 판단 결과를 결정적 Go Guard로 검증한다.
- 승인된 판단만 플랫폼 중립적인 `DesiredDeploymentSpec`으로 변환한다.
- 배포 상태와 성능 Feedback을 이용해 스케일링 필요 여부를 판단한다.
- 규칙 기반과 Qwen 기반 판단을 같은 입력과 평가 기준으로 비교한다.
- Agent Registry가 실제 실행 Agent의 신원, capability, bounded action을 통제한다.

## 2. 담당 경계

### geon이 담당한다

- 요구사항 분석 결과와 인프라 추천 결과의 결합
- `DEPLOY`, `REJECT`, `RETRY` 판단
- 입력, Agent 결과, 도메인 판단의 Go Guard 검증
- 플랫폼 중립적인 `DesiredDeploymentSpec` 생성
- 배포 결과의 성공 및 실패 원인 요약
- `KEEP`, `SCALE_OUT`, `SCALE_IN` 판단
- 규칙 기반과 Qwen 기반 추론 비교
- Agent Registry 기반 실행 권한 관리

### geon이 담당하지 않는다

- 운영 수준 App Registry와 버전 관리
- 실제 플랫폼용 Manifest 변환과 배포 실행
- 실제 CSP, 리전, VM 세부 사양의 최종 결정
- 실제 VM 생성, 삭제, Scale-out, Scale-in 실행
- 운영 수준 모니터링 수집 및 저장
- 동일 VM 내 다중 응용 스케줄링
- Kubernetes 및 컨테이너 배포

현재 외부 담당 기능은 기존 `MockDeploymentAdapter` 또는 handoff Adapter 경계로
유지한다. `SIMULATED`와 `READY`는 실제 배포 성공을 의미하지 않는다.

## 3. 선택한 구조

geon은 두 개의 논리적 Internal Agent를 제공한다.

| Agent | 단계 | 입력 | 출력 |
| --- | --- | --- | --- |
| `AIApplicationAutomationAgent` | 배포 전 | `ApplicationProfile`, `ResourceRecommendation` | `DEPLOY`, `REJECT`, `RETRY` 제안 |
| `OperationOptimizationAgent` | 배포 후 | `deployment.status.changed`, `optimization.feedback.created`, SLO | `KEEP`, `SCALE_OUT`, `SCALE_IN` 제안 |

두 Agent의 결과는 모두 Agent 외부의 Go Guard를 통과해야 한다. Agent는 실제 배포나
인프라 제어를 실행하지 않는다.

`AIApplicationAutomationAgent`는 기존 기본 Internal Agent를 유지한다.
`OperationOptimizationAgent`는 현재 `evaluateScalingDecision`으로 구현된 결정적
스케일링 로직을 Registry와 실행 증거가 있는 Agent 역할로 승격한다. 알고리즘을
불필요하게 다시 작성하지 않는다.

## 4. 전체 데이터 흐름

```text
application.analysis.request
  -> Requirement Analyzer
  -> ApplicationProfile
  -> Resource Recommender
  -> ResourceRecommendation
  -> Registry에서 AIApplicationAutomationAgent 선택 및 권한 확인
  -> Agent Request Guard
  -> 배포 판단 전략 실행
  -> Agent Result Guard
  -> Deployment Decision Guard
  -> DEPLOY
       -> geon이 DesiredDeploymentSpec 생성
       -> Mock 또는 handoff Adapter 전달
     REJECT
       -> 거부 사유와 증거 기록 후 종료
     RETRY
       -> correction_request 기록 후 종료

deployment.status.changed + optimization.feedback.created
  -> Registry에서 OperationOptimizationAgent 선택 및 권한 확인
  -> Agent Request Guard
  -> 운영 최적화 전략 실행
  -> Agent Result Guard
  -> Scaling Decision Guard
  -> KEEP / SCALE_OUT / SCALE_IN
  -> 실행하지 않고 권고와 증거만 기록
```

한 배포 흐름은 같은 `correlation_id`와 `trace_id`를 유지한다. 각 Agent 실행은 별도의
`run_id`를 가질 수 있지만 원래 배포 흐름과 연결되어야 한다.

## 5. Agent Registry 정책

### 5.1 배포 판단 Agent

```text
capability: ai_application_automation
bounded action: generate_deployment_decision
default: AIApplicationAutomationAgent
```

### 5.2 운영 최적화 Agent

```text
capability: ai_application_operation_optimization
bounded action: generate_scaling_decision
default: OperationOptimizationAgent
```

설정 파일에는 두 capability의 기본 Agent를 각각 선언한다. 기본 Internal Agent는
설정으로 보호하고, 사용자가 등록한 Runtime Agent만 삭제할 수 있는 기존 정책을
유지한다.

Runtime Agent는 다음 조건을 모두 만족할 때만 해당 단계의 선택 목록에 표시한다.

- `enabled=true`
- 요구 capability 보유
- 요구 bounded action 보유
- 실행 시점에 등록 endpoint가 올바른 결과 계약을 반환

등록은 endpoint 프로세스의 실행을 의미하지 않는다. endpoint 연결 실패 시 Internal
Agent로 자동 대체하지 않고 명시적인 `AGENT_EXECUTION_FAILED`로 종료한다.

## 6. Agent 결과 계약

### 6.1 배포 판단 결과

```json
{
  "decision": "DEPLOY",
  "selected_candidate_id": "mock-gpu-l4",
  "reason": "GPU와 메모리 요구조건을 만족합니다.",
  "confidence": 0.91,
  "reasoning_mode": "rule_based"
}
```

`DesiredDeploymentSpec`은 Agent 결과 자체가 아니다. geon이 위 결과를 Guard로 검증한
후 `ApplicationProfile`과 선택된 자원 후보를 조합하여 생성한다.

### 6.2 운영 최적화 결과

```json
{
  "action": "SCALE_OUT",
  "current_replicas": 1,
  "desired_replicas": 2,
  "reason": "latency_p95_ms SLO 위반",
  "evidence": ["latency_p95_ms"],
  "reasoning_mode": "rule_based"
}
```

기존 내부 값 `NO_ACTION`은 API와 UI 경계에서 `KEEP`으로 표현한다. 저장된 기존 데이터와
테스트 호환성이 필요한 곳에서는 `NO_ACTION`을 입력 alias로 허용한다. `UPDATE`는 계약
상 예약하되, 업데이트 원인을 판별할 신뢰 가능한 증거 규격이 정의되기 전까지 자동으로
생성하지 않는다.

## 7. 추론 전략

Agent와 추론 전략을 분리한다. Agent는 Registry가 관리하는 역할과 권한의 단위이고,
추론 전략은 Agent가 판단을 생성하는 구현 방식이다.

### 7.1 규칙 기반

- 기본 운영 경로
- 결정적이고 재현 가능
- Ollama 없이 실행 가능
- 기존 배포 판단 및 `evaluateScalingDecision` 로직 재사용

### 7.2 Qwen 제안

- 동일 입력으로 Qwen 판단 제안 생성
- 모델, latency, 원본 제안, 파싱 상태 기록
- provider unavailable을 배포 판단 실패와 구분

### 7.3 Qwen 제안과 Go Guard

- Qwen 원본 제안을 먼저 기록
- 동일한 결정적 Go Guard 적용
- 승인, 거부, 수정 필요 결과를 비교 증거로 저장
- 실제 자동화 경로에서는 Guard를 우회하지 않음

세 전략은 별도 Agent 이름으로 부풀리지 않는다. 같은 Agent 역할 안에서
`reasoning_mode`로 구분한다.

## 8. Guard 구조

### Agent Request Guard

- Registry 등록 및 enabled 상태
- capability와 bounded action
- 필수 입력과 식별자
- credential, token 등 금지 필드

### Agent Result Guard

- Agent 신원과 `run_id`
- 결과 상태와 bounded action
- JSON 형식과 응답 크기
- 필수 결과 필드

### Deployment Decision Guard

- `DEPLOY`, `REJECT`, `RETRY` 값
- 선택 후보의 추천 목록 포함 여부
- ApplicationProfile 최소 자원 만족 여부
- 신뢰도 범위
- 수정 요청의 완전성

### Scaling Decision Guard

- `KEEP`, `SCALE_OUT`, `SCALE_IN` 값
- 현재 replica와 최소, 최대 범위
- 한 번에 1개 replica만 변경
- SLO 위반 또는 저사용률 증거
- 배포 상태가 `RUNNING`인지 확인

Guard 거부 시 Adapter 전달이나 제어 요청을 생성하지 않는다.

## 9. 웹 변경

기존 세 메뉴를 유지한다.

```text
자동화 Agent
Agent 및 정책
실험 결과
```

### 자동화 Agent

- 입력 방식 선택
- 배포 판단 Agent 선택
- 요구사항 분석, 추천, Agent 판단, Guard, Adapter 단계 표시
- `DesiredDeploymentSpec`과 중간 증거 표시
- 핵심 실행은 선택 Agent의 안전한 기본 전략을 사용

### Agent 및 정책

- 배포 판단 Agent와 운영 최적화 Agent를 capability별로 구분
- 각 Agent의 source, capability, bounded action, enabled 상태 표시
- Runtime Agent 등록 및 삭제 기능 유지

### 실험 결과

- 배포 판단 결과와 추론 전략 비교
- 규칙 기반, Qwen 원시 제안, Qwen+Guard를 같은 Flow에서 비교
- 배포 상태와 성능 Feedback 입력
- 운영 최적화 Agent 선택. 생략하면 Registry 기본 Agent 사용
- 운영 최적화 Agent 실행 결과
- `KEEP`, `SCALE_OUT`, `SCALE_IN` 판단과 증거 표시

새로운 최상위 메뉴나 별도의 플랫폼 화면은 추가하지 않는다.

## 10. API 호환성

기존 API를 삭제하지 않는다.

- `POST /api/v1/agent-control/automation-runs`
- `GET /api/v1/agent-control/automation-runs/{run_id}`
- `POST /api/v1/agent-control/deployment-status`
- `POST /api/v1/agent-control/optimization-feedback`
- `POST /api/v1/agent-control/flows/{correlation_id}/reasoning-comparison`

운영 최적화 Agent 실행 증거는 기존 Flow의 `scaling_decision`을 확장하여 저장한다.
`optimization-feedback`은 Common JSON 본문을 변경하지 않고 선택적인
`operation_agent` 쿼리 매개변수를 허용한다. 생략하면 Registry 기본 Agent를 사용한다.
필요한 경우에만 capability별 eligible Agent 목록과 선택 Agent 필드를 기존 응답에
추가한다. 기존 클라이언트가 보내지 않는 필드는 Registry 기본값을 사용한다.

## 11. 오류 처리

| 상황 | 결과 |
| --- | --- |
| Profile 또는 Recommendation 누락 | `RETRY`와 correction request |
| 적합한 자원 후보 없음 | `RETRY` |
| Agent 권한 불일치 | `AGENT_AUTHORIZATION_REJECTED` |
| Runtime endpoint 연결 실패 | `AGENT_EXECUTION_FAILED` |
| Agent 결과 계약 위반 | `AGENT_RESULT_REJECTED` |
| 배포 판단 Guard 거부 | `REJECTED`, Desired Spec 없음 |
| Feedback 누락 | 운영 최적화 대기, 추정값 생성 금지 |
| replica 범위 위반 | Scaling Guard 거부 |
| Qwen 사용 불가 | `provider_unavailable`, 규칙 결과는 유지 |

## 12. 검증 시나리오

1. 정상 GPU 후보가 존재하면 `DEPLOY`, Guard 승인, Desired Spec 생성
2. 적합한 후보가 없으면 `RETRY`, Desired Spec 미생성
3. 조작된 후보 ID를 반환하면 Result 또는 Domain Guard 거부
4. Runtime Agent endpoint가 꺼져 있으면 `AGENT_EXECUTION_FAILED`
5. 정상 SLO와 정상 사용률이면 `KEEP`
6. 지연시간 또는 처리량 SLO 위반이면 bounded `SCALE_OUT`
7. 정상 SLO와 지속 저사용률이면 bounded `SCALE_IN`
8. 최소 또는 최대 replica에서 범위를 넘는 제안은 Guard 거부
9. 동일 입력에 규칙, Qwen, Qwen+Guard 결과와 latency 기록
10. Mock Adapter 결과가 `SIMULATED`이며 실제 배포로 표시되지 않음

## 13. 완료 기준

- 기본 Internal 배포 판단 Agent가 기존 자동화 흐름을 회귀 없이 수행한다.
- 기존 스케일링 판단이 `OperationOptimizationAgent`의 Registry 권한과 실행 증거를 갖는다.
- 최종 `DesiredDeploymentSpec` 생성 책임이 geon에 있음을 API와 UI에서 명확히 보여준다.
- 규칙, Qwen, Qwen+Guard 비교 결과가 같은 Flow에 기록된다.
- 핵심 Mock 실험은 Ollama와 외부 Runtime Agent 없이 재현할 수 있다.
- Runtime Agent를 선택한 경우에는 실제 endpoint 결과만 사용하며 묵시적 fallback이 없다.
- AppDeploy와 실제 VM 코드는 변경하지 않는다.
