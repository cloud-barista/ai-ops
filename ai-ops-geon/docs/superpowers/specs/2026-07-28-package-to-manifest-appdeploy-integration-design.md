# Package-to-Manifest AppDeploy 통합 설계

## 1. 목적

geon Agent Control에서 실행할 앱 소스를 업로드하면 AppDeploy의 기존 API를
순서대로 호출하여 Package와 App Spec을 생성하고, 앱을 등록해
`app_version_id`를 발급받은 뒤, 같은 ControlRun 안에서 Qwen Planner와
Go Guard를 거쳐 검증된 `DeploymentManifest`를 생성한다.

AppDeploy 코드는 변경하지 않는다. geon은 통합 워크플로와 판단 증적을
관리하고, AppDeploy는 Package 생성, App Registry, VM Target 선택 및 실제
배포를 담당한다.

## 2. 시스템 경계

### geon 책임

- 사용자 자연어 요청과 앱 업로드 입력 수신
- AppDeploy Package 및 App 등록 API 호출 순서 관리
- 발급된 `app_version_id`를 ControlRun에 자동 연결
- Agent Registry에서 Manifest Planner Agent 선택 및 권한 확인
- Qwen Planner를 통한 배포 요구사항 구조화
- Go Request Guard 및 Go Manifest Guard 실행
- 승인된 `DeploymentManifest`와 단계별 증적 저장
- 사용자가 승인한 Manifest를 AppDeploy에 제출
- Package, App 등록, Manifest, Deployment 결과를 같은 `run_id`로 표시

### AppDeploy 책임

- `POST /api/v1/artifacts/packages`로 업로드 소스 Package 생성
- Package 응답에 등록 가능한 `app_spec` 반환
- `POST /api/v1/apps`로 App 등록 및 `app_id`, `app_version_id` 발급
- `POST /api/v1/deployments`로 승인된 Manifest 수신
- VM readiness와 자원 요구사항을 비교하여 최종 Target 선택
- Runtime Adapter 선택 및 실제 배포
- `deployment_id`, Target, Runtime, 상태, 로그 반환

## 3. 사용자 흐름

geon Manifest Workflow 첫 화면에 두 입력 모드를 제공한다.

### 새 앱 업로드

1. 사용자가 `run.sh`, binary 또는 소스 ZIP을 선택한다.
2. Package 유형, 앱 이름, 버전, entrypoint, runtime을 입력한다.
3. 자연어 배포 요구와 비용 정책을 입력한다.
4. geon이 AppDeploy Package API에 파일을 스트리밍한다.
5. geon이 Package 응답의 `app_spec`을 App 등록 API에 전달한다.
6. 발급된 `app_version_id`를 ControlRun 요청에 자동 설정한다.
7. Request Guard, Agent Registry, Agent Dispatcher, Qwen Planner,
   Manifest Guard를 순서대로 실행한다.
8. 승인된 `DeploymentManifest`를 화면에 표시한다.
9. 사용자가 `AppDeploy 배포`를 누르면 기존 ControlRun submit API로
   Manifest를 제출한다.

### 기존 등록 앱 사용

기존 `app_version_id` 입력 방식은 유지한다. 이미 AppDeploy에 등록된 앱은
Package와 App 등록 단계를 건너뛰고 현재 Manifest 생성 단계부터 시작한다.

## 4. API 설계

### 4.1 업로드 기반 ControlRun 생성

`POST /api/v1/control-runs/from-package`

Content-Type은 `multipart/form-data`를 사용한다.

