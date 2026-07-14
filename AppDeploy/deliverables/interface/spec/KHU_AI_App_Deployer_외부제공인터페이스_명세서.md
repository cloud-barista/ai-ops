# KHU AI App Deployer 외부 제공 인터페이스 명세서

**문서 기준:** 경희대학교 담당 범위 / 1차년도 / CPU·GPU VM 기반 AI 응용 등록·배포 프로토타입  
**시스템명:** AI App Deployer  
**기준 API Prefix:** `/api/v1`  
**기준 계약:** `contracts/openapi/openapi.yaml`  
**문서 목적:** 이노그리드, 베스핀글로벌 Web Console/MCP, ETRI 통합시험 환경, 시험자 및 운영자가 경희대학교 AI App Deployer를 호출할 수 있도록 외부 제공 REST API 계약, 요청·응답 구조, 상태값, 에러 코드, 책임 경계, 시험 기준을 정의한다.

---

## 0. 인터페이스 제공 범위 요약

본 명세서는 경희대학교가 제공하는 **AI App Deployer 외부 호출 인터페이스**를 정의한다. 1차년도 구현은 CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입이며, 외부 시스템은 본 인터페이스를 통해 AI App 등록·삭제, Runtime Credential과 Runtime/Target Profile 관리, 자원 점검, 배포 요청, 상태 조회, 로그 조회, 중지 요청, 모니터링 조회를 수행한다.

| 구분 | 포함 여부 | 설명 |
| --- | --- | --- |
| 선택형 Package 생성 API | 포함 | 프리셋 또는 Go/Python/Node.js/Linux binary/Shell source를 Linux amd64 package와 App Spec으로 생성한다. |
| AI App 등록 API | 포함 | AI App Spec을 등록하고 App ID, Version ID를 발급한다. |
| AI App 등록 삭제 API | 포함 | 참조가 없거나 모든 참조 Deployment가 STOPPED인 App Registry record를 삭제하고 이력과 artifact 원본은 유지한다. |
| Runtime Credential API | 포함 | SSH Credential을 프로세스 메모리에 등록하고 비밀값을 제외한 메타데이터 조회와 삭제를 제공한다. |
| Runtime/Target Profile 삭제 API | 포함 | 미참조 또는 STOPPED 참조 Profile을 삭제하며, STOPPED 이력과 Credential은 유지하고 Target의 현재 Inventory snapshot만 함께 정리한다. |
| CPU/GPU VM 배포 API | 포함 | Runtime Profile과 Target Profile을 기준으로 배포 요청을 생성한다. |
| Resource Check API | 포함 | CPU/GPU VM, Storage, Runtime readiness를 점검하고 snapshot을 저장한다. |
| Monitoring API | 포함 | Deployment 상태, Runtime health, Alarm, Metric placeholder를 조회한다. |
| ETRI AI-Infra 연동 | 골격 포함 | 실제 API 계약 확정 전까지 Mock/Fixture 및 Adapter 경계로 제공한다. |
| Innogrid/Bespin 연동 | 계약 제공 | 외부 시스템이 호출할 REST API와 책임 경계를 제공한다. |
| Docker/Kubernetes/Container 배포 | 제외 | 1차년도 인터페이스에 포함하지 않는다. Kubernetes Control Plane 기반 컨테이너 배포는 3차년도 확장 범위이다. |
| LLM 운영관리/Agent Interface/추론 최적화 | 제외 | 다른 담당 범위의 산출물이며 본 인터페이스에 포함하지 않는다. |

### 0.1 본 인터페이스가 제공하는 것

