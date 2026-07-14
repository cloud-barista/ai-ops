# 기능/API 가이드 초안

## 기준
- API Prefix: `/api/v1`
- API 계약: `contracts/openapi/openapi.yaml`
- 공통 에러 응답: `ErrorResponse`
- 1차년도 artifact.type: `package`, `git`, `binary`, `script`
- 컨테이너 기반 배포 API는 포함하지 않는다.
- 후속 개발 우선순위: `docs/planning/경희대_1차년도_개발_백로그.md`
- 제출 체크리스트: `deliverables/release/1차년도_제출_패키지_체크리스트.md`
- 외부 연동 경계: `deliverables/interface/external/외부_연동_경계_정리.md`
- 외부 제공 인터페이스 명세: `deliverables/interface/spec/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md`

## 핵심 API
| Method | Endpoint | 설명 |
| --- | --- | --- |
| GET | /openapi.yaml | OpenAPI 원본 YAML 조회 |
| GET | /swagger | Swagger/Redoc HTML 문서 조회 |
| GET | /api/v1/healthz | 서버 생존 상태 확인 |
| GET | /api/v1/readiness | 저장소/Runtime/Target/외부 API 준비 상태 확인 |
| POST | /api/v1/credentials | SSH Credential을 프로세스 메모리에 등록 |
| GET | /api/v1/credentials | 프로세스 메모리 Credential 메타데이터 목록 조회 |
| DELETE | /api/v1/credentials/{credential_id} | 프로세스 메모리 Credential 삭제 |
| POST | /api/v1/artifacts/packages | 서버 프리셋 또는 업로드한 유형별 소스를 Linux amd64 package로 생성 |
| POST | /api/v1/apps | AI App 등록 |
| GET | /api/v1/apps | AI App 목록 조회 |
| DELETE | /api/v1/apps/{app_id} | 미참조 또는 모든 참조 Deployment가 STOPPED인 AI App 등록 삭제 |
| POST | /api/v1/runtime-profiles | Runtime Profile 등록 |
| GET | /api/v1/runtime-profiles | Runtime Profile 목록 조회 |
| DELETE | /api/v1/runtime-profiles/{runtime_profile_id} | 미참조 또는 모든 참조 Deployment가 STOPPED인 Runtime Profile 삭제 |
| POST | /api/v1/target-profiles | Target Profile 등록 |
| GET | /api/v1/target-profiles | Target Profile 목록 조회 |
| DELETE | /api/v1/target-profiles/{target_profile_id} | 미참조 또는 모든 참조 Deployment가 STOPPED인 Target Profile 삭제 |
| POST | /api/v1/deployments | AI App 배포 요청 |
| GET | /api/v1/deployments/{deployment_id} | 배포 상태 조회 |
| GET | /api/v1/deployments/{deployment_id}/logs | 배포 로그 조회 |
| POST | /api/v1/deployments/{deployment_id}/stop | 배포 중지 |
| POST | /api/v1/resources/check | Target 자원 readiness 점검 |
| GET | /api/v1/inference/{deployment_id}/health | Running deployment inference health proxy |
| POST | /api/v1/inference/{deployment_id}/invoke | Running deployment inference request proxy |
| GET | /api/v1/monitoring/summary | Deployment/Runtime/Alarm 통합 모니터링 요약 |
| GET | /api/v1/monitoring/runtime-health | Target별 Runtime health snapshot 조회 |
| GET | /api/v1/monitoring/alarms | Deployment 실패 이벤트 기반 알람 요약 조회 |

## Swagger 확인
서버 실행 후 다음 URL에서 API 문서를 확인한다.

```text
http://localhost:8080/swagger
http://localhost:8080/openapi.yaml
```

### Runtime Credential 관리

`POST /api/v1/credentials`는 `credential_id`, `credential_type=ssh`, `auth_type`, `ssh_user`, 필수 `host_key_fingerprint`, `ssh_timeout_seconds`와 인증 방식에 맞는 `private_key` 또는 `password`를 받는다. `host_key_fingerprint`는 신뢰 가능한 별도 채널에서 확인한 canonical OpenSSH SHA256 fingerprint이며 Secret이 아닌 서버 공개키 메타데이터이다. `private_key_passphrase`는 private key 방식에서만 선택적으로 사용한다. Web은 동일 이름의 입력 필드, CLI는 `--host-key-fingerprint`, ENV resolver는 참조 prefix 뒤의 `_SSH_HOST_KEY_FINGERPRINT`로 이 값을 받는다. 비밀값이 포함된 정적 요청 예제 파일은 제공하지 않는다.