필드:

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `source` | 예 | 업로드할 단일 파일 또는 소스 ZIP |
| `package_type` | 예 | `go`, `python`, `node`, `binary`, `script` |
| `app_name` | 예 | App Spec metadata.name |
| `app_version` | 예 | App Spec metadata.version |
| `entrypoint` | 예 | Package 실행 진입점 |
| `runtime_type` | 예 | `cpu` 또는 `gpu` |
| `service_port` | 아니요 | 서비스 포트 |
| `healthcheck_path` | 아니요 | HTTP health 경로 |
| `natural_language_request` | 예 | Qwen Planner에 전달할 배포 요구 |
| `candidate_id` | 예 | 등록된 Qwen Planner candidate |
| `requested_by` | 아니요 | 기본값 `ai-agent` |
| `agent_name` | 아니요 | 기본 Manifest Planner Agent |
| `target_profile_id` | 아니요 | AppDeploy Target hint |
| `cpu` | 예 | Manifest CPU 요구량 |
| `memory` | 예 | Manifest 메모리 요구량 |
| `gpu` | 예 | Manifest GPU 개수 |
| `storage` | 예 | Manifest 저장공간 요구량 |
| `cost_policy` | 아니요 | 예: `min_cost` |

성공 응답은 확장된 ControlRun이다.

```json
{
  "run_id": "run-...",
  "status": "MANIFEST_APPROVED",
  "application": {
    "package": {
      "package_type": "script",
      "artifact_uri": "file:///packages/app.tar.gz",
      "archive_name": "app.tar.gz",
      "checksum": "sha256:..."
    },
    "registration": {
      "app_id": "app-...",
      "app_version_id": "appver-..."
    },
    "app_spec": {
      "artifact": {
        "type": "package",
        "uri": "file:///packages/app.tar.gz"
      },
      "entrypoint": {
        "command": "bash",
        "args": ["run.sh"]
      },
      "runtime": {
        "type": "gpu",
        "accelerator": "nvidia"
      },
      "resources": {
        "cpu": "1",
        "memory": "4Gi",
        "gpu": "1",
        "storage": "1Gi"
      }
    }
  },
  "manifest": {
    "schema_version": "deployment.khu.ai/v1alpha1",
    "kind": "DeploymentManifest",
    "spec": {
      "app_version_id": "appver-...",
      "accelerator": "nvidia",
      "resources": {
        "cpu": "4",
        "memory": "8Gi",
        "gpu": "1",
        "storage": "20Gi"
      },
      "requested_by": "ai-agent",
      "requirements": {
        "runtime": "gpu",
        "resources": {
          "cpu": "4",
          "memory": "8Gi",
          "gpu": "1",
          "storage": "20Gi"
        },
        "cost_policy": "min_cost"
      }
    }
  }
}
```

### 4.2 Manifest 제출

기존 API를 유지한다.

`POST /api/v1/control-runs/{run_id}/submit`

geon은 승인된 `manifest`를 AppDeploy의
`POST /api/v1/deployments`에 `{"manifest": ...}` 형식으로 보낸다.

## 5. 내부 컴포넌트

### AppDeploy Package Client

업로드 파일과 Package metadata를 AppDeploy
`/api/v1/artifacts/packages`에 multipart로 전달한다. 전체 파일을 메모리에
복사하지 않고 요청 스트림으로 전달하며 크기 제한과 timeout을 적용한다.

### AppDeploy App Registry Client

Package 응답의 `app_spec`만 `{"app_spec": ...}`로 감싸
`/api/v1/apps`에 전달한다. 응답의 `app_id`와 `app_version_id`를 보존한다.

### Application Workflow Orchestrator

Package, App 등록, 기존 ControlRun 생성을 한 순서로 연결한다. AppDeploy의
각 호출 결과를 ControlRun 단계에 기록하고, 중간 실패 시 성공한 단계의
식별자를 응답에 남긴다.

### Manifest Schema Adapter

geon의 `DeploymentManifest` 모델을 최신 AppDeploy 계약과 맞춘다.
`spec.requirements.runtime`, `resources`, `cost_policy`, `slo`, `labels`를
지원한다. 기존 최상위 `spec.resources`는 하위 호환을 위해 유지하며
`requirements.resources`와 동일한 유효 자원 요구량으로 정규화한다.

## 6. 검증 규칙