```text
- AI App 등록/조회/등록 삭제
- 유형 선택형 Package 생성과 App Spec 반환
- Runtime Credential 등록/메타데이터 조회/삭제
- Runtime Profile 등록/조회/삭제
- Target Profile 등록/조회/삭제
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
| Northbound Public Integration API | 외부 시스템 → AI App Deployer | Innogrid, Bespin Web Console, Bespin MCP-like 호출, 운영자 | AI App 등록·삭제, 배포 요청, 상태·로그·모니터링 조회를 제공한다. |
| Admin/Infra API | 운영자/통합시험 관리자 → AI App Deployer | 경희대, ETRI 통합시험 관리자 | Runtime Credential, Runtime Profile, Target Profile, Resource Check, Resource Inventory를 관리한다. |
| Southbound Adapter Contract | AI App Deployer → 외부 실행/연계 시스템 | ETRI AI-Infra, API Gateway, 향후 외부 Runtime | 실제 외부 API 계약이 확정되기 전까지 Adapter Interface, Mock/Fixture, 에러 매핑으로 관리한다. |

### 1.1 Northbound Public Integration API

외부 플랫폼 또는 Console이 AI App Deployer를 호출하기 위한 API이다. 1차년도 제출/통합시험에서는 이 그룹이 가장 중요한 제공 인터페이스이다.

| 기능 | Endpoint | 제공 대상 |
| --- | --- | --- |
| OpenAPI 원본 | `GET /openapi.yaml` | 전체 연동 주체 |
| Swagger UI | `GET /swagger` | 전체 연동 주체 |
| 서버 상태 | `GET /api/v1/healthz` | Gateway, 운영자, 통합시험 |
| 준비 상태 | `GET /api/v1/readiness` | Gateway, 운영자, 통합시험 |
| Package 생성 | `POST /api/v1/artifacts/packages` | Innogrid, Bespin Web/CLI/Shell, 운영자 |
| AI App 등록 | `POST /api/v1/apps` | Innogrid, Bespin, 운영자 |
| AI App 목록 | `GET /api/v1/apps` | Innogrid, Bespin, 운영자 |
| AI App 상세 | `GET /api/v1/apps/{app_id}` | Innogrid, Bespin, 운영자 |
| AI App 등록 삭제 | `DELETE /api/v1/apps/{app_id}` | Innogrid, Bespin Web Console/CLI, 운영자 |
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
| Runtime Credential 등록 | `POST /api/v1/credentials` | SSH private key 또는 password와 신뢰된 host key fingerprint를 프로세스 메모리에 일시 등록 |
| Runtime Credential 목록 | `GET /api/v1/credentials` | 비밀값과 ENV Credential을 제외하고 host key fingerprint를 포함한 Runtime 메타데이터 조회 |
| Runtime Credential 삭제 | `DELETE /api/v1/credentials/{credential_id}` | Target 참조 여부와 무관하게 Runtime Credential 삭제 |
| Runtime Profile 등록 | `POST /api/v1/runtime-profiles` | mock, cpu_vm, gpu_vm, etri_aiinfra skeleton 등록 |
| Runtime Profile 목록 | `GET /api/v1/runtime-profiles` | 등록된 Runtime 능력 조회 |
| Runtime Profile 삭제 | `DELETE /api/v1/runtime-profiles/{runtime_profile_id}` | 미참조 또는 모든 참조 Deployment가 STOPPED일 때 등록 삭제 |
| Target Profile 등록 | `POST /api/v1/target-profiles` | AWS/Azure/GCP/ETRI 제공 VM 대상 등록 |
| Target Profile 목록 | `GET /api/v1/target-profiles` | 배포 가능한 VM/AI-Infra 대상 조회 |
| Target Profile 삭제 | `DELETE /api/v1/target-profiles/{target_profile_id}` | 미참조 또는 모든 참조 Deployment가 STOPPED일 때 등록과 현재 Inventory snapshot 삭제 |
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
| 데이터 형식 | 요청과 응답은 JSON을 기본으로 한다. Package source upload만 `multipart/form-data`를 사용하고 ai-ops-geon preset은 JSON을 사용한다. |
| 문자 인코딩 | UTF-8을 사용한다. |
| OpenAPI 우선 | `contracts/openapi/openapi.yaml`을 단일 기준 계약으로 관리한다. |
| 공통 추적 | 모든 응답에는 `request_id`를 포함한다. 요청 Header `X-Request-Id`가 있으면 이를 사용하고, 없으면 서버가 생성한다. |
| 에러 응답 | 모든 실패는 표준 `ErrorResponse` 구조와 `error.code`를 사용한다. |
| 보안 | Credential 생성 요청만 SSH secret을 입력받지만 정적 요청 예시는 제공하지 않는다. 응답·목록·로그·증적에는 Secret을 포함하지 않는다. SSH host key fingerprint는 신뢰 가능한 별도 채널에서 확인하는 필수 공개 메타데이터이며 응답·목록에 포함한다. Target Profile은 최대 256자의 제한된 `cred://namespace/id` 참조만 사용한다. |
| 로그 | 배포 관련 응답과 로그에는 `deployment_id`, `stage`, `component`, `error_code`를 포함한다. |
| 1차년도 범위 | VM 기반 배포만 제공한다. Container/Kubernetes API와 enum은 활성화하지 않는다. |
| 외부 API | 실제 ETRI/Innogrid/Bespin API는 계약 확정 후 Adapter 교체로 연동한다. |

