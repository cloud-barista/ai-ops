# CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입 개발설계서

**AI 반도체 기반 AI 응용 배포 및 운용 구조 설계 연계 / 경희대학교 담당 범위 / 최신본**

- 대상 산출물: AI 응용 등록·배포 프로토타입
- 관련 설계서: AI 반도체 기반 AI 응용 배포 및 운용 구조 설계서
- 개발 기준: Go 언어, Echo 프레임워크, Swagger/OpenAPI, GitHub, 최소 2종 이상 LLM 코딩 에이전트 교차 검증
- 1차년도 대상 환경: AWS Ubuntu VM + NVIDIA GPU, 기관별 독립 VM, AWS/Azure/GCP Ubuntu VM + NVIDIA GPU, ETRI 통합 시험 VM
- 범위 제외: 컨테이너 기반 배포, Kubernetes, Docker Compose, OCI Image, Container Registry, LLM 운영관리, 에이전트 등록관리, 추론 최적화 전략

## 0. 문서 목적

본 문서는 1차년도 경희대학교 담당 범위 중 「CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입 개발」을 수행하기 위한 개발설계서이다. 구조 설계서가 전체 AI 응용 배포·운용 구조를 정의한다면, 본 문서는 이를 실제 프로토타입으로 구현하기 위한 API, 모듈, 데이터 모델, Runtime Adapter, 개발 일정, 시험 기준, 에이전트 작업 단위를 정의한다.

본 문서는 공식 산출물인 AI 응용 등록·배포 프로토타입의 구현 기준 문서이며, 기능/API 가이드, 설치 활용 가이드, 시험 가이드, 에이전트 개발용 Markdown의 상위 기준으로 사용한다.

## 1. 1차년도 프로토타입 범위

| 구분 | 포함 여부 | 기준 |
| --- | --- | --- |
| AI App 등록 | 포함 | App Spec 저장, 버전 관리, 실행 패키지·명령·모델 참조·자원 요구사항 검증 |
| AI App 배포 요청 | 포함 | App Version과 Runtime/Target Profile을 기반으로 Deployment 생성 |
| CPU VM 실행 | 포함 | Ubuntu VM에서 패키지, 스크립트, 바이너리 기반 실행 흐름 검증 |
| GPU VM 실행 | 포함 | NVIDIA GPU VM에서 GPU Runtime readiness 확인 후 배포 흐름 검증 |
| Mock Runtime | 포함 | 외부 VM/API 지연 시 독립 개발 및 계약 시험 수행 |
| ETRI AI-Infra 연동 준비 | 포함 | Adapter Interface와 Contract Test를 먼저 작성하고, API 명세 확보 후 연결 |
| 이노그리드 연동 | 포함 | App 등록/배포 API 책임 경계와 연동 흐름 정의 |
| 베스핀 API/Web/MCP 연동 | 포함 | 우리 응용 배포 시스템 API를 외부 Web/MCP에서 호출 가능하게 구성 |
| 컨테이너 배포 | 제외 | 3차년도 GPU/TPU/NPU 컨테이너 기반 확장 범위 |
| LLM 운영관리/에이전트 등록관리 | 제외 | 별도 산출물이며 본 프로토타입에 섞지 않음 |

## 1.1 현재 구현 및 증적 연결

2026-06-25 기준 프로토타입은 현재 구현 기준선을 제출 후보로 고정하고, 문서와 시험 증적을 맞추는 단계이다. 최신 자동 증적은 `test/results/20260625-173058`에 있으며 `go test`, `go vet`, API smoke가 모두 통과했다.

