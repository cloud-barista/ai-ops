# LLM Deployment Planner와 이중 Go Guard 연계 흐름

## 1. 역할

이 저장소의 핵심 역할은 **Registry 기반 LLM Deployment Planner와 검증된 `DeploymentManifest`를 제공하는 geon Control Plane**입니다. Go Request Guard가 사용자의 자연어 배포 요구를 먼저 검사하고, Agent Registry가 Manifest 생성 capability와 bounded Action을 가진 활성 Planner Agent를 결정합니다. 승인된 Planner가 Qwen을 사용해 CPU, 메모리, GPU, 디스크, accelerator 요구량을 구조화하면 Go Manifest Guard가 생성 결과를 다시 검사합니다.

모든 단계는 backend `ControlRun`의 동일한 `run_id`로 기록됩니다. Manifest 생성은 AppDeploy 없이 완료할 수 있습니다. 플래너는 실제 VM이나 Runtime Adapter를 선택하지 않으며, 선택적 제출 이후 App Spec 조회, Target 선택, readiness 검사, artifact 준비, VM 배포와 이벤트 저장은 AppDeploy가 담당합니다.

## 2. 전체 흐름

```mermaid
flowchart LR
    U["자연어 배포 요구"]
    CR["ControlRun 생성<br/>run_id"]
    RG["Go Request Guard<br/>요청자·범위·민감정보 검사"]
    AR["Agent Registry<br/>capability·bounded Action"]
    P["Qwen Planner<br/>자원 요구량 결정"]
    M["DeploymentManifest"]
    MG["Go Manifest Guard<br/>형식·값·권한·보안 검증"]
    O["최종 Manifest<br/>MANIFEST_APPROVED"]
    D["선택적 AppDeploy 제출<br/>Target 선택·배포 실행"]
    P2["선택적 배포 후 경로<br/>Autonomous Loop·Feedback"]

    U --> CR --> RG
    RG -->|승인| AR
    AR -->|승인| P --> M --> MG
    RG -->|거부| RX["요청 거부"]
    AR -->|거부| AX["Agent 거부"]
    MG -->|승인| O
    MG -->|거부| MX["Manifest 거부"]
    O -. 사용자가 제출 선택 .-> D -. deployment_id .-> P2
```

실제 API 흐름은 다음과 같습니다.

```text
POST /api/v1/control-runs
  -> Go Request Guard 검증
  -> Agent Registry에서 configuration Planner Agent 선택
  -> Qwen endpoint 호출
  -> DeploymentManifest JSON 생성
  -> Go Manifest Guard 검증
  -> MANIFEST_APPROVED 반환

POST /api/v1/control-runs/{run_id}/submit  (선택)
  -> Agent Registry submit 권한 재검증
  -> AppDeploy POST /api/v1/deployments
  -> GET /api/v1/deployments/{deployment_id}
  -> GET /api/v1/deployments/{deployment_id}/logs
```

기존 `POST /api/v1/planner/deployments`는 호환성을 위해 위 두 단계를 한 번에 조합하지만, 연구 산출물의 기준 API는 Manifest 생성과 제출이 분리된 ControlRun API입니다.

## 3. 플래너 입력

| 필드 | 의미 |
| --- | --- |
| `natural_language_request` | 사용자의 배포 요구 |
| `app_version_id` | AppDeploy에 등록된 App Version 식별자 |
| `candidate_id` | 호출할 실제 LLM 후보 |
| `agent_name` | 선택적 configuration Agent 이름. 생략하면 Registry 선언 순서로 적합한 Agent 선택 |
| `target_profile_id` | 선택적 Target hint. 최종 선택 권한은 AppDeploy에 있음 |
| `parameters` | 앱 실행에 필요한 비밀정보가 아닌 추가 파라미터 |

## 4. Agent Registry 오케스트레이션

Agent Registry는 단순 목록 화면이 아니라 Manifest Planner 선택과 권한 검증의 실제 진입점입니다.

| 조건 | Manifest 생성 |
| --- | --- |
| `source=configuration` | 필수 |
| `enabled=true` | 필수 |
| capability `deployment_manifest_planning` | 필수 |
| bounded Action `generate_deployment_manifest` | 생성 시 필수 |
| bounded Action `submit_deployment_manifest` | AppDeploy 제출 시 다시 검증 |

`AIApplicationAutomationAgent`는 기본 등록 프로필일 뿐 시스템 전체와 동일하지 않습니다. Runtime Agent endpoint는 등록·검증·handoff 계획 대상이지만, 현재 범위에서 geon이 해당 endpoint를 직접 호출하는 외부 워크플로 엔진은 구현하지 않습니다.

## 5. Go Request Guard

LLM 호출 전에 다음 항목을 결정적으로 검사합니다.

| 검증 | 기준 |
| --- | --- |
| 필수 입력 | 자연어 요구, App Version, LLM candidate가 존재하는지 확인 |
| 요청 길이 | 정책의 최대 길이를 초과하는 요청 거부 |
| 요청자 | `config/planner_guard_policy.json`의 allowlist 확인 |
| 1차년도 범위 | Kubernetes, 컨테이너, shell 실행 등 VM 범위 밖 요청 거부 |
| 민감정보 | password, token, credential, API key와 유사한 parameter key 거부 |