### 2.1 공통 Header

| Header | 필수 | 설명 |
| --- | --- | --- |
| `Content-Type: application/json` | 요청 body가 있을 때 필수 | JSON 요청 본문 사용 |
| `Content-Type: multipart/form-data` | Package source 업로드 시 필수 | boundary는 HTTP client가 생성하며 source를 binary part로 전송 |
| `Accept: application/json` | 권장 | JSON 응답 요청 |
| `X-Request-Id` | 선택 | 외부 시스템이 생성한 추적 ID. 없으면 서버가 생성한다. |
| `Authorization` | 환경별 선택 | API Gateway 또는 통합시험 인증 정책 확정 후 사용한다. 1차년도 로컬 smoke에서는 생략 가능하나, Credential API 기본 정책은 loopback peer·loopback Host·same-origin이고 원격 허용 운영 환경은 TLS와 인증을 적용한 reverse proxy를 전제로 한다. |

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
| `operating_mode` | `local_mock`, `dry_run`, `vm_process`, `remote_api` |
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
| `vm.credential_ref` | 최대 256자의 제한된 `cred://namespace/id` credential 참조. Secret 직접 입력은 거부 |
| `runtime.runtime_type` | 대상의 실행 유형 |
| `gpu.vendor` | GPU 사용 시 `nvidia` |
| `gpu.count` | GPU 개수 |
| `storage.artifact_dir` | 실행 패키지 업로드 경로 |
| `storage.model_dir` | 모델 참조 경로 |
| `storage.log_dir` | 로그 경로 |

`vm.credential_ref`는 소문자 영숫자와 단일 하이픈으로 구성된 `cred://namespace/id` 형식만 허용한다. `cred://runtime/{credential_id}`는 Runtime Credential registry를 조회하고, 그 밖의 유효한 ref는 기존 ENV resolver fallback을 사용한다. 실제 key/password/token을 Target Profile에 기록하려는 요청은 거부한다.

### 3.4 Runtime Credential 핵심 필드

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `credential_id` | Y | `^[a-z0-9]+(?:-[a-z0-9]+)*$`, 2~63자 |
| `credential_type` | Y | 현재 `ssh`만 허용 |
| `auth_type` | Y | `private_key` 또는 `password` |
| `ssh_user` | Y | SSH 사용자 |
| `host_key_fingerprint` | Y | 신뢰 가능한 별도 채널에서 확인한 canonical OpenSSH SHA256 fingerprint. 필수 공개 메타데이터 |
| `private_key` | 조건부 | private_key 방식에서 필수, writeOnly, 최대 65536자 |
| `private_key_passphrase` | N | private_key 방식에서만 선택, writeOnly, 최대 4096자 |
| `password` | 조건부 | password 방식에서 필수, writeOnly, 최대 4096자 |
| `ssh_timeout_seconds` | N | 1~300초, 기본 30초 |

