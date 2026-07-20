# Interface Contract Test Checklist

| TC ID | 항목 | 실행 기준 | 기대 결과 |
| --- | --- | --- | --- |
| TC-IF-001 | OpenAPI 조회 | `GET /openapi.yaml` | YAML 정상 응답 |
| TC-IF-002 | Swagger 조회 | `GET /swagger` | 문서 화면 정상 표시 |
| TC-IF-003 | CPU App 등록 | `deliverables/interface/examples/requests/app-create-cpu.json` | app_id, app_version_id 반환 |
| TC-IF-004 | GPU App 등록 | `deliverables/interface/examples/requests/app-create-gpu.json` | app_id, app_version_id 반환 |
| TC-IF-005 | Container artifact 거부 | `deliverables/interface/examples/requests/app-create-invalid-container.json` | APP_SPEC_INVALID 반환 |
| TC-IF-APP-001 | App 등록 삭제 | 미사용 App에 `DELETE /api/v1/apps/{app_id}` 또는 `apps delete <app-id> --yes` | `app-delete-success.json` 형태의 응답, 목록/상세 제외, 같은 name/version 재등록 가능 |
| TC-IF-APP-002 | STOPPED 이력 App 삭제 | 모든 참조 Deployment를 STOPPED로 만든 뒤 DELETE | App 등록 삭제, STOPPED Deployment 이력과 package/git/binary/script 및 VM 파일 유지 |
| TC-IF-APP-003 | App 삭제 참조 보호 | STOPPED 외 참조 Deployment가 하나 이상인 App에 DELETE | APP_SPEC_INVALID/409, App 등록과 Deployment 이력 및 artifact·VM 파일 유지 |
| TC-IF-007 | Target Profile 등록 | `deliverables/interface/examples/requests/target-profile-aws-gpu.json` | target_profile_id, profile_id 반환 |
| TC-IF-PROFILE-001 | 미참조 Profile 삭제 | 미사용 Runtime/Target에 각 DELETE API를 호출하고 이름 없는 Profile도 확인 | 정확한 공통 응답 필드, `name` 항상 포함(미저장 시 빈 문자열), 목록 제외 |
| TC-IF-PROFILE-002 | STOPPED 이력 Profile 삭제 | 모든 참조 Deployment를 STOPPED로 만든 뒤 Runtime/Target DELETE | Profile 삭제, STOPPED Deployment/Event/Metric 이력 유지 |
| TC-IF-PROFILE-003 | Profile 삭제 참조 보호 | STOPPED 외 참조 Deployment가 하나 이상인 Runtime/Target에 DELETE | 각각 `RUNTIME_PROFILE_INVALID`/409, `TARGET_PROFILE_INVALID`/409이며 Profile과 Inventory 유지 |
| TC-IF-PROFILE-004 | Target Inventory·Credential 경계 | Resource Check 뒤 Target을 삭제하고 Credential registry 및 Runtime 삭제 응답도 확인 | Target snapshot 실제 제거 시만 `inventory_deleted=true`, Credential 유지, Runtime은 항상 `false` |
| TC-IF-PROFILE-005 | 없는 Profile 삭제 | 없는 Runtime/Target ID 또는 삭제 완료 ID에 DELETE | `NOT_FOUND`/404 |
| TC-IF-008 | Resource Check | `deliverables/interface/examples/requests/resource-check-gpu.json` | available 또는 표준 failure 반환 |
| TC-IF-009 | Deployment 생성 | `deliverables/interface/examples/requests/deployment-create-gpu.json` | deployment_id, 표준 status 반환 |
| TC-IF-009-M | Manifest-only Deployment 생성 | `deliverables/interface/examples/requests/deployment-create-manifest.json` | 등록된 Profile 참조로 Manifest 정규화·배포, 응답에 manifest 반환 |
| TC-IF-009-T | Planner Manifest Deployment 생성 | `POST /api/v1/deployments`에 `manifest.spec.app_version_id`와 resource envelope를 전송하고 Target hint는 생략 | App Deployer가 VM readiness/자원 매칭 후 선택한 `target_profile_id`를 응답과 normalized manifest에 기록 |
| TC-IF-010 | Deployment 상태 조회 | `GET /api/v1/deployments/{deployment_id}` | 표준 status enum 반환 |
| TC-IF-011 | Deployment 로그 조회 | `GET /api/v1/deployments/{deployment_id}/logs` | request_id, deployment_id, stage 포함 |
| TC-IF-012 | Deployment 중지 | `deliverables/interface/examples/requests/deployment-stop.json` | STOPPING 또는 STOPPED 반환 |
| TC-IF-013 | Monitoring summary | `GET /api/v1/monitoring/summary` | 상태 집계 반환 |
| TC-IF-014 | Metric placeholder | metric create/list API | 저장/조회 가능 |
| TC-IF-015 | 범위 검수 | OpenAPI paths/components 검색 | Docker/K8s/Container API 없음 |
| TC-IF-PKG-001 | 프리셋 Package 생성 | `deliverables/interface/examples/requests/package-build-aiops-geon.json`을 `POST /api/v1/artifacts/packages`에 JSON으로 전송 | package, checksum, app_spec 반환 |
| TC-IF-PKG-002 | 업로드 Package 생성 | script 파일과 `package_type=script`를 multipart로 전송 | Linux amd64 tar.gz와 등록 가능한 app_spec 반환 |
| TC-IF-PKG-003 | Package 연속 배포 | Package 응답의 app_spec 등록 → Deployment 요청 (Target hint 선택) | App Deployer가 readiness/자원 검사를 수행하고 선택 결과와 표준 deployment status 반환 |
| TC-IF-PKG-004 | Package 입력 거부 | 미지원 유형, 빈 source, unsafe ZIP 또는 제한 초과 업로드 | APP_SPEC_INVALID 또는 APP_ARTIFACT_NOT_FOUND 반환 |
| TC-IF-CRED-001 | Runtime Credential 등록 | 신뢰된 OpenSSH SHA256 fingerprint와 실행 중 생성한 canary로 Web/API/CLI의 private_key/password 분기를 각각 등록하고 정적 secret fixture는 만들지 않음 | 응답은 `host_key_fingerprint` 등 공개 메타데이터와 `persistent=false`만 포함하고 비밀값은 포함하지 않음 |
| TC-IF-CRED-002 | Runtime Credential 목록/ENV | Runtime 등록 후 GET하고 `_SSH_HOST_KEY_FINGERPRINT`를 포함한 ENV Credential도 구성 | Runtime 항목은 fingerprint를 포함하고 ENV 항목과 모든 비밀값은 제외하며, ENV fingerprint 누락은 해석 실패 |
| TC-IF-CRED-003 | Runtime Credential 삭제 | Target이 참조하는 ID를 DELETE한 뒤 SSH runner 실제 연결과 dry-run을 각각 수행하고 동일 ID를 다시 DELETE | 첫 삭제 성공, 실제 SSH 연결은 Credential 누락 실패, dry-run은 Credential 미해석, 재삭제는 `NOT_FOUND`/404 |
| TC-IF-CRED-004 | Credential 입력·접근 보호 | ID/인증 분기/fingerprint·개별 secret 길이 오류, 중복, 128 KiB 초과, non-JSON POST, 비loopback peer/Host, cross-origin 요청 | 각각 `TARGET_PROFILE_INVALID`/400·409·413 또는 `GATEWAY_AUTH_FAILED`/403 |
| TC-IF-CRED-005 | Credential 수명·비노출 | 등록 후 프로세스를 재시작하고 응답·store·로그·Web/CLI 출력·증적에서 canary 검색 | Runtime 목록이 비고 canary가 어디에도 남지 않음 |
| TC-IF-CRED-006 | SSH host key 고정 | 신뢰된 fingerprint와 일치/불일치하는 SSH 서버 키로 실제 handshake 또는 callback 시험 | 일치 키만 허용하고 불일치 키는 인증 정보 사용 전에 거부 |
| TC-IF-CRED-007 | Target credential_ref 보호 | 유효한 `cred://namespace/id`, 256자 경계, 임의 문자열·비밀번호·PEM·token canary를 Target 등록 | 제한 형식만 저장하며 Secret 직접 입력은 400이고 store/목록/오류에 canary가 없음 |
| TC-IF-CRED-008 | Credential 원격 opt-in | `AIAPP_CREDENTIAL_API_ALLOW_REMOTE=true`에서 TLS·인증 reverse proxy를 거쳐 호출 | 인증된 원격 호출만 허용되고 proxy 구성·감사 로그를 증적으로 보존 |
