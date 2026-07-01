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