등록 응답과 목록 item은 `request_id`(등록 응답만), `credential_id`, `credential_ref`, `credential_type`, `auth_type`, `ssh_user`, `host_key_fingerprint`, `ssh_timeout_seconds`, `persistent=false`, `created_at`만 반환한다. 삭제 응답은 `request_id`, `credential_id`, `credential_ref`, `deleted=true`, `deleted_at`만 반환한다. 비밀값은 프로세스 메모리에만 보관하고 재시작 시 소실되며, ENV resolver가 제공하는 Credential은 목록에 나타나지 않는다. Web/API는 `host_key_fingerprint`, CLI는 `--host-key-fingerprint`, ENV는 참조 prefix 뒤의 `_SSH_HOST_KEY_FINGERPRINT`로 같은 값을 받는다.

### 3.5 Runtime/Target Profile 삭제 응답

Runtime/Target Profile 삭제는 참조 Deployment가 없거나 모든 참조가 `STOPPED`일 때만 허용한다. `STOPPED` 외 참조가 하나라도 있으면 Runtime은 `RUNTIME_PROFILE_INVALID`/409, Target은 `TARGET_PROFILE_INVALID`/409이며, 없는 Profile은 `NOT_FOUND`/404이다. 성공 응답은 다음 공통 필드를 사용한다.

| 필드 | 설명 |
| --- | --- |
| `request_id` | 요청 추적 식별자 |
| `profile_type` | `runtime` 또는 `target` |
| `profile_id` | 삭제한 Profile 식별자 |
| `name` | 저장된 이름. 이름이 없더라도 빈 문자열로 항상 반환 |
| `deleted` | 성공 시 `true` |
| `inventory_deleted` | Runtime은 항상 `false`. Target은 현재 Resource Inventory snapshot을 실제로 제거한 경우만 `true` |
| `deleted_at` | RFC 3339 삭제 시각 |

삭제 후에도 STOPPED Deployment/Event/Metric 이력은 유지한다. Target Profile이 참조하던 Runtime Credential은 cascade 삭제하지 않으며 Credential DELETE도 Target 참조와 독립적으로 처리한다.

---

## 4. 주요 연동 시나리오

### 4.0 시나리오 P: 유형 선택형 Package 생성과 배포 연결

**호출 주체:** Innogrid, Bespin Web/CLI/Shell, 운영자

**Endpoint:** `POST /api/v1/artifacts/packages`

**목적:** 허용 유형의 소스를 Linux amd64 tar.gz와 등록 가능한 App Spec으로 만든다.

ai-ops-geon 프리셋은 JSON으로 호출한다.

```json
{
  "preset": "aiops-geon-service-control",
  "app_version": "0.1.0",
  "service_port": 18089
}
```

일반 소스는 multipart로 호출한다.

```bash
curl -X POST http://localhost:8080/api/v1/artifacts/packages \
  -F package_type=script \
  -F source=@run.sh \
  -F app_name=sample-shell-app \
  -F app_version=0.1.0 \
  -F entrypoint=run.sh \
  -F runtime_type=cpu
```

지원 유형은 `aiops-geon-service-control`, `go`, `python`, `node`, `binary`, `script`이다. 임의 서버 source path나 build command는 받지 않는다. 응답에는 `artifact_uri`, `archive_name`, `checksum`, `app_spec`이 포함된다.

Package 기반 배포 client는 다음 기존 API를 순서대로 조합한다.

```text
POST /api/v1/artifacts/packages
  -> response.app_spec
POST /api/v1/apps {"app_spec": response.app_spec}
  -> response.app_version_id
POST /api/v1/resources/check
  -> status=available인 경우에만 계속
POST /api/v1/deployments {app_version_id, runtime_profile_id, target_profile_id}
```

각 단계는 독립 요청이므로 후속 단계가 실패해도 이미 생성한 archive와 등록된 App은 자동 삭제되지 않는다. 따라서 client는 실패 시 `archive_name`, `artifact_uri`, `app_version_id`를 남겨 정리 또는 재시도에 사용해야 한다. Resource Check는 Target readiness를 반환하며 Runtime Profile 존재·호환성은 Deployment API에서 검증한다. `file://` artifact는 Package 생성 서버와 Deployment 처리 서버가 같은 파일시스템을 사용할 때만 유효하다.

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

