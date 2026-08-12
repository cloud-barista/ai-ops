# LLM_Op: 자연어 운영 요청에서 AppDeploy 초안까지

## 목표

LLM_Op은 사용자 자연어와 선택적 상태·로그·모니터링을 정규화하고, 자연어 Safeguard review와 별도 resource Proposal을 거쳐 AppDeploy가 받을 exact `DeploymentCreateRequest` 초안을 만든다.

현재 목표는 모델 선택이 아니다. caller가 `candidate_id`로 이미 지정한 Planner model binding을 사용한다. 실제 Qwen/API 호출과 AppDeploy 제출은 하지 않는다.

## 현재 구현 흐름

~~~text
bounded Request snapshot
  -> deterministic Request Guard
  -> natural-language Safeguard LLM (allow/clarify/reject)
  -> bounded Manifest Proposal LLM
  -> deterministic exact-resource/readiness Guard
  -> Go mapper + AppDeploy Manifest Guard
  -> HANDOFF_READY / not_submitted
~~~

통합 시 첫 Safeguard는 geon의 live Requirement Analyzer나 기존 full-Manifest LLM보다 먼저 실행한다. geon이 승인한 `INITIAL` revision 뒤에서는 LLM_Op이 AppDeploy 전용 prepare-only 투영만 담당하며, `OperationOptimizationAgent`와 `OPTIMIZED` revision은 처리하지 않는다. 별도 구현도 이 순서와 상태·action 계약을 기준으로 삼는다. 자세한 역할 기준은 `../coordination/llm-op-guard-first-integration-standard.md`에 있다.

두 LLM 단계는 전체 Manifest나 보안 정책을 작성하지 않는다. 보안 정책과 non-LLM field injection은 Go 코드가 소유한다. 해당 identity 값의 인증·registry binding은 공개 route 전 integration 책임이다.

## 주요 구현

- 8,000-rune user request, raw 32 KiB field/2 MiB envelope, deep request snapshot
- fixed 10-minute freshness, 1-minute skew, collection/text ceilings
- log secret redaction, user secret reject
- user·observation prompt-control과 zero-width/bidi/control hygiene
- Target/Runtime/provider/endpoint/command 및 알려진 unsupported hardware/topology/SLO/deployment requirement fail-closed
- strict single JSON, canonical lowercase/duplicate/depth/node/output size validation
- Profile minima, exact recommendation, direct exact resource intent의 deterministic semantic guard
- supplied-stale/anonymous/duplicate/unavailable/down/cross-source contradictory resource target 거부
- 성공 model narrative를 결정적 public explanation으로 교체
- non-empty untyped parameters 전면 거부
- exact AppDeploy `DeploymentCreateRequest` 준비, POST 없음

## entrypoint

| 경로 | 용도 | 네트워크 |
| --- | --- | --- |
| `NewOfflineFixturePlanner` | 고정 provider/model evidence + pre-recorded review/Proposal JSON demo | 구조적으로 없음 |
| `PrepareWithConfig` | system clock을 소유한 향후 OpenAI-compatible Qwen integration | 기본 `AllowLiveCompletion=false` |
| `ReviewRequest` → approved Flow → `PrepareApproved` | offline Guard-first 분리 검증 | 구조적으로 없음; trusted in-process |
| `ReviewWithConfig` → approved Flow → `PrepareApprovedWithConfig` | live Guard-first Go integration | 기본 disabled; HTTP resume API가 아님 |

임의 completion client를 받는 constructor는 package-private다. Proposal-only 내부 `Planner`는 test 구성 요소이며 integration이 직접 호출하면 안 된다.

수동 브라우저 entrypoint는 /llm-op-demo다. 저장 fixture 또는 사용자가 중계한 raw JSON으로 두 completion을 설명·검증하며, 페이지 자체의 모델 API 호출과 AppDeploy POST는 모두 0이다.

## 모델 binding

offline catalog는 한 개의 caller-pinned candidate를 둔다.

- candidate: `qwen3.5-ops-planner`
- intended model: `qwen3.5:4b`
- evidence actual model: `fixture-qwen-contract-not-executed`
- provider: `offline-fixture`
- initial preference weight: 1.0 metadata-only
- model selection, endpoint, key, weights, benchmark: 없음/미실행

Planner model과 배포할 AI workload model은 별도 이름 공간이다.

## HANDOFF_READY의 의미

`HANDOFF_READY`는 현재 구문·책임·exact 자원·제공된 freshness/readiness 모순 검사를 통과한 prepare-only body가 있다는 뜻이다. 실제 Qwen 실행, telemetry provenance, capacity, scheduler 배치, 승인, 배포 성공을 뜻하지 않는다.

resource snapshot 미제공은 unknown으로 남아 draft handoff가 가능하다. snapshot을 제공했지만 stale이거나 Target status/runtime/availability가 불충분하면 create를 거부한다.

## 문서

- `00-existing-assets-audit.md`: 작업 전 기존 자산 점검. historical/non-normative
- `01-contract.md`: normative request/result/guard/handoff 계약
- `02-delivery-plan.md`: 완료 항목, integration/live gate, 검증 순서
- `03-common-json-bridge.md`: geon Common JSON supported subset
- `04-implementation-completeness-audit.md`: 실제 구현·미지원·잠정값 감사
- `05-safeguard-policy-llm-guide.md`: 권한 분리, 위협 모델, prompt/output 규칙
- `06-demo-scenarios.md`: AI service catalog, 47개 입력 시나리오, offline demo
- 07-manual-two-stage-browser-demo.md: 복사·붙여넣기 방식의 2단계 LLM 브라우저 시연 실행서
- 08-operation-context-adapter-contract.md: 외부 상태·로그 Adapter의 입력 형식과 책임 경계
- `../../examples/llm-op/README.md`: fixture 실행 입력과 expected output
- ../../schemas/llm-op/: 두 LLM raw JSON 출력의 기계 판독 schema

## 검증 상태

PowerShell로 demo JSON과 service/scenario cross-reference를 확인했다. 브라우저의 prompt drift, 8개 materialized scenario, strict JSON과 두 단계 전이는 순수 Node test로 검증한다. 워크스페이스 보안 규칙상 Go toolchain은 실행하지 않았으므로 컴파일·Go test pass를 주장하지 않는다. Qwen endpoint, model weights, API key, AppDeploy POST도 사용하지 않았다.
