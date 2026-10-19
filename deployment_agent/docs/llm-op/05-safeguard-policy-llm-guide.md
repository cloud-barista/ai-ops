# Safeguard 및 bounded 정책 제안 LLM 가이드

## 용어와 권한

이 구현에서 “정책 생성 LLM”은 보안 정책 파일을 작성하거나 권한을 결정하는 LLM을 뜻하지 않는다. 세 계층을 분리한다.

| 계층 | 소유자 | 권한 |
| --- | --- | --- |
| 결정적 정책 | 신뢰된 Go 코드 + 검토된 config | 허용 requester, prepare-only, 입력 상한, 금지 범위, exact 자원 계약을 강제 |
| 자연어 Safeguard LLM | 비신뢰 completion | allow / clarification / reject를 제안 |
| Manifest Proposal LLM | 비신뢰 completion | 제한된 action, 설명, accelerator, 네 자원값을 제안 |

LLM은 guard policy를 편집하지 않고, requester 권한·Target·provider·runtime·credential·endpoint·명령을 정하지 않는다. LLM 출력은 결정적 검증을 통과해야만 Manifest 초안에 반영된다.

## 신뢰 경계

~~~text
인증되지 않은 자연어·관측
  -> raw envelope / text hygiene / Request Guard
  -> bounded normalized projection
  -> [비신뢰] Safeguard review LLM
  -> strict JSON / identity / text validation
  -> [연결 시] SAFEGUARD_APPROVED continuation
  -> [geon] Requirement/Profile/Recommendation/DEPLOY Guard/INITIAL revision
  -> [LLM_Op] continuation binding 재검산
  -> [비신뢰] Manifest Proposal LLM
  -> strict JSON / exact semantic guard / readiness guard
  -> [신뢰] Go mapper + AppDeploy Manifest Guard
  -> POST하지 않은 DeploymentCreateRequest
~~~

단일-process `Prepare/PrepareWithConfig`는 위 흐름을 연속 실행한다. geon과 분리할 때는 `ReviewRequest/ReviewWithConfig` → `ProjectApprovedInitialFlow` → trusted in-process `PrepareApproved`를 사용한다. 최초 Safeguard는 geon의 live analyzer보다 먼저 실행하고 allow 이외 결과를 다른 planner로 우회하지 않는다. 후반 진입점은 Safeguard LLM을 반복하지 않고 SHA-256 continuation을 재계산한 뒤 Proposal만 호출한다. `PlanningConstraints`만 trusted enrichment로 제외되며 다른 요청·관측·policy·candidate·AppVersion 값은 최초 결정에 결합된다. SHA-256은 인증 seal이 아니므로 외부 resume API는 server-side opaque record나 HMAC/서명이 마련되기 전까지 금지한다.

신뢰되는 값도 출처가 구분돼야 한다.

- `requested_by`, app version, candidate/deployment/Target hint, structured constraints는 인증된 integration layer가 구성해야 한다.
- 현재 public Request type 자체는 provenance를 증명하지 않는다.
- Common JSON `app_id/app_version`과 AppDeploy `app_version_id`는 trusted registry의 같은 등록 항목으로 결합해야 한다.
- observation `source` 문자열은 형식만 검증하며 서명이나 소유권 증명이 아니다.
- Common JSON correlation/causation join은 메시지 진위·replay 방지를 증명하지 않는다.
- public live entrypoint는 system clock을 내부 소유하지만, effective evaluation time을 Result evidence에 남기는 계약은 아직 없다.

## 위협 모델