| 구현 항목 | 현재 상태 | 대표 증적/문서 |
| --- | --- | --- |
| API server와 Swagger | 구현 완료 | `/openapi.yaml`, `/swagger`, `docs/api/openapi.html` |
| App 등록 및 artifact 검증 | 구현 완료 | `api-smoke/02-app-cpu.json`, container 거부 테스트 |
| Runtime/Target Profile | 구현 완료 | `examples/requests/runtime-*.json`, `target-*.json` |
| CPU VM Adapter | 구현 완료 | dry-run, SSH runner, script upload 경로 |
| GPU VM Adapter | 구현 완료 | `nvidia-smi` readiness, dry-run, SSH runner 경로 |
| Deployment 상태 전이 | 구현 완료 | `api-smoke/06-deployment-cpu.json`, `08-deployment-logs.json` |
| Stop flow | 구현 완료 | `api-smoke/14-stop-deployment.json` |
| Monitoring/Metric | 구현 완료 | `monitoring-summary.json`, `monitoring-metrics.json` |
| ETRI AI-Infra skeleton | 구현 완료 | `examples/fixtures/etri-aiinfra/*.json` |
| 실제 외부 API 호출 | 제외 | `docs/external/외부_연동_경계_정리.md` |

최종 제출 전에는 `.\scripts\collect-evidence.ps1 -Port 18083`을 다시 실행하여 기준선 증적을 갱신하고, `docs/release/1차년도_제출_패키지_체크리스트.md` 기준으로 문서·API·시험 결과를 점검한다.

## 2. 개발 목표 및 사용자 시나리오

프로토타입의 목표는 CPU/GPU VM 기반 환경에서 AI App을 등록하고, 배포 요청을 생성하고, Runtime/Target 자원 매칭 후 배포 실행과 상태·로그 조회를 수행하는 흐름을 검증하는 것이다. 1차년도는 상용 운영 자동화보다 재현 가능한 시험, 명확한 에러 메시지, 외부 연동 대비가 중요하다.

| 사용자 시나리오 | 설명 | 완료 기준 |
| --- | --- | --- |
| App 등록 | 운영자 또는 외부 시스템이 AI App Spec을 등록한다. | App ID와 App Version이 생성된다. |
| Target 등록 | ETRI 제공 VM 또는 기관별 독립 VM을 Target Profile로 등록한다. | Target ID가 생성되고 readiness 점검이 가능하다. |
| GPU 자원 확인 | NVIDIA GPU VM에서 GPU Runtime 사용 가능 여부를 확인한다. | nvidia-smi 또는 Runtime Check 로그가 남는다. |
| 배포 요청 | 등록된 App Version과 Target을 지정해 Deployment를 생성한다. | Deployment ID와 REQUESTED 상태가 반환된다. |
| 배포 실행 | Runtime Adapter가 VM 실행 또는 외부 AI-Infra API 호출을 수행한다. | DEPLOYING 이후 RUNNING 또는 실패 상태가 기록된다. |
| 로그 조회 | 배포 단계별 로그와 외부 API 호출 로그를 조회한다. | request_id, deployment_id, stage, error_code가 포함된다. |
| 중지 | 실행 중인 배포를 중지한다. | STOPPING→STOPPED 상태 전이가 기록된다. |

## 3. 프로토타입 아키텍처

![그림 1. CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입 개발 구조](../images/prototype_development_structure.png)

| 계층 | 구성요소 | 구현 기준 |
| --- | --- | --- |
| API Layer | Echo Handler, Middleware, Swagger UI | /api/v1 prefix, request_id, 공통 에러 응답 |
| Service Layer | App Service, Deployment Service, Resource Service | 비즈니스 규칙, 상태 전이, Resource Matching |
| Repository Layer | App Repository, Deployment Repository, Profile Repository | 1차년도는 파일 기반 또는 SQLite 등 경량 저장소 허용 |
| Runtime Layer | Mock, CPU VM, GPU VM, ETRI AI-Infra Adapter | Runtime Adapter Interface를 공통 계약으로 사용 |
| External Layer | Innogrid, Bespin, Gateway Adapter | 외부 명세 변화는 Adapter와 Contract Test로 흡수 |
| Operation Layer | Logger, Error Mapper, Readiness, Test Result Collector | 로그·에러 최대화, 민감정보 마스킹 |

## 4. GitHub 저장소 구조

