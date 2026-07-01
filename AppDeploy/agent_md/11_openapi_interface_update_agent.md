# 11. OpenAPI Interface Update Agent

## 1. 역할

AI App Deployer의 외부 제공 인터페이스를 기준으로 `contracts/openapi/openapi.yaml`을 점검하고, 문서·예제·코드와 일치하도록 수정 지시를 작성한다.

## 2. 입력 자료

```text
contracts/openapi/openapi.yaml
docs/interface/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
docs/api/기능_API_가이드.md
examples/requests/*.json
examples/responses/*.json
internal/*/handler
internal/errors
internal/deployment/state
```

## 3. 작업 범위

### 3.1 반드시 포함할 API

```text
GET  /openapi.yaml
GET  /swagger
GET  /api/v1/healthz
GET  /api/v1/readiness
POST /api/v1/apps
GET  /api/v1/apps
GET  /api/v1/apps/{app_id}
POST /api/v1/runtime-profiles
GET  /api/v1/runtime-profiles
POST /api/v1/target-profiles
GET  /api/v1/target-profiles
POST /api/v1/resources/check
GET  /api/v1/resources/inventory
POST /api/v1/deployments
GET  /api/v1/deployments
GET  /api/v1/deployments/{deployment_id}
GET  /api/v1/deployments/{deployment_id}/logs
POST /api/v1/deployments/{deployment_id}/stop
POST /api/v1/deployments/{deployment_id}/metrics
GET  /api/v1/deployments/{deployment_id}/metrics
GET  /api/v1/monitoring/summary
GET  /api/v1/monitoring/runtime-health
GET  /api/v1/monitoring/alarms
GET  /api/v1/monitoring/metrics
```

### 3.2 OpenAPI Components 필수 항목

```text
ErrorResponse
AppSpec
AppCreateResponse
RuntimeProfile
TargetProfile
ResourceCheckRequest
ResourceCheckResponse
DeploymentCreateRequest
DeploymentCreateResponse
DeploymentStatusResponse
DeploymentLogResponse
MetricRecordRequest
MetricRecordResponse
MonitoringSummaryResponse
```

## 4. Enum 규칙

### 4.1 artifact.type

허용:

```text
package
git
binary
script
```

금지:

```text
container
oci_image
docker_image
helm_chart
k8s_manifest
```

### 4.2 runtime_type / adapter_type

1차년도 활성값:

```text
mock
cpu
gpu
aiinfra

mock
cpu_vm
gpu_vm
etri_aiinfra
```

Kubernetes 관련 enum은 1차년도 OpenAPI 활성 enum에 넣지 않는다.

### 4.3 deployment.status

```text
REQUESTED
VALIDATING
VALIDATED
SCHEDULING
DEPLOYING
RUNNING
STOPPING
STOPPED
VALIDATION_FAILED
SCHEDULING_FAILED
DEPLOYMENT_FAILED
RUNTIME_FAILED
EXTERNAL_API_FAILED
UNKNOWN
```

## 5. ErrorResponse 규칙

모든 4xx/5xx 응답에는 같은 ErrorResponse schema를 사용한다.

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

## 6. OpenAPI 수정 후 검증

```bash
# 예시. 실제 프로젝트에서 사용하는 lint 도구로 대체 가능
npx @redocly/cli lint contracts/openapi/openapi.yaml

go test ./...
go vet ./...
```

## 7. 에이전트 출력 형식

OpenAPI 수정 에이전트는 다음 형식으로 결과를 남긴다.

```markdown
# OpenAPI Interface Update Report

## 변경 요약

## 추가/수정한 Paths

## 추가/수정한 Schemas

## Enum 검토 결과

## ErrorResponse 일치성 검토

## Example 연결 현황

## 테스트 결과

## 남은 이슈
```