등록된 비밀값은 파일 저장소에 기록하지 않고 AppDeploy 프로세스 메모리에만 보관한다. 응답과 `GET /api/v1/credentials` 목록에는 식별자, `cred://runtime/{credential_id}`, 유형, SSH 사용자, `host_key_fingerprint`, timeout, `persistent=false`, 생성 시각만 포함하며 private key, passphrase, password는 반환하지 않는다. 서버 재시작 시 Runtime Credential은 모두 소실되고, 환경변수로 주입한 기존 Credential은 목록에 나타나지 않는다.

Target Profile에는 실제 비밀값 대신 최대 256자의 제한된 `credential_ref`를 지정한다. 소문자 영숫자와 단일 하이픈으로 구성된 `cred://namespace/id` 형식만 허용하며 Secret 원문은 등록 단계에서 거부한다. `cred://runtime/{credential_id}`는 Runtime Credential을 조회하고, 그 밖의 유효한 ref는 기존 ENV resolver를 사용한다. Target이 참조 중이어도 Runtime Credential을 삭제할 수 있으며, 삭제 응답은 `credential_id`, `credential_ref`, `deleted=true`, `deleted_at`을 반환한다. 삭제된 참조는 SSH runner가 실제 연결할 때 Credential 누락으로 실패하지만 dry-run은 Credential을 해석하지 않는다.

Credential API는 기본적으로 TCP peer와 HTTP `Host`가 모두 loopback이고, `Origin`이 있으면 요청 `Host`와 같은 요청만 허용한다. POST는 `application/json`만 받는다. 원격 호출은 `AIAPP_CREDENTIAL_API_ALLOW_REMOTE=true`일 때만 허용하며, 운영 환경에서는 TLS와 인증을 적용한 reverse proxy 뒤에서만 노출한다. 잘못된 fingerprint·입력이나 개별 secret 필드 최대 길이 위반은 `TARGET_PROFILE_INVALID`/400, 중복 ID는 같은 코드의 409, 전체 JSON 본문 128 KiB 초과는 같은 코드의 413으로 반환한다. 허용되지 않은 peer/Host/Origin은 `GATEWAY_AUTH_FAILED`/403, 없는 ID 삭제는 `NOT_FOUND`/404이다.

CLI는 `credentials add --host-key-fingerprint SHA256:<base64>`, `credentials list`, `credentials delete <id> --yes`를 제공한다. Runtime Credential은 CLI 프로세스 수명에 묶이므로 등록 후 배포까지 대화형 셸 안에서 이어서 수행하며, secret 원문을 명령행 인자로 전달하지 않는다.

## 현재 배포 대상
현재 Deployment가 배포하는 대상은 App Spec의 `artifact`와 `entrypoint`이다.

- `POST /api/v1/artifacts/packages`의 JSON 요청은 `preset=aiops-geon-service-control`을 사용한다. multipart 요청은 `go`, `python`, `node`, `binary`, `script` 유형과 소스 ZIP/단일 파일을 받아 tar.gz package와 등록 가능한 App Spec을 반환한다. 임의 서버 경로나 build 명령은 받지 않고 유형별 고정 규칙만 사용한다.
- `artifact.type=script`, `artifact.uri=file://...`이면 로컬 스크립트 파일을 대상 VM의 `storage.artifact_dir/{app_name}/{version}` 경로로 업로드한다.
- 업로드 후 해당 디렉터리에서 `entrypoint.command`와 `entrypoint.args`를 실행한다.
- `POST /api/v1/deployments/{deployment_id}/stop`은 Deployment 상태를 `STOPPING`/`STOPPED`로 갱신하고, CPU/GPU VM Adapter를 통해 원격 VM의 배포 프로세스를 종료한다. 이미 `STOPPED`이면 Adapter를 다시 호출하지 않고 현재 상태를 반환한다.
- CPU VM은 script 실행 흐름을 확인하고, GPU VM은 배포 전 `nvidia-smi` readiness를 확인한다.
- 현재 smoke 예제는 `examples/cpu-smoke-run.sh`, `examples/gpu-smoke-run.sh`이며, 실제 AI 모델 서버는 동일한 방식으로 실행 스크립트를 App Spec에 지정해 배포한다.
- 컨테이너 이미지, Registry, Kubernetes 리소스는 1차년도 배포 대상이 아니다.

