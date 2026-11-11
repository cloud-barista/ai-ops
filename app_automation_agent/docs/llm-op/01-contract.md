# LLM_Op v1alpha1 계약

## 범위

LLM_Op은 자연어 운영 요청과 선택적 상태·로그·모니터링을 받아 prepare-only AppDeploy `DeploymentManifest` 초안을 만든다. LLM은 두 단계의 bounded JSON만 제안하고 Go mapper가 LLM 밖에서 받은 identity field를 주입한다. 현재 Request type 자체는 그 값의 인증·등록 관계를 증명하지 않는다. Target/Runtime/provider 선택, credential, 실행 명령, 승인, POST는 이 계약에 없다.

이 문서가 LLM_Op의 normative contract다. 심층 위협 모델은 `05-safeguard-policy-llm-guide.md`, 구현 증적과 미지원 경계는 `04-implementation-completeness-audit.md`를 따른다.

## 입력

`LLMOperationRequest` 핵심 필드:

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `api_version` | 예 | `ai-ops.llm-operation/v1alpha1` |
| `request_id`, `correlation_id` | 예 | bounded ASCII identifier |
| `trace_id` | 아니오 | bounded ASCII identifier |
| `candidate_id` | 예 | caller가 이미 고정한 Planner candidate ID |
| `requested_by` | 예 | Guard policy allowlist와 일치. 인증 principal 증명은 integration 책임 |
| `application.app_version_id` | 예 | integration-supplied 값. 공개 route는 registry로 Common JSON app identity와 결합해야 함 |
| `application.deployment_id` | 아니오 | log/metric/alarm scope와 일치해야 함 |
| `application.user_request` | 예 | 최대 8,000 rune 또는 더 작은 policy limit |
| `application.target_profile_id` | 아니오 | integration-supplied hint. LLM이 생성하지 않으며 서버 측 binding 전에는 trusted가 아님 |
| `application.planning_constraints` | 아니오 | Profile minima와 선택적 exact recommendation |
| `application.parameters` | 아니오 | v1alpha1에서는 non-empty 전부 거부 |
| `operation_context.*` | 아니오 | source·observed_at wrapper |
| `policy.mode` | 예 | `prepare_only`만 허용 |
| `policy.approval_reference` | 아니오 | 현재 반드시 빈 값 |

`application.parameters`는 downstream schema가 `additionalProperties`이기 때문에 allowlist 없이 안전성을 증명할 수 없다. 따라서 크기·secret denylist를 통과하더라도 non-empty이면 `manifest_parameters_disabled`로 `REQUEST_REJECTED`되고 Manifest에 복사되지 않는다.

## raw snapshot 전 상한

caller-owned map/pointer alias와 거대 문자열을 모델 호출 전에 차단한다.

| 항목 | 상한 |
| --- | --- |
| user request | 8,000 rune |
| raw string field | 32 KiB |
| raw request text 합계 | 2 MiB |
| parameters | 64 KiB text/JSON, depth 16, node 1,000, key 128 rune; 이후 non-empty 전면 거부 |
| resource targets | 100 |
| runtime health | 100 |
| alarms | 100 |
| status buckets | 50 |
| raw log items | 500 |

상한을 통과하면 typed Request 전체를 JSON deep snapshot하고 이후 모든 guard·prompt·mapper가 그 snapshot만 사용한다.

## 정규화

고정 security ceiling보다 큰 caller override는 거부한다.

| 항목 | ceiling | 처리 |
| --- | --- | --- |
| observation age | 10분 | stale 본문 제외, source 종류 기록 |
| future/parent skew | 1분 | wrapper 또는 항목 거부 |
| retained logs | 50 | 초과 drop evidence |
| log message | 2,000 rune | redaction 후 절단 |
| 짧은 text field | 128 rune | source/ID는 형식 위반 거부, display field는 bounded sanitize |
| Qwen user message | 128 KiB | 초과 `REQUEST_REJECTED` |