```text
ai-app-deployer/
├── cmd/
│   └── server/
│       └── main.go
├── internal/
│   ├── app/
│   │   ├── handler/
│   │   ├── service/
│   │   ├── repository/
│   │   ├── validator/
│   │   └── model/
│   ├── deployment/
│   │   ├── handler/
│   │   ├── service/
│   │   ├── state/
│   │   └── model/
│   ├── runtime/
│   │   ├── adapter.go
│   │   ├── mock/
│   │   ├── cpuvm/
│   │   ├── gpuvm/
│   │   └── aiinfra/
│   ├── resource/
│   │   ├── matcher/
│   │   ├── inventory/
│   │   └── profile/
│   ├── external/
│   │   ├── etri/
│   │   ├── innogrid/
│   │   └── bespin/
│   ├── logger/
│   ├── errors/
│   └── config/
├── api/
│   └── openapi.yaml
├── schemas/
├── examples/
├── docs/
│   ├── api/
│   ├── install/
│   ├── test/
│   └── prompts/
├── agent_md/
├── scripts/
├── test/
│   ├── unit/
│   ├── api/
│   ├── contract/
│   ├── integration/
│   └── e2e/
├── Makefile
├── go.mod
└── README.md
```

| 경로 | 책임 |
| --- | --- |
| internal/app | App 등록, 버전 관리, App Spec 검증 |
| internal/deployment | Deployment 생성, 상태 머신, Event Log |
| internal/runtime | Runtime Adapter Interface와 Mock/CPU VM/GPU VM/AI-Infra 구현 |
| internal/resource | Runtime Profile, Target Profile, Resource Inventory, 매칭 로직 |
| internal/external | ETRI, 이노그리드, 베스핀글로벌 연동 Adapter |
| internal/logger | 구조화 로그, request_id, 민감정보 마스킹 |
| internal/errors | 표준 에러 코드, HTTP 응답 매핑 |
| api/openapi.yaml | Swagger/OpenAPI 계약 |
| schemas | App Spec, Runtime Profile, Target Profile JSON Schema |
| agent_md | 코딩 에이전트 역할별 작업 지시 |

## 5. 데이터 명세

프로토타입은 App Spec, Runtime Profile, Target Profile, Deployment, Deployment Event를 핵심 데이터로 사용한다. 모든 명세는 구조 설계서와 동일한 필드명과 상태값을 사용해야 한다.

| 데이터 | 설명 | 저장 기준 |
| --- | --- | --- |
| App | AI App의 논리적 식별자 | app_id, name, latest_version |
| AppVersion | 실제 배포 가능한 App 명세 버전 | app_version_id, app_id, version, app_spec |
| RuntimeProfile | Runtime 능력과 Adapter 유형 | runtime_type, accelerator, adapter_type, operating_mode |
| TargetProfile | 배포 대상 VM/외부 API 정보 | csp, vm, gpu, storage, network, credential_ref |
| Deployment | 배포 작업 단위 | deployment_id, app_version_id, runtime_profile_id, target_profile_id, status |
| DeploymentEvent | 상태 전이와 상세 로그 | event_id, deployment_id, stage, message, error_code |
| ResourceInventory | 최근 자원 점검 결과 | target_profile_id, gpu_available, storage_available, last_checked_at |

### 5.1 App Spec 예시

```yaml
schema_version: appspec.khu.ai/v1alpha1
kind: AIApp
metadata:
  name: sample-gpu-inference
  version: 0.1.0
artifact:
  type: package
  uri: s3://example-artifacts/sample-gpu-inference-0.1.0.tar.gz
  checksum: sha256:example
entrypoint:
  command: ./run.sh
  args: ["--host=0.0.0.0", "--port=8080"]
runtime:
  type: gpu
  accelerator: nvidia
resources:
  cpu: "4"
  memory: 16Gi
  gpu: "1"
  storage: 20Gi
model_refs:
  - name: sample-model
    version: 0.1.0
    uri: s3://example-models/sample-model/
    mount_path: /opt/aiapp/models/sample-model
network:
  ports:
    - name: http
      app_port: 8080
      protocol: TCP
healthcheck:
  type: http
  path: /health
```

### 5.2 Target Profile 예시

