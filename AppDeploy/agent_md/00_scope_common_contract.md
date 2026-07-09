# 00. 공통 범위 및 계약

## 목적
모든 코딩 에이전트는 본 문서를 먼저 읽고 작업한다. 현재 산출물 범위는 `AI 반도체기반 AI 응용배포 및 운용구조설계`와 그 구현 검증 수단인 `CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입`이다.

## 반드시 지킬 범위
- 경희대학교 담당 범위는 AI App Deployer Control Plane, App 등록·배포, Resource Matcher/Scheduler, VM Runtime Adapter, 로그·에러·시험 구조이다.
- 1차년도 구현 대상은 CPU/GPU VM 기반이다.
- Docker, Docker Compose, Kubernetes, OCI Image, Container Registry 기반 구현은 제안하지 않는다. 컨테이너는 3차년도부터 도입된다.
- LLM 운영관리, 에이전트 등록관리, 추론 최적화 전략은 별도 산출물이다. 본 프로토타입 코드에 섞지 않는다.

## 공통 기술 기준
- Language: Go
- Web framework: Echo
- API contract: Swagger/OpenAPI
- Structured logging: `github.com/rs/zerolog/log`
- Configuration: `viper` 기반 설정 로딩과 환경변수 override
- Repository: GitHub
- 개발 방식: 최소 2종 이상 LLM 코딩 에이전트 활용 및 교차 검증
- 문서 방식: DOCX는 공식 설계서, MD는 개발 실행 지시/가이드, Swagger는 API 계약

## 공통 개발 규칙
- 모든 구조화 로그는 `github.com/rs/zerolog/log`를 사용한다. `fmt.Println`, 표준 라이브러리 `log`, bare `logrus` 호출은 사용하지 않는다.
- 로그에는 작업을 추적할 수 있는 문맥 필드(`request_id`, `deployment_id`, `component`, `stage`, 외부 연동 provider 등)를 붙인다.
- credential, token, password, SSH key, 내부 endpoint 원문 같은 민감정보는 코드, 로그, 예제, 증적에 남기지 않는다.
- Handler는 Echo request context를 받아 core/service 계층으로 전파한다. Handler 내부에서 `context.Background()`를 새로 만들지 않는다.
- core/service/repository/runtime/external 계층 함수는 가능한 한 `context.Context`를 첫 번째 인자로 받는다.
- library/server code에서 `panic`을 사용하지 않는다. 모든 error는 명시적으로 처리하고, 반환할 때는 `fmt.Errorf("...: %w", err)`처럼 문맥을 감싼다.
- REST Handler는 내부 오류 원문이나 stack trace를 API caller에게 그대로 노출하지 않고 표준 ErrorResponse와 의미 있는 HTTP status로 변환한다.
- 외부 API, SSH, 파일/네트워크 I/O에는 timeout과 context cancellation을 적용한다. 재시도는 idempotent 작업에 한해 exponential backoff와 jitter를 사용한다.
- 새 Go dependency를 추가하기 전 표준 라이브러리와 기존 dependency로 해결 가능한지 확인하고, Apache-2.0과 호환되는 라이선스(MIT, BSD, ISC 등)만 사용한다.

## 공통 상태값
`REQUESTED`, `VALIDATING`, `VALIDATED`, `SCHEDULING`, `DEPLOYING`, `RUNNING`, `STOPPING`, `STOPPED`, `VALIDATION_FAILED`, `SCHEDULING_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`, `EXTERNAL_API_FAILED`, `UNKNOWN`

## 공통 에러 코드
`APP_SPEC_INVALID`, `APP_ARTIFACT_NOT_FOUND`, `ENTRYPOINT_INVALID`, `RUNTIME_PROFILE_INVALID`, `TARGET_PROFILE_INVALID`, `RESOURCE_INSUFFICIENT`, `GPU_RUNTIME_NOT_FOUND`, `NVIDIA_DRIVER_NOT_FOUND`, `CSP_VM_UNREACHABLE`, `STORAGE_PATH_UNAVAILABLE`, `AI_INFRA_API_TIMEOUT`, `AI_INFRA_API_FAILED`, `GATEWAY_AUTH_FAILED`, `BESPIN_API_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`
