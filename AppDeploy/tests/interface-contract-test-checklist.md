# Interface Contract Test Checklist

| TC ID | 항목 | 실행 기준 | 기대 결과 |
| --- | --- | --- | --- |
| TC-IF-001 | OpenAPI 조회 | `GET /openapi.yaml` | YAML 정상 응답 |
| TC-IF-002 | Swagger 조회 | `GET /swagger` | 문서 화면 정상 표시 |
| TC-IF-003 | CPU App 등록 | `examples/interface/requests/app-create-cpu.json` | app_id, app_version_id 반환 |
| TC-IF-004 | GPU App 등록 | `examples/interface/requests/app-create-gpu.json` | app_id, app_version_id 반환 |
| TC-IF-005 | Container artifact 거부 | `examples/interface/requests/app-create-invalid-container.json` | APP_SPEC_INVALID 반환 |
| TC-IF-006 | Runtime Profile 등록 | `examples/interface/requests/runtime-profile-gpu-vm.json` | runtime_profile_id, profile_id 반환 |
| TC-IF-007 | Target Profile 등록 | `examples/interface/requests/target-profile-aws-gpu.json` | target_profile_id, profile_id 반환 |
| TC-IF-008 | Resource Check | `examples/interface/requests/resource-check-gpu.json` | available 또는 표준 failure 반환 |
| TC-IF-009 | Deployment 생성 | `examples/interface/requests/deployment-create-gpu.json` | deployment_id, 표준 status 반환 |
| TC-IF-010 | Deployment 상태 조회 | `GET /api/v1/deployments/{deployment_id}` | 표준 status enum 반환 |
| TC-IF-011 | Deployment 로그 조회 | `GET /api/v1/deployments/{deployment_id}/logs` | request_id, deployment_id, stage 포함 |
| TC-IF-012 | Deployment 중지 | `examples/interface/requests/deployment-stop.json` | STOPPING 또는 STOPPED 반환 |
| TC-IF-013 | Monitoring summary | `GET /api/v1/monitoring/summary` | 상태 집계 반환 |
| TC-IF-014 | Metric placeholder | metric create/list API | 저장/조회 가능 |
| TC-IF-015 | 범위 검수 | OpenAPI paths/components 검색 | Docker/K8s/Container API 없음 |
