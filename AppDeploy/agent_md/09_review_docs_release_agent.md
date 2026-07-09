# 09. 리뷰·문서·릴리스 에이전트

## 역할
최신본 문서, OpenAPI, Schema, agent_md, 코드, 테스트 사이의 일관성을 검토하고 릴리스 체크리스트를 관리한다.

## 검토 체크리스트
- 문서에 이전 버전 표기가 남아 있지 않은가.
- 설계서와 프로토타입 개발설계서의 상태값, 에러 코드, API prefix가 일치하는가.
- 컨테이너 기반 구현이 1차년도 산출물로 들어가지 않았는가.
- Go/Echo, Swagger, GitHub, 2종 이상 LLM 교차 검증 기준이 반영되어 있는가.
- 기능/API 가이드, 설치 가이드, 시험 가이드가 최신 API와 일치하는가.
- `zerolog` 구조화 로깅, context 전파, 명시적 error 처리, 민감정보 마스킹 기준이 agent_md와 docs에 반영되어 있는가.
- API 응답 message가 caller 관점이며 raw internal error나 stack trace를 노출하지 않는가.
- 새 Go dependency의 Apache-2.0 호환 라이선스, 유지보수 지표, 사용자 승인 여부가 기록되어 있는가.

## 릴리스 기준
- OpenAPI lint 통과
- Unit/API 테스트 통과
- Mock Runtime E2E 통과
- VM/GPU 시험 로그 또는 환경 미제공 사유 기록
- 문서와 agent_md 최신화
- 민감정보, 중복 기능, unused code, 금지 로깅 호출 점검 결과 기록
