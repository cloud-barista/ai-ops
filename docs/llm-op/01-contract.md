# LLM_Op 입출력 및 Safeguard 계약

## 1. 계약 목적

이 계약은 `통합 다이어그램 (초안)`의 자연어 요청 → 요구 분석/ApplicationProfile → Safe Guard·Repair → Manifest 흐름을 기준으로 사용자 요청, 서버 상태 정보 문서, Log Data를 하나의 제한된 입력으로 만들고, AppDeployer DeploymentManifest에 맞는 결과만 내보내기 위한 것이다. 구조도 제안안 B는 두 LLM/Guard 단계의 상세 참고다.

외부에 새 배포 형식을 만들지 않는다. LLM_Op의 내부 결과 객체 중 manifest 필드만 AppDeployer 공식 DeploymentManifest가 된다.

## 2. 입력: LLMOperationRequest

버전은 ai-ops.llm-operation/v1alpha1로 시작한다. geon의 ControlRun 또는 별도 API가 이 객체를 받아도 되지만, 초기에 기존 Planner API의 입력을 무단 변경하지 않는다.

전체 실행 예제는 examples/llm-op/fresh-latency-request.json에 있다. 입력은 candidate_id, 선택적 application.deployment_id, AppDeployer 모델에 맞춘 resource/monitoring/log wrapper, bounded metrics_summary를 포함한다.

### 필수 및 선택 필드

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| api_version, request_id, correlation_id, candidate_id, requested_by | 예 | 추적, 상위 계층이 이미 지정한 Qwen 구성 결합, 요청자 정책에 사용. 식별자는 bounded ASCII이며 8 rune 이상. 모델 비교·선택은 이 계약의 범위가 아님 |
| trace_id | 아니오 | 있으면 bounded ASCII·8 rune 이상으로 검증하고 결과에 보존 |
| application.app_version_id | 예 | bounded ASCII·8 rune 이상의 등록 애플리케이션 버전 식별자이며 LLM이 변경하지 않음 |
| application.deployment_id | 아니오 | 있으면 bounded ASCII·8 rune 이상이며 관측 로그·metric이 어느 기존 배포에 속하는지 연결하는 신뢰 범위 |
| application.user_request | 예 | 8,000 rune 상한과 금칙어·권한 검증 대상 |
| application.target_profile_id | 아니오 | caller가 제공하는 3..128 rune bounded ASCII optional hint. Qwen 구조 입력에는 전달하지 않고 최종 Target 확정으로 해석하지 않음 |
| application.planning_constraints | 아니오 | 신뢰 통합 계층이 제공한 Profile 최소값과 선택적 `recommended_resources` exact 단일-node 값. source Profile/Recommendation ID는 상관관계 증적이지 Target ID가 아니며 Qwen에는 ID를 제거한 자원 계약만 전달 |
| application.parameters | 아니오 | caller가 제공한 JSON만 허용하고 Qwen에는 전달하지 않음. 64 KiB, 깊이 16, node 1,000, key 128 rune 상한과 비밀값·책임 경계 key/value 검사를 통과한 뒤 Manifest에 복제 |
| operation_context | 아니오 | 없으면 관측 없는 신규 배포 요구로 취급하되 결과 evidence에 명시 |
| resource_snapshot, monitoring_summary, deployment_logs, metrics_summary | 아니오 | 각 객체는 source와 observed_at을 포함하는 wrapper 형식 |
| policy.mode | 예 | 1차 구현에서는 prepare_only만 허용 |
| policy.approval_reference | 아니오 | 검증 주체가 없으므로 1차 구현에서는 값이 있으면 거부 |

### 관측 데이터 처리

