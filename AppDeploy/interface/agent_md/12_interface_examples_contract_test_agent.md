# 12. Interface Examples and Contract Test Agent

## 1. 역할

외부 제공 인터페이스를 실제로 호출해 볼 수 있도록 요청/응답 예제 JSON, smoke script, interface contract test checklist를 생성하고 검증한다.

## 2. 산출물

```text
examples/requests/app-create-cpu.json
examples/requests/app-create-gpu.json
examples/requests/app-create-invalid-container.json
examples/requests/runtime-profile-mock.json
examples/requests/runtime-profile-cpu-vm.json
examples/requests/runtime-profile-gpu-vm.json
examples/requests/target-profile-aws-gpu.json
examples/requests/resource-check-gpu.json
examples/requests/deployment-create-gpu.json
examples/requests/deployment-stop.json
examples/requests/metric-create-placeholder.json

examples/responses/app-create-success.json
examples/responses/deployment-create-success.json
examples/responses/deployment-status-running.json
examples/responses/deployment-logs-success.json
examples/responses/error-app-spec-invalid.json
examples/responses/error-gpu-runtime-not-found.json

tests/interface-contract-test-checklist.md
```

## 3. 예제 작성 규칙

```text
- 실제 IP, 계정, token, SSH key, cloud credential을 넣지 않는다.
- VM 접속 정보는 credential_ref만 사용한다.
- artifact.type은 package/git/binary/script만 사용한다.
- 실패 예제에 한해서 container artifact를 넣고 APP_SPEC_INVALID를 기대한다.
- GPU 예제에는 runtime.type=gpu, accelerator=nvidia, resources.gpu=1을 포함한다.
- Target Profile에는 storage.artifact_dir, storage.model_dir, storage.log_dir을 포함한다.
- 모든 response 예제에는 request_id를 포함한다.
```

## 4. Interface Contract Test 항목

| TC ID | 항목 | 기대 결과 |
| --- | --- | --- |
| TC-IF-001 | OpenAPI 조회 | `/openapi.yaml` 정상 응답 |
| TC-IF-002 | Swagger 조회 | `/swagger` 정상 표시 |
| TC-IF-003 | CPU App 등록 | app_id, app_version_id 반환 |
| TC-IF-004 | GPU App 등록 | app_id, app_version_id 반환 |
| TC-IF-005 | Container artifact 거부 | APP_SPEC_INVALID 반환 |
| TC-IF-006 | Runtime Profile 등록 | runtime_profile_id 반환 |
| TC-IF-007 | Target Profile 등록 | target_profile_id 반환 |
| TC-IF-008 | Resource Check | READY 또는 상세 failure 반환 |
| TC-IF-009 | Deployment 생성 | deployment_id, REQUESTED 반환 |
| TC-IF-010 | Deployment 상태 조회 | 표준 status enum 반환 |
| TC-IF-011 | Deployment 로그 조회 | request_id, deployment_id, stage 포함 |
| TC-IF-012 | Deployment 중지 | STOPPING 또는 STOPPED 반환 |
| TC-IF-013 | Monitoring summary | 상태 집계 반환 |
| TC-IF-014 | Metric placeholder | 저장/조회 가능 |
| TC-IF-015 | 범위 검수 | Docker/K8s/Container API 없음 |

## 5. Smoke Script 지시

`scripts/api-smoke.ps1` 또는 동일 기능의 shell script는 다음 순서로 실행되어야 한다.

```text
1. GET /api/v1/healthz
2. GET /api/v1/readiness
3. POST /api/v1/apps CPU 예제
4. POST /api/v1/apps GPU 예제
5. POST /api/v1/apps invalid container 예제
6. POST /api/v1/runtime-profiles
7. POST /api/v1/target-profiles
8. POST /api/v1/resources/check
9. POST /api/v1/deployments
10. GET /api/v1/deployments/{deployment_id}
11. GET /api/v1/deployments/{deployment_id}/logs
12. POST /api/v1/deployments/{deployment_id}/metrics
13. GET /api/v1/monitoring/summary
14. GET /api/v1/monitoring/runtime-health
15. GET /api/v1/monitoring/alarms
16. GET /api/v1/monitoring/metrics
17. POST /api/v1/deployments/{deployment_id}/stop
```

## 6. 에이전트 출력 형식

```markdown
# Interface Example and Contract Test Report

## 생성한 예제 파일

## API별 요청/응답 연결표

## Smoke 실행 결과

## 실패 케이스 확인

## 민감정보 점검 결과

## 남은 이슈
```
