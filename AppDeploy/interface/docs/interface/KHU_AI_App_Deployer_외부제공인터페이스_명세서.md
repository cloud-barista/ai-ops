# KHU AI App Deployer 외부 제공 인터페이스 명세서

**문서 기준:** 경희대학교 담당 범위 / 1차년도 / CPU·GPU VM 기반 AI 응용 등록·배포 프로토타입  
**시스템명:** AI App Deployer  
**기준 API Prefix:** `/api/v1`  
**기준 계약:** `contracts/openapi/openapi.yaml`  
**문서 목적:** 이노그리드, 베스핀글로벌 Web Console/MCP, ETRI 통합시험 환경, 시험자 및 운영자가 경희대학교 AI App Deployer를 호출할 수 있도록 외부 제공 REST API 계약, 요청·응답 구조, 상태값, 에러 코드, 책임 경계, 시험 기준을 정의한다.

---

## 0. 인터페이스 제공 범위 요약

본 명세서는 경희대학교가 제공하는 **AI App Deployer 외부 호출 인터페이스**를 정의한다. 1차년도 구현은 CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입이며, 외부 시스템은 본 인터페이스를 통해 AI App 등록, Runtime/Target Profile 등록, 자원 점검, 배포 요청, 상태 조회, 로그 조회, 중지 요청, 모니터링 조회를 수행한다.

| 구분 | 포함 여부 | 설명 |
| --- | --- | --- |
| AI App 등록 API | 포함 | AI App Spec을 등록하고 App ID, Version ID를 발급한다. |
| CPU/GPU VM 배포 API | 포함 | Runtime Profile과 Target Profile을 기준으로 배포 요청을 생성한다. |
| Resource Check API | 포함 | CPU/GPU VM, Storage, Runtime readiness를 점검하고 snapshot을 저장한다. |
| Monitoring API | 포함 | Deployment 상태, Runtime health, Alarm, Metric placeholder를 조회한다. |
| ETRI AI-Infra 연동 | 골격 포함 | 실제 API 계약 확정 전까지 Mock/Fixture 및 Adapter 경계로 제공한다. |
| Innogrid/Bespin 연동 | 계약 제공 | 외부 시스템이 호출할 REST API와 책임 경계를 제공한다. |
| Docker/Kubernetes/Container 배포 | 제외 | 1차년도 인터페이스에 포함하지 않는다. Kubernetes Control Plane 기반 컨테이너 배포는 3차년도 확장 범위이다. |
| LLM 운영관리/Agent Interface/추론 최적화 | 제외 | 다른 담당 범위의 산출물이며 본 인터페이스에 포함하지 않는다. |

### 0.1 본 인터페이스가 제공하는 것

```text
- AI App 등록/조회
- Runtime Profile 등록/조회
- Target Profile 등록/조회
- CPU/GPU VM 자원 readiness 점검
- AI App 배포 요청 생성
- Deployment 상태 조회
- Deployment 로그 조회
- Deployment 중지 요청
- 운영 모니터링 요약 조회
- Runtime health, alarm, metric placeholder 조회
- OpenAPI/Swagger 문서 제공
```

### 0.2 본 인터페이스가 제공하지 않는 것

```text
- 실제 ETRI AI-Infra 내부 API 구현
- 실제 Innogrid 내부 플랫폼 API 구현
- 실제 Bespin Web Console/MCP 내부 구현
- Docker, Docker Compose, Kubernetes, Helm, Container Registry 연동
- Kubernetes Node/Pod capacity 평가
- LLM 운영관리 구조
- Agent Registry / Agent Interface
- 자연어 기반 에이전트 제어
- 추론 최적화 전략
```

---

## 1. 인터페이스 분류

외부 제공 인터페이스는 호출 방향과 사용 주체에 따라 세 가지로 구분한다.