- source 또는 observed_at이 없는 관측 wrapper는 정규화 오류로 `REQUEST_REJECTED`가 되며 LLM 근거로 사용하지 않는다. wrapper와 typed 내부 시각은 RFC3339, 문자열 log timestamp는 RFC3339Nano로 해석한다. malformed typed RFC3339 값은 Request decoding 단계에서 거부되어 LLM에 도달하지 않는다.
- 1차 구현의 잠정 최대 관측 연령은 10분이고 허용 미래 시각 및 부모 wrapper보다 늦은 내부 시각의 skew는 1분이다. 운영 데이터의 실제 갱신 주기가 확인되면 geon과 합의해 바꾼다.
- 최대 관측 연령을 넘은 wrapper는 본문을 Qwen prompt에서 제외하고, 결과 evidence의 `stale_sources`에 source 종류를 기록한다. 단, collection 상한과 deployment scope 검사는 freshness보다 먼저 적용된다. metrics는 numeric 범위·NaN/Inf와 deployment ID 길이도 stale 제외보다 먼저 검사하므로 이 경계를 위반한 stale 입력은 `REQUEST_REJECTED`다.
- wrapper가 fresh여도 resource `last_checked_at`, monitoring `generated_at`·`latest_at`, 각 log timestamp를 교차검증한다. 오래된 내부 항목은 제외한다. typed 내부 시각의 누락·zero value·허용 미래/skew 초과는 정규화에서 요청을 거부한다.
- malformed log timestamp는 해당 log만 제외하고 `dropped_logs`를 증가시킨다. stale log도 제외해 `dropped_logs`와 `stale_sources`에 반영하지만, 허용 미래/skew 초과 log는 wrapper 전체를 거부한다.
- collection 상한은 freshness보다 먼저 검사하므로 오래된 wrapper라도 상한을 넘으면 제외가 아니라 `REQUEST_REJECTED`가 된다.
- 정규화하는 관측 문자열과 Qwen에 전달될 사용자 요청·로그·알람·상태 문자열에는 아래 상한 및 redaction을 적용한다. assignment 형태의 credential 값, Bearer token, PEM private-key marker를 탐지하며 라벨 없는 임의 opaque secret까지 완전 탐지한다고 보장하지 않는다. Qwen-bound 문자열에 그대로 반복된 8 rune 이상의 신뢰 식별자도 치환한다. `redacted_values`는 정규화 관측 전체와 Qwen-bound 문자열에서 탐지·치환한 정규식 match, private-key marker, 신뢰 식별자 수의 합이며, 모든 정제 문자열이 실제 prompt에 투영됐다는 뜻은 아니다.
- 3..7 rune의 짧은 `target_profile_id` hint는 `gpu` 같은 일반 분류어와 식별자 echo를 안전하게 구분할 수 없어 exact-ID redaction 대상이 아니다. 이 필드는 구조 입력에서는 제외되지만 caller는 짧은 opaque tenant ID나 민감값을 넣지 않아야 한다.
- LLM에는 식별자를 제거한 resource·metric 요약, alarm count·latest_at·retryable, log timestamp·level·component·error code·stage와 redaction·길이 제한을 거친 메시지를 전달한다. 변경 전 원문 로그와 구조 식별자는 전달하지 않는다.
- `application.deployment_id`가 있으면 log·metric·alarm의 deployment ID가 모두 일치해야 한다. 값이 없으면 per-deployment metrics wrapper와 non-empty log·alarm 입력을 거부하고, 전역 resource snapshot과 alarm 없는 monitoring aggregate만 허용한다. 이 scope 검사는 freshness보다 먼저 수행한다. AppDeployer monitoring 응답의 전역 status·deployment 집계는 수신할 수 있지만 scoped Qwen prompt에서는 제외한다.
- `retryable` 값은 현재 제한된 Qwen 컨텍스트에 포함되지만, 이를 결정적으로 `do_not_retry`로 변환하는 정책과 제출·재시도 adapter는 후속 범위다.

### 1차 구현의 잠정 상한

| 항목 | 잠정 기본값 | 초과 처리 |
| --- | --- | --- |
| 관측 최대 연령 | 10분 | wrapper 제외 및 `stale_sources` 기록 |
| 미래·부모 시각 skew | 1분 | REQUEST_REJECTED |
| application.user_request | 8,000 rune | REQUEST_REJECTED |
| resource targets | 100개 | REQUEST_REJECTED |
| monitoring runtime-health | 100개 | REQUEST_REJECTED |
| monitoring alarms | 100개 | REQUEST_REJECTED |
| deployment status buckets | 50개 | REQUEST_REJECTED |
| 입력 log items | 500개 | REQUEST_REJECTED |
| Qwen에 포함할 log items | 최신 50개 | 나머지를 `dropped_logs`에 반영 |
| log·alarm message | 2,000 rune | 문자열 절단. `dropped_logs`는 증가하지 않음 |
| level·component·stage·error code 등 짧은 필드 | 128 rune | 자유문자열 절단. wrapper와 fresh resource/monitoring의 ID 위반은 REQUEST_REJECTED이고 stale resource/monitoring 본문은 내부 ID 검사 전에 제외될 수 있다. metrics ID는 stale이어도 검사하며 위반 시 REQUEST_REJECTED, log item ID 위반은 해당 log drop |
| application.parameters | JSON 64 KiB, 깊이 16, node 1,000, key 128 rune | REQUEST_REJECTED |
| Qwen user message(JSON payload + 고정 prefix) | 128 KiB | Qwen 호출 전 `REQUEST_REJECTED` |
| Qwen completion content | 64 KiB | MODEL_UNAVAILABLE |

