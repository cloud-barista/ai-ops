# Draft PR 본문 초안: LLM_Op → geon

@gunsun2000 안녕하세요. 이 Draft PR은 geon을 대체하거나 바로 병합하기 위한 PR이 아니라, 별도 LLM_Op 작업의 존재와 경계를 공유하고 API 결합점·버그를 지속적으로 협의하기 위한 채널입니다.

## 게시·검증 상태

- 이 본문은 base=`geon`, head=`LLM_Op` Draft PR의 협업 메시지로 사용합니다. geon 브랜치에 직접 push하거나 별도 cherry-pick을 요구하지 않습니다.
- 요구사항정의서상 SFR-OPS-04~06은 2027 항목이며, 이 PR은 해당 흐름을 앞서 구체화하는 2026 선행 PoC·부분 구현입니다. 공식 요구사항 완료를 주장하지 않습니다.
- 현재 증적은 fixture JSON 구문 확인과 소스·계약의 정적 검토입니다. 작업 환경 보안 정책에 따라 Go 테스트와 demo command는 아직 실행하지 않았습니다.
- 따라서 아래 HANDOFF_READY는 구문·알려진 책임 경계·자원 상한과 명시 자원값/Profile 최소값/선택 Resource exact 값, 제공된 fresh Target ID/status/runtime/availability의 결정적 모순 검사를 통과한 prepare-only 초안이라는 구현 의도를 뜻합니다. 일반 자연어 의미의 정답, 관측 provenance, 실제 capacity, Qwen·AppDeployer 종단간 성공 증적을 뜻하지 않습니다.

## LLM_Op의 범위

- 사용자 자연어 요청 + AppDeployer 상태·로그·bounded metric 정규화
- 잠정 10분 freshness·1분 timestamp skew, 입력 log 500개·보존 log 50개, Qwen user message 128 KiB, collection·문자열 제한, Bearer/API key/PEM·신뢰 ID redaction
- parameters JSON은 pre-snapshot 64 KiB·깊이 16·node 1,000·key 128 rune을 먼저 검사하지만, v1alpha1에서는 non-empty이면 전부 거부하고 Manifest에 복사하지 않음
- 상위 계층이 지정한 Candidate 설정을 결합하는 OpenAI 호환 Qwen 연결 코드. 모델 비교·선택 로직은 없고 `AllowLiveCompletion=false`가 기본이며 실제 endpoint·과금 API는 호출하지 않음
- 같은 caller-bound candidate와 공통 정규화 context를 사용하되 review/Proposal별 `required_output` 계약을 분리한 두 단계. fixture demo는 두 고정 JSON만 사용
- Completion content 64 KiB 제한과 status·candidate/provider/model의 adapter/config 일관성 검사(provider attestation은 아님)
- Safeguard LLM에는 allow/clarify/reject만, Proposal LLM에는 제한된 자원 Proposal만 허용
- 구조화 제약이 없으면 지원하는 affirmative exact 자연어 CPU·GPU·memory·storage 문법, 있으면 자연어 양수 값/Profile 하한·명시적 `GPU 0` exact no-GPU·선택 Resource candidate exact 값을 Proposal과 대조하고, CPU·memory·storage 0, 모호·범위·상충·conditional 값과 fresh readiness 모순을 fail-closed
- app_version_id/requested_by/optional target hint는 LLM이 아닌 Go mapper가 적용하지만, 현재 Request type은 인증·registry binding을 증명하지 않음
- create confidence 0.5 이상과 CPU 256·GPU 16·memory 2Ti·storage 64Ti 상한 및 AppDeploy Go Manifest Guard 통과 후 HANDOFF_READY 반환
- `evidence.input.*_included`는 정제된 관측 본문의 prompt 포함 여부이며 실제 추론 사용·feasibility 증명이 아님
- demo completion은 실제 Qwen 호출이 아닌 고정 JSON fixture
- offline provider/model evidence는 `offline-fixture` / `fixture-qwen-contract-not-executed`로 코드에서 고정되고 endpoint/API-key field는 거부
- 현재 검증 경로의 Qwen·AppDeployer 네트워크 호출 수와 API 비용은 0
- 초기에는 `prepare_only`이며 AppDeployer POST를 호출하지 않음
- HANDOFF_READY의 `next_endpoint`는 정보성 상대 경로이며 호출·승인·도달 가능성을 뜻하지 않음
- `handoff.prepared_request`와 golden fixture는 AppDeploy `DeploymentCreateRequest`의 정확한 body key를 제공하지만 실제 전송하지 않음
- AppDeployer 제출 adapter와 승인 reference 검증 adapter는 아직 없음
- Repair는 자동 값 수정·재호출이 아니라 clarify/reject로 fail-closed하며 사용자 왕복·재시도 계약은 후속 범위
- 필수 ID 누락, 비-`prepare_only` mode, 임의 approval reference, parameter 경계 위반은 `REQUEST_REJECTED`; Safeguard review의 `reject_request`와 Proposal의 `reject_unsafe_request`도 같은 상태 사용
- offline 공식 종단 진입점은 `llmop.NewOfflineFixturePlanner(...).Prepare`, 단일-process live 진입점은 system clock을 소유한 `llmop.PrepareWithConfig`; Guard-first 연결은 offline `ReviewRequest/PrepareApproved`, live Go integration `ReviewWithConfig/PrepareApprovedWithConfig` 사이에 `ProjectApprovedInitialFlow`를 둠. resume 함수는 외부 HTTP body가 아닌 trusted in-process 경계이며, 내부 `llmop.Planner` 직접 호출은 review를 우회하므로 통합 경로에서 사용 금지
- `REQUEST_REJECTED`, `CLARIFICATION_REQUIRED`, `MODEL_UNAVAILABLE`, `MANIFEST_REJECTED`, `CONFIGURATION_ERROR`를 기존 full-Manifest planner fallback으로 성공 변환하지 않으며, only `HANDOFF_READY`가 다음 단계로 갈 수 있어도 POST는 별도 승인 전 0회

