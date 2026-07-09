# 08. 시험·검증 에이전트

## 역할
Unit/API/Contract/Integration/E2E/Failure Test를 작성하고 시험 가이드와 로그 수집 기준을 정리한다.

## 필수 시험
- TC-PROT-001 App 등록
- TC-PROT-002 container artifact 거부
- TC-PROT-003 Mock 배포
- TC-PROT-004 CPU VM 배포
- TC-PROT-005 GPU VM readiness
- TC-PROT-006 GPU VM 배포
- TC-PROT-007 로그 조회
- TC-PROT-008 중지 요청
- TC-PROT-009 ETRI Contract
- TC-PROT-010 Bespin/MCP Contract

## 시험 증적
- API 요청/응답
- request_id, deployment_id 포함 로그
- VM/GPU readiness 로그
- 실패 시 error_code와 원인 로그
- 사람 검증자의 판정 기록

## 코드 변경 검증 순서
1. 추가/수정한 단위 코드부터 먼저 검증한다.
2. 전체 코드베이스를 빠르게 재검증한다.
3. 민감정보 점검: hard-coded credential, token, password, 개인 정보, 내부 endpoint 원문이 추가되지 않았는지 확인한다.
4. 중복 기능 점검: 기존 함수/모듈과 같은 책임의 새 구현을 만들지 않았는지 확인한다.
5. unused code 점검: unused variable, function, import가 없는지 확인한다.
6. 로그 점검: `fmt.Println`, 표준 라이브러리 `log`, bare `logrus` 운영 로그가 추가되지 않았고 `zerolog` 구조화 로그를 사용하는지 확인한다.
7. 에러 점검: error를 `_`로 버리지 않았고, raw `err.Error()`를 API response로 직접 노출하지 않았는지 확인한다.

## 기본 명령
- 코드 변경 시 `go test ./...`, `go vet ./...`를 수행한다.
- OpenAPI 변경 시 `docs/api/openapi.html`을 재생성하고 Redocly lint/build 결과를 기록한다.
- 신규 dependency를 추가한 경우 license/유지보수 지표와 사용자 승인 여부를 기록한다.