| 위협 | 예 | 현재 방어 |
| --- | --- | --- |
| direct prompt injection | “이전 지시 무시”, system prompt 공개 | user/parameter/observation heuristic + LLM prompt 분리 + output guard |
| indirect prompt injection | log/alarm/status key에 명령 삽입 | 모든 Qwen-bound 문자열 사전 검사 |
| display spoofing | bidi override, zero-width, control, HTML-like output | input/output hygiene |
| parser ambiguity | duplicate `action`, `Action` case variant, trailing JSON | canonical key·duplicate 검사, strict struct decode, single value 검사 |
| secret leakage | bearer token, API key, private key | preflight reject 또는 log redaction, output secret 검사 |
| responsibility escape | Target, provider, runtime, endpoint, command | deterministic deny boundary + LLM output 검사 |
| resource inflation | Profile minimum 4 CPU인데 256 CPU 제안 | exact recommendation 또는 derived minimum exact match |
| stale evidence laundering | stale snapshot이 “없음”으로 바뀜 | supplied-stale create 거부; missing은 unknown으로 표시 |
| TOCTOU | completion 중 caller가 map/pointer 변경 | request deep snapshot |
| network/cost surprise | fixture 경로가 실제 client 호출 | public offline factory에 network client가 없음 |
| fixture evidence spoof | offline JSON이 실제 provider/Qwen 실행처럼 표시 | provider/model label 고정, endpoint/API-key field 거부 |
| narrative spoof | reason이 H100/p95 또는 다른 자원값 성공을 주장 | unsupported claim 거부; create 성공 설명·reason code를 Go 값으로 교체 |

prompt-injection, exact-resource, unsupported-requirement 검사는 모두 명시적으로 지원하는 문법·알려진 표현에 대한 heuristic이다. unicode 우회, soft/conditional 동의어, 새로운 SLO·topology 표현을 완전 판별한다고 주장하지 않는다. 핵심 안전성은 모델이 잘못 allow/create해도 identity field·자원 계약·출력 범위를 결정적 guard가 다시 제한하는 데 있다. 공개 자연어 API 전에는 allowlisted structured intent 차원 또는 검증된 parser가 필요하다. clarify/reject 유도형 availability 공격은 남을 수 있으므로 운영에서는 입력 provenance와 rate limiting이 필요하다.

Bearer/API-key/PEM처럼 표식이 있는 secret은 검사하지만 opaque하거나 label-free인 비밀값을 완전 탐지할 수 없다. 외부 provider로 telemetry를 보내려면 redaction만으로 승인하지 말고 데이터 분류·보존·egress 정책을 별도 gate로 둔다.

## 단계 0: 결정적 Request Guard

completion 전에 다음을 검사한다.

- fixed 8,000-rune user request와 더 작은 policy limit
- pre-snapshot raw field 32 KiB, text envelope 2 MiB
- prepare-only, 빈 approval reference
- bounded identifier와 allowed requester
- non-empty untyped parameters 전면 거부
- credential, Target, runtime, provider, endpoint, command 경계
- user·observation prompt-control pattern
- zero-width·bidi·금지 control character
- resource negation/contrast grammar
- conditional/preference resource grammar
- 알려진 hardware SKU/runtime capability, topology, SLO/cost, OS/port/network 미표현 요구
- deployment-scoped log/metric/alarm 일치
- structured planning constraints 형식·상한

실패하면 두 LLM 모두 호출하지 않고 `REQUEST_REJECTED`다.

## 단계 1: 자연어 Safeguard review

입력은 raw object가 아니라 identifier 제거·redaction·freshness·collection 상한을 거친 projection이다. 출력은 JSON object 하나다.

~~~json
{
  "decision": "allow_request",
  "reason_code": "BOUNDED_REQUEST_ALLOWED",
  "reason": "The request is bounded to prepare-only resource planning.",
  "confidence": 0.98
}
~~~

규칙:

- `decision`: `allow_request`, `request_clarification`, `reject_request`
- `reason_code`: 3..80 uppercase ASCII/digit/underscore telemetry label
- `reason`: 1..1,000 rune display-safe 설명
- `confidence`: 0..1, allow는 0.5 이상
- unknown field, duplicate/case-variant key, 다중 JSON, unsafe display text는 거부
- allow가 아니면 Proposal completion은 0회

reason code와 confidence는 모델 자기보고다. 권한 판정이나 calibrated probability가 아니다.

## 단계 2: Manifest Proposal