```yaml
target_profile_id: target-aws-gpu-001
name: aws-gpu-vm-001
csp: aws
region: ap-northeast-2
os:
  type: ubuntu
  version: "22.04"
vm:
  host: gpu-vm.example.internal
  ssh_port: 22
  credential_ref: cred://etri/aws-gpu-vm-001
runtime:
  runtime_type: gpu
  accelerator: nvidia
  operating_mode: vm_process
gpu:
  vendor: nvidia
  count: 1
  driver_required: true
storage:
  artifact_dir: /opt/aiapp/artifacts
  model_dir: /opt/aiapp/models
  log_dir: /var/log/aiapp
network:
  service_port_range: "18080-18100"
```

## 6. API 설계

API는 Swagger/OpenAPI를 기준 계약으로 관리한다. Handler 구현, API 테스트, 외부 연동 테스트는 항상 openapi.yaml과 동기화되어야 한다.

| Method | Endpoint | 기능 | 우선순위 |
| --- | --- | --- | --- |
| GET | /api/v1/healthz | 서버 생존 확인 | 필수 |
| GET | /api/v1/readiness | 저장소, Runtime, 외부 API 준비 상태 확인 | 필수 |
| POST | /api/v1/apps | AI App 등록 | 필수 |
| GET | /api/v1/apps | AI App 목록 조회 | 필수 |
| GET | /api/v1/apps/{app_id} | AI App 상세 조회 | 필수 |
| POST | /api/v1/deployments | 배포 요청 생성 | 필수 |
| GET | /api/v1/deployments | 배포 목록 조회 | 필수 |
| GET | /api/v1/deployments/{deployment_id} | 배포 상태 조회 | 필수 |
| GET | /api/v1/deployments/{deployment_id}/logs | 배포 로그 조회 | 필수 |
| POST | /api/v1/deployments/{deployment_id}/stop | 배포 중지 | 필수 |
| POST | /api/v1/runtime-profiles | Runtime Profile 등록 | 필수 |
| GET | /api/v1/runtime-profiles | Runtime Profile 목록 조회 | 필수 |
| POST | /api/v1/target-profiles | Target Profile 등록 | 필수 |
| GET | /api/v1/target-profiles | Target Profile 목록 조회 | 필수 |
| POST | /api/v1/resources/check | Target 자원 점검 | 필수 |
| GET | /api/v1/resources/inventory | 자원 점검 결과 조회 | 권장 |

### 6.1 공통 에러 응답

```json
{
  "request_id": "req-20260715-000001",
  "error": {
    "code": "GPU_RUNTIME_NOT_FOUND",
    "message": "NVIDIA GPU runtime check failed for target target-aws-gpu-001",
    "details": {
      "target_profile_id": "target-aws-gpu-001",
      "stage": "VALIDATING"
    },
    "retryable": false
  }
}
```

## 7. 배포 상태 머신

![그림 2. 프로토타입 배포 처리 흐름](../images/prototype_deployment_flow.png)

```text
REQUESTED -> VALIDATING -> VALIDATED -> SCHEDULING -> DEPLOYING -> RUNNING
RUNNING -> STOPPING -> STOPPED
VALIDATING -> VALIDATION_FAILED
SCHEDULING -> SCHEDULING_FAILED
DEPLOYING -> DEPLOYMENT_FAILED | EXTERNAL_API_FAILED
RUNNING -> RUNTIME_FAILED
ANY_ACTIVE_STATE -> UNKNOWN
```

| 상태 | 진입 조건 | 주요 로그 |
| --- | --- | --- |
| REQUESTED | 배포 API 호출 성공 | deployment created |
| VALIDATING | App/Target 검증 시작 | app spec validation started |
| VALIDATED | 필수 필드와 Runtime 요구사항 검증 성공 | app spec validation passed |
| SCHEDULING | Resource Matcher 실행 | target matching started |
| DEPLOYING | Runtime Adapter 실행 시작 | runtime deploy requested |
| RUNNING | Healthcheck 또는 프로세스 확인 성공 | app healthcheck passed |
| STOPPING | 중지 요청 수신 | stop requested |
| STOPPED | 중지 완료 | app stopped |
| VALIDATION_FAILED | App Spec 또는 Target 검증 실패 | validation failed |
| SCHEDULING_FAILED | 자원 매칭 실패 | resource matching failed |
| DEPLOYMENT_FAILED | VM 실행 또는 준비 실패 | deployment command failed |
| RUNTIME_FAILED | 실행 중 장애 | runtime failure detected |
| EXTERNAL_API_FAILED | ETRI/Bespin/Gateway API 실패 | external api failed |