128 KiB는 고정 system message를 제외한 user message 상한이다. provider의 전체 context-window 적합성은 실제 Qwen 모델이 정해진 뒤 Candidate 설정과 통합 시험에서 별도로 검증한다.

`application.parameters`는 arbitrary execution escape hatch가 아니다. 중첩 key와 string value에서 credential, runtime/adapter, endpoint·URL, command·shell, Target/VM, cloud/provider, SSH, container/Kubernetes/Docker와 구성된 Guard 금칙어 같은 책임 경계 표현을 거부한다. 알려진 명령·provider·host 표현을 보수적으로 검사하지만 임의 문자열의 실행 의도를 완전 판별한다고 보장하지 않는다. 통과한 값도 caller가 제공한 신뢰 입력일 뿐 LLM이 생성하거나 변경하지 않는다.

현재 관측 wrapper의 `source`는 bounded identifier 형식만 검사한다. 관측 데이터의 서명, caller의 소유권, AppDeployer 조회 결과와의 동일성은 검증하지 않으므로 결과 evidence는 검증된 telemetry provenance가 아니다.

## 3. 요청 Safeguard

LLM 호출 전 아래 검사를 모두 수행한다.

| 검사 | 실패 결과 |
| --- | --- |
| 필수 ID, app_version_id, 사용자 요청 존재 | REQUEST_REJECTED |
| 요청자 allowlist 및 승인 모드 | REQUEST_REJECTED |
| VM 1차년도 범위, 금칙어, 비밀 파라미터 | REQUEST_REJECTED |
| 사용자 요청 길이 상한 초과 | REQUEST_REJECTED |
| malformed typed RFC3339 | Request decoding error, LLM 미호출 |
| 잘못된 관측 source, 누락·zero timestamp 또는 허용 미래 시각 초과 | REQUEST_REJECTED |
| 최대 연령을 넘은 관측 | 관측 본문 제외 및 `stale_sources` 기록 |
| log 입력 500개 초과 | REQUEST_REJECTED |
| 보존 log 50개 초과 | 최신 50개만 유지하고 나머지를 `dropped_logs`에 기록 |
| log·alarm message 2,000 rune 초과 | 문자열 절단. `dropped_logs`는 증가하지 않음 |
| parameters 64 KiB·깊이 16·node 1,000·key 128 rune 초과 또는 비밀·책임 경계 key/value 포함 | REQUEST_REJECTED |
| `prepare_only`가 아닌 mode 또는 값이 있는 `approval_reference` | REQUEST_REJECTED |

기존 geon Planner Guard의 금지 원칙은 그대로 사용한다. 새 계약은 상태·로그 민감정보와 관측 freshness를 추가한다.

## 4. 두 Qwen 단계와 최소 컨텍스트

`SafeguardedPlanner`는 결정적 Request Guard를 통과한 동일한 정규화 context를 두 개의 분리된 completion 단계에 사용하되, 첫 단계에는 review 전용 `required_output`, 둘째 단계에는 Proposal 전용 `required_output`을 넣는다. Proposal 계약을 첫 단계에 노출하거나 review 계약을 둘째 단계에 재사용하지 않는다.

첫째, 자연어 Safeguard review는 JSON 객체 하나에 `decision`, `reason_code`, `reason`, `confidence`만 반환한다. `decision`은 `allow_request`, `request_clarification`, `reject_request` 중 하나이고, `reason_code`는 `^[A-Z][A-Z0-9_]{2,79}$`, `reason`은 비어 있지 않은 1,000 rune 이하의 안전한 설명, `confidence`는 반드시 존재하는 0..1 숫자여야 한다. `allow_request` confidence는 0.5 이상이어야 한다. unknown field·다중 JSON·신뢰 ID·비밀값·명령·Target/Runtime, `provider` token 또는 caller-bound provider 식별자를 포함한 review는 `MODEL_UNAVAILABLE`로 fail-closed한다. allow가 아니면 Manifest Proposal completion을 호출하지 않는다.