- `runtime_type=gpu`이면 `gpu >= 1`, accelerator는 `nvidia`여야 한다.
- `runtime_type=cpu`이면 `gpu=0`, accelerator는 `none`이어야 한다.
- Package 응답의 App Spec과 Manifest가 CPU/GPU 유형에서 충돌하면
  Manifest Guard가 거부한다.
- Manifest의 `app_version_id`는 App 등록 응답과 정확히 일치해야 한다.
- 업로드 파일명, entrypoint와 metadata에는 경로 탈출 문자를 허용하지 않는다.
- Secret, SSH key, CSP credential은 Manifest와 ControlRun에 저장하지 않는다.
- Target Profile은 선택적 hint이며 최종 선택은 AppDeploy가 수행한다.
- `cost_policy`는 AppDeploy가 지원하는 값만 허용하며 최초 지원값은
  `min_cost`이다.

## 7. 실패 및 재시도

Package, App 등록, Manifest 생성은 서로 다른 외부/내부 단계다. 자동
rollback은 수행하지 않는다.

| 실패 단계 | ControlRun 상태 | 보존 항목 | 재시도 |
| --- | --- | --- | --- |
| Package | `PACKAGE_FAILED` | AppDeploy 오류 | 파일부터 재시도 |
| App 등록 | `APP_REGISTRATION_FAILED` | Package URI, checksum | 등록부터 재시도 |
| Request/Agent Guard | 기존 reject 상태 | Package, App ID, App Version ID | 요청 수정 후 새 Run |
| Qwen/Manifest Guard | 기존 reject 상태 | Package, App 등록, Guard 증적 | Planner 단계 재실행 |
| AppDeploy 제출 | `APPDEPLOY_FAILED` | 승인 Manifest, App Version ID | 기존 submit 재시도 |

Package 또는 App 등록이 성공한 뒤 후속 단계가 실패하면 응답의
`partial_result`에 `artifact_uri`, `archive_name`, `app_id`,
`app_version_id`를 제공한다.

## 8. 웹 화면

Manifest Workflow의 입력 순서를 다음과 같이 정리한다.

1. App Source
2. App Metadata
3. Deployment Requirements
4. Planner Settings
5. 실행 결과

진행 단계는 다음 순서로 표시한다.

`앱 업로드 → Package 생성 → App 등록 → 사용자 요청 → Request Guard →
Agent Registry → Agent 실행 → Qwen Planner → Manifest Guard`

기존 등록 앱 모드에서는 앞의 세 단계가 `skipped`로 표시된다.

결과 영역에는 Package, App 등록, Manifest, Guard, Deployment 결과를
접을 수 있는 독립 섹션으로 표시한다. `AppDeploy 배포` 버튼은
`MANIFEST_APPROVED`일 때만 활성화한다.

## 9. 테스트

- AppDeploy Package multipart 요청 계약 테스트
- Package 응답에서 App 등록 요청으로 `app_spec`만 전달하는 테스트
- 발급된 `app_version_id`가 Planner와 Manifest에 연결되는 테스트
- GPU/CPU 자원 및 runtime 충돌 Guard 테스트
- `requirements.cost_policy=min_cost` 직렬화 테스트
- Package 성공 후 App 등록 실패 시 partial result 테스트
- App 등록 성공 후 Manifest 거부 시 식별자 보존 테스트
- 기존 `app_version_id` 방식 회귀 테스트
- 브라우저에서 업로드부터 Manifest 승인까지의 단계 표시 테스트
- AppDeploy 미실행 상태의 오류 안내 테스트

## 10. 제외 범위

- AppDeploy 소스 변경
- geon 내부에서 Package를 직접 빌드하는 기능
- 자동 VM 생성
- Credential 및 SSH key 관리
- Kubernetes, Container Registry, OCI image 배포
- Guard 승인 없이 자동 배포하는 기능
- 실패 시 AppDeploy Package 또는 App 등록 자동 삭제
