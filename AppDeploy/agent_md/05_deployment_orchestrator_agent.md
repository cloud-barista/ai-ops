# 05. Deployment Orchestrator 에이전트

## 역할
Deployment 생성, 상태 전이, DeploymentEvent 기록, Runtime Adapter 호출 흐름을 구현한다.

Planner는 App 요구사항과 DeploymentManifest를 만들고 상태 관찰·재시도 판단을 담당한다. Orchestrator/App Deployer는 artifact 생성, 등록 Target Profile 열거, VM/runtime readiness 확인, Manifest 자원 매칭, 실제 Target 선택과 배포 실행을 담당한다. Manifest의 `target_profile_id`는 선택적 hint이며 선택 결과는 응답과 normalized Manifest에 기록한다.

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
- Orchestrator 로그는 `github.com/rs/zerolog/log`로 남기고 `request_id`, `deployment_id`, `component=orchestrator`, `stage`, `status`, `error_code`, `retryable`을 가능한 한 필드로 붙인다.
- `fmt.Println`, 표준 라이브러리 `log`, bare `logrus`로 DeploymentEvent 또는 운영 로그를 대체하지 않는다.
- Deployment 생성/중지/로그 조회 service 함수는 `context.Context`를 첫 번째 인자로 받고 RuntimeAdapter 호출까지 같은 context를 전파한다.
- 실패 이벤트 message에는 사용자가 조치할 수 있는 원인을 적되, raw internal error, stack trace, credential, 내부 endpoint 원문은 남기지 않는다.
- RuntimeAdapter 또는 외부 연동 호출 실패는 표준 Deployment 상태와 공통 에러 코드로 정규화한다.
- 재시도는 idempotent한 외부 확인/조회성 작업에 한해 timeout, backoff, jitter를 적용한다.
