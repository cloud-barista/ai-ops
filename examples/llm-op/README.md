# LLM_Op 무서버 시연

이 시연은 실제 Qwen이나 AppDeployer를 호출하지 않는다. 두 `fixtureCompletionClient`가 각각 고정 자연어 Safeguard review와 고정 Manifest Proposal을 completion 결과로 반환하고, 이를 동일한 정규화·Safeguard·Manifest mapping 코드에 넣도록 작성되어 있다. 두 단계는 같은 정규화 context를 쓰지만 review/Proposal별 `required_output` 계약은 분리된다. demo CLI에는 endpoint·API key·HTTP client 인자가 없으므로 이 경로의 네트워크 호출과 API 비용은 0이다. AppDeployer 제출 adapter와 승인 검증 adapter도 현재 구현에 없다.

아래는 아직 실행 증적이 아닌 예상 trace다.

~~~text
Request Guard approved
  -> observations fresh
  -> 1 sensitive log value redacted
  -> bounded Qwen safeguard review allows request
  -> separate bounded Qwen proposal parsed
  -> AppDeploy Go Manifest Guard approved
  -> HANDOFF_READY
  -> AppDeploy calls: 0
~~~

fixture JSON의 구문은 PowerShell로 확인했지만, 보안 프로그램 정책상 Go 테스트와 demo는 아직 실행하지 않았다. 사용자의 별도 허가를 받은 뒤 `go/service-control-api` 모듈 위치에서 사용할 PowerShell 명령 형태는 다음과 같다.

~~~powershell
go run ./cmd/llmop-demo `
  -request ../../examples/llm-op/fresh-latency-request.json `
  -safeguard-output ../../examples/llm-op/fresh-latency-safeguard-review.json `
  -model-output ../../examples/llm-op/fresh-latency-qwen-proposal.json `
  -policy ../../config/planner_guard_policy.json `
  -now 2026-08-05T14:05:00+09:00
~~~

실행 시 기대하는 성공 결과의 핵심 값은 다음과 같다.

- status: HANDOFF_READY
- evidence.safeguard_review.decision: allow_request
- safeguard.request.valid: true
- safeguard.manifest.valid: true
- evidence.input.observation_status: fresh
- evidence.input.resource_snapshot_included: true
- evidence.input.redacted_values: 1
- manifest.spec.app_version_id: appver-llm-inference-v1
- manifest.spec.target_profile_id: 없음
- handoff.submission_mode: not_submitted
- handoff.next_endpoint: /api/v1/deployments
- handoff.prepared_request: `fresh-latency-appdeploy-request.expected.json`과 동일한 AppDeploy POST body

Qwen prompt와 제안에는 app_version_id, deployment_id, Target ID가 없으며 Runtime, credential 또는 명령 필드도 없다. 자원 상태는 Target 식별자가 제거된 가용 개수로 요약하고, 신뢰 ID와 AppDeployer Manifest 형식은 Go mapper가 채운다.

stale 관측 fixture를 추가할 때는 관측 본문이 Qwen prompt에서 제외되고 결과 evidence의 `stale_sources`에 해당 종류가 기록되는지를 확인한다. stale 자체가 `CLARIFICATION_REQUIRED`를 보장하지는 않는다.

`fresh-latency-appdeploy-request.expected.json`은 전송 결과가 아니라 후속 파트에 넘길 정확한 key shape의 golden이다. Agent Control Common JSON envelope와는 별도 계약이며, 공식 결합점이 정해지기 전에는 서로를 같은 payload로 취급하지 않는다.
