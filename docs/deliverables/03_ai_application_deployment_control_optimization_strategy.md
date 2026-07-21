# AI 응용 배포·제어 추론 최적화 전략 설계서
English title: AI Application Deployment and Control Optimization Strategy

## 1. 설계 목적

본 설계는 자연어 AI 응용 배포 요구를 Go Request Guard로 선검증하고 LLM으로 분석한 뒤, CPU·메모리·GPU·디스크·accelerator 요구량을 구조화하여 Go Manifest Guard를 거쳐 AppDeploy에 전달하는 Go 기반 Deployment Planner를 정의합니다.

플래너는 VM을 직접 생성하거나 최종 Target을 선택하지 않습니다. AppDeploy가 App Spec, Target Profile, readiness, Runtime Adapter를 기준으로 실제 배포 대상을 선택하고 실행합니다.

![이중 Go Guard 기반 LLM Planner와 AppDeploy 연계 흐름](../images/service_control_architecture.png)

## 2. 구성

| 구성 | 역할 |
| --- | --- |
| Go Request Guard | LLM 호출 전 요청자, 1차년도 VM 범위, 민감 파라미터 검증 |
| LLM Planner | 자연어 요구 분석과 Deployment Manifest 생성 |
| Go Manifest Guard | Manifest 형식, 자원 값, 요청 보존, 비밀정보 유입 검증 |
| AppDeploy Client | 배포 요청 전달, 상태 polling, 로그 수집 |
| Agent Registry | 플래너와 연계 에이전트의 역할·capability·허용 Action 관리 |

## 3. 처리 흐름

```text
자연어 배포 요구 + app_version_id
  -> Go Request Guard 승인 또는 거부
  -> 실제 LLM endpoint 호출
  -> CPU/메모리/GPU/디스크/accelerator 결정
  -> DeploymentManifest 생성
  -> Go Manifest Guard 승인 또는 거부
  -> AppDeploy POST /api/v1/deployments
  -> 상태 polling 및 로그 조회
  -> 결과와 재시도 권고 반환
```

## 4. Deployment Manifest

플래너 출력은 AppDeploy 계약인 `deployment.khu.ai/v1alpha1` 형식을 사용합니다.

```json
{
  "schema_version": "deployment.khu.ai/v1alpha1",
  "kind": "DeploymentManifest",
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

`target_profile_id`는 선택 사항이며 Target hint로만 사용합니다. 최종 Target 선택은 AppDeploy가 수행합니다.

## 5. 추론 최적화 전략

| 판단 항목 | 전략 |
| --- | --- |
| CPU | 앱 실행과 전처리에 필요한 최소 core를 정수 문자열로 생성 |
| Memory | 모델 적재와 런타임 여유를 반영한 `Mi/Gi/Ti` 단위 생성 |
| GPU | GPU 필요 여부와 개수를 결정하고 `accelerator=nvidia`와 일관성 검사 |
| Storage | artifact, 모델, 로그를 고려한 최소 저장공간 요구 생성 |
| Target | 플래너는 요구 envelope만 제공하고 AppDeploy가 실제 준비 상태와 비교 |

LLM의 출력은 최종 사실이 아니라 후보 계획입니다. 두 Go Guard를 모두 통과한 요구 envelope만 AppDeploy로 전달됩니다.

## 6. 이중 Go Guard 정책

### 6.1 Go Request Guard

1. 자연어 요구, App Version, LLM candidate 필수값 확인
2. 요청 길이와 요청자 allowlist 확인
3. 1차년도 VM 범위를 벗어나는 Kubernetes·컨테이너·shell 실행 요청 거부
4. password, token, credential, private key, API key 유사 파라미터 거부

### 6.2 Go Manifest Guard

1. `schema_version`과 `kind` 고정
2. 요청의 `app_version_id`와 선택적 Target hint 변조 방지
3. CPU/GPU 정수와 memory/storage 단위 검증
4. accelerator와 GPU 개수 조합 검증
5. 알 수 없는 필드와 복수 JSON 객체 거부
6. password, token, credential, private key, API key 유사 파라미터 거부

검증 실패 시 AppDeploy API를 호출하지 않습니다. LLM 오류를 규칙 기반 성공 결과로 대체하지 않습니다.

## 7. 상태 확인과 재시도

AppDeploy 상태는 다음 순서로 조회합니다.

```text
REQUESTED -> VALIDATING -> VALIDATED -> SCHEDULING -> DEPLOYING -> RUNNING
```

실패 상태는 `VALIDATION_FAILED`, `SCHEDULING_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`, `EXTERNAL_API_FAILED` 등으로 보존합니다. `retryable=true`가 명시된 오류만 재시도 가능으로 판단하고, 배포 생성 POST는 자동 반복하지 않습니다.

## 8. 구현 위치

| 기능 | 경로 |
| --- | --- |
| 요청 Guard·정책 | `go/service-control-api/internal/plannerguard/`, `config/planner_guard_policy.json` |
| Manifest 모델·Guard | `go/service-control-api/internal/appdeploy/` |
| LLM 생성·polling orchestration | `go/service-control-api/internal/deploymentplanner/` |
| Echo API | `POST /api/v1/planner/deployments` |
| CLI | `run-appdeploy-planner` |
| 계약 snapshot | `contracts/appdeploy/deployment_manifest.schema.json` |
| 요청·응답 예시 | `examples/requests/run-appdeploy-planner.json`, `examples/responses/run-appdeploy-planner-success.json` |

## 9. 실행 예시

```bash
cd go/service-control-api

go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1
```

실제 성공 결과를 얻으려면 LLM endpoint와 AppDeploy 서버가 모두 실행 중이고 App Version 및 Target Profile이 AppDeploy에 등록되어 있어야 합니다.

## 10. 범위와 한계

- 현재 구현은 Go 기반 연구 prototype입니다.
- AppDeploy가 선택한 Target과 배포 상태를 반환하며 플래너가 가상 VM 후보를 만들지 않습니다.
- 실제 운영 인증, TLS, 영속 메시지 큐와 자동 재배포 정책은 후속 통합 항목입니다.
- 1차년도 범위는 VM 기반이며 Kubernetes 배포를 산출물로 주장하지 않습니다.
