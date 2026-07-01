# 05. Deployment Orchestrator 에이전트

## 역할
Deployment 생성, 상태 전이, DeploymentEvent 기록, Runtime Adapter 호출 흐름을 구현한다.

## 상태 머신
```text
REQUESTED -> VALIDATING -> VALIDATED -> SCHEDULING -> DEPLOYING -> RUNNING
RUNNING -> STOPPING -> STOPPED
VALIDATING -> VALIDATION_FAILED
SCHEDULING -> SCHEDULING_FAILED
DEPLOYING -> DEPLOYMENT_FAILED | EXTERNAL_API_FAILED
RUNNING -> RUNTIME_FAILED
```

## 구현 기준
- 모든 상태 전이는 DeploymentEvent로 저장한다.
- 실패 시 표준 에러 코드와 retryable 여부를 기록한다.
- Orchestrator는 Runtime 구현체에 직접 의존하지 않고 RuntimeAdapter Interface에만 의존한다.
- Mock Runtime으로 E2E 흐름을 먼저 완성한다.
