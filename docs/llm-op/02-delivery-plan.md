# LLM_Op 전달 계획과 완료 기준

## 현재 milestone

현재 milestone은 서버 없이 시연 가능한 prepare-only 경로다.

~~~text
Request
  -> deterministic preflight
  -> offline Safeguard review fixture
  -> offline Proposal fixture
  -> deterministic semantic/AppDeploy Guard
  -> HANDOFF_READY + exact DeploymentCreateRequest
  -> no POST
~~~

model selection, Qwen server 운영, Target/Runtime 선택, 실제 배포는 milestone 밖이다.

## 구현 완료 항목

| ID | 산출물 | 완료 기준 | 상태 |
| --- | --- | --- | --- |
| M1 | Request/Result model | 상태·로그·monitoring·metrics wrapper와 stable status | 코드 작성됨 |
| M2 | Normalizer | freshness, timestamp, redaction, collection·text ceiling | 코드 작성됨 |
| M3 | Request Guard | prepare-only, scope, secret, injection, text hygiene, unsupported requirement | 코드 작성됨 |
| M4 | Safeguard LLM harness | allow/clarify/reject strict JSON; allow만 다음 단계 | 코드 작성됨 |
| M5 | Proposal LLM harness | bounded action/reason/resources strict JSON | 코드 작성됨 |
| M6 | Semantic Guard | exact natural language/Profile/recommendation, readiness contradiction | 코드 작성됨 |
| M7 | AppDeploy mapper | non-LLM identity field + exact `DeploymentCreateRequest` | 코드 작성됨; route trust binding은 미구현 |
| M8 | offline factory | endpoint/API key/HTTP client 없음, provider/model evidence 고정 | 코드 작성됨 |
| M9 | Common JSON bridge | supported single-node subset lossless projection | 코드 작성됨 |
| M10 | demo data | 4 service metadata, caller-pinned Qwen intent, 47 scenarios | 정적 catalog 작성됨 |
| M11 | 수동 브라우저 lab | 별도 /llm-op-demo, 대표 8개 흐름, prompt copy, raw JSON gate, not-submitted handoff | 코드·문서·Node contract test 작성됨 |

“코드 작성됨”은 Go test 통과와 동일하지 않다. 현재 보안 규칙상 Go toolchain을 실행하지 않았다. M11의 순수 JavaScript contract test는 Node로 실행했으며 Go server route의 compile·serve 성공을 대신하지 않는다.

## 이번 감사에서 강화한 acceptance criteria

1. public API로 임의 completion client를 넣어 live gate를 우회할 수 없어야 한다.
2. caller가 검증 뒤 request map/pointer를 바꿔도 결과가 달라지지 않아야 한다.
3. non-empty parameters는 downstream allowlist 전까지 모두 거부해야 한다.
4. user뿐 아니라 log/alarm/status/resource/metric string의 prompt-control을 completion 전에 검사해야 한다.
5. input/output bidi, zero-width, control character와 model output duplicate/case-variant key를 거부해야 한다.
6. Profile-only 요청은 deterministic derived minimum보다 크게 부풀릴 수 없어야 한다.
7. supplied-stale resource snapshot, anonymous/invalid monitoring target, duplicate target ID, non-available/down runtime, cross-source textual contradiction은 normalization 또는 create를 거부해야 한다.
8. fixed request/normalizer/raw envelope ceiling은 caller config로 완화할 수 없어야 한다.
9. 알려진 hardware/SKU/CUDA, SLO/cost/topology/device-memory/OS/port/network 요구와 conditional resource 표현을 조용히 유실하면 안 된다.
10. 성공 Result에는 상충 가능한 model reason/assumptions를 복사하지 않고 결정적 설명을 사용해야 한다.
11. offline fixture가 실제 provider/model 실행 label을 가장할 수 없어야 한다.
12. invalid Result ID와 Common JSON evidence ID를 그대로 echo하지 않아야 한다.
13. live public entrypoint의 freshness clock은 caller가 바꿀 수 없어야 한다.
14. 실패 결과에는 Manifest, next endpoint, prepared request가 없어야 한다.

## demo acceptance scenarios

