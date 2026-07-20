# 10. 외부 인터페이스 에이전트

## 역할
AI App Deployer의 외부 제공 인터페이스, OpenAPI 계약, 예제, smoke/contract test, 외부 책임 경계를 함께 검토한다.

Deployment 인터페이스에서 Planner는 App/resource 요구사항과 Manifest를 제공하고 상태·재시도 정책을 결정한다. Target Profile 선택, VM readiness/자원 검사, artifact 생성 및 배포 실행은 App Deployer 책임이다. `target_profile_id`는 선택적 hint로만 취급한다.

## 담당 범위
- AI App 등록/조회/등록 삭제 API
- Target Profile 등록/조회/삭제 API
- Resource Check / Resource Inventory API
- Deployment 생성/목록/상태/로그/중지 API
- Monitoring summary/runtime-health/alarms/metrics API
- OpenAPI/Swagger 제공
- 외부 연동 책임 경계 문서화
- 요청/응답 예제 JSON
- Interface smoke/contract test 기준

## 제외 범위
- Docker, Docker Compose, Kubernetes, Helm, OCI Image, Container Registry 연동
- Kubernetes Node/Pod capacity 평가
- LLM 운영관리
- Agent Registry / Agent Interface
- 자연어 기반 배포 요청 처리
- 추론 최적화 전략
- 실제 ETRI/Innogrid/Bespin 내부 API 구현

## OpenAPI 기준
- API source of truth는 `contracts/openapi/openapi.yaml`이다.
- 모든 업무 API는 `/api/v1` 하위에 둔다. `/openapi.yaml`, `/swagger`는 문서 제공 경로로 유지한다.
- Handler godoc/Swagger 주석을 작성하더라도 OpenAPI 계약과 일치해야 한다.
- OpenAPI, Go handler, examples, smoke script, docs는 함께 갱신한다.
- 활성 enum은 `agent_md/00_scope_common_contract.md`의 상태값과 에러 코드를 따른다.
- `artifact.type`은 `package`, `git`, `binary`, `script`만 허용한다.
- App 삭제 API는 Registry 레코드만 삭제하고 STOPPED Deployment 이력, artifact, VM 배포 파일을 유지한다. STOPPED 이외 상태의 참조가 있으면 409를 반환한다.
- Runtime/Target Profile 삭제 API는 미참조 또는 모든 참조 Deployment가 STOPPED일 때만 등록을 삭제한다. STOPPED Deployment/Event/Metric 이력과 Credential은 유지하고, Target의 현재 Inventory snapshot만 함께 제거한다. STOPPED 외 참조가 있으면 Profile 종류에 맞는 409를 반환한다.

## ErrorResponse 기준
```json
{
  "request_id": "req-...",
  "error": {
    "code": "APP_SPEC_INVALID",
    "message": "...",
    "details": {},
    "retryable": false
  }
}
```

- 모든 실패 응답은 표준 ErrorResponse를 사용한다.
- `message`는 API caller 관점으로 작성한다.
- raw `err.Error()`, stack trace, DB/SSH connection string, credential, token, 내부 endpoint 원문을 `message`나 `details`에 넣지 않는다.
- 운영 로그는 `zerolog` 구조화 로그를 사용하고 `request_id`, `deployment_id`, `component`, `stage` 등 추적 필드를 포함한다.

## 예제 및 Smoke 기준
- 실제 IP, 계정, token, SSH key, cloud credential을 넣지 않는다.
- VM 접속 정보는 `credential_ref`만 사용한다.
- 실패 예제에 한해서 container artifact를 넣고 `APP_SPEC_INVALID`를 기대한다.
- GPU 예제에는 `runtime.type=gpu`, `accelerator=nvidia`, `resources.gpu=1`을 포함한다.
- 모든 response 예제에는 `request_id`를 포함한다.
- `scripts/api-smoke.ps1` 또는 동일 기능 script는 OpenAPI 조회, App/Profile/Resource/Deployment/Logs/Monitoring/Metrics/Stop 흐름을 검증한다.

## 외부 책임 경계
| 대상 | 경희대학교 제공 | 외부 기관 확정 필요 |
| --- | --- | --- |
| ETRI | Target Profile, Resource Check, ETRI AI-Infra Adapter skeleton | VM 접속 정보, AI-Infra API, API Gateway 정책 |
| Innogrid | App 및 Runtime/Target Profile 등록/등록 삭제/배포 API | 호출 주체, field mapping, artifact·이력 보존 정책 |
| Bespin Web Console | OpenAPI 기반 App/Profile 조회/등록 삭제/배포/모니터링 API | 화면 action과 인증 정책 |
| Bespin MCP-like API | REST API 호출 시나리오 | MCP tool schema |
| API Gateway | healthz/readiness 및 `/api/v1` 라우팅 대상 | 인증/라우팅/timeout/retry |

실제 외부 API는 계약 확정 후 `internal/external` client 교체 방식으로 연동한다. 현재 상세 경계는 `deliverables/interface/external/외부_연동_경계_정리.md`에서 관리한다.

## 검토 체크리스트
- 모든 업무 API가 `/api/v1` 하위인지 확인한다.
- 문서의 endpoint가 `openapi.yaml`과 일치하는지 확인한다.
- 예제 request JSON으로 smoke test가 가능한지 확인한다.
- 모든 실패 예제가 표준 ErrorResponse인지 확인한다.
- 문서, OpenAPI, 코드, 테스트가 동일한 상태값과 에러 코드를 사용하는지 확인한다.
- Docker/Kubernetes/Container 관련 API가 포함되지 않는지 확인한다.
- 실제 외부 플랫폼 API 구현으로 오해될 표현이 없는지 확인한다.