log credential-like 값은 `[REDACTED]`로 치환한다. user request나 parameters의 credential-like 값은 completion 전에 거부한다.

deployment ID가 있으면 logs, metrics, alarms가 같은 deployment scope여야 한다. 없으면 per-deployment metrics와 non-empty logs/alarms를 거부한다. unscoped global monitoring aggregate만 제한적으로 사용할 수 있다.

## 결정적 Request Guard

다음은 completion 0회로 `REQUEST_REJECTED`다.

- required/API/version/identifier/requester/prepare-only 위반
- secret-bearing user text 또는 parameter
- non-empty parameters
- Target/VM/provider/runtime/endpoint/command/container/Kubernetes 요청
- user 또는 Qwen-bound observation의 prompt-control instruction
- zero-width, bidi override, forbidden control character
- resource negation/contrast/conditional/preference grammar
- 알려진 NPU/TPU/FPGA/ASIC·GPU/CPU SKU·architecture·CUDA/driver capability 표현
- 알려진 direct SLO, cost, replica/multi-node, GPU device memory, throughput 표현
- OS/image/port/network/region/zone/volume/mount/topology처럼 v1alpha1에 없는 알려진 deployment detail
- observation deployment scope mismatch
- malformed planning constraints

위 검사는 명시적으로 지원하는 affirmative exact grammar와 알려진 금지 표현에 대한 heuristic이다. 그 범위에서 미표현 요구를 조용히 버리고 네 자원값만 생성하지 않는다. 임의의 자연어 의미 완전성을 증명하지 않으므로 공개 API 전에는 allowlisted structured intent 차원 또는 검증된 parser가 release blocker다.

## 두 LLM 단계

geon과 분리 연결할 때도 논리적으로는 같은 두 단계다. `ReviewRequest/ReviewWithConfig`가 1단계만 실행하고 승인 continuation을 내보낸다. 이 최초 요청에는 `PlanningConstraints`가 없어야 한다. geon의 승인된 `INITIAL` revision을 결합한 뒤 trusted in-process `PrepareApproved/PrepareApprovedWithConfig`가 continuation의 SHA-256 binding을 재계산하고 2단계만 실행한다. 따라서 성공 흐름의 completion 수는 두 번이며 Safeguard를 반복하지 않는다. `PlanningConstraints`만 승인 뒤 trusted enrichment로 허용되고, 그 외 최초 요청 필드는 binding 대상이다. plain SHA-256은 인증 seal이 아니므로 외부 caller에게 이 resume 경로를 노출하지 않는다.

### 1. Safeguard review

출력:

~~~json
{
  "decision": "allow_request",
  "reason_code": "BOUNDED_REQUEST_ALLOWED",
  "reason": "The request is bounded to prepare-only planning.",
  "confidence": 0.98
}
~~~

allow/clarify/reject만 허용한다. allow confidence는 0.5 이상이다. allow가 아니면 Proposal completion은 호출하지 않는다.

### 2. Manifest Proposal

출력:

~~~json
{
  "action": "create_deployment_manifest",
  "reason_code": "RESOURCE_PLAN_READY",
  "reason": "The exact resource contract is satisfied.",
  "confidence": 0.95,
  "accelerator": "nvidia",
  "resources": {
    "cpu": "4",
    "memory": "16Gi",
    "gpu": "1",
    "storage": "20Gi"
  },
  "assumptions": []
}
~~~

허용 action은 create/clarify/reject 세 개다. create만 accelerator/resources를 포함한다.

두 parser는 다음을 거부한다.

- 64 KiB 초과
- unknown field
- duplicate key와 case-variant key
- 여러 JSON 값 또는 trailing value
- JSON depth 16 또는 node 1,000 초과
- missing confidence
- bidi/control/HTML angle bracket가 있는 display text
- secret, trusted ID, Target/provider/runtime/endpoint/command 표현
- H100, CUDA, p95/SLO, cost, topology 등 지원하지 않는 요구를 만족했다는 표현