| 범주 | 대표 입력 | 기대 |
| --- | --- | --- |
| fresh GPU success | exact 4 CPU/16 GiB/1 GPU/20 GiB + available/ok snapshot | HANDOFF_READY |
| explicit CPU, snapshot absent | exact 4/8 GiB/0/100 GiB | HANDOFF_READY, readiness unknown |
| ambiguous | “적당한 사양” | Safeguard clarification, Proposal 0회 |
| secret | Bearer token in user text | Request Guard, completion 0회 |
| indirect injection | log/status key에 “ignore previous…” | Request Guard, completion 0회 |
| stale resource | supplied snapshot >10분 | MANIFEST_REJECTED |
| stale non-resource | stale monitoring/log/metric, snapshot absent | stale context 제외 후 exact request로 draft 가능 |
| readiness conflict | boolean false, status/runtime down, anonymous/duplicate target | REQUEST_REJECTED 또는 MANIFEST_REJECTED |
| exact mismatch | recommendation 4 CPU, Proposal 8 CPU | MANIFEST_REJECTED |
| Profile inflation | minima보다 큰 LLM 값, exact recommendation 없음 | MANIFEST_REJECTED |
| unsupported direct requirement | NPU/H100/CUDA, replica/multi-node, p95 SLO, GPU VRAM, Ubuntu/port | REQUEST_REJECTED |
| bridge loss | SLO/cost/multi-replica/device memory | bridge error, LLM_Op Result 없음 |
| parser ambiguity | duplicate/case-variant/deep JSON | MODEL_UNAVAILABLE 또는 MANIFEST_REJECTED |

전체 row는 `examples/llm-op/user-input-scenarios.json`에 있다. 현재 row별 runtime 실행 증적이 아니므로 `static_contract_catalog_not_executed`를 유지한다.

## 통합 단계의 불변식

geon 또는 다른 caller는 다음을 지켜야 한다.

1. `requested_by`를 인증 principal에서 설정하고 body 값을 신뢰하지 않는다.
2. registry에서 Common JSON `app_id/app_version`과 AppDeploy `app_version_id`를 동일 등록 항목으로 결합한다.
3. `candidate_id`, `deployment_id`, `target_profile_id`는 server-side trusted binding으로 덮어쓴다.
4. PlanningConstraints는 trusted ApplicationProfile/ResourceRecommendation builder만 작성한다.
5. telemetry source를 인증하고 replay/freshness policy를 적용한다.
6. target hint가 있으면 trusted snapshot을 요구하거나 readiness-unknown을 별도 상태로 표현한다.
7. LLM_Op reject/clarify/error를 legacy full-Manifest planner fallback으로 성공 변환하지 않는다.
8. 내부 Proposal-only `Planner`를 직접 호출하지 않는다.
9. live는 system clock을 소유한 `PrepareWithConfig`로만 들어오고, offline은 고정 evidence의 `NewOfflineFixturePlanner`로만 들어온다.
10. only `HANDOFF_READY`가 다음 단계로 갈 수 있지만, 이 상태만으로 POST하지 않는다.
11. AppDeploy 제출 전 동일 Manifest를 다시 검증하고 승인·idempotency를 확인한다.

## 다음 milestone: route 통합 전

아래가 합의되기 전 공개 route를 추가하지 않는다.

- 인증 principal → `requested_by` overwrite
- registry의 Common JSON app identity ↔ AppDeploy app version binding
- candidate/deployment/Target hint의 server-side binding
- Common JSON/telemetry trusted producer와 replay protection
- trusted evaluation time과 effective `evaluated_at` evidence
- Projection evidence를 final Result/audit record와 digest로 결합
- stable error code와 clarification `missing_fields`/question/resume 계약
- status mapping 소유자와 retryability
- AppDeploy 승인 verifier와 idempotency key
- exact supported fields를 가진 parameters 대체 schema
- Request/Result/Safeguard/Proposal machine-readable JSON Schema 또는 OpenAPI component

## 다음 milestone: live Qwen 전

아래가 모두 완료돼야 `AllowLiveCompletion=true`를 검토한다.

- HTTPS/host allowlist, redirect·private network policy
- 전용 secret resolver; candidate가 임의 environment variable을 선택하지 못함
- timeout/output-token/rate/cost ceiling
- provider-reported model과 configured model 분리
- `finish_reason=stop`, tool/function call 부재 확인
- candidate/policy strict JSON, duplicate ID, trailing value, 크기 검증
- prompt/config/policy digest와 revision evidence
- 외부 provider로 보낼 telemetry의 data-governance 승인

## 검증 순서

Go 실행 승인을 받기 전:

1. JSON parse와 demo data validator
2. Markdown link와 drift 검사
3. `git diff --check`, conflict/TODO scan
4. source/test 정적 대조

Go 실행 승인을 받은 뒤:

1. `gofmt` diff 검토
2. targeted `go test ./internal/llmop ./internal/llmopbridge ./cmd/llmop-demo`
3. full module test/vet
4. offline demo 결과와 golden 비교
5. 47-row table-driven harness 실행 증적 생성

live Qwen과 AppDeploy POST는 별도 승인·환경·release gate 없이는 실행하지 않는다.
