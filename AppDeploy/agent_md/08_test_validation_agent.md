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