#### App 등록 삭제

**Endpoint:** `DELETE /api/v1/apps/{app_id}`

웹 콘솔은 App 목록의 `등록 삭제`, CLI는 `apps delete <app-id> --yes`로 같은 API를 호출한다. 삭제 성공 응답은 다음과 같다.

```json
{
  "request_id": "req-20260715-000003",
  "app_id": "app-001",
  "app_version_id": "appver-001",
  "name": "sample-gpu-inference",
  "version": "0.1.0",
  "deleted": true,
  "artifact_deleted": false,
  "deleted_at": "2026-07-15T10:10:00Z"
}
```

삭제 대상의 `app_id` 또는 `app_version_id`를 참조하는 Deployment가 없거나 모두 `STOPPED`이면 등록을 삭제한다. STOPPED Deployment 이력은 보존하며, `STOPPED` 외 상태가 하나라도 있으면 `APP_SPEC_INVALID`/409로 거부하고 App 등록을 유지한다. 이 API는 Registry record만 삭제하며 App Spec의 package, git, binary, script 원본과 대상 VM에 배포된 파일은 삭제하지 않는다.

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

### 4.3.1 시나리오 C-1: Runtime Credential 등록·조회·삭제

**호출 주체:** 운영자, 통합시험 관리자

**Endpoint:** `POST/GET /api/v1/credentials`, `DELETE /api/v1/credentials/{credential_id}`

**목적:** CPU/GPU VM SSH Credential을 현재 AppDeploy 프로세스 수명 동안만 제공한다.

Credential 생성 요청은 인증 방식에 맞는 secret을 포함하므로 정적 JSON 예제 파일이나 본문 예시를 제공하지 않는다. 시험 client는 실행 중에 임시 값을 생성하고 응답·로그·증적에 남기지 않아야 한다. 호출자는 SSH 접속 경로와 분리된 VM 운영자 또는 CSP 콘솔 같은 신뢰 가능한 채널에서 OpenSSH SHA256 host key fingerprint를 확인해 Web/API, CLI `--host-key-fingerprint` 또는 ENV `_SSH_HOST_KEY_FINGERPRINT`로 제공한다. 등록 후 Target Profile에는 응답의 `cred://runtime/{credential_id}`를 지정한다.

GET은 `{request_id, items}` 구조로 `host_key_fingerprint`를 포함한 Runtime registry 공개 메타데이터만 반환하며 ENV Credential은 제외한다. DELETE는 Target Profile 참조가 있어도 성공하고 `deleted=true`, `deleted_at`을 반환한다. 삭제된 참조는 SSH runner의 다음 실제 VM 연결에서 Credential 누락으로 실패하지만 dry-run은 Credential을 해석하지 않는다. API는 기본적으로 loopback peer와 loopback `Host`, `Origin`이 있을 때 same-origin을 모두 요구하며, 원격 요청은 `AIAPP_CREDENTIAL_API_ALLOW_REMOTE=true`와 TLS·인증 reverse proxy가 함께 준비된 경우에만 허용한다.

### 4.3.2 시나리오 C-2: Runtime/Target Profile 등록 삭제

**호출 주체:** 운영자, 이노그리드, 베스핀 Web/CLI/Shell

**Endpoint:** `DELETE /api/v1/runtime-profiles/{runtime_profile_id}`, `DELETE /api/v1/target-profiles/{target_profile_id}`

**목적:** 사용하지 않거나 STOPPED Deployment 이력만 참조하는 Profile 등록을 안전하게 삭제한다.

```json
{
  "request_id": "req-20260715-000003",
  "profile_type": "target",
  "profile_id": "target-aws-gpu-001",
  "name": "AWS GPU VM",
  "deleted": true,
  "inventory_deleted": true,
  "deleted_at": "2026-07-15T10:10:00Z"
}
```