## 8. 모듈별 개발 설계

### 8.1 App Registry

| 항목 | 설계 기준 |
| --- | --- |
| 입력 | App Spec YAML/JSON |
| 처리 | name/version 중복 검증, schema_version 검증, artifact/entrypoint/runtime/resources 저장 |
| 출력 | app_id, app_version_id, created_at |
| 실패 | APP_SPEC_INVALID, APP_ARTIFACT_NOT_FOUND, ENTRYPOINT_INVALID |
| 테스트 | 유효 App 등록, 중복 버전 등록, 필수 필드 누락, container artifact 거부 |

### 8.2 Spec Validator

Validator는 JSON Schema 검증과 Go 코드 기반 의미 검증을 함께 수행한다. JSON Schema는 필드 형식과 필수값을 검증하고, Go Validator는 Runtime과 Target의 호환성, GPU 요구량, 포트 범위, credential_ref 존재 여부를 검증한다.

| 검증 항목 | 규칙 |
| --- | --- |
| artifact.type | package, git, binary, script만 허용. container는 거부 |
| runtime.type | cpu, gpu, aiinfra, mock 허용 |
| resources.gpu | runtime.type=gpu이면 1 이상 권장 |
| entrypoint.command | 빈 문자열 금지 |
| model_refs.mount_path | 절대 경로 권장 |
| network.ports.app_port | 1~65535 범위 |

### 8.3 Deployment Orchestrator

Orchestrator는 Deployment 생성 후 상태 머신을 따라 검증, 매칭, 실행, 확인, 실패 처리를 수행한다. 모든 단계는 DeploymentEvent로 기록한다.

| 단계 | 처리 |
| --- | --- |
| CreateDeployment | app_version_id, runtime_profile_id, target_profile_id 검증 |
| Validate | App Spec과 Target 호환성 검증 |
| Schedule | Resource Matcher로 배포 가능 여부 판단 |
| Prepare | Artifact와 model_refs를 Target 경로로 준비 |
| Deploy | Runtime Adapter 호출 |
| Confirm | Healthcheck 또는 상태 확인 |
| PersistEvent | 단계별 이벤트와 에러 기록 |

### 8.4 Resource Matcher

Resource Matcher는 App의 자원 요구사항과 Target Profile/Resource Inventory를 비교한다. 1차년도는 정교한 스케줄러보다 명확한 매칭 실패 원인 제공을 우선한다.

| 입력 | 출력 | 실패 코드 |
| --- | --- | --- |
| AppSpec.resources, RuntimeProfile, TargetProfile, ResourceInventory | DeploymentPlan | RESOURCE_INSUFFICIENT, GPU_RUNTIME_NOT_FOUND, STORAGE_PATH_UNAVAILABLE |

### 8.5 Runtime Adapter

```go
type RuntimeAdapter interface {
    ValidateTarget(ctx context.Context, target TargetProfile) error
    HealthCheck(ctx context.Context, runtime RuntimeProfile, target TargetProfile) error
    Prepare(ctx context.Context, app AppSpec, target TargetProfile) (*PrepareResult, error)
    Deploy(ctx context.Context, plan DeploymentPlan) (*DeployResult, error)
    GetStatus(ctx context.Context, deploymentID string) (*RuntimeStatus, error)
    GetLogs(ctx context.Context, deploymentID string, opt LogQuery) ([]RuntimeLog, error)
    Stop(ctx context.Context, deploymentID string) error
}
```

| Adapter | 구현 범위 |
| --- | --- |
| Mock Runtime | 상태 전이, 가짜 로그, 실패 fixture, 외부 의존성 없는 E2E 테스트 |
| CPU VM | SSH 또는 VM Agent 방식 중 환경에 맞게 선택. 패키지 전송, 실행 명령, Healthcheck |
| GPU VM | CPU VM 기능 + nvidia-smi, driver, CUDA/runtime 확인, GPU 환경변수 주입 |
| ETRI AI-Infra | 외부 API 계약 기반 배포 요청, 상태 조회, 로그 조회 매핑 |

