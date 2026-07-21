# geon deletion controls design

## Goal

geon Agent Control에서 geon이 소유한 시험 데이터를 안전하게 삭제한다. AppDeploy가 소유한 App, Deployment, Runtime Profile, Target Profile 및 실제 VM 자원은 이 기능의 범위에 포함하지 않는다.

## Scope

삭제 대상은 다음 세 종류로 제한한다.

1. 실행 중인 geon 프로세스에 API로 등록한 Runtime Agent
2. geon Autonomous Loop가 메모리에 보관하는 최근 이벤트
3. 브라우저 localStorage에 보관한 최근 제어 결과

`config/agent_registry.json`에서 로드한 Configuration Agent는 연구 프레임워크의 정책 구성 요소이므로 API와 웹 화면에서 삭제할 수 없다. 특히 `AIApplicationAutomationAgent`는 Qwen Planner, Agent Registry 및 Go Guard 연결에 사용되므로 보호한다.

## API design

### DELETE `/api/v1/agents/{name}`

- `source=runtime`인 Agent만 삭제한다.
- Configuration Agent 삭제 요청에는 `403 Forbidden`을 반환한다.
- 존재하지 않는 Agent에는 `404 Not Found`를 반환한다.
- 성공 시 `200 OK`와 삭제한 Agent 이름, source 및 `deleted=true`를 반환한다.
- 삭제는 runtime store의 mutex 안에서 원자적으로 처리한다.
- 다른 요청이 삭제 직전에 읽은 Agent snapshot은 유효할 수 있지만, 삭제 완료 후 새 조회와 invocation plan 생성에는 Agent가 나타나지 않는다.

### DELETE `/api/v1/autonomy/events`

- 호출 시점까지 저장된 Autonomous Loop 이벤트를 원자적으로 비운다.
- 성공 시 `200 OK`와 `deleted_count`, `deleted=true`를 반환한다.
- loop 실행 여부, Monitor Only/Guarded Auto 모드, SLO 설정, deployment state, cooldown 및 Action budget은 변경하지 않는다.
- loop가 실행 중이면 삭제 후 발생한 새 이벤트는 다시 저장한다.
- event sequence는 재사용하지 않고 계속 증가시켜 이벤트 식별 순서를 보존한다.

### Authorization

두 DELETE API는 기존 Autonomous Loop 상태 변경 API와 동일한 관리자 보호 규칙을 사용한다.

- loopback 요청은 로컬 개발용으로 허용한다.
- 외부 bind 환경에서는 `AIOPS_AUTONOMY_ADMIN_TOKEN` Bearer token이 필요하다.
- 관리자 토큰은 응답, 이벤트 및 로그에 기록하지 않는다.

## Web UI

### Agent Registry

- Runtime Agent 행에만 Lucide trash 아이콘 버튼을 표시한다.
- Configuration Agent에는 삭제 버튼 대신 잠금 상태를 표시한다.
- 삭제 전 Agent 이름을 포함한 확인창을 표시한다.
- 성공 후 Agent 목록, Agent count 및 선택 상태를 다시 불러온다.
- 현재 선택된 Agent를 삭제하면 상세 영역을 빈 상태로 전환한다.

### Autonomous Loop

- Event Timeline 헤더에 `Clear events` 아이콘 버튼을 추가한다.
- 확인창에는 설정과 AppDeploy 자원은 삭제되지 않는다고 표시한다.
- 성공 후 Timeline을 다시 불러오되 새 이벤트가 이미 발생했다면 그대로 표시한다.

### Local history

- 기존 기록 삭제 버튼에 확인창을 추가한다.
- localStorage의 geon history만 삭제하며 서버 데이터에는 영향을 주지 않는다.

## Error handling

- 삭제 요청 중에는 해당 버튼을 disabled 상태로 유지한다.
- `403`, `404`, 네트워크 오류는 기존 toast UI로 표시한다.
- API 오류가 발생하면 화면에서 항목을 먼저 제거하지 않는다.
- 서버 성공 응답 후 목록을 재조회하여 화면과 서버 상태를 일치시킨다.

## Testing

### Runtime store and service tests

- Runtime Agent 삭제 성공
- 삭제 후 조회 및 invocation plan 생성 실패
- Configuration Agent 삭제 거부
- 존재하지 않는 Agent 삭제 시 not found
- 동시 list/delete에서 race 없이 일관된 snapshot 반환

### Autonomy manager and API tests

- 이벤트 삭제 수 반환
- 삭제 후 event list가 비어 있음
- mode, config, state, cooldown 및 Action count 유지
- 삭제 후 생성된 이벤트 sequence가 이전 sequence보다 큼
- 외부 요청의 관리자 인증 적용

### Web tests

- DELETE endpoint 문자열과 삭제 control 존재
- Runtime/Configuration Agent별 삭제 control 분기
- confirmation 및 refresh handler 연결
- 전체 JavaScript syntax check

## Non-goals

- AppDeploy App, Deployment, Runtime Profile 및 Target Profile 삭제
- CB-Tumblebug Infra 또는 CSP VM 삭제
- `config/agent_registry.json` 자동 수정
- 연구 증적 파일과 benchmark 결과 삭제
- 삭제 복구 및 영구 audit database