## geon과 겹치지 않도록 한 부분

- 새 코드는 internal/llmop, internal/llmopbridge, cmd/llmop-demo, examples/llm-op에 격리했습니다.
- 기존 deploymentplanner.GenerateInput, ControlRun, Agent Registry, API route, OpenAPI는 아직 수정하지 않았습니다.
- 기존 Agent Control의 ApplicationProfile → ResourceRecommendation → Common JSON envelope는 수정하지 않았습니다. 별도 bridge는 correlation/trace/causation과 Profile/Recommendation join을 확인해 Profile minima와 선택된 single-node Resource candidate exact 값을 분리 투영하며 ModelRecommendation·artifact·resource hint는 제외합니다. 실제 LocalRequirementAnalyzer 기본 CPU Profile보다 큰 catalog 후보도 exact 값으로 보존하고, device-memory minimum이 있는 자연어 GPU 경로는 AppDeploy v1에서 강제할 수 없어 fail-closed합니다. 공식 route 결합 위치는 이 PR에서 합의해야 합니다.
- bridge는 Common JSON `app_id/app_version`과 AppDeploy `app_version_id`의 registry 관계를 증명하지 못합니다. 공개 route는 인증 principal·registry와 server-side candidate/deployment/Target binding을 소유해야 하며 A의 분석을 B의 AppVersion/Target hint와 결합하지 못하게 해야 합니다.
- 공식 HTTP 결합점은 geon 방향을 확인한 뒤 별도 작은 커밋으로 제안하겠습니다.

먼저 확인할 문서:

- docs/coordination/to-geon-llm-op-handoff.md
- docs/coordination/geon-bug-notes.md
- docs/llm-op/01-contract.md
- docs/llm-op/03-common-json-bridge.md
- docs/llm-op/07-manual-two-stage-browser-demo.md
- docs/llm-op/08-operation-context-adapter-contract.md
- examples/llm-op/README.md

## 별도 브라우저 시연 표면

- 기존 geon index.html/app.js의 고정 3-view는 바꾸지 않았습니다.
- 새 /llm-op-demo route는 전용 llm_op_demo HTML/CSS/JS에 격리되어 있습니다.
- 대표 8개 흐름에서 prompt를 복사하고 raw Safeguard/Proposal JSON을 붙여넣을 수 있으며, 저장 fixture로는 외부 AI 호출 없이 끝까지 재생됩니다.
- 페이지의 모델 API 호출과 AppDeploy POST는 0회입니다. 사용자가 외부 AI 서비스에 직접 prompt를 넣는 비용·보존은 페이지 밖 책임입니다.
- embedding과 VLM 성공 materialized fixture를 추가해 네 workload 유형 모두 두 번째 LLM 출력까지 시연할 수 있습니다.
- 브라우저 validator는 발표용 mirror이며 Go Guard를 대체하지 않습니다. prompt 상수 drift는 Node contract test가 검사합니다.
- webui.go의 embed/asset route를 수정할 때 /llm-op-demo와 llm_op_demo 전용 asset을 보존하거나 이 PR에서 알려 주십시오.

## 확인을 요청하는 사항

1. 공식 결합점을 기존 ControlRun으로 둘지 별도 prepare-only Planner endpoint로 둘지
2. AppDeployer 관측값을 geon이 수집해 전달할지, LLM_Op 요청자가 제공할지
3. 승인 검증·idempotency 계약이 생기기 전 prepare_only 경계를 유지하는 데 동의하는지
4. geon에서 변경 예정인 충돌 고위험 파일이나 계약이 있는지
5. 10분 age·1분 skew·50개 retained log 및 자원 상한을 provisional defaults로 유지할지 실제 운영 주기에 맞춰 조정할지
6. 모델 선택은 상위 계층 책임으로 유지하고 LLM_Op에는 명시적 candidate 결합만 둘지
7. 현재 bridge가 fail-closed하는 SLO·cost·GPU device memory·multi-replica minimum을 어느 계약에서 표현할지
8. LocalRequirementAnalyzer가 자연어 GPU 요청에 넣는 device-memory minimum을 AppDeploy v1이 표현하지 못하는 현재 계약 공백을 어떻게 해소할지
9. Common JSON app identity ↔ AppDeploy app version registry binding과 server-side Target/candidate/deployment binding을 어느 계층이 소유할지

## 정적 검토 중 공유할 버그

- AppDeployer COMPLETED를 geon polling이 성공 terminal로 처리하지 않는 문제
- 한글 요청 길이가 Request Guard에서는 rune, Generator에서는 byte로 계산되는 불일치
- DeploymentManifest JSON Schema가 OpenAPI/Go 모델보다 느슨하고 requirements가 누락된 drift
- X-Request-ID 전달과 idempotency가 분리되어 있지 않아 POST 재시도 시 중복 Deployment 위험이 있는 계약 공백
- AppDeployer와 geon에 approval reference를 검증할 verifier 계약이 없는 공백
- 기존 full-Manifest Qwen Generator와 새 bounded-Proposal LLM_Op을 동시에 공개할 경우 Guard·상태 계약이 갈라지는 위험

위 항목은 실행 재현 결과가 아니라 정적 검토 발견이며 상세 근거는 `docs/coordination/geon-bug-notes.md`에 있습니다. 버그를 서로 발견하거나 재현 결과가 달라지면 이 Draft PR에서 근거와 영향을 교환하겠습니다.