## 9. VM Runtime 개발 기준

1차년도는 컨테이너 기반 실행을 사용하지 않고, VM에서 패키지/스크립트/바이너리를 준비한 뒤 실행하는 방식을 기준으로 한다. 실제 VM 접근 방식은 제공 환경에 따라 SSH 방식 또는 VM-side Agent 방식 중 선택 가능하나, 내부 Runtime Adapter Interface는 동일하게 유지한다.

| 방식 | 설명 | 장점 | 주의사항 |
| --- | --- | --- | --- |
| SSH 실행 방식 | AI App Deployer가 credential_ref 기반으로 VM에 접속하여 파일 준비와 명령 실행 | 구현이 단순하고 1차 PoC에 적합 | SSH Key와 명령 로그 마스킹 필요 |
| VM-side Agent 방식 | 대상 VM에 경량 Agent를 설치하고 HTTP/gRPC로 실행 요청 | 장기적으로 안정적이고 보안 정책 적용 용이 | Agent 설치·업데이트 절차 필요 |
| ETRI AI-Infra API 방식 | ETRI API에 배포 요청을 위임 | 통합 구조와 부합 | API 명세와 Gateway 정책 필요 |

GPU VM Adapter는 최소 다음 점검을 수행한다.

```text
1. Target VM 접속 가능 여부 확인
2. OS 및 작업 디렉터리 확인
3. nvidia-smi 실행 가능 여부 확인
4. GPU 개수와 App 요구량 비교
5. NVIDIA Driver/CUDA 정보 로그 기록
6. Artifact 및 모델 경로 접근성 확인
7. App 실행 명령 제출
8. Healthcheck 또는 프로세스 상태 확인
```

## 10. 외부 연동 개발 기준

| 연동 대상 | 1차년도 개발 기준 | 테스트 방식 |
| --- | --- | --- |
| ETRI AI-Infra | Adapter Interface와 Mock/Fixture 우선. API 명세 확보 후 실 API 연결 | Contract Test, Gateway 경유 호출 시험 |
| ETRI API Gateway | base_url, auth header, timeout, retry를 설정으로 분리 | readiness, 인증 실패, timeout 테스트 |
| 이노그리드 | App 등록/배포 API 호출 주체와 책임 경계 정리 | App 등록/배포 시퀀스 테스트 |
| 베스핀글로벌 API | 베스핀 API/Web Console/MCP가 우리 API를 호출할 수 있도록 API 계약 제공 | API Contract Test, MCP 호출 시나리오 |

외부 연동은 반드시 내부 표준 모델로 정규화한다. 외부 API 응답 구조가 달라도 내부 Deployment 상태와 에러 코드는 유지한다.

## 11. 로그 및 에러 구현 기준

로그는 최대화하되 민감정보는 마스킹한다. 모든 API 요청에는 request_id를 부여하고, 배포 작업에는 deployment_id를 부여한다.

| 필드 | 필수 여부 | 설명 |
| --- | --- | --- |
| timestamp | 필수 | ISO-8601 시간 |
| level | 필수 | DEBUG, INFO, WARN, ERROR |
| request_id | 필수 | API 요청 추적 ID |
| deployment_id | 배포 관련 필수 | 배포 작업 ID |
| component | 필수 | api, validator, orchestrator, runtime-adapter 등 |
| stage | 필수 | 현재 처리 단계 |
| message | 필수 | 사람이 이해 가능한 메시지 |
| error_code | 실패 시 필수 | 표준 에러 코드 |
| elapsed_ms | 권장 | 처리 시간 |
| external_api | 외부 호출 시 필수 | ETRI, Bespin, Gateway 등 |

| 민감정보 | 처리 기준 |
| --- | --- |
| API Key, Access Token | 전체 마스킹 |
| SSH Private Key | 로그 출력 금지 |
| Password | 로그 출력 금지 |
| Cloud Credential | credential_ref만 기록 |
| 외부 API 원문 응답 | Secret 제거 후 요약 저장 |

