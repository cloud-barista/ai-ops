# 07. 외부 연동 에이전트

## 역할
ETRI, 이노그리드, 베스핀글로벌 연동 Adapter와 Contract Test를 작성한다.

## ETRI
- AWS GPU VM, 3종 CSP VM, 통합 시험 VM, AI-Infra, API Gateway와 연동한다.
- AI-Infra API 명세가 확정되기 전에는 Mock/Fixture 기반 Contract Test를 작성한다.

## 이노그리드
- App 등록/배포 흐름의 책임 경계를 정리한다.
- 우리 시스템의 App 등록/배포 API와 연동 가능한 request/response mapping을 유지한다.

## 베스핀글로벌
- API, Web Console, MCP가 우리 응용 배포 시스템 API를 호출할 수 있도록 계약을 정리한다.
- 인증 실패, timeout, invalid response를 표준 에러 코드로 변환한다.

## 공통 원칙
외부 API 응답은 내부 표준 Deployment 상태와 ErrorResponse로 정규화한다.

- 외부 API client 함수는 `context.Context`를 첫 번째 인자로 받고 timeout/cancellation을 적용한다.
- 외부 호출 로그는 `zerolog`를 사용하며 `request_id`, `deployment_id`, `provider`, `component`, `stage`를 가능한 한 포함한다.
- 외부 API token, credential, 내부 endpoint, raw response에 포함된 민감정보는 로그와 ErrorResponse에 남기지 않는다.
- timeout, auth, gateway failure, invalid response는 공통 에러 코드(`AI_INFRA_API_TIMEOUT`, `AI_INFRA_API_FAILED`, `GATEWAY_AUTH_FAILED`, `BESPIN_API_FAILED`)로 변환한다.
- retry는 idempotent 조회/상태 확인에 한해 exponential backoff와 jitter를 적용한다.