| 분류 | 방향 | 사용 주체 | 설명 |
| --- | --- | --- | --- |
| Northbound Public Integration API | 외부 시스템 → AI App Deployer | Innogrid, Bespin Web Console, Bespin MCP-like 호출, 운영자 | AI App 등록, 배포 요청, 상태·로그·모니터링 조회를 제공한다. |
| Admin/Infra API | 운영자/통합시험 관리자 → AI App Deployer | 경희대, ETRI 통합시험 관리자 | Runtime Profile, Target Profile, Resource Check, Resource Inventory를 관리한다. |
| Southbound Adapter Contract | AI App Deployer → 외부 실행/연계 시스템 | ETRI AI-Infra, API Gateway, 향후 외부 Runtime | 실제 외부 API 계약이 확정되기 전까지 Adapter Interface, Mock/Fixture, 에러 매핑으로 관리한다. |

### 1.1 Northbound Public Integration API

외부 플랫폼 또는 Console이 AI App Deployer를 호출하기 위한 API이다. 1차년도 제출/통합시험에서는 이 그룹이 가장 중요한 제공 인터페이스이다.

| 기능 | Endpoint | 제공 대상 |
| --- | --- | --- |
| OpenAPI 원본 | `GET /openapi.yaml` | 전체 연동 주체 |
| Swagger UI | `GET /swagger` | 전체 연동 주체 |
| 서버 상태 | `GET /api/v1/healthz` | Gateway, 운영자, 통합시험 |
| 준비 상태 | `GET /api/v1/readiness` | Gateway, 운영자, 통합시험 |
| AI App 등록 | `POST /api/v1/apps` | Innogrid, Bespin, 운영자 |
| AI App 목록 | `GET /api/v1/apps` | Innogrid, Bespin, 운영자 |
| AI App 상세 | `GET /api/v1/apps/{app_id}` | Innogrid, Bespin, 운영자 |
| 배포 요청 | `POST /api/v1/deployments` | Innogrid, Bespin, MCP-like 호출, 운영자 |
| 배포 목록 | `GET /api/v1/deployments` | Bespin Web Console, 운영자 |
| 배포 상태 | `GET /api/v1/deployments/{deployment_id}` | 전체 연동 주체 |
| 배포 로그 | `GET /api/v1/deployments/{deployment_id}/logs` | 전체 연동 주체 |
| 배포 중지 | `POST /api/v1/deployments/{deployment_id}/stop` | 운영자, Web Console |
| 모니터링 요약 | `GET /api/v1/monitoring/summary` | 운영자, Web Console |
| Runtime Health | `GET /api/v1/monitoring/runtime-health` | 운영자, Web Console |
| Alarm Summary | `GET /api/v1/monitoring/alarms` | 운영자, Web Console |
| Metric 조회 | `GET /api/v1/monitoring/metrics` | 운영자, Web Console |

### 1.2 Admin/Infra API

Runtime, Target, 자원 점검은 일반 사용자가 아니라 시험 관리자 또는 인프라 관리자가 사용하는 API로 분리한다.

| 기능 | Endpoint | 사용 기준 |
| --- | --- | --- |
| Runtime Profile 등록 | `POST /api/v1/runtime-profiles` | mock, cpu_vm, gpu_vm, etri_aiinfra skeleton 등록 |
| Runtime Profile 목록 | `GET /api/v1/runtime-profiles` | 등록된 Runtime 능력 조회 |
| Target Profile 등록 | `POST /api/v1/target-profiles` | AWS/Azure/GCP/ETRI 제공 VM 대상 등록 |
| Target Profile 목록 | `GET /api/v1/target-profiles` | 배포 가능한 VM/AI-Infra 대상 조회 |
| 자원 점검 | `POST /api/v1/resources/check` | VM 접속성, storage, nvidia-smi, Runtime readiness 확인 |
| 자원 Inventory | `GET /api/v1/resources/inventory` | 최근 Resource Check 결과 조회 |

### 1.3 Southbound Adapter Contract

AI App Deployer가 외부 실행 환경 또는 기관 플랫폼을 호출하기 위한 내부 계약이다. 1차년도에는 외부 API가 확정되지 않은 상태를 전제로 하므로 실제 외부 플랫폼 내부 API 구현은 하지 않는다.