둘째, Manifest Proposal Qwen 시스템 지시는 다음을 강제한다.

1. JSON 객체 하나만 반환한다.
2. action, reason_code, reason, confidence, accelerator, resources, assumptions만 반환한다.
3. `action`은 `create_deployment_manifest`, `request_clarification`, `reject_unsafe_request` 중 하나다. 모든 action에 regex를 만족하는 `reason_code`, 비어 있지 않은 1,000 rune 이하 `reason`, 반드시 존재하는 0..1 `confidence`가 필요하고 `assumptions`는 최대 10개, 각 1..500 rune이다.
4. create action만 `accelerator`와 `resources`를 포함한다. accelerator는 `none|nvidia`이고 resources는 CPU, memory, GPU, storage 네 문자열을 모두 포함하며 create confidence는 0.5 이상이다. non-create action은 accelerator와 resources를 생략한다.
5. CPU, memory, GPU, storage, accelerator 요구사항만 제안한다.
6. VM ID, Cloud A/B/C, Runtime Adapter, endpoint, credential, 명령어, Kubernetes 또는 컨테이너 리소스를 만들지 않는다.
7. 오래된 관측 본문은 정규화 단계에서 이미 제외된다. Qwen에는 `observation_status`와 `stale_sources`만 전달되며, 남은 명시적 사용자 요구나 정제 관측 입력 없이 자원 값을 만들지 않도록 지시한다.
8. app_version_id, deployment_id, target_profile_id의 구조 필드는 Qwen prompt와 출력에서 제외한다. Go mapper가 별도 신뢰 입력으로 채운다. 3..7 rune의 짧은 target hint 값은 사용자 자유문자열에 반복될 때 exact redaction을 보장하지 않는다는 2절의 제한을 유지한다.
9. planning constraints가 있으면 source Profile/Recommendation ID는 제거하고 feasible flag, Profile 자원 최소값, accelerator와 선택된 Resource candidate의 exact `recommended_resources`만 전달한다. 구조화 제약이 없으면 자연어 명시값을 exact로 검사한다. 구조화 제약이 있으면 자연어의 양수 자원값과 Profile 값은 하한으로 검사하되, 명시적 `GPU 0`은 exact no-GPU 제약이고 CPU·memory·storage의 명시적 0은 무효다. exact 추천값이 있으면 이를 그대로 반환하도록 요구한다. ModelRecommendation과 InferenceConfiguration은 전달하지 않는다.

Qwen endpoint, 모델명, API key 환경변수는 기존 OpenAI 호환 Candidate 설정 방식으로 주입할 수 있다. `candidate_id`는 caller가 이미 지정한 구성을 찾기 위한 결합 키이며 LLM_Op은 모델을 비교·선택하지 않는다. 두 단계는 같은 caller-bound candidate를 사용한다. 저장소에는 실제 endpoint나 비밀값을 기록하지 않는다. `PrepareWithConfig`의 `ProviderOptions.AllowLiveCompletion` 기본값은 false이며 명시적 허용 없이는 어느 completion도 HTTP client를 호출하지 않는다. true는 두 단계에 최대 두 번의 live 호출을 허용하는 통합 선택이므로 현재 단계에서는 켜지 않고 두 fixture completion client만 검증 대상으로 사용한다. 공식 종단 진입점은 `SafeguardedPlanner`이며 내부 `Planner` 직접 호출은 review 단계를 우회하므로 Proposal 단위 테스트 외의 통합 경로로 사용하지 않는다.

현재 Repair는 자동 수정 loop가 아니다. Safeguard가 명확화를 요구하거나 deterministic semantic Guard가 모순을 찾으면 Manifest와 prepared request 없이 종료한다. 사용자 수정 입력, 재시도 횟수와 승인 정책이 정의되기 전에는 모델이 값을 바꿔 다시 제출하지 않는다.