거부 시 `REQUEST_REJECTED`와 검사 결과를 반환하며 LLM과 AppDeploy를 호출하지 않습니다. 현재 `requested_by`는 인증 토큰에서 도출한 신원이 아니라 프로토타입 caller ID이므로, 운영 통합 단계에서는 인증 계층의 principal로 대체해야 합니다.

예시:

```json
{
  "natural_language_request": "GPU 1개, CPU 4개, 메모리 16Gi가 필요한 추론 앱을 배포해 주세요.",
  "app_version_id": "appver-llm-inference-v1",
  "candidate_id": "qwen3.5-ops-planner"
}
```

## 6. LLM이 생성하는 Manifest

```json
{
  "schema_version": "deployment.khu.ai/v1alpha1",
  "kind": "DeploymentManifest",
  "metadata": {"name": "llm-inference"},
  "spec": {
    "app_version_id": "appver-llm-inference-v1",
    "accelerator": "nvidia",
    "resources": {
      "cpu": "4",
      "memory": "16Gi",
      "gpu": "1",
      "storage": "20Gi"
    },
    "requested_by": "ai-ops-geon-planner"
  }
}
```

LLM은 VM ID, Runtime Adapter, credential, shell command, 컨테이너 또는 Kubernetes 리소스를 만들지 않습니다. `target_profile_id`가 없으면 AppDeploy가 등록된 Target 중 준비 상태와 자원 요구를 만족하는 대상을 선택합니다.

## 7. Go Manifest Guard 검증

| 검증 | 기준 |
| --- | --- |
| 계약 | `schema_version`, `kind`, JSON 필드 형식 확인 |
| 요청 보존 | LLM이 `app_version_id`와 Target hint를 바꾸지 않았는지 확인 |
| 자원 값 | CPU/GPU 정수, memory/storage 단위, accelerator 조합 확인 |
| 비밀정보 | password, token, credential, API key와 유사한 parameter 거부 |
| 출력 제한 | 알 수 없는 JSON 필드와 복수 JSON 객체 거부 |

검증 실패 시 AppDeploy를 호출하지 않으며 `MANIFEST_REJECTED`로 종료합니다. LLM 호출 실패도 규칙 기반 가짜 Manifest로 대체하지 않습니다.

## 8. 선택적 AppDeploy 연계

Go Manifest Guard를 통과하면 geon의 핵심 산출물은 완성됩니다. 사용자가 `POST /api/v1/control-runs/{run_id}/submit`을 호출한 경우에만 동일 Manifest가 AppDeploy의 다음 공식 API에 전달됩니다.

| 단계 | AppDeploy API |
| --- | --- |
| 배포 요청 | `POST /api/v1/deployments` |
| 상태 조회 | `GET /api/v1/deployments/{deployment_id}` |
| 로그 조회 | `GET /api/v1/deployments/{deployment_id}/logs` |

플래너는 `REQUESTED -> VALIDATING -> VALIDATED -> SCHEDULING -> DEPLOYING -> RUNNING` 상태를 제한된 횟수로 조회합니다. 실패 상태도 그대로 반환합니다.

## 9. 배포 후 연결

AppDeploy가 반환한 `deployment_id`는 같은 ControlRun에 저장됩니다. 이후 Action Proposal, Feedback, Autonomous Loop는 선택적으로 이 `run_id`를 참조합니다.

```text
DEPLOYED ControlRun
→ AppDeploy Metric·상태 관찰
→ Qwen operational Action Proposal
→ Agent Registry + Go Action Guard
→ Monitor Only 또는 Guarded Auto
→ Feedback와 Autonomy Event에 run_id 기록
```

이 경로의 Action Proposal은 배포 전 `DeploymentManifest`와 다른 계약입니다. Autonomous Loop는 Manifest 생성 과정에 끼어들지 않으며, Run을 선택해도 자동 시작되지 않습니다.

## 10. 재시도 원칙

- 상태 조회는 설정된 횟수와 간격 안에서만 반복합니다.
- AppDeploy `ErrorResponse.retryable=true`가 명시된 경우에만 재시도 가능성을 표시합니다.
- 실패 상태 이름만 보고 임의로 재배포하지 않습니다.
- 동일 배포의 중복 생성을 피하기 위해 `POST /deployments`를 자동 재전송하지 않습니다.

## 11. 책임 경계

| 구성요소 | 책임 |
| --- | --- |
| Go Request Guard | LLM 호출 전 요청자, 허용 범위, 민감정보 검사 |
| Agent Registry | 활성 configuration Agent 선택, capability와 bounded Action 권한 검증 |
| Qwen Deployment Planner | 자연어 요구 분석, 자원 요구량 결정, Manifest 초안 생성 |
| Go Manifest Guard | LLM 생성 Manifest 계약과 정책의 결정적 검증 |
| ControlRun | 요청·Agent 선택·Guard 결과·Manifest·선택적 배포 결과를 동일 `run_id`로 기록 |
| AppDeploy | App Spec 조회, Target/Adapter 선택, readiness, artifact, VM 배포, 상태·이벤트 저장 |
| 인프라 계층 | 실제 CPU/GPU VM 제공과 수명주기 관리 |

이 구분에 따라 본 프로젝트는 Registry 기반 판단·검증·Manifest 산출·연계 프레임워크이고, AppDeploy는 실제 배포 실행 계층입니다. AppDeploy 구현과 외부 Agent endpoint 실행은 geon의 소유 범위가 아닙니다.