출력은 전체 Manifest가 아니라 아래 bounded proposal이다.

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

허용 action:

- `create_deployment_manifest`
- `request_clarification`
- `reject_unsafe_request`

create만 accelerator와 resources를 포함한다. non-create가 해당 필드를 포함하면 거부한다. app/deployment/Target ID, provider, runtime, endpoint, credential, command, arbitrary parameters는 어떤 text field에도 넣을 수 없다.

두 단계에 tool/function schema는 없고 tool call, shell, endpoint 호출을 실행하지 않는다. reason과 assumptions는 plain-text audit/display 데이터일 뿐 명령, template, 재프롬프트, authorization 또는 fallback 입력이 아니다. create가 최종 guard를 통과하면 raw model reason/assumptions는 public 성공 Decision에서 버리고 `BOUNDED_MANIFEST_PREPARED`와 결정적 설명으로 교체한다.

## 결정적 자원 규칙

| 입력 상태 | 허용 Proposal |
| --- | --- |
| structured constraints 없음 | user request에 affirmative exact CPU·GPU·memory·storage 네 값이 모두 있어야 하고 Proposal과 exact 일치 |
| Profile 최소값만 있음 | 각 자원별 `max(Profile minimum, 명확한 사용자 양수값)`과 exact 일치 |
| exact `recommended_resources` 있음 | recommendation과 CPU·memory·GPU·storage·accelerator exact 일치 |
| user `GPU 0` + GPU minimum > 0 | 거부 |
| CPU·memory·storage 0 | 거부 |
| 근사·범위·소수·분수·상충·부정·대조 | 거부 |
| 전역 ceiling 초과 | 거부 |

전역 ceiling은 resource recommendation이 아니다. exact 계약이 있는 경우에만 create가 성공한다.

CPU/GPU count와 memory/storage quantity는 canonical decimal/unit 문자열만 허용한다. 예를 들어 GPU `01`은 숫자로 해석 가능해도 거부한다.

## 관측 freshness와 readiness

| resource snapshot 상태 | create 처리 |
| --- | --- |
| 미제공 | unknown; 명시/exact 자원 계약이 있으면 draft handoff 가능 |
| 제공했으나 wrapper stale | `MANIFEST_REJECTED` |
| fresh지만 target 0개 또는 내부 target stale | `MANIFEST_REJECTED` |
| fresh target status/runtime 불량 | `MANIFEST_REJECTED` |
| fresh availability 부족 | `MANIFEST_REJECTED` |
| fresh `available` + `ok` + required booleans true | readiness contradiction 없음 |

monitoring/log/metrics가 stale이면 본문을 prompt에서 제외하고 `stale_sources`에 기록한다. resource snapshot 부재가 readiness를 증명하지 않으므로 `HANDOFF_READY`는 deployment-ready가 아니라 exact request-body draft-ready다.

## 모델 결합

`candidate_id`는 caller-pinned key다. LLM_Op은 ranking이나 fallback을 하지 않는다. 두 단계는 동일 candidate를 사용하므로 “두 모델의 합의”나 독립적인 이중 보안 권위가 아니다. 결정적 Go guard만 trust anchor다.

offline demo:

- `NewOfflineFixturePlanner`
- provider: `offline-fixture`
- evidence actual model: `fixture-qwen-contract-not-executed`
- intended model metadata: `qwen3.5:4b`
- initial preference weight 1.0은 metadata-only이며 선택 계산에 사용하지 않음
- endpoint, API key, HTTP client, loaded weights 없음
- 다른 provider/model label, endpoint, API-key field가 있는 offline candidate는 completion 전에 거부

live integration:

- 단일-process: `PrepareWithConfig`
- Guard-first 분리: `ReviewWithConfig` → approved geon Flow → trusted in-process `PrepareApproved`
- 기본 `AllowLiveCompletion=false`
- 현재 `actual_model` evidence는 configured label이며 provider attestation이 아님
- live release blocker는 `04-implementation-completeness-audit.md` 참조

## 수동 브라우저 completion