`name`은 저장값이 없을 때도 빈 문자열로 포함한다. Runtime 삭제는 `inventory_deleted=false`이며, Target 삭제는 현재 snapshot을 실제로 제거한 경우에만 `true`이다. STOPPED Deployment/Event/Metric 이력과 Runtime Credential은 유지한다. STOPPED 외 참조 충돌은 Profile 종류에 맞는 `*_PROFILE_INVALID`/409, 미존재는 `NOT_FOUND`/404로 반환한다.

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
  "status": "available",
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
  "status": "RUNNING",
  "message": "Deployment request accepted and processed by the prototype"
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
| `APP_SPEC_INVALID` | 400/409 | App Spec 필수 필드·형식 오류 또는 STOPPED 외 Deployment가 참조하는 App 등록 삭제 충돌 | 아니오 |
| `APP_ARTIFACT_NOT_FOUND` | 400/404 | 실행 패키지 또는 스크립트 위치 확인 실패 | 조건부 |
| `ENTRYPOINT_INVALID` | 400 | VM에서 실행할 명령 또는 작업 디렉터리 오류 | 아니오 |
| `RUNTIME_PROFILE_INVALID` | 400/409 | Runtime Profile 형식·필수 필드 오류(400) 또는 STOPPED 외 Deployment 참조가 있는 삭제 충돌(409) | 아니오 |
| `TARGET_PROFILE_INVALID` | 400/409/413 | Target Profile·credential_ref 오류, Credential 입력·host key fingerprint·개별 secret 최대 길이 오류(400), ID 중복 또는 STOPPED 외 Deployment 참조가 있는 삭제 충돌(409), 전체 JSON 본문 128 KiB 초과(413) | 아니오 |
| `RESOURCE_INSUFFICIENT` | 409 | CPU/Memory/GPU/Storage 요구량 충족 실패 | 조건부 |
| `GPU_RUNTIME_NOT_FOUND` | 409 | NVIDIA GPU 또는 GPU Runtime 확인 실패 | 조건부 |
| `NVIDIA_DRIVER_NOT_FOUND` | 409 | NVIDIA Driver 확인 실패 | 조건부 |
| `CSP_VM_UNREACHABLE` | 503 | 대상 VM 접근 실패 | 예 |
| `STORAGE_PATH_UNAVAILABLE` | 409 | Artifact/Model/Log 경로 접근 실패 | 조건부 |
| `AI_INFRA_API_TIMEOUT` | 504 | ETRI AI-Infra API 응답 지연 | 예 |
| `AI_INFRA_API_FAILED` | 502 | ETRI AI-Infra API 호출 실패 | 조건부 |
| `GATEWAY_AUTH_FAILED` | 401/403 | API Gateway 인증 실패 또는 Credential API peer/Host/Origin 접근 정책 위반 | 아니오 |
| `NOT_FOUND` | 404 | 요청한 Runtime Credential, Runtime/Target Profile 등 리소스가 없음 | 아니오 |
| `BESPIN_API_FAILED` | 502 | 베스핀 API 호출 실패 | 조건부 |
| `DEPLOYMENT_FAILED` | 500 | 배포 실행 실패 | 조건부 |
| `RUNTIME_FAILED` | 500 | 실행 중 Runtime 장애 | 조건부 |

---

## 7. 외부 연동 책임 경계

