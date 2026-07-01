# 기능/API 가이드 초안

## 기준
- API Prefix: `/api/v1`
- API 계약: `contracts/openapi/openapi.yaml`
- 공통 에러 응답: `ErrorResponse`
- 1차년도 artifact.type: `package`, `git`, `binary`, `script`
- 컨테이너 기반 배포 API는 포함하지 않는다.
- 후속 개발 우선순위: `docs/planning/경희대_1차년도_개발_백로그.md`
- 제출 체크리스트: `docs/release/1차년도_제출_패키지_체크리스트.md`
- 외부 연동 경계: `docs/external/외부_연동_경계_정리.md`
- 외부 제공 인터페이스 명세: `docs/interface/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md`

## 핵심 API
| Method | Endpoint | 설명 |
| --- | --- | --- |
| GET | /openapi.yaml | OpenAPI 원본 YAML 조회 |
| GET | /swagger | Swagger/Redoc HTML 문서 조회 |
| GET | /api/v1/healthz | 서버 생존 상태 확인 |
| GET | /api/v1/readiness | 저장소/Runtime/Target/외부 API 준비 상태 확인 |
| POST | /api/v1/apps | AI App 등록 |
| GET | /api/v1/apps | AI App 목록 조회 |
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

## 현재 배포 대상
현재 Deployment가 배포하는 대상은 App Spec의 `artifact`와 `entrypoint`이다.

- `artifact.type=script`, `artifact.uri=file://...`이면 로컬 스크립트 파일을 대상 VM의 `storage.artifact_dir/{app_name}/{version}` 경로로 업로드한다.
- 업로드 후 해당 디렉터리에서 `entrypoint.command`와 `entrypoint.args`를 실행한다.
- `POST /api/v1/deployments/{deployment_id}/stop`은 Deployment 상태를 `STOPPING`/`STOPPED`로 갱신하고, CPU/GPU VM Adapter를 통해 원격 VM의 배포 프로세스를 종료한다.
- CPU VM은 script 실행 흐름을 확인하고, GPU VM은 배포 전 `nvidia-smi` readiness를 확인한다.
- 현재 smoke 예제는 `examples/cpu-smoke-run.sh`, `examples/gpu-smoke-run.sh`이며, 실제 AI 모델 서버는 동일한 방식으로 실행 스크립트를 App Spec에 지정해 배포한다.
- 컨테이너 이미지, Registry, Kubernetes 리소스는 1차년도 배포 대상이 아니다.

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

외부 제공 인터페이스 예제는 `examples/interface/requests`와 `examples/interface/responses`에 별도로 둔다. `POST /api/v1/apps`는 기존 `{"app_spec": {...}}` 래퍼와 외부 제공 인터페이스의 직접 App Spec 본문을 모두 허용한다.

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
```

- `scripts/api-smoke.ps1` checks healthz, readiness, app/profile registration, resource check, deployment, logs, metrics, stop, and monitoring endpoints.
- `scripts/interface-smoke.ps1` checks the external interface examples under `examples/interface`.
- `scripts/collect-evidence.ps1` runs `go test`, `go vet`, API smoke, readiness, and monitoring checks, then stores results under `test/results`.
- `examples/fixtures/etri-aiinfra` stores current ETRI AI-Infra mock success/failure mapping fixtures.
- 제출 증적 구성 기준은 `docs/evidence/증적_패키지_가이드.md`를 따른다.