| 대상 | 1차년도 처리 | 실제 연동 시 교체 지점 |
| --- | --- | --- |
| ETRI AI-Infra | Mock/Fixture skeleton, timeout/auth/failure 표준 에러 매핑 | `internal/external/etri`, `internal/runtime/aiinfra` |
| ETRI API Gateway | base_url, auth header, timeout, retry 설정 경계만 정의 | Gateway Adapter 설정 |
| Innogrid | App 등록/배포 호출 주체와 데이터 매핑 문서화 | Innogrid 호출 client 또는 external adapter |
| Bespin Web Console | OpenAPI 기반 호출 계약 제공 | Console UI 또는 API client |
| Bespin MCP-like API | 배포 요청·상태 조회·로그 조회 호출 시나리오 제공 | MCP tool wrapper 또는 REST client |

---

## 2. 공통 인터페이스 원칙

| 원칙 | 기준 |
| --- | --- |
| API Prefix | 모든 업무 API는 `/api/v1` 하위에 둔다. 단, `/openapi.yaml`, `/swagger`는 문서 제공 경로로 별도 제공한다. |
| 데이터 형식 | 요청과 응답은 JSON을 기본으로 한다. App Spec은 JSON 또는 YAML 원본을 받을 수 있더라도 API 계약에서는 JSON 구조를 우선한다. |
| 문자 인코딩 | UTF-8을 사용한다. |
| OpenAPI 우선 | `contracts/openapi/openapi.yaml`을 단일 기준 계약으로 관리한다. |
| 공통 추적 | 모든 응답에는 `request_id`를 포함한다. 요청 Header `X-Request-Id`가 있으면 이를 사용하고, 없으면 서버가 생성한다. |
| 에러 응답 | 모든 실패는 표준 `ErrorResponse` 구조와 `error.code`를 사용한다. |
| 보안 | Secret은 요청/응답 예시에 포함하지 않는다. VM 접속 정보는 `credential_ref`로만 표현한다. |
| 로그 | 배포 관련 응답과 로그에는 `deployment_id`, `stage`, `component`, `error_code`를 포함한다. |
| 1차년도 범위 | VM 기반 배포만 제공한다. Container/Kubernetes API와 enum은 활성화하지 않는다. |
| 외부 API | 실제 ETRI/Innogrid/Bespin API는 계약 확정 후 Adapter 교체로 연동한다. |

### 2.1 공통 Header

| Header | 필수 | 설명 |
| --- | --- | --- |
| `Content-Type: application/json` | 요청 body가 있을 때 필수 | JSON 요청 본문 사용 |
| `Accept: application/json` | 권장 | JSON 응답 요청 |
| `X-Request-Id` | 선택 | 외부 시스템이 생성한 추적 ID. 없으면 서버가 생성한다. |
| `Authorization` | 환경별 선택 | API Gateway 또는 통합시험 인증 정책 확정 후 사용한다. 1차년도 로컬 smoke에서는 생략 가능하다. |

### 2.2 공통 성공 응답 원칙

성공 응답은 API별 본문 구조가 다르더라도 다음 식별자를 가능한 한 포함한다.

```json
{
  "request_id": "req-20260715-000001",
  "result": "success"
}
```

### 2.3 공통 에러 응답

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

| 필드 | 설명 |
| --- | --- |
| `request_id` | 요청 추적 ID |
| `error.code` | 표준 에러 코드 |
| `error.message` | 사람이 이해 가능한 에러 설명 |
| `error.details` | 대상 ID, stage, component 등 상세 정보. Secret 포함 금지 |
| `error.retryable` | 동일 요청 재시도 가능 여부 |

---

## 3. 데이터 계약 요약

### 3.1 App Spec 핵심 필드

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `schema_version` | Y | `appspec.khu.ai/v1alpha1` |
| `kind` | Y | `AIApp` |
| `metadata.name` | Y | AI App 이름 |
| `metadata.version` | Y | AI App 버전. 동일 name/version 중복 등록 불가 |
| `artifact.type` | Y | `package`, `git`, `binary`, `script` 중 하나 |
| `artifact.uri` | Y | 실행 패키지, Git 저장소, 바이너리, 스크립트 위치 |
| `entrypoint.command` | Y | VM에서 실행할 명령 |
| `entrypoint.args` | N | 실행 인자 |
| `runtime.type` | Y | `mock`, `cpu`, `gpu`, `aiinfra` 중 하나 |
| `runtime.accelerator` | N | GPU 사용 시 `nvidia` |
| `resources` | Y | CPU, Memory, GPU, Storage 요구사항 |
| `model_refs` | N | AI App 실행에 필요한 모델 참조 정보. 별도 모델 관리 시스템이 아님 |
| `network.ports[].app_port` | N | VM 프로세스 서비스 포트 |
| `healthcheck` | N | HTTP 또는 command 기반 상태 확인 |