| 기관/시스템 | 우리 제공 인터페이스 | 상대방 제공 또는 확정 필요사항 | 1차년도 처리 |
| --- | --- | --- | --- |
| ETRI | Target Profile, Resource Check, ETRI AI-Infra Adapter skeleton, 표준 에러 매핑 | AWS GPU VM 접속 정보, 3종 CSP VM 정보, AI-Infra API 명세, API Gateway 정책 | Mock/Fixture 및 Contract Test 우선. 실 API 확정 후 client 교체 |
| 이노그리드 | Credential 관리, App 및 Runtime/Target Profile 등록·삭제 API, 배포 요청 API, 상태·로그 조회 API | Credential/App/Profile 등록·삭제/배포 흐름에서 호출 주체, 신뢰된 host key fingerprint 제공 경로, 필드 매핑, 실패 처리 정책 | REST API 계약과 비밀값 없는 요청/응답 예시 제공 |
| 베스핀글로벌 Web Console | Credential 관리, App 및 Runtime/Target Profile 등록 삭제, App/Deployment/Monitoring 조회 API, 배포 요청 API | Console 화면에서 호출할 API, fingerprint 신뢰 채널, 인증 방식, 사용자 action mapping | OpenAPI와 비밀값 없는 예제 JSON 제공 |
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
- deliverables/interface/examples/requests
- deliverables/interface/examples/responses
- scripts/interface-smoke.ps1
- deliverables/interface/tests/interface-contract-test-checklist.md
- 기능/API 가이드
- 외부제공인터페이스 명세서
```

Credential secret을 담은 요청 예제 파일은 동기화 대상에서 제외한다. Contract test는 실행 시 임시 canary를 생성하고 비밀값을 응답·로그·증적에 저장하지 않는다.

---

## 9. 인터페이스 수용 기준

| TC ID | 시험 항목 | 수용 기준 |
| --- | --- | --- |
| TC-IF-001 | OpenAPI 조회 | `GET /openapi.yaml`이 정상 응답한다. |
| TC-IF-002 | Swagger 조회 | `GET /swagger`가 정상 표시된다. |
| TC-IF-003 | App 등록 예제 | 제공한 CPU/GPU App JSON으로 등록이 성공한다. |
| TC-IF-004 | Container artifact 거부 | `container` artifact 예제가 `APP_SPEC_INVALID`를 반환한다. |
| TC-IF-APP-001 | App 등록 삭제 | 미참조 App 삭제가 `deleted=true`, `artifact_deleted=false`, `deleted_at`을 반환하고 조회 목록에서 제외된다. |
| TC-IF-APP-002 | STOPPED 이력 App 삭제 | 모든 참조 Deployment가 STOPPED이면 App 등록은 삭제되고 Deployment 이력과 artifact 원본은 유지된다. |
| TC-IF-APP-003 | App 삭제 참조 보호 | STOPPED 외 참조 Deployment가 하나라도 있으면 `APP_SPEC_INVALID`/409이고 App 등록과 artifact 원본이 유지된다. |
| TC-IF-005 | Runtime/Target 등록 | 제공한 Profile 예제 등록이 성공한다. |
| TC-IF-PROFILE-001 | 미참조 Profile 삭제 | Runtime/Target 삭제가 공통 응답 필드를 반환하고 목록에서 제외되며, 저장된 이름이 없으면 `name=""`이다. |
| TC-IF-PROFILE-002 | STOPPED 이력 Profile 삭제 | 모든 참조 Deployment가 STOPPED이면 삭제되고 Deployment/Event/Metric 이력은 유지된다. |
| TC-IF-PROFILE-003 | Profile 삭제 참조 보호 | STOPPED 외 참조가 있으면 Runtime은 `RUNTIME_PROFILE_INVALID`/409, Target은 `TARGET_PROFILE_INVALID`/409이며 Profile과 Inventory가 유지된다. |
| TC-IF-PROFILE-004 | Target Inventory·Credential 경계 | Target의 현재 snapshot이 실제 제거될 때만 `inventory_deleted=true`이고 Credential은 유지되며, Runtime은 항상 `false`이다. |
| TC-IF-PROFILE-005 | 없는 Profile 삭제 | Runtime/Target 미존재 삭제가 `NOT_FOUND`/404를 반환한다. |
| TC-IF-006 | Resource Check | CPU/GPU Target readiness 응답이 반환된다. |
| TC-IF-007 | Deployment 생성 | 배포 요청이 `deployment_id`와 표준 Deployment 상태를 반환한다. 현재 프로토타입은 동기 처리 후 `RUNNING`까지 전이될 수 있다. |
| TC-IF-008 | 상태 조회 | Deployment 상태가 표준 Enum으로 반환된다. |
| TC-IF-009 | 로그 조회 | 로그에 `request_id`, `deployment_id`, `stage`, `component`가 포함된다. |
| TC-IF-010 | 중지 요청 | `STOPPING` 또는 `STOPPED` 상태 전이가 기록된다. |
| TC-IF-011 | Monitoring 조회 | summary/runtime-health/alarms/metrics 응답이 반환된다. |
| TC-IF-012 | 외부 API 실패 매핑 | ETRI mock timeout/auth/failure가 표준 에러 코드로 매핑된다. |
| TC-IF-013 | 범위 검수 | Docker/Kubernetes/Container 관련 API와 enum이 1차년도 활성 계약에 포함되지 않는다. |
| TC-IF-PKG-001 | 프리셋 Package 생성 | preset JSON 요청이 checksum과 app_spec을 반환한다. |
| TC-IF-PKG-002 | 업로드 Package 생성 | script multipart 요청이 checksum과 app_spec을 반환한다. |
| TC-IF-PKG-003 | Package 연속 배포 | app_spec 등록, Resource Check, available일 때만 Deployment 생성이 기존 계약으로 연결된다. |
| TC-IF-PKG-004 | Package 입력 보호 | 미지원 유형, 빈 source, unsafe ZIP, 크기 초과 요청이 표준 에러로 거부된다. |
| TC-IF-CRED-001 | Credential 등록 | 두 인증 분기가 필수 host key fingerprint와 함께 등록되고 응답에는 fingerprint 등 공개 메타데이터와 `persistent=false`만 반환된다. |
| TC-IF-CRED-002 | Credential 목록/ENV | Runtime 목록은 fingerprint를 포함하고 ENV Credential과 secret은 제외하며, ENV fingerprint 누락은 해석 실패한다. |
| TC-IF-CRED-003 | Credential 삭제 | Target 참조 중 삭제가 성공하고, SSH runner의 다음 실제 연결은 Credential 누락으로 실패하지만 dry-run은 해석하지 않으며 재삭제는 `NOT_FOUND`/404이다. |
| TC-IF-CRED-004 | Credential 입력·접근 보호 | 잘못된 fingerprint·입력·Content-Type·개별 secret 길이·크기 제한은 표준 400/409/413으로, 비허용 peer/Host/Origin은 `GATEWAY_AUTH_FAILED`/403으로 거부된다. |
| TC-IF-CRED-005 | Credential 수명·비노출 | 재시작 후 Runtime 목록이 비고 실행 중 생성한 canary가 응답·store·로그·Web/CLI 출력·증적에 남지 않는다. |
| TC-IF-CRED-006 | SSH 서버 신원 고정 | Web/API/CLI/ENV에 제공한 fingerprint와 서버 host key가 일치할 때만 연결되고 불일치하면 SSH 인증 전에 거부된다. |
| TC-IF-CRED-007 | Target Credential 참조 보호 | 최대 256자의 `cred://namespace/id` 형식만 저장되고 Secret canary 직접 입력은 응답·store·목록에 남기지 않고 거부된다. |
| TC-IF-CRED-008 | Credential 원격 opt-in | TLS·인증 reverse proxy가 인증 없는 원격 요청을 거부하고 인증된 요청만 `AIAPP_CREDENTIAL_API_ALLOW_REMOTE=true` 서버로 전달한다. |

---

## 10. 외부 제공 패키지 구성

외부 기관에 인터페이스를 제공할 때는 OpenAPI만 전달하지 않고, 문서·예제·시험 체크리스트를 함께 제공한다.

```text
deliverables/interface/
├── spec/
│   └── KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
├── external/
│   └── 외부_연동_경계_정리.md
├── examples/
│   ├── requests/
│   │   ├── package-build-aiops-geon.json
│   │   ├── app-create-cpu.json
│   │   ├── app-create-gpu.json
│   │   ├── app-create-invalid-container.json
│   │   ├── runtime-profile-gpu-vm.json
│   │   ├── target-profile-aws-gpu.json
│   │   ├── resource-check-gpu.json
│   │   └── deployment-create-gpu.json
│   └── responses/
│       ├── package-build-success.json
│       ├── app-create-success.json
│       ├── app-delete-success.json
│       ├── deployment-create-success.json
│       ├── deployment-status-running.json
│       ├── deployment-logs-success.json
│       └── error-app-spec-invalid.json
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
