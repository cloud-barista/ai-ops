# LLM_Op offline demo data

## 안전 경계

이 디렉터리의 demo는 실제 Qwen, model weight, API key, endpoint, AppDeploy를 사용하지 않는다. `NewOfflineFixturePlanner`가 pre-recorded Safeguard review JSON과 Proposal JSON만 반환하며 HTTP client를 만들 수 없다.

`ai-service-catalog.json`은 다음을 명시한다.

- network, weights, model selection, AppDeploy submit: false
- caller-pinned candidate: `qwen3.5-ops-planner`
- intended model metadata: `qwen3.5:4b`
- completion evidence model: `fixture-qwen-contract-not-executed`
- initial preference weight 1.0: metadata-only, 선택 계산 없음
- 4개 AI workload service의 ID, app version, task, I/O, 표시용 기본 자원

service 기본 자원은 자동 planning input이 아니다. CLI는 service/app version 정합성과 형식만 확인한다. trusted planning constraints는 Request 또는 Common JSON bridge가 제공해야 한다.

## golden files

- `fresh-latency-request.json`: natural language + fresh resource/monitoring/log/metric input
- `fresh-latency-safeguard-review.json`: bounded allow review
- `fresh-latency-qwen-proposal.json`: bounded create Proposal
- `fresh-latency-appdeploy-request.expected.json`: expected exact AppDeploy body

예상 trace:

~~~text
Request Guard approved
  -> fresh observations normalized
  -> one log secret redacted
  -> offline Safeguard fixture allowed
  -> offline Proposal fixture parsed
  -> semantic/AppDeploy Guard approved
  -> HANDOFF_READY
  -> AppDeploy calls: 0
~~~

## CLI 입력

Go 실행은 이번 작업에서 수행하지 않았다. 사용자가 Go toolchain 실행을 별도로 승인할 경우 module 디렉터리 `go/service-control-api`에서 사용할 명령:

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

기대 핵심:

- `status=HANDOFF_READY`
- `safeguard.request.valid=true`
- `safeguard.manifest.valid=true`
- `evidence.safeguard_review.decision=allow_request`
- `evidence.input.resource_snapshot_included=true`
- `evidence.input.redacted_values=1`
- `manifest.spec.target_profile_id` 없음
- `handoff.submission_mode=not_submitted`
- `handoff.prepared_request`가 golden과 동일
- `decision.reason_code=BOUNDED_MANIFEST_PREPARED`; fixture model의 create reason/assumptions는 public 성공 설명에 복사되지 않음

`actual_model=fixture-qwen-contract-not-executed`이어야 한다. intended Qwen label을 실제 실행 evidence로 출력하면 안 된다.

## 사용자 입력 catalog

`user-input-scenarios.json`에는 success, clarification, security/scope/grammar/normalization/semantic/bridge/model-output rejection을 포함한 47개 사례가 있다.

이 파일은 runtime result가 아니라 contract catalog다. `validation_status=static_contract_catalog_not_executed`를 제거하려면 각 row가 실제 offline pipeline/bridge를 호출하는 table-driven test와 저장된 결과를 먼저 만들어야 한다.

## 정적 검증

정상 PowerShell policy에서 다음을 실행한다.

~~~powershell
& .\scripts\validate-llmop-demo-data.ps1
~~~

현재 컴퓨터의 PowerShell 정책이 script 실행을 차단하면 정책을 임의로 완화하지 말고 JSON parse·교차참조를 read-only 명령으로 확인하거나 관리자 정책을 따른다.

## 수동 브라우저 시연

service-control-api의 embedded UI를 실행한 연구 환경에서는 /llm-op-demo에서 8개 대표 흐름을 실행할 수 있다.

- "예시 응답 넣기": 외부 AI 호출 없이 저장 JSON 재생
- "전체 복사": system/user 합성 prompt를 시연자의 AI 서비스로 이동
- raw JSON 붙여넣기: Safeguard allow일 때만 Proposal 단계 개방
- 최종 화면: 두 번째 LLM 출력과 not_submitted AppDeploy body 초안 표시

페이지는 모델 API와 AppDeploy를 호출하지 않는다. 시연자가 별도 AI 서비스에 prompt를 넣으면 그 서비스의 네트워크·과금·보존 정책은 별도로 적용된다.

상세 실행서는 ../../docs/llm-op/07-manual-two-stage-browser-demo.md, 상태 Adapter 입력 계약은 ../../docs/llm-op/08-operation-context-adapter-contract.md를 참조한다.