#### 3.1.1 허용 Artifact Type

```text
package
git
binary
script
```

#### 3.1.2 1차년도 거부 Artifact Type

```text
container
oci_image
docker_image
helm_chart
k8s_manifest
```

### 3.2 Runtime Profile 핵심 필드

| 필드 | 설명 |
| --- | --- |
| `runtime_profile_id` | Runtime Profile 식별자 |
| `runtime_type` | `mock`, `cpu`, `gpu`, `aiinfra` |
| `adapter_type` | `mock`, `cpu_vm`, `gpu_vm`, `etri_aiinfra` |
| `accelerator` | `none`, `nvidia` |
| `operating_mode` | `dry_run`, `vm_process`, `remote_api` |
| `readiness` | Runtime readiness 점검 방식 |

### 3.3 Target Profile 핵심 필드

| 필드 | 설명 |
| --- | --- |
| `target_profile_id` | Target 식별자 |
| `csp` | `aws`, `azure`, `gcp`, `etri`, `local`, `mock` 등 |
| `region` | CSP region 또는 시험 환경 식별자 |
| `os.type` | 1차년도 기준 `ubuntu` |
| `vm.host` | 대상 VM host 또는 alias |
| `vm.ssh_port` | SSH 사용 시 port |
| `vm.credential_ref` | Secret 값을 직접 쓰지 않는 credential 참조 |
| `runtime.runtime_type` | 대상의 실행 유형 |
| `gpu.vendor` | GPU 사용 시 `nvidia` |
| `gpu.count` | GPU 개수 |
| `storage.artifact_dir` | 실행 패키지 업로드 경로 |
| `storage.model_dir` | 모델 참조 경로 |
| `storage.log_dir` | 로그 경로 |

---

## 4. 주요 연동 시나리오

### 4.1 시나리오 A: AI App 등록

**호출 주체:** Innogrid, Bespin Web Console, 운영자  
**Endpoint:** `POST /api/v1/apps`  
**목적:** AI App Spec을 등록하고 `app_id`, `app_version_id`를 확보한다.

#### 요청 예시

```json
{
  "schema_version": "appspec.khu.ai/v1alpha1",
  "kind": "AIApp",
  "metadata": {
    "name": "sample-gpu-inference",
    "version": "0.1.0"
  },
  "artifact": {
    "type": "script",
    "uri": "file://examples/gpu-smoke-run.sh"
  },
  "entrypoint": {
    "command": "./gpu-smoke-run.sh",
    "args": []
  },
  "runtime": {
    "type": "gpu",
    "accelerator": "nvidia"
  },
  "resources": {
    "cpu": "4",
    "memory": "16Gi",
    "gpu": "1",
    "storage": "20Gi"
  },
  "network": {
    "ports": [
      {
        "name": "http",
        "app_port": 8080,
        "protocol": "TCP"
      }
    ]
  }
}
```

#### 성공 응답 예시

```json
{
  "request_id": "req-20260715-000001",
  "app_id": "app-001",
  "app_version_id": "appver-001",
  "name": "sample-gpu-inference",
  "version": "0.1.0",
  "created_at": "2026-07-15T10:00:00Z"
}
```

#### 실패 예시: container artifact 거부

```json
{
  "request_id": "req-20260715-000002",
  "error": {
    "code": "APP_SPEC_INVALID",
    "message": "artifact.type container is not supported in year 1",
    "details": {
      "field": "artifact.type",
      "allowed": ["package", "git", "binary", "script"]
    },
    "retryable": false
  }
}
```

### 4.2 시나리오 B: Runtime Profile 등록

**호출 주체:** 경희대/ETRI 시험 관리자  
**Endpoint:** `POST /api/v1/runtime-profiles`  
**목적:** 배포 실행 방식과 Adapter 유형을 등록한다.

