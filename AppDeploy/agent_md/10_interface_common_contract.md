# 10. Interface Common Contract Agent

## 1. 역할

AI App Deployer의 외부 제공 인터페이스를 생성·검토하는 모든 에이전트가 공통으로 준수해야 하는 범위, 용어, 금지사항, 산출물 기준을 정의한다.

## 2. 담당 범위

본 작업은 다음 두 산출물에만 연결된다.

```text
1. AI 반도체 기반 AI 응용 배포 및 운용 구조 설계
2. CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입 개발
```

## 3. 인터페이스 제공 범위

```text
- AI App 등록/조회 API
- Runtime Profile 등록/조회 API
- Target Profile 등록/조회 API
- Resource Check / Resource Inventory API
- Deployment 생성/목록/상태/로그/중지 API
- Monitoring summary/runtime-health/alarms/metrics API
- OpenAPI/Swagger 제공
- 외부 연동 책임 경계 문서화
- 요청/응답 예제 JSON
- Interface smoke/contract test 기준
```

## 4. 1차년도 제외 범위

다음 항목은 구현하거나 OpenAPI 활성 계약에 포함하지 않는다.

```text
- Docker 기반 배포
- Docker Compose 기반 배포
- Kubernetes 기반 배포
- Helm 기반 배포
- OCI Image / Container Registry 연동
- Kubernetes Node/Pod capacity 평가
- LLM 운영관리
- Agent Registry / Agent Interface
- 자연어 기반 배포 요청 처리
- 추론 최적화 전략
- 실제 ETRI/Innogrid/Bespin 내부 API 구현
```

## 5. 필수 용어

| 용어 | 의미 |
| --- | --- |
| AI App Deployer | 경희대학교가 구현하는 AI 응용 등록·배포·운용 상위 관리 제어 계층 |
| AI App Deployment | 본 시스템의 배포 작업 도메인 객체. Kubernetes Deployment와 다름 |
| Runtime Profile | Runtime 능력과 Adapter 유형을 정의하는 프로파일 |
| Target Profile | 실제 배포 대상 VM 또는 외부 API 대상 정보를 정의하는 프로파일 |
| Resource Inventory | Resource Check 결과 snapshot |
| Runtime Adapter | Mock/CPU VM/GPU VM/ETRI AI-Infra 실행을 추상화하는 내부 인터페이스 |

## 6. 공통 개발 규칙

```text
- 모든 업무 API는 /api/v1 하위에 둔다.
- /openapi.yaml과 /swagger는 문서 제공 경로로 유지한다.
- request_id를 모든 응답에 포함한다.
- 실패 응답은 ErrorResponse 형식을 따른다.
- Secret 값은 request/response example, log, test fixture에 포함하지 않는다.
- credential_ref만 문서와 예제에 사용한다.
- OpenAPI, Go handler, examples, smoke script, docs는 함께 갱신한다.
```

## 7. 산출물

```text
contracts/openapi/openapi.yaml

docs/interface/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
docs/interface/외부연동_책임경계.md
docs/interface/상태_에러코드_정의.md

examples/requests/*.json
examples/responses/*.json

tests/interface-contract-test-checklist.md
scripts/api-smoke.ps1
```

## 8. 검토 체크리스트

| 항목 | 기준 |
| --- | --- |
| API Prefix | 모든 업무 API가 `/api/v1` 하위인지 확인 |
| OpenAPI 일치 | 문서의 endpoint가 openapi.yaml과 일치하는지 확인 |
| Example 동작 | 제공한 request JSON으로 smoke test가 가능한지 확인 |
| ErrorResponse | 모든 실패 예제가 표준 형식인지 확인 |
| 상태 Enum | 문서, OpenAPI, 코드, 테스트가 동일한 상태값을 사용하는지 확인 |
| Container 제외 | Docker/Kubernetes/Container 관련 API가 포함되지 않는지 확인 |
| 외부 API 경계 | 실제 외부 플랫폼 API 구현으로 오해될 표현이 없는지 확인 |