/llm-op-demo는 모델 endpoint를 호출하지 않는다. 시연자가 두 단계의 exact prompt를 복사하고 외부 AI가 반환한 raw JSON을 붙여넣는 교육·협업용 경로다.

- 일반 채팅 UI는 system/user role 우선순위를 보장하지 않을 수 있으므로 API 동등 실행으로 주장하지 않는다.
- Safeguard와 Proposal은 별도 completion이다. Safeguard reason/output을 Proposal prompt에 넣지 않는다.
- 외부 AI provider·model identity, finish reason, tool call은 attestation되지 않는다.
- raw output을 사람이 고쳐 다음 단계로 승격하지 않는다.
- 브라우저 parser·semantic 검사는 demo contract mirror이며 authoritative Go Guard를 대체하지 않는다.
- 저장 fixture 사용 시 외부 AI 호출 0회다. 직접 외부 AI에 입력하면 해당 서비스의 과금·보존 정책이 적용될 수 있다.

전체 절차와 상태 전이는 07-manual-two-stage-browser-demo.md를 따른다.

## 실패 상태

| 상태 | 의미 |
| --- | --- |
| `SAFEGUARD_APPROVED` | 최초 Request Guard와 Safeguard가 downstream planning만 허용; Manifest·POST 권한 없음 |
| `REQUEST_REJECTED` | deterministic input/policy/scope 실패 또는 LLM reject action |
| `CLARIFICATION_REQUIRED` | LLM이 free-text 명확화 요청; Manifest와 handoff 없음 |
| `MODEL_UNAVAILABLE` | candidate/client/completion/review 계약 실패 |
| `MANIFEST_REJECTED` | Proposal parser·contract·semantic·AppDeploy Guard 실패 |
| `CONFIGURATION_ERROR` | policy/candidate config 또는 live-disabled 경계 |
| `HANDOFF_READY` | POST하지 않은 exact AppDeploy body draft가 준비됨 |

semantic internal code는 현재 wire Result에 직접 노출되지 않는다. scenario catalog는 `decision_reason_code`, `semantic_guard_code`, `bridge_error_contains`의 의미를 섞지 않는다.

현재 clarification은 `missing_fields`, canonical question, resume token이 없는 free-text다. 자동 재시도·resume·routing을 붙이지 않으며 구조화 왕복 계약이 생기기 전에는 사람에게 표시하는 종료 상태로만 다룬다.

## release 검토 체크리스트

- [ ] prompt·policy version과 digest가 evidence에 남는가
- [ ] 인증 principal이 caller-controlled `requested_by`를 덮어쓰는가
- [ ] registry가 Common JSON app identity와 AppDeploy app version을 결합하는가
- [ ] candidate/deployment/Target hint를 server-side binding이 덮어쓰는가
- [ ] structured constraints와 telemetry가 trusted builder에서만 오는가
- [ ] target hint에 trusted snapshot 또는 별도 readiness-unknown 상태가 있는가
- [ ] rejected status를 legacy planner fallback으로 성공 변환하지 않는가
- [ ] 최초 Safeguard가 모든 live analyzer·Manifest LLM보다 먼저 실행되는가
- [ ] `PrepareApproved`가 continuation을 재검산하고 Safeguard를 반복 호출하지 않는가
- [ ] geon `INITIAL` revision과 LLM_Op AppDeploy body를 캐스팅·병렬 제출하지 않는가
- [ ] only `HANDOFF_READY`가 다음 단계로 가며 여전히 POST하지 않는가
- [ ] live egress/secret/model response/cost gate가 모두 구현됐는가
- [ ] trusted clock과 evaluated-at/prompt/config/policy digest가 evidence에 남는가
- [ ] tool/function call, finish reason, provider-reported model을 검증하는가
- [ ] structured clarification과 machine-readable schema가 있는가
- [ ] AppDeploy가 동일 Manifest를 다시 검증하는가
- [ ] runtime test와 adversarial scenario 결과가 증적으로 저장됐는가