```json
{
  "runtime_profile_id": "runtime-gpu-vm-001",
  "name": "nvidia-gpu-vm-runtime",
  "runtime_type": "gpu",
  "adapter_type": "gpu_vm",
  "accelerator": "nvidia",
  "operating_mode": "dry_run",
  "readiness": {
    "type": "command",
    "command": "nvidia-smi"
  }
}
```

### 4.3 시나리오 C: Target Profile 등록

**호출 주체:** 경희대/ETRI 시험 관리자  
**Endpoint:** `POST /api/v1/target-profiles`  
**목적:** 실제 배포 대상 VM 또는 ETRI AI-Infra remote target을 등록한다.

```json
{
  "target_profile_id": "target-aws-gpu-001",
  "name": "aws-gpu-vm-001",
  "csp": "aws",
  "region": "ap-northeast-2",
  "os": {
    "type": "ubuntu",
    "version": "22.04"
  },
  "vm": {
    "host": "gpu-vm.example.internal",
    "ssh_port": 22,
    "credential_ref": "cred://etri/aws-gpu-vm-001"
  },
  "runtime": {
    "runtime_type": "gpu",
    "accelerator": "nvidia",
    "operating_mode": "vm_process"
  },
  "gpu": {
    "vendor": "nvidia",
    "count": 1,
    "driver_required": true
  },
  "storage": {
    "artifact_dir": "/opt/aiapp/artifacts",
    "model_dir": "/opt/aiapp/models",
    "log_dir": "/var/log/aiapp"
  }
}
```

### 4.4 시나리오 D: Resource Check

**호출 주체:** 운영자, ETRI 통합시험 관리자  
**Endpoint:** `POST /api/v1/resources/check`  
**목적:** CPU/GPU VM 및 Runtime readiness를 점검하고 Resource Inventory를 갱신한다.

```json
{
  "runtime_profile_id": "runtime-gpu-vm-001",
  "target_profile_id": "target-aws-gpu-001",
  "checks": ["connectivity", "storage", "gpu", "runtime"]
}
```

성공 응답은 다음 정보를 포함해야 한다.

```json
{
  "request_id": "req-20260715-000003",
  "target_profile_id": "target-aws-gpu-001",
  "runtime_profile_id": "runtime-gpu-vm-001",
  "status": "READY",
  "checks": {
    "connectivity": "PASS",
    "storage": "PASS",
    "gpu": "PASS",
    "runtime": "PASS"
  },
  "details": {
    "gpu_vendor": "nvidia",
    "gpu_count": 1,
    "nvidia_smi": "available"
  },
  "checked_at": "2026-07-15T10:10:00Z"
}
```

### 4.5 시나리오 E: Deployment 생성

**호출 주체:** Innogrid, Bespin Web Console/MCP-like 호출, 운영자  
**Endpoint:** `POST /api/v1/deployments`  
**목적:** 등록된 AI App Version을 지정 Runtime/Target으로 배포 요청한다.

```json
{
  "app_id": "app-001",
  "app_version_id": "appver-001",
  "runtime_profile_id": "runtime-gpu-vm-001",
  "target_profile_id": "target-aws-gpu-001",
  "requested_by": "bespin-console",
  "parameters": {
    "mode": "dry-run"
  }
}
```

```json
{
  "request_id": "req-20260715-000004",
  "deployment_id": "dep-20260715-000001",
  "status": "REQUESTED",
  "message": "Deployment request accepted"
}
```

### 4.6 시나리오 F: Deployment 상태 조회

**Endpoint:** `GET /api/v1/deployments/{deployment_id}`

```json
{
  "request_id": "req-20260715-000005",
  "deployment_id": "dep-20260715-000001",
  "status": "RUNNING",
  "app_id": "app-001",
  "app_version_id": "appver-001",
  "runtime_profile_id": "runtime-gpu-vm-001",
  "target_profile_id": "target-aws-gpu-001",
  "updated_at": "2026-07-15T10:30:00Z"
}
```

### 4.7 시나리오 G: Deployment 로그 조회

**Endpoint:** `GET /api/v1/deployments/{deployment_id}/logs`