결과의 `evidence.input.*_included`는 freshness·내부 timestamp·scope·redaction·길이 제한을 거친 사용 가능한 본문의 bounded prompt 포함 여부다. `resource_snapshot_included`는 fresh target이 하나 이상, `logs_included`는 보존 log가 하나 이상일 때 true다. `monitoring_included`는 unscoped 요청에서는 fresh global summary가 있으면 true이고, deployment-scoped 요청에서는 보존 alarm이 하나 이상일 때만 true다. `metrics_included`는 fresh metrics wrapper가 있으면 true다. 이 값은 Qwen이 해당 본문을 실제 추론 근거로 사용했다는 증명, Proposal과 관측값의 의미적 일치, 자원 feasibility 또는 관측 provenance를 뜻하지 않는다.

## 5. 출력: LLMOperationResult

아래 JSON은 주요 필드만 보여 주는 비규범 축약 예시다. 실제 성공 결과에는 `trace_id`, Guard reason/checks, `latency_ms`, `dropped_logs`와 `handoff.prepared_request`가 함께 직렬화된다. `prepared_request`는 top-level `manifest`를 AppDeploy의 정확한 `DeploymentCreateRequest.manifest` 키로 한 번 감싼 값이며 전송되지는 않는다. `StageError`는 Result 내부 오류 정보 필드가 아니라 별도 Go error 반환값이다. 외부 API를 만들 때는 이 문서 대신 별도 JSON Schema로 정확한 직렬화 계약을 고정해야 한다.

~~~json
{
  "api_version": "ai-ops.llm-operation/v1alpha1",
  "request_id": "req-20260805-001",
  "correlation_id": "corr-123",
  "status": "HANDOFF_READY",
  "decision": {
    "action": "create_deployment_manifest",
    "reason_code": "FRESH_LATENCY_SIGNAL_AND_EXPLICIT_GPU_REQUIREMENT",
    "reason": "사용자 요청과 최근 지연 경보가 GPU 추론 배포 요구와 일치합니다.",
    "confidence": 0.95,
    "assumptions": [],
    "observation_status": "fresh"
  },
  "safeguard": {
    "request": {
      "valid": true,
      "status": "approved",
      "policy_version": "v1"
    },
    "manifest": {
      "valid": true,
      "status": "approved"
    }
  },
  "manifest": {
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
  },
  "evidence": {
    "safeguard_review": {
      "provider": "fixture-openai-compatible",
      "candidate_id": "qwen3.5-ops-planner",
      "actual_model": "fixture-qwen",
      "decision": "allow_request",
      "reason_code": "BOUNDED_REQUEST_ALLOWED",
      "confidence": 0.99
    },
    "model": {
      "provider": "fixture-openai-compatible",
      "candidate_id": "qwen3.5-ops-planner",
      "actual_model": "fixture-qwen"
    },
    "input": {
      "observation_status": "fresh",
      "resource_snapshot_included": true,
      "monitoring_included": true,
      "logs_included": true,
      "metrics_included": true,
      "redacted_values": 1
    }
  },
  "handoff": {
    "submission_mode": "not_submitted",
    "next_endpoint": "/api/v1/deployments",
    "prepared_request": {
      "manifest": "top-level manifest와 동일한 DeploymentManifest 객체"
    }
  }
}
~~~

### 상태값

| status | 의미 | AppDeployer 호출 |
| --- | --- | --- |
| HANDOFF_READY | 구문·책임 경계·자원 상한·결정적 자원값 정합성 Guard를 통과한 prepare-only Manifest 초안이 준비됨 | 호출하지 않음 |
| CLARIFICATION_REQUIRED | Safeguard review의 `request_clarification` 또는 Proposal의 `request_clarification`이 추가 사용자 정보를 요구 | 호출하지 않음 |
| REQUEST_REJECTED | Request Guard·preflight 위반, Safeguard review의 `reject_request` 또는 Proposal의 `reject_unsafe_request` | 호출하지 않음 |
| MANIFEST_REJECTED | Proposal JSON·의미 정합성·Manifest mapping/Go Guard가 형식·경계·자원 규칙을 위반 | 호출하지 않음 |
| MODEL_UNAVAILABLE | 어느 단계든 Qwen 후보·client·transport·Completion envelope/identity 오류, 또는 Safeguard review JSON 계약 오류 | 호출하지 않음 |
| CONFIGURATION_ERROR | Candidate·Guard 정책 파일을 읽을 수 없거나 live completion을 명시적으로 허용하지 않음 | 호출하지 않음 |