### Package 기반 공통 배포 순서

Web, CLI, PowerShell/Bash client는 새 배포 전용 API를 만들지 않고 다음 OpenAPI 계약을 순서대로 조합한다.

1. `POST /api/v1/artifacts/packages`로 Package와 `app_spec`을 생성한다.
2. `POST /api/v1/apps`에 `{"app_spec": <package-response.app_spec>}`를 보내 `app_version_id`를 발급받는다.
3. `POST /api/v1/resources/check`로 선택한 Target의 readiness를 점검한다.
4. 응답 `status=available`일 때만 `POST /api/v1/deployments`에 발급된 `app_version_id`, `runtime_profile_id`, `target_profile_id`를 보낸다. Runtime Profile 존재·호환성은 이 Deployment 요청에서 검증한다.

프리셋 요청은 `application/json`, 업로드 요청은 `multipart/form-data`이다. 생성 응답 전체를 App 등록 본문으로 보내지 않고 `app_spec`만 래핑한다. 각 단계는 독립 API 호출이므로 후속 단계 실패 시 이미 생성된 Package나 App을 자동 rollback하지 않는다. CLI와 셸은 실패 시 archive/App 식별자를 오류 또는 `partial_result`로 남긴다.

```bash
curl -X POST http://localhost:8080/api/v1/artifacts/packages \
  -F package_type=script -F source=@run.sh \
  -F app_name=sample-shell-app -F app_version=0.1.0 \
  -F entrypoint=run.sh -F runtime_type=cpu
```

CLI는 `packages build`, `packages deploy` 또는 `deploy package`를 제공한다. HTTP 서버 상태를 그대로 사용하는 자동화는 `scripts/package-deploy.ps1`과 `scripts/package-deploy.sh`를 사용한다.

### Runtime/Target Profile 등록 삭제

`DELETE /api/v1/runtime-profiles/{runtime_profile_id}`와 `DELETE /api/v1/target-profiles/{target_profile_id}`는 Profile 등록 정보만 삭제한다. 해당 Profile을 참조하는 Deployment가 없거나 모두 `STOPPED`일 때 200을 반환하고, STOPPED가 아닌 참조가 있으면 각각 `RUNTIME_PROFILE_INVALID`/409와 `TARGET_PROFILE_INVALID`/409를 반환한다. 없는 ID는 `NOT_FOUND`/404이다.

공통 `ProfileDeleteResponse`는 `request_id`, `profile_type`, `profile_id`, `name`, `deleted=true`, `inventory_deleted`, `deleted_at`을 항상 반환한다. Profile 이름이 없으면 `name`은 빈 문자열이다. Runtime 삭제의 `inventory_deleted`는 항상 false이고, Target 삭제는 현재 Resource Inventory snapshot이 실제 제거됐는지를 반환한다.

삭제 후에도 STOPPED Deployment와 연결된 Event·Metric 이력은 유지한다. Target Profile 삭제는 Runtime Credential을 연쇄 삭제하지 않으며, Credential 삭제 역시 Target 참조 여부와 독립적이다.

```bash
curl -X DELETE http://localhost:8080/api/v1/runtime-profiles/rt-cpu-001
curl -X DELETE http://localhost:8080/api/v1/target-profiles/target-cpu-001
```

### App 등록 삭제

`DELETE /api/v1/apps/{app_id}`는 해당 App의 Registry record만 삭제한다. 참조가 없거나 `app_id` 또는 `app_version_id`를 참조하는 모든 Deployment가 `STOPPED`이면 삭제할 수 있고, STOPPED Deployment 이력은 보존한다. `STOPPED` 외 상태가 하나라도 있으면 `APP_SPEC_INVALID`/409를 반환한다. 성공 응답에는 `deleted=true`, `artifact_deleted=false`, `deleted_at`이 포함되며 package, git, binary, script 원본과 VM 배포 파일은 유지한다.

```bash
curl -X DELETE http://localhost:8080/api/v1/apps/app-001
```

```powershell
go run ./cmd/appdeployer apps delete app-001 --yes
```