```json
{
  "request_id": "req-20260715-000006",
  "deployment_id": "dep-20260715-000001",
  "logs": [
    {
      "timestamp": "2026-07-15T10:20:00Z",
      "level": "INFO",
      "component": "runtime-adapter",
      "stage": "DEPLOYING",
      "message": "GPU runtime readiness check passed",
      "error_code": null
    }
  ]
}
```

### 4.8 시나리오 H: Deployment 중지

**Endpoint:** `POST /api/v1/deployments/{deployment_id}/stop`

```json
{
  "requested_by": "operator",
  "reason": "integration test completed"
}
```

```json
{
  "request_id": "req-20260715-000007",
  "deployment_id": "dep-20260715-000001",
  "status": "STOPPING",
  "message": "Stop request accepted"
}
```

### 4.9 시나리오 I: Monitoring 조회

| API | 목적 |
| --- | --- |
| `GET /api/v1/monitoring/summary` | Deployment 상태 집계, Runtime health, alarm summary를 통합 조회한다. |
| `GET /api/v1/monitoring/runtime-health` | Target별 Resource Check snapshot을 조회한다. |
| `GET /api/v1/monitoring/alarms` | ERROR 이벤트와 `error_code` 기반 알람 요약을 조회한다. |
| `GET /api/v1/monitoring/metrics` | latency, throughput, quality_score 등 metric placeholder를 조회한다. |

---

## 5. 상태값 정의

| 상태 | 의미 | 종료 상태 |
| --- | --- | --- |
| `REQUESTED` | 배포 요청 생성 | 아니오 |
| `VALIDATING` | App/Target 명세 검증 중 | 아니오 |
| `VALIDATED` | 명세 검증 완료 | 아니오 |
| `SCHEDULING` | Runtime/Target 매칭 및 배포 계획 생성 중 | 아니오 |
| `DEPLOYING` | 패키지 준비 또는 실행 요청 중 | 아니오 |
| `RUNNING` | AI App 실행 확인 완료 | 아니오 |
| `STOPPING` | 중지 요청 처리 중 | 아니오 |
| `STOPPED` | 정상 중지 완료 | 예 |
| `VALIDATION_FAILED` | 명세 검증 실패 | 예 |
| `SCHEDULING_FAILED` | 자원/대상 매칭 실패 | 예 |
| `DEPLOYMENT_FAILED` | 배포 실행 실패 | 예 |
| `RUNTIME_FAILED` | 실행 중 Runtime 장애 | 예 |
| `EXTERNAL_API_FAILED` | 외부 API 호출 실패 | 예 |
| `UNKNOWN` | 일시적 상태 확인 불가 | 아니오 |

### 5.1 상태 전이 기준

```text
REQUESTED
  -> VALIDATING
  -> VALIDATED
  -> SCHEDULING
  -> DEPLOYING
  -> RUNNING
  -> STOPPING
  -> STOPPED

실패 전이:
VALIDATING -> VALIDATION_FAILED
SCHEDULING -> SCHEDULING_FAILED
DEPLOYING -> DEPLOYMENT_FAILED | EXTERNAL_API_FAILED
RUNNING -> RUNTIME_FAILED
ANY_ACTIVE_STATE -> UNKNOWN
```

---

## 6. 표준 에러 코드

| 에러 코드 | HTTP 예시 | 설명 | 재시도 가능성 |
| --- | --- | --- | --- |
| `APP_SPEC_INVALID` | 400 | App Spec 필수 필드 또는 형식 오류 | 아니오 |
| `APP_ARTIFACT_NOT_FOUND` | 400/404 | 실행 패키지 또는 스크립트 위치 확인 실패 | 조건부 |
| `ENTRYPOINT_INVALID` | 400 | VM에서 실행할 명령 또는 작업 디렉터리 오류 | 아니오 |
| `RUNTIME_PROFILE_INVALID` | 400 | Runtime Profile 형식 또는 필수 필드 오류 | 아니오 |
| `TARGET_PROFILE_INVALID` | 400 | Target Profile 형식 또는 접속 정보 오류 | 아니오 |
| `RESOURCE_INSUFFICIENT` | 409 | CPU/Memory/GPU/Storage 요구량 충족 실패 | 조건부 |
| `GPU_RUNTIME_NOT_FOUND` | 409 | NVIDIA GPU 또는 GPU Runtime 확인 실패 | 조건부 |
| `NVIDIA_DRIVER_NOT_FOUND` | 409 | NVIDIA Driver 확인 실패 | 조건부 |
| `CSP_VM_UNREACHABLE` | 503 | 대상 VM 접근 실패 | 예 |
| `STORAGE_PATH_UNAVAILABLE` | 409 | Artifact/Model/Log 경로 접근 실패 | 조건부 |
| `AI_INFRA_API_TIMEOUT` | 504 | ETRI AI-Infra API 응답 지연 | 예 |
| `AI_INFRA_API_FAILED` | 502 | ETRI AI-Infra API 호출 실패 | 조건부 |
| `GATEWAY_AUTH_FAILED` | 401/403 | API Gateway 인증 실패 | 아니오 |
| `BESPIN_API_FAILED` | 502 | 베스핀 API 호출 실패 | 조건부 |
| `DEPLOYMENT_FAILED` | 500 | 배포 실행 실패 | 조건부 |
| `RUNTIME_FAILED` | 500 | 실행 중 Runtime 장애 | 조건부 |