두 Qwen 단계 모두 `request_clarification`을 낼 수 있지만, 현재 결정적 Request Guard는 필수 ID 누락·비-`prepare_only` mode·`approval_reference`를 이 상태로 보내지 않고 `REQUEST_REJECTED`로 처리한다. stale 관측도 자동 명확화 사유가 아니라 prompt 제외와 `stale_sources` 기록으로 처리한다. 명확화 입력을 사용자에게 다시 받는 API 흐름은 후속 범위다.

HANDOFF_READY는 관측의 진위, 수량 단위의 실제 자원 가용성, Target 호환성, VM 배포 가능성 또는 실행 성공을 뜻하지 않는다. 현재 구현은 명시된 자연어·구조 자원값 및 fresh availability boolean과의 결정적 모순을 별도로 거부하지만 일반적인 의미·feasibility 검증을 보장하지 않는다. AppDeployer POST를 수행하지 않으며 제출 adapter와 승인 검증 adapter도 없다. `handoff.next_endpoint`는 상대 경로 안내이고 `handoff.prepared_request`는 exact body 초안일 뿐, endpoint 호출·승인·인증·도달 가능성이 확인되었다는 뜻이 아니다.

## 6. 출력 Safeguard

Qwen 결과를 파싱한 뒤 결정적 규칙으로 아래를 확인한다.

| 검증 | 요구사항 |
| --- | --- |
| JSON 단일 Proposal | 설명문, 다중 객체, 알려지지 않은 필드 거부 |
| Completion envelope | status executed, non-negative latency, non-empty content, 최대 64 KiB |
| Completion adapter 일관성 | candidate_id, provider, actual_model이 구성값과 일치해야 함. 이 metadata는 기본 client가 candidate config에서 채우므로 provider attestation이 아님 |
| 필수 출력 | confidence 필드 존재, 0..1 범위. reason 최대 1,000 rune, assumptions 최대 10개·각 500 rune, reason_code 3..80자의 대문자 ASCII·숫자·underscore. Manifest 생성 action은 confidence 0.5 이상 필요 |
| Manifest 계약 | mapper가 schema_version, kind, 신뢰 ID를 채운 뒤 Go Guard 검사 |
| 신뢰 필드 보존 | Qwen 출력에서 app_version_id, deployment_id, Target 관련 필드를 허용하지 않음 |
| 자원 정책 | CPU 1..256, GPU 0..16, memory 최대 2Ti, storage 최대 64Ti, 지원하지 않는 단위와 GPU/accelerator 불일치 거부 |
| 자연어 정합성 | create action은 CPU·GPU·memory·storage의 정확값이 모두 명시되거나 신뢰 planning constraints가 있어야 함. 구조화 제약이 없으면 명시값과 Proposal이 단위 정규화 후 정확히 일치한다. 구조화 제약이 있으면 양수 명시값은 하한, `GPU 0`은 exact no-GPU 제약이며 CPU·memory·storage 0은 무효다. 누락·근사·범위·소수·분수·상충 중복은 거부 |
| 구조화 자원 계약 | Common JSON의 Profile 하한·accelerator를 만족하고, `recommended_resources`가 있으면 선택된 단일-node CPU·memory·GPU·storage·accelerator exact 값과 Proposal이 일치해야 함. source ID와 Resource candidate ID는 prompt·Manifest에 넣지 않음 |
| fresh readiness | fresh snapshot이 있으면 필요한 availability boolean이 동일한 Target에서 동시에 true여야 함. optional target hint가 있으면 그 Target만 검사. stale/없는 snapshot은 음성 증거로 해석하지 않음 |
| 책임 경계 | VM ID, Target 확정, Runtime, `provider` token·cloud provider 선택, credential, endpoint, 명령어, Kubernetes 관련 필드 거부 |
| 출력 사유 | reason_code·reason·assumptions의 비밀값, 8 rune 이상 신뢰 ID, endpoint·명령·Target/Runtime 경계 표현 거부 |
| 실행 정책 | prepare_only만 허용하고 제출 adapter는 호출하지 않음 |