웹 콘솔에서는 App 목록의 `등록 삭제`를 사용하며 실제 요청 전에 확인 창을 표시한다.

## 외부 API 연동 골격
외부 API 명세가 확정되기 전까지 실제 ETRI/이노그리드/베스핀글로벌 API 호출은 구현하지 않는다.

- `internal/external`에 외부 배포 클라이언트 인터페이스와 표준 에러 매핑을 둔다.
- `internal/external/etri`, `internal/external/innogrid`, `internal/external/bespin`은 업체별 클라이언트 교체 지점이다.
- 현재 서버는 `runtime_type=aiinfra`, `adapter_type=etri_aiinfra`, `operating_mode=remote_api` 조합을 ETRI Mock/Fixture 클라이언트로 처리한다.
- 외부 연동 실패는 `AI_INFRA_API_TIMEOUT`, `AI_INFRA_API_FAILED`, `GATEWAY_AUTH_FAILED`, `BESPIN_API_FAILED` 같은 표준 에러 코드로 정규화한다.

## 모니터링 API
모니터링 API는 현재 저장된 Deployment, DeploymentEvent, ResourceInventory를 기반으로 운영 상태를 요약한다.

- `/api/v1/monitoring/summary`: 전체 상태, Deployment 상태 집계, Runtime health, 알람 요약을 함께 반환한다.
- `/api/v1/monitoring/runtime-health`: `/api/v1/resources/check`로 저장된 Target별 health snapshot을 반환한다.
- `/api/v1/monitoring/alarms`: ERROR 이벤트와 `error_code`를 기준으로 실패 원인을 집계한다.

## 요청 예시
```bash
curl -X POST http://localhost:8080/api/v1/apps \
  -H 'Content-Type: application/json' \
  -d @examples/app-create-request.json
```

외부 제공 인터페이스 예제는 `deliverables/interface/examples/requests`와 `deliverables/interface/examples/responses`에 별도로 둔다. `POST /api/v1/apps`는 기존 `{"app_spec": {...}}` 래퍼와 외부 제공 인터페이스의 직접 App Spec 본문을 모두 허용한다.

## Metric API

Inference metric placeholder는 1차년도 운용 상태 확장을 위한 저장/조회 경로이다.

| Method | Endpoint | Description |
| --- | --- | --- |
| POST | /api/v1/deployments/{deployment_id}/metrics | Record latency, throughput, quality_score, request_count, and error_count |
| GET | /api/v1/deployments/{deployment_id}/metrics | List metrics for one deployment |
| GET | /api/v1/monitoring/metrics | List metrics across deployments |

Request examples are stored under `examples/requests`.

## Inference Proxy API

Running deployment inference can be invoked through the control-plane API without calling SSH manually.

`/invoke` defaults to `POST /generate` on the first App Spec network port. The request body may override `path`, `port`, `timeout_seconds`, and `body`.

CPU VM 배포, GPU VM 배포, inference proxy, stop process 종료 흐름은 테스트 완료했다.

## Smoke And Evidence

```powershell
.\scripts\api-smoke.ps1 -BaseUrl http://localhost:8080
.\scripts\interface-smoke.ps1 -BaseUrl http://localhost:8080
.\scripts\collect-evidence.ps1 -Port 18080
.\scripts\package-deploy.ps1 -Action build -PackageType script -Source .\examples\cpu-smoke-run.sh -Entrypoint cpu-smoke-run.sh -AppName sample-shell-app
```

- `scripts/api-smoke.ps1` checks healthz, readiness, app/profile registration, resource check, deployment, logs, metrics, stop, and monitoring endpoints.
- `scripts/interface-smoke.ps1` checks the external interface examples under `deliverables/interface/examples`.
- `scripts/collect-evidence.ps1` runs `go test`, `go vet`, API smoke, readiness, and monitoring checks, then stores results under `deliverables/evidence/results`.
- Credential 시험은 실행 중에 임시 비밀값을 생성해 수행하며 비밀값이 든 요청 파일, 응답, 로그 또는 증적을 저장하지 않는다.
- `examples/fixtures/etri-aiinfra` stores current ETRI AI-Infra mock success/failure mapping fixtures.
- 제출 증적 구성 기준은 `deliverables/evidence/증적_패키지_가이드.md`를 따른다.