---

## 7. 외부 연동 책임 경계

| 기관/시스템 | 우리 제공 인터페이스 | 상대방 제공 또는 확정 필요사항 | 1차년도 처리 |
| --- | --- | --- | --- |
| ETRI | Target Profile, Resource Check, ETRI AI-Infra Adapter skeleton, 표준 에러 매핑 | AWS GPU VM 접속 정보, 3종 CSP VM 정보, AI-Infra API 명세, API Gateway 정책 | Mock/Fixture 및 Contract Test 우선. 실 API 확정 후 client 교체 |
| 이노그리드 | App 등록 API, 배포 요청 API, 상태·로그 조회 API | App 등록/배포 흐름에서 호출 주체, 필드 매핑, 실패 처리 정책 | REST API 계약과 요청/응답 예시 제공 |
| 베스핀글로벌 Web Console | App/Deployment/Monitoring 조회 API, 배포 요청 API | Console 화면에서 호출할 API, 인증 방식, 사용자 action mapping | OpenAPI와 예제 JSON 제공 |
| Bespin MCP-like API | 배포 요청, 상태 조회, 로그 조회, 중지 API | Tool 입력 schema와 REST API mapping | API 호출 시나리오와 에러 매핑 제공 |
| API Gateway | `/api/v1` 라우팅 대상 API, healthz/readiness | 인증, 라우팅, timeout, retry, path rewrite 정책 | Gateway 설정 항목 문서화 |

### 7.1 책임 경계 원칙

```text
경희대학교는 AI App Deployer REST API와 OpenAPI 계약을 제공한다.
외부 기관은 해당 API를 호출하는 플랫폼, Console, MCP-like 도구 또는 통합시험 환경을 구성한다.
ETRI AI-Infra, Innogrid, Bespin의 실제 내부 API는 각 기관 계약 확정 후 Adapter 교체 방식으로 연동한다.
```

---

## 8. 인터페이스 버전 및 변경 관리

| 항목 | 정책 |
| --- | --- |
| API 버전 | 1차년도 API는 `/api/v1`을 사용한다. |
| OpenAPI 파일 | `contracts/openapi/openapi.yaml`을 기준으로 한다. |
| 인터페이스 릴리스 태그 | `interface-v1.0.0-rc1` 형식을 권장한다. |
| 하위 호환 변경 | 설명, example, optional field 추가는 minor 변경으로 관리한다. |
| 비호환 변경 | endpoint 삭제, required field 변경, enum 삭제/변경, 응답 구조 변경은 major 변경으로 관리한다. |
| 변경 절차 | OpenAPI 변경 → 예제 JSON 변경 → smoke/contract test 변경 → 문서 변경 → 리뷰 승인 순서로 진행한다. |

### 8.1 OpenAPI 변경 시 필수 동기화 대상

```text
- Go Handler
- Request/Response DTO
- JSON Schema
- examples/requests
- examples/responses
- scripts/api-smoke.ps1
- contract-test-checklist.md
- 기능/API 가이드
- 외부제공인터페이스 명세서
```

---

## 9. 인터페이스 수용 기준