## 12. 개발 단계 및 일정

| 단계 | 기간 | 구현 목표 | 완료 기준 |
| --- | --- | --- | --- |
| P0. 기반 구축 | 6월 | GitHub, Go/Echo, OpenAPI, 기본 패키지 구조 | /healthz, /readiness, Swagger UI 동작 |
| P1. App 등록 MVP | 6~7월 | App Registry, Spec Validator, Schema | CPU/GPU App 등록 API 성공 |
| P2. 배포 MVP | 7월 | Deployment Orchestrator, State/Event, Mock Runtime | Mock Runtime으로 REQUESTED→RUNNING 확인 |
| P3. CPU/GPU VM PoC | 7~8월 | CPU VM/GPU VM Adapter, Resource Check | AWS GPU VM에서 GPU readiness 로그 확보 |
| P4. 1차 독립 시험 | 8월 | 기관별 독립 VM 시험, 시험 가이드 | App 등록→배포→상태→로그 시험 결과 확보 |
| P5. 통합 준비 | 9월 | ETRI 통합 VM, API Gateway 설정, 외부 Adapter | Gateway/readiness/contract test 초안 완료 |
| P6. 외부 연동 PoC | 10~11월 | ETRI AI-Infra, 이노그리드, 베스핀 API/Web/MCP 연동 | 프로토타입 수준 통합 흐름 확인 |
| P7. 안정화/릴리스 | 12월 | 장애 시험, 문서, 릴리스 태그 | 최종 시험 로그와 release artifact 정리 |

## 13. 코딩 에이전트 활용 방식

![그림 3. 코딩 에이전트 기반 개발·교차 검증 흐름](../images/agent_crosscheck_flow.png)

| 역할 | 책임 | 산출물 |
| --- | --- | --- |
| Agent A 구현 담당 | Go/Echo 코드, Handler, Service, Adapter 구현 | 코드, 구현 설명, 자체 테스트 결과 |
| Agent B 리뷰 담당 | OpenAPI 일치성, 테스트, 에러 처리, 보안, 컨테이너 범위 혼입 점검 | 리뷰 리포트, 수정 요청, 테스트 케이스 |
| 사람 검증자 | 실제 VM/GPU/외부 API 환경 시험과 최종 수용 판정 | 시험 로그, 이슈 판정, 릴리스 승인 |

모든 기능 구현은 다음 흐름을 따른다.

```text
요구사항/프롬프트 작성
→ Agent A 구현
→ Agent B 리뷰 및 테스트 보강
→ 사람의 로컬/VM 시험
→ 실패 로그 기반 수정
→ 재검증
→ GitHub PR/Merge
```

## 14. 시험 계획 및 수용 기준

| 시험 유형 | 목적 | 주요 대상 |
| --- | --- | --- |
| Unit Test | Validator, State Machine, Resource Matcher 단위 검증 | internal/app, internal/deployment, internal/resource |
| API Test | OpenAPI 기준 요청/응답 검증 | /api/v1/apps, /api/v1/deployments |
| Contract Test | 외부 API Mock/Fixture 검증 | ETRI, Innogrid, Bespin Adapter |
| Integration Test | VM/Runtime/Storage 연동 검증 | CPU VM, GPU VM, Resource Check |
| E2E Test | 등록→배포→상태→로그 전체 흐름 | Mock Runtime, GPU VM PoC |
| Failure Test | 장애 상황 검증 | GPU 미탐지, VM 접근 실패, 외부 API Timeout |

| TC ID | 시험 항목 | 수용 기준 |
| --- | --- | --- |
| TC-PROT-001 | App 등록 | App ID와 App Version 반환 |
| TC-PROT-002 | container artifact 거부 | APP_SPEC_INVALID 반환 |
| TC-PROT-003 | Mock 배포 | RUNNING 상태와 이벤트 로그 생성 |
| TC-PROT-004 | CPU VM 배포 | VM 실행 명령 제출 및 Healthcheck 확인 |
| TC-PROT-005 | GPU VM readiness | nvidia-smi 또는 GPU Runtime 점검 로그 확보 |
| TC-PROT-006 | GPU VM 배포 | GPU 요구 App의 배포 흐름 수행 또는 환경 미제공 사유 기록 |
| TC-PROT-007 | 로그 조회 | deployment_id 기준 단계별 로그 조회 |
| TC-PROT-008 | 중지 요청 | RUNNING→STOPPING→STOPPED 전이 |
| TC-PROT-009 | ETRI Contract | 요청/응답 매핑과 에러 정규화 검증 |
| TC-PROT-010 | Bespin/MCP Contract | 외부 호출 경로와 표준 응답 검증 |