기계 판독 shape는 schemas/llm-op/safeguard-review.v1alpha1.schema.json과 schemas/llm-op/manifest-proposal.v1alpha1.schema.json에 있다. JSON Schema는 field·enum·기본 길이 형상을 공유하기 위한 보조 계약이다. 중복 key, byte/depth/node 상한, exact 자원 의미, secret/identifier hygiene, freshness/readiness는 raw text를 받는 Go Guard가 추가로 강제한다.

두 단계는 동일한 caller-pinned candidate를 쓰며 독립적인 두 security authority가 아니다. 결정적 Go guard가 trust anchor다.

tool/function schema는 어느 단계에도 제공하지 않으며 tool call이나 command를 실행하지 않는다. create가 최종 승인되면 model `reason`과 `assumptions`는 public 성공 설명에 복사하지 않고 Go가 `BOUNDED_MANIFEST_PREPARED`와 결정적 설명으로 교체한다. clarification/reject text도 표시·감사용 plain text일 뿐 template, command, 재프롬프트, authorization 입력으로 사용하지 않는다.

## 자원 의미 규칙

전역 format ceiling:

- CPU: 1..256 정수
- GPU: 0..16 정수
- memory: positive Mi/Gi/Ti, 최대 2 TiB
- storage: positive Mi/Gi/Ti, 최대 64 TiB
- accelerator: `none` 또는 `nvidia`

전역 ceiling은 추천값이 아니다.

| 계약 입력 | create 조건 |
| --- | --- |
| constraints 없음 | user가 affirmative exact CPU/GPU/memory/storage 네 값을 모두 명시하고 Proposal과 exact 일치 |
| Profile minima만 있음 | Proposal이 component-wise `max(Profile minima, 명확한 user positive value)`와 exact 일치 |
| exact `recommended_resources` 있음 | Proposal이 recommendation과 exact 일치 |

`GPU 0`은 exact no-GPU다. CPU/memory/storage 0, 모호·범위·분수·소수·중복 충돌은 거부한다. source Profile/Recommendation ID와 Resource candidate ID는 prompt와 Manifest에 전달하지 않는다.

## resource snapshot 의미

| 입력 | create 처리 |
| --- | --- |
| snapshot 미제공 | readiness unknown. exact resource contract가 있으면 draft handoff 가능 |
| snapshot 제공, outer stale | semantic `stale_resource_snapshot` 거부 |
| outer fresh, usable target 0 | 거부 |
| target ID가 비어 있거나 형식 위반 | normalization 거부 |
| 동일 target ID가 중복됨 | semantic `ambiguous_resource_snapshot` 거부 |
| target status != `available` 또는 runtime_health != `ok` | 거부 |
| CPU/memory/storage 또는 필요한 GPU boolean false | 거부 |
| fresh monitoring runtime row의 target ID가 비어 있거나 형식 위반 | normalization 거부 |
| resource와 fresh monitoring runtime 상태가 모순 | 거부 |
| 동일 target에서 status/runtime/required booleans 충족 | readiness contradiction 없음 |

snapshot boolean은 실제 수량 capacity나 schedulability를 증명하지 않는다. `HANDOFF_READY`는 deploy-ready가 아니다.

## 모델 연결 경계

production live 단일-process entrypoint는 `PrepareWithConfig`다. Guard-first Go integration은 `ReviewWithConfig`와 `PrepareApprovedWithConfig` 쌍이며, 후반 resume는 trusted in-process orchestration 전용이다. 이 public Go entrypoint들은 내부 `NewNormalizer`의 system clock을 사용하며 caller가 freshness 시각을 바꿀 수 없다. Go 함수가 exported라는 사실은 HTTP caller 권한을 뜻하지 않는다. `ProviderOptions.AllowLiveCompletion=false`가 기본이며 false이면 HTTP client 호출 전에 `CONFIGURATION_ERROR`다. true는 현재 release blocker를 통과했다는 뜻이 아니므로 데모에서 사용하지 않는다.