자연어 정확값 parser는 의도적으로 bounded grammar다. 현재 지원하는 기본 형태는 `CPU 4`, `CPU: 4`, `GPU 1개`, `메모리 16Gi`, `memory 16384Mi`, `저장소 20Gi`, `storage 20Gi`처럼 자원 label이 수치 앞에 오는 표현이다. memory/storage 단위는 `Mi`, `Gi`, `Ti`만 지원한다. `4 CPU cores`, `16 GiB memory`, `CPU 4.5`, 범위·근사·상충 중복 또는 열거하지 않은 자연어 표현은 값을 추측하지 않고 create를 거부하거나 Safeguard의 clarification 경로로 보낸다. 이 grammar는 일반 자연어 전체를 이해한다는 주장이 아니며, 새 표현을 허용할 때는 fail-open을 막는 fixture를 함께 추가한다.

## 7. AppDeployer API 매핑

AppDeployer 브랜치의 OpenAPI에 아래 경로가 문서화되어 있다. 다음 표는 연동 계약 참고이며 현재 `internal/llmop`이 이 API를 호출한다는 뜻이 아니다.

| LLM_Op 단계 | AppDeployer 경로 | 용도 |
| --- | --- | --- |
| 관측 입력 구성 | GET /api/v1/resources/inventory | 최근 자원 inventory 조회 |
| 관측 입력 구성 | GET /api/v1/monitoring/summary | 배포 집계, Runtime health, 알람 요약 |
| 관측 입력 구성 | GET /api/v1/monitoring/runtime-health | Target별 Runtime health |
| 관측 입력 구성 | GET /api/v1/deployments/{deployment_id} | 개별 상태 |
| 관측 입력 구성 | GET /api/v1/deployments/{deployment_id}/logs | 표준 배포·Runtime 로그 |
| 관측 입력 구성 | GET /api/v1/deployments/{deployment_id}/metrics | 배포 단위 metric 요약 |
| 제출 | POST /api/v1/deployments | DeploymentManifest 전달 |
| 제출 후 확인 | GET /api/v1/deployments/{deployment_id} | 상태 조회 |

현재 geon에는 배포·상태·로그·metric·monitoring summary client가 있지만 resource inventory와 개별 runtime-health 전용 client 메서드는 없다. 따라서 1차 LLM_Op은 관측값을 요청 본문으로 받아 정규화하며, 직접 조회 adapter와 제출 adapter는 후속 결합 작업으로 둔다. DeploymentManifest는 internal/appdeploy 타입과 Go Guard를 canonical 계약으로 사용한다.

`examples/llm-op/fresh-latency-appdeploy-request.expected.json`은 현재 성공 fixture가 후속 AppDeploy client에 넘길 정확한 HTTP body shape다. Agent Control의 Common JSON `DeploymentCreateRequestEnvelope`는 artifact, target_runtime, inference configuration 등 별도 필드를 요구하므로 이 body와 동일한 형식이 아니다. `internal/llmopbridge`는 그 envelope를 AppDeploy body로 캐스팅하지 않고, 앞 단계의 analysis/context/resource recommendation에서 Profile 최소값과 선택 Resource candidate의 exact 단일-node 값만 LLM_Op request로 투영한다. geon 기본 CPU Profile보다 큰 카탈로그 후보는 보존해 연결하지만, device-memory minimum이 있는 현재 자연어 GPU 경로는 AppDeploy v1에 표현할 수 없어 거부한다. 상세 제한은 `03-common-json-bridge.md`에 있다.

## 8. 미확정 연결 정보

- 실제 Qwen endpoint, actual_model, 인증 방식과 소유자
- AppDeployer 서비스 base URL 및 개발·통합 환경별 접근 방식
- Scheduler Agent A/B가 제공할 서버 상태 원문과 갱신 주기
- 승인 reference의 발급 주체와 검증 API

현재 demo에는 Safeguard review와 Proposal Qwen 응답 JSON을 각각 그대로 반환하는 두 fixture completion client가 있다. 이 demo는 HTTP client나 endpoint를 받지 않으므로 Qwen 네트워크 호출과 API 비용이 발생하는 경로가 없다. AppDeployer 호출 adapter와 승인 검증 adapter도 없다. 실제 Qwen 연결 정보가 없거나 Candidate가 비활성화된 경로는 `MODEL_UNAVAILABLE`, fixture가 구문·책임 경계·자원 상한·결정적 자원 정합성 Guard를 통과한 prepare-only 초안 경로는 `HANDOFF_READY`를 의도한다. 이 동작은 아직 Go 실행으로 검증하지 않았다.
