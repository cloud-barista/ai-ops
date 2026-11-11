# Offline demo와 사용자 입력 시나리오

## 목적

demo는 네트워크·모델 weight·API key·AppDeploy POST 없이 동일한 normalization, 두 단계 parser, deterministic guard, mapper를 보여준다. AI 서비스 metadata와 Planner 모델 binding은 `examples/llm-op/ai-service-catalog.json`, 입력 예시는 `examples/llm-op/user-input-scenarios.json`에 있다.

## 모델 이름 공간 분리

두 종류의 모델을 혼동하지 않는다.

| 종류 | 역할 | 현재 demo |
| --- | --- | --- |
| Planner model | 자연어 Safeguard와 resource Proposal | caller-pinned `qwen3.5-ops-planner`; intended model `qwen3.5:4b` |
| Workload model | AppVersion이 나중에 서비스할 AI model | 각 `ai_services[].workload_model_ref` |

demo completion evidence의 `actual_model`은 `fixture-qwen-contract-not-executed`다. intended model은 목표 호환 label일 뿐 실행 증적이 아니다. `initial_preference_weight=1.0`도 metadata-only이고 ranking·selection에 쓰이지 않는다.

## AI 서비스 예시

| service_id | task | app_version_id | 기본 자원 metadata |
| --- | --- | --- | --- |
| `svc-llm-inference-demo-v1` | 한국어 chat completion | `appver-llm-inference-v1` | 4 CPU, 16 GiB, GPU 1, 20 GiB |
| `svc-ko-intent-demo-v1` | 한국어 의도 분류 | `appver-ko-intent-v1` | 4 CPU, 8 GiB, GPU 0, 100 GiB |
| `svc-ko-embedding-demo-v1` | 문서 embedding | `appver-ko-embedding-v1` | 8 CPU, 16 GiB, GPU 0, 100 GiB |
| `svc-doc-vlm-demo-v1` | 문서 이미지 이해 | `appver-doc-vlm-v1` | 8 CPU, 32 GiB, GPU 1, 100 GiB |

이 자원값은 service catalog 표시·정합성 metadata다. CLI는 service ID와 request `app_version_id` 일치, offline invariant, 자원 형식을 검증하지만 이 metadata를 LLM 입력이나 trusted planning constraint로 자동 승격하지 않는다. 실제 planning constraint는 request 또는 Common JSON bridge가 별도로 제공해야 한다.

## scenario 범위

47건은 다음을 포함한다.

- fresh GPU/CPU 성공과 stale non-resource context 제외
- 모호 요청 clarification
- secret, prompt injection(user/log/status), zero-width·bidi
- Target/Runtime/provider/command 책임 경계
- non-empty parameters 거부
- resource 중복·근사·0·부정·대조
- conditional/preference resource, H100/CUDA/CPU SKU, multi-node, OS/port 미표현 요구
- GPU profile 충돌, fresh availability/status/runtime 모순, supplied-stale snapshot, duplicate Target ID
- monitoring runtime의 빈 Target ID와 bridge의 unsafe/하위 계약 밖 ID
- exact recommendation mismatch, Profile-only inflation, global ceiling
- Common JSON success와 SLO/cost/replica/device-memory fail-closed
- duplicate JSON key, unsafe/unsupported model reason

각 row의 `expected`는 wire status와 내부 감사 관점을 분리한다.

- `guard_check`: Request Guard check 이름
- `semantic_guard_code`: 현재 wire에 노출되지 않는 정적 감사용 internal code
- `bridge_error_contains`: LLM_Op Result 생성 전 bridge error 일부
- `safeguard_calls`, `proposal_calls`: 기대 호출 횟수

scenario catalog는 전체 runtime 실행 결과가 아니다. `validation_status=static_contract_catalog_not_executed`가 이를 명시한다.

## 브라우저에서 실행하는 대표 흐름

/llm-op-demo는 47개 catalog 중 발표 가치가 큰 6개 row를 실제 bounded context와 raw output에 연결한다. catalog에 성공 golden이 없던 embedding과 VLM은 브라우저 전용 materialized success 2개를 추가하여 네 workload 유형 모두 두 번째 LLM 출력까지 시연할 수 있다.

브라우저의 총 8개 흐름:

- chat fresh GPU 성공
- intent CPU 성공과 readiness unknown
- embedding CPU 성공 확장
- VLM GPU 성공 확장
- 모호 요청 clarification
- Target 직접 선택 사전 거부
- 로그 속 prompt injection 사전 거부
- 중복 Proposal JSON key 후단 거부

이 두 success 확장은 user-input-scenarios.json의 47개 정적 catalog 수를 바꾸지 않는다. page의 materialized source와 validator는 go/service-control-api/internal/webui/static/llm_op_demo_contract.js에 있고, 실행 절차는 07-manual-two-stage-browser-demo.md에 있다.

## 무서버 성공 fixture

기존 `fresh-latency-*` 네 파일은 한 개의 end-to-end golden을 구성한다.

~~~powershell
go run ./cmd/llmop-demo `
  -request ../../examples/llm-op/fresh-latency-request.json `
  -safeguard-output ../../examples/llm-op/fresh-latency-safeguard-review.json `
  -model-output ../../examples/llm-op/fresh-latency-qwen-proposal.json `
  -policy ../../config/planner_guard_policy.json `
  -catalog ../../examples/llm-op/ai-service-catalog.json `
  -service svc-llm-inference-demo-v1 `
  -now 2026-08-05T14:05:00+09:00
~~~

이 명령은 보안 규칙상 이번 감사에서 실행하지 않았다. 사용자가 Go 실행을 별도로 승인할 때 사용할 명령이다. CLI는 `NewOfflineFixturePlanner`를 사용하고 endpoint/API key/HTTP client 인자를 제공하지 않는다.

기대 핵심:

- `HANDOFF_READY`
- Safeguard allow 1회, Proposal 1회
- log의 demo secret 1개 redaction
- fresh resource snapshot aggregate 포함
- target 선택 없음
- `submission_mode=not_submitted`
- `prepared_request`가 golden AppDeploy body와 동일
- 성공 `decision.reason_code=BOUNDED_MANIFEST_PREPARED`; raw model reason/assumptions는 public 성공 설명에 없음

## 정적 데이터 검증

Go를 실행하지 않고 다음을 수행할 수 있다.

~~~powershell
& .\scripts\validate-llmop-demo-data.ps1
~~~

validator는 다음을 확인한다.

- network, weights, model selection, submit가 모두 false
- 정확히 하나의 caller-pinned Planner binding
- fixture evidence model과 intended Qwen model 분리
- initial weight가 metadata-only
- 4개 service ID/app version uniqueness와 자원 형식
- 24건 이상 scenario, unique ID, service/model cross-reference
- stage/status/call-count invariant

## runtime scenario harness로 확장할 때

각 scenario를 실제 증적으로 승격하려면 row만 읽고 synthetic status를 반환하면 안 된다. 단계별 fixture client와 실제 `Prepare`/bridge 호출을 구성해 다음을 assert해야 한다.

1. 조기 거부 시 completion 0회
2. clarify/reject 뒤 Proposal 0회
3. 실패 결과에 Manifest, endpoint, prepared request 없음
4. 성공 body가 exact golden과 일치
5. internal semantic code와 wire reason code를 혼합하지 않음
6. 네트워크 transport 자체가 구성되지 않음