public offline entrypoint는 `NewOfflineFixturePlanner`다. pre-recorded review/Proposal JSON 문자열만 받고 endpoint, API key, HTTP client가 없다. provider와 actual-model evidence는 아래 두 fixture label로 고정되며 다른 label, endpoint, API-key field를 가진 Candidate는 completion 전에 거부된다.

demo binding:

- candidate ID: `qwen3.5-ops-planner`
- provider evidence: `offline-fixture`
- actual model evidence: `fixture-qwen-contract-not-executed`
- intended model metadata: `qwen3.5:4b`
- weight 1.0: metadata-only, selection disabled

`actual_model`은 live provider attestation이 아니라 configured/completion-adapter identity label이다.

## Result 상태

| status | 의미 | prepared request |
| --- | --- | --- |
| `SAFEGUARD_APPROVED` | 최초 Request Guard와 Safeguard review가 동일 요청의 downstream planning만 허용 | 없음; 권한·배포 승인이 아닌 continuation evidence만 있음 |
| `HANDOFF_READY` | exact draft body가 모든 현재 guard를 통과 | 있음, POST 안 함 |
| `CLARIFICATION_REQUIRED` | 모델 free-text clarification | 없음 |
| `REQUEST_REJECTED` | deterministic guard 또는 모델 reject | 없음 |
| `MODEL_UNAVAILABLE` | candidate/client/review completion 계약 실패 | 없음 |
| `MANIFEST_REJECTED` | Proposal/parser/semantic/AppDeploy guard 실패 | 없음 |
| `CONFIGURATION_ERROR` | policy/candidate/live gate 설정 실패 | 없음 |

실패 result는 Manifest, next endpoint, prepared request를 노출하지 않는다.

## AppDeploy handoff

성공 시 mapper가 다음 non-LLM field를 넣는다. 공개 route에서는 인증 principal·registry·trusted telemetry builder가 이 값들을 덮어쓰고 결합해야 하며, 현재 caller Request만으로 trusted provenance를 주장하지 않는다.

- schema version과 kind
- caller `app_version_id`
- optional integration-bound `target_profile_id` hint
- caller `requested_by`
- 검증된 accelerator/resources

non-empty parameters는 없다. `handoff.prepared_request`는 exact `DeploymentCreateRequest{manifest}`다. `next_endpoint=/api/v1/deployments`는 상대 경로 안내이며 호출 증적이 아니다. submission mode는 항상 `not_submitted`다.

## Evidence 해석

`*_included`는 bounded prompt projection에 해당 관측 종류가 반영됐다는 뜻이다. resource snapshot은 raw Target 본문이 아니라 식별자를 제거한 availability aggregate다. 이 값은 Qwen의 실제 근거 사용, 관측 provenance, feasibility를 증명하지 않는다.

Safeguard/clarification/reject의 model `reason_code`와 `confidence`, 성공 Decision에 남는 confidence는 자기보고 display/audit telemetry다. downstream authorization, retry, command, fallback 결정을 내리는 값으로 쓰지 않는다. 성공 reason code/text는 결정적 Go 값이다. semantic internal code는 현재 wire Result에 별도 필드로 노출되지 않는다. clarification은 아직 `missing_fields`, canonical question, resume token이 없는 free-text 계약이다.

## Common JSON bridge

`internal/llmopbridge`는 analysis/context/resource recommendation의 correlation·trace·causation과 Profile/Recommendation join을 검사해 supported single-node subset만 투영한다. ModelRecommendation, artifact, inference config, runtime, VM을 읽지 않는다.

SLO, cost, multi-replica, GPU device memory는 AppDeploy v1에 lossless하게 표현할 수 없어 bridge에서 거부한다. join consistency는 producer authenticity, replay protection, ownership을 증명하지 않고 `Projection.Evidence`가 자동으로 final Result에 보존되는 것도 아니다. Common JSON `app_id/app_version`과 AppDeploy `app_version_id`의 registry binding도 bridge가 증명하지 않는다. 이 identity/provenance 결합은 공개 route 전 release blocker다.
