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
- Repository: GitHub
- 개발 방식: 최소 2종 이상 LLM 코딩 에이전트 활용 및 교차 검증
- 문서 방식: DOCX는 공식 설계서, MD는 개발 실행 지시/가이드, Swagger는 API 계약

## 공통 상태값
`REQUESTED`, `VALIDATING`, `VALIDATED`, `SCHEDULING`, `DEPLOYING`, `RUNNING`, `STOPPING`, `STOPPED`, `VALIDATION_FAILED`, `SCHEDULING_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`, `EXTERNAL_API_FAILED`, `UNKNOWN`

## 공통 에러 코드
`APP_SPEC_INVALID`, `APP_ARTIFACT_NOT_FOUND`, `ENTRYPOINT_INVALID`, `RUNTIME_PROFILE_INVALID`, `TARGET_PROFILE_INVALID`, `RESOURCE_INSUFFICIENT`, `GPU_RUNTIME_NOT_FOUND`, `NVIDIA_DRIVER_NOT_FOUND`, `CSP_VM_UNREACHABLE`, `STORAGE_PATH_UNAVAILABLE`, `AI_INFRA_API_TIMEOUT`, `AI_INFRA_API_FAILED`, `GATEWAY_AUTH_FAILED`, `BESPIN_API_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`
