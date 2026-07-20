# AppDeploy 연계 LLM Planner 설계

## 목표

`geon` service-control prototype을 AppDeploy 앞단의 LLM 기반 Deployment Planner로 확장한다. Planner는 자연어 App 배포 요구를 분석해 AppDeploy 계약에 맞는 `DeploymentManifest`를 생성하고 Go Guard로 검증한 뒤 AppDeploy에 제출한다. 이후 배포 상태와 로그를 조회하고 명시적인 `retryable` 정보에 따라 재시도 가능 여부를 판단한다.

## 책임 경계

### Planner

- 자연어 App 요구와 `app_version_id` 수신
- CPU, memory, GPU, storage와 accelerator 요구량 결정
- `deployment.khu.ai/v1alpha1` Manifest 생성
- Manifest 형식과 정책 검증
- `POST /api/v1/deployments` 요청
- Deployment 상태 polling과 로그 조회
- 명시적인 AppDeploy 오류의 `retryable` 판단 결과 기록

### AppDeploy

- App Spec과 App Version 조회
- Target Profile 후보와 readiness 확인
- Manifest 요구사항과 Target 자원·Runtime 매칭
- 실제 Target 및 Adapter 선택
- artifact 준비와 VM 또는 외부 AI Infra 배포
- 상태 전이, 이벤트와 로그 저장

Planner는 Target VM을 직접 선택하거나 SSH 배포를 수행하지 않는다. `target_profile_id`는 사용자가 제공한 경우에만 선택적 hint로 전달한다.

## 입력 계약

Planner 요청은 다음 필드를 사용한다.

```json
{
  "natural_language_request": "NVIDIA GPU 1개와 메모리 16Gi가 필요한 추론 서비스를 배포해줘.",
  "app_version_id": "appver-example",
  "candidate_id": "local-ollama-ops-llm",
  "target_profile_id": "",
  "requested_by": "ai-ops-geon-planner",
  "parameters": {
    "port": 18080
  },
  "poll_interval_ms": 1000,
  "max_poll_attempts": 60
}
```

`natural_language_request`, `app_version_id`, `candidate_id`는 필수다. `target_profile_id`는 선택 사항이다. Credential, private key, API key와 같은 Secret은 요청과 Manifest에 넣지 않는다.

## Manifest 계약

LLM은 AppDeploy의 `deployment_manifest.schema.json`에 대응하는 Manifest를 제안한다.

```json
{
  "schema_version": "deployment.khu.ai/v1alpha1",
  "kind": "DeploymentManifest",
  "metadata": {
    "name": "llm-inference-deployment"
  },
  "spec": {
    "app_version_id": "appver-example",
    "accelerator": "nvidia",
    "resources": {
      "cpu": "4",
      "memory": "16Gi",
      "gpu": "1",
      "storage": "20Gi"
    },
    "requested_by": "ai-ops-geon-planner",
    "parameters": {
      "port": 18080
    }
  }
}
```

Go 계층은 신뢰 경계에 속하는 `app_version_id`, `target_profile_id`, `requested_by`, `parameters`를 요청값으로 고정한다. LLM은 accelerator와 자원 요구량 및 사람이 읽을 수 있는 metadata name을 결정한다.

## 2단계 Go Guard

### 사전 검증

- 필수 request field 확인
- 자연어 요구 최대 길이 제한
- App Version ID와 Target Profile hint 형식 확인
- parameter key에서 credential·secret·token·private key 금지
- 활성화된 LLM candidate 확인

### 사후 검증

- `schema_version`과 `kind` 상수 확인
- `app_version_id`와 선택적 `target_profile_id` 고정
- accelerator가 `none` 또는 `nvidia`인지 확인
- CPU·memory·GPU·storage 요구량 존재 및 형식 확인
- `none`이면 GPU 0, `nvidia`이면 GPU 1 이상인지 확인
- unknown field와 추가 JSON value 거부

## AppDeploy client

환경 변수 `AIOPS_APPDEPLOY_BASE_URL`에 `/api/v1`까지 포함한 base URL을 설정한다. client는 다음 호출을 제공한다.

- `POST /deployments`
- `GET /deployments/{deployment_id}`
- `GET /deployments/{deployment_id}/logs`

모든 호출은 `context.Context`를 전달하고 응답 크기와 timeout을 제한한다. 2xx가 아닌 응답은 AppDeploy `ErrorResponse`로 정규화하며 raw 응답 전체를 외부 API에 노출하지 않는다.

## 상태 추적과 재시도 판단

성공 종료 상태는 `RUNNING`이다. 실패 종료 상태는 `VALIDATION_FAILED`, `SCHEDULING_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`, `EXTERNAL_API_FAILED`, `UNKNOWN`이다.

Planner는 상태 조회 자체를 정해진 polling 횟수까지 반복한다. 배포 생성 요청을 자동 재전송하면 중복 Deployment가 생길 수 있으므로 자동 재제출하지 않는다. AppDeploy `ErrorResponse.retryable=true`가 명시된 경우에만 `retry_recommended=true`를 반환한다. 상태값만으로 retry 가능성을 추측하지 않는다.

## API와 CLI

- REST: `POST /api/v1/planner/deployments`
- CLI: `run-appdeploy-planner`

응답은 생성된 Manifest, LLM provider/model/latency, Go Guard 결과, AppDeploy DeploymentResponse, polling 횟수, 로그, retry 판단을 포함한다.

## 호환성

기존 `/api/v1/automation/action-proposals`와 VM 검증 API는 기존 검증 증적 호환을 위해 유지한다. README와 공식 설계 문서에서는 AppDeploy Planner 흐름을 주 경로로 설명하고 기존 Action handoff는 범용 실험 API로 구분한다.

## 검증

- LLM Manifest 생성 성공과 strict JSON 실패
- app_version_id 또는 target hint 변조 거부
- 자원 요구량과 accelerator 불일치 거부
- AppDeploy POST body contract 확인
- REQUESTED에서 RUNNING까지 polling
- 실패 상태에서 로그 수집
- 명시적 retryable 오류만 retry 권고
- Echo endpoint와 CLI 실행 검증
- `make test`, `make vet`, `make lint`, `make swag`

## 비범위

- Kubernetes 또는 container manifest 생성
- Planner의 Target VM 직접 선택
- SSH credential 처리
- 실제 Runtime Adapter 구현
- AppDeploy 내부 저장소·스케줄러 재구현
- retryable 근거가 없는 배포 자동 재제출