## 15. 기능/API·설치·시험 가이드 연계

프로토타입 개발설계서의 세부 실행 문서는 Markdown으로 분리한다. 문서 최소화 원칙에 따라 긴 설명서보다 재현 가능한 명령, API 예제, 시험 로그 수집 절차를 중심으로 작성한다.

| 문서 | 형식 | 포함 내용 |
| --- | --- | --- |
| 기능/API 가이드 | MD + Swagger | API 목록, 요청/응답 예시, 에러 코드, Swagger 링크 |
| 설치 활용 가이드 | MD | Go 설치, 환경변수, 서버 실행, VM Target 등록, GPU 확인 명령 |
| 시험 가이드 | MD | TC ID, 사전 조건, 실행 명령, 기대 결과, 로그 수집 |
| 프롬프트 기록 | MD | 구현 프롬프트, 리뷰 프롬프트, 시험 프롬프트, 교차 검증 결과 |

## 16. 완료 기준

1차년도 CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입은 다음 기준을 만족하면 완료로 판단한다.

| 구분 | 완료 기준 |
| --- | --- |
| 기능 | App 등록, App 조회, Target 등록, 배포 요청, 상태 조회, 로그 조회, 중지 API가 동작한다. |
| Runtime | Mock Runtime과 최소 1개 VM Runtime Adapter가 동작한다. GPU VM은 제공 환경 기준으로 readiness 또는 PoC 로그를 확보한다. |
| 자원 | CPU/GPU/VM/Storage 조건을 Runtime Profile, Target Profile, Resource Inventory 기준으로 검증한다. |
| 외부 연동 | ETRI/Bespin/Innogrid 연동 Adapter와 Contract Test가 준비된다. 실 API 제공 시 설정 기반으로 연결 가능해야 한다. |
| 문서 | 설계서, 프로토타입 개발설계서, OpenAPI, Schema, 기능/API 가이드, 설치 가이드, 시험 가이드, agent_md가 동일한 용어를 사용한다. |
| 컨테이너 | Docker/Kubernetes/Container Registry 기반 기능은 포함하지 않는다. |
| 개발 증적 | 2종 이상 코딩 에이전트 활용 기록, 리뷰 결과, 시험 로그가 GitHub에 보존된다. |

## 17. 외부 제공 인터페이스 구현 기준

프로토타입 서버는 기존 내부 smoke 흐름과 외부 제공 인터페이스 예제를 모두 수용한다. `POST /api/v1/apps`는 기존 `{"app_spec": {...}}` 래퍼 형식과 외부 제공 인터페이스의 직접 App Spec 본문을 모두 허용한다. 성공 응답에는 요청 추적을 위해 `request_id`를 포함한다.

| 구현 항목 | 기준 |
| --- | --- |
| OpenAPI | `contracts/openapi/openapi.yaml`에 외부 제공 인터페이스 호환 필드를 반영한다. |
| 예제 | `examples/interface/requests`와 `examples/interface/responses`에 외부 제공용 JSON을 둔다. |
| Smoke | `scripts/interface-smoke.ps1`로 직접 App Spec, `dry_run` Runtime Profile, GPU Target, Resource Check, Deployment, Logs, Metric, Monitoring, Stop 흐름을 검증한다. |
| Contract Test | `tests/interface-contract-test-checklist.md`와 `internal/server/e2e_test.go`의 외부 인터페이스 E2E를 기준으로 한다. |
| 책임 경계 | 실제 외부 API 호출은 구현하지 않고 `internal/external`과 `internal/runtime/aiinfra`를 교체 지점으로 유지한다. |