| TC ID | 시험 항목 | 수용 기준 |
| --- | --- | --- |
| TC-IF-001 | OpenAPI 조회 | `GET /openapi.yaml`이 정상 응답한다. |
| TC-IF-002 | Swagger 조회 | `GET /swagger`가 정상 표시된다. |
| TC-IF-003 | App 등록 예제 | 제공한 CPU/GPU App JSON으로 등록이 성공한다. |
| TC-IF-004 | Container artifact 거부 | `container` artifact 예제가 `APP_SPEC_INVALID`를 반환한다. |
| TC-IF-005 | Runtime/Target 등록 | 제공한 Profile 예제 등록이 성공한다. |
| TC-IF-006 | Resource Check | CPU/GPU Target readiness 응답이 반환된다. |
| TC-IF-007 | Deployment 생성 | 배포 요청이 `deployment_id`와 `REQUESTED`를 반환한다. |
| TC-IF-008 | 상태 조회 | Deployment 상태가 표준 Enum으로 반환된다. |
| TC-IF-009 | 로그 조회 | 로그에 `request_id`, `deployment_id`, `stage`, `component`가 포함된다. |
| TC-IF-010 | 중지 요청 | `STOPPING` 또는 `STOPPED` 상태 전이가 기록된다. |
| TC-IF-011 | Monitoring 조회 | summary/runtime-health/alarms/metrics 응답이 반환된다. |
| TC-IF-012 | 외부 API 실패 매핑 | ETRI mock timeout/auth/failure가 표준 에러 코드로 매핑된다. |
| TC-IF-013 | 범위 검수 | Docker/Kubernetes/Container 관련 API와 enum이 1차년도 활성 계약에 포함되지 않는다. |

---

## 10. 외부 제공 패키지 구성

외부 기관에 인터페이스를 제공할 때는 OpenAPI만 전달하지 않고, 문서·예제·시험 체크리스트를 함께 제공한다.

```text
khu-ai-app-deployer-interface-v1/
├── README.md
├── contracts/
│   └── openapi.yaml
├── docs/
│   └── interface/
│       ├── KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
│       ├── 외부연동_책임경계.md
│       └── 상태_에러코드_정의.md
├── examples/
│   ├── requests/
│   │   ├── app-create-cpu.json
│   │   ├── app-create-gpu.json
│   │   ├── app-create-invalid-container.json
│   │   ├── runtime-profile-gpu-vm.json
│   │   ├── target-profile-aws-gpu.json
│   │   ├── resource-check-gpu.json
│   │   └── deployment-create-gpu.json
│   └── responses/
│       ├── app-create-success.json
│       ├── deployment-create-success.json
│       ├── deployment-status-running.json
│       ├── deployment-logs-success.json
│       └── error-app-spec-invalid.json
├── scripts/
│   └── api-smoke.ps1
└── tests/
    └── interface-contract-test-checklist.md
```

---

## 11. 문서 반영 위치

구조 설계서에는 다음 장을 추가한다.

```text
14. 외부 제공 인터페이스 설계
```

프로토타입 개발설계서에는 다음 장을 추가한다.

```text
17. 외부 제공 인터페이스 구현 기준
```

기능/API 가이드는 본 명세서와 OpenAPI를 기준으로 외부 개발자가 사용할 수 있는 API 사용 문서로 최신화한다.

---

## 12. 최종 판단

본 인터페이스는 경희대학교가 담당하는 1차년도 산출물의 외부 호출 계약이다. 핵심은 실제 외부 플랫폼 내부 API를 구현하는 것이 아니라, AI App Deployer가 외부에 제공하는 REST/OpenAPI 계약을 안정화하고, 예제 요청·응답, 상태·에러 코드, 책임 경계, Contract Test 기준을 함께 제공하는 것이다.

따라서 1차년도 인터페이스 제공 작업은 다음 순서로 진행한다.

```text
1. openapi.yaml 최신화
2. 외부제공인터페이스 명세서 작성
3. 요청/응답 예제 JSON 정리
4. 상태/에러 코드 정의 문서화
5. 외부 연동 책임 경계 문서화
6. interface smoke/contract test 작성
7. 구조 설계서와 프로토타입 개발설계서에 인터페이스 장 추가
8. 릴리스 패키지 생성
```
