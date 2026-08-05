# 전달용: LLM_Op과 geon의 협업 경계

## 전달 목적

이 문서는 LLM_Op 브랜치에서 작성한 geon 작업자용 인수인계 문서다. geon의 기존 Planner·ControlRun 작업을 중단하거나 대체하지 않고, 최신 `통합 다이어그램 (초안)`의 자연어 → ApplicationProfile/상태 분석 → Safe Guard·Repair → Manifest 흐름을 안전하게 연결하기 위한 합의안을 제시한다. 구조도 제안안 B는 두 LLM/Guard 단계의 상세 참고다. 요구사항정의서상 SFR-OPS-04~06은 2027 항목이며, 이 PR은 2026 선행 PoC·부분 구현이다.

LLM_Op은 자연어 요청·상태·로그 입력에서 prepare-only DeploymentManifest 초안을 만드는 흐름을 주도한다. 통합 다이어그램의 LLM 선택기는 현재 범위가 아니며, `candidate_id`는 상위 계층이 이미 지정한 구성을 결합하기 위한 값이다. 실제 Qwen API와 AppDeployer POST는 이번 검증에서 호출하지 않는다.

이 문서의 원격 공유는 base=`geon`, head=`LLM_Op`인 Draft PR만 사용한다. geon 브랜치에 직접 push하거나 별도 cherry-pick을 요구하지 않는다.

Draft PR을 만들 때 geon 최신 커밋 작성자인 @gunsun2000을 멘션하고, 해당 PR 하나를 결합점·계약 변경·상호 버그 공유 채널로 유지한다.

## 공통으로 확인한 현재 기반

- geon은 자연어 요청과 app_version_id를 받아 Qwen 기반 DeploymentManifest를 생성하고 Go Guard를 통과시키는 흐름을 갖고 있다.
- geon에는 OpenAI 호환 llmclient, deploymentplanner, appdeploy client, ControlRun, Planner Guard가 있다.
- geon Agent Control에는 ApplicationProfile → ResourceRecommendation → DesiredDeploymentSpec → Common JSON DeploymentCreateRequestEnvelope 경로가 이미 있다.
- geon은 AppDeployer의 POST /api/v1/deployments, 상태 조회, 로그 조회를 사용하는 공식 Planner 경로를 문서화하고 있다.
- AppDeployer는 최종 Target Profile, VM, Runtime Adapter 선택과 실제 배포·로그·모니터링을 담당한다.
- Qwen의 local Ollama 예시 설정은 존재하지만, 실제 운영 endpoint가 제공된 상태는 아니다.

## LLM_Op이 맡을 범위

| LLM_Op 소유 | geon 소유 또는 기존 경로 |
| --- | --- |
| 사용자 요청 + resource snapshot + monitoring summary + deployment log를 받는 정규화 입력 | ControlRun, Agent Registry, 기존 자연어 Planner API |
| 로그 redaction, 관측 freshness, 정제 후 prompt 포함 여부 요약 | 요청자 principal·Agent capability 및 telemetry provenance 관리 |
| Qwen 자연어 Safeguard review와 별도 Proposal의 제한 JSON 형식 | 기존 Planner의 app_version_id 신뢰 필드 관리 |
| 입력·출력 Safeguard 규칙과 Golden fixture | 기존 Go Planner Guard·Manifest Guard의 공통 정책 |
| Common JSON analysis/context/resource recommendation의 일관성 검증, Profile minima와 선택 Resource exact 단일-node 값의 분리 투영 | 기존 envelope 생성·저장, ModelRecommendation, DesiredDeploymentSpec 전체 계약 |
| AppDeployer Manifest로의 제한된 변환과 handoff 상태 | Target/Runtime 선택, 실제 제출 실행, 상태 polling 기본 경로 |

현재 LLM_Op의 `handoff.prepared_request`는 `internal/appdeploy.DeploymentCreateRequest`의 exact body이며, Agent Control의 Common JSON envelope와 동일하지 않다. 새 `internal/llmopbridge`는 기존 analysis/context/resource recommendation chain을 검사하고 Profile 최소값과 이미 선택된 Resource candidate의 exact 단일-node 값을 분리해 LLM_Op `PlanningConstraints`로 옮긴다. 기본 CPU Profile보다 큰 카탈로그 후보는 exact 값으로 보존하며, device-memory minimum이 있는 현재 자연어 GPU 경로는 AppDeploy v1에서 강제할 수 없어 fail-closed한다. ModelRecommendation, artifact, Resource hint와 기존 DeploymentCreateRequestEnvelope는 변환하지 않는다. 새 공개 HTTP 파이프라인을 병렬로 확정하지 않고, Draft PR에서 다음 중 공식 결합 위치를 합의한다.

1. LLM_Op의 safeguarded 요구 분석을 기존 ApplicationProfile 생성 경계에 결합하고 이후 geon Common JSON 흐름을 유지한다.
2. 기존 AppDeploy Planner 경로가 LLM_Op의 exact prepared request를 소비하되 Agent Control flow와의 역할 중복을 해소한다.

LLM_Op은 geon의 기존 deploymentplanner.GenerateInput을 즉시 바꾸지 않는다. 첫 단계에서는 별도 `internal/llmop` 패키지와 문서 계약으로 관측 입력을 정규화한다. 통합 시에만 geon 작업자와 합의하여 ControlRun 또는 Planner Service의 명시적인 결합점을 정한다.

현재 `internal/llmop`의 관측 입력은 caller가 요청 본문으로 제공한 값을 정규화하는 방식이다. source 문자열 형식은 검사하지만 서명·소유권·AppDeployer 조회 결과와의 동일성은 검증하지 않는다. AppDeployer 조회·제출 adapter와 승인 검증 adapter는 없다. 무서버 demo도 실제 Qwen endpoint가 아니라 Safeguard review와 Manifest Proposal JSON fixture를 반환하는 두 completion client를 사용한다. 두 단계는 공통 정규화 context에 서로 다른 `required_output` 계약을 사용한다. future integration용 `PrepareWithConfig`도 `AllowLiveCompletion=false`가 기본이라 명시적 허용 없이는 어느 completion도 HTTP client에 도달하지 않으며, true는 동일 candidate로 최대 두 live completion을 허용한다. 이번 작업에서는 켜지 않았다. 이 코드와 단위 테스트는 정적으로 검토했지만 Go 도구로 실행하지 않았다.

## 반드시 지켜야 하는 계약

1. 최종 제출물은 AppDeployer DeploymentManifest다.
2. app_version_id는 호출자가 제공한 신뢰 필드이며 LLM이 생성·변경하지 않는다.
3. target_profile_id는 optional hint이고 최종 Target 선택 권한은 AppDeployer에 있다.
4. LLM_Op과 geon 모두 VM ID, Runtime Adapter, credential, API key, private key, endpoint, shell 명령, Kubernetes/컨테이너 리소스를 Manifest에 넣지 않는다.
5. Qwen 장애·형식 오류에는 추측 Manifest를 만들지 않는다.
6. 승인 없는 요청은 Manifest 생성까지만 허용하고 AppDeployer POST는 하지 않는다.
7. 실제 VM 배포 성공은 AppDeployer의 deployment_id와 상태 증적 없이는 주장하지 않는다.
8. HANDOFF_READY는 구문·책임 경계·자원 상한과 명시 자원값/Profile 최소값/선택 Resource exact 값/fresh availability boolean의 결정적 모순 검사를 통과한 prepare-only 초안이다. 일반 자연어 의미의 정답, 실제 capacity, Target 호환성 또는 배포 가능성 보장은 아니다.
9. `evidence.input.*_included`는 정제된 관측 본문의 bounded Qwen prompt 포함 여부다. Qwen의 실제 사용이나 검증된 telemetry provenance를 뜻하지 않는다.
10. Completion의 candidate/provider/model 일치는 adapter/config 일관성 검사이며 provider가 실제 모델을 증명한 attestation이 아니다.
11. `handoff.next_endpoint`는 후속 연동을 위한 정보성 상대 경로이며 호출·승인·도달 가능성을 뜻하지 않는다.

## LLM_Op이 제공할 계약

입력·출력 상세는 docs/llm-op/01-contract.md에 있다.

~~~text
LLMOperationRequest
  user_request + app_version_id
  + resource_snapshot / monitoring_summary / deployment_logs
  + correlation_id / requested_by / policy mode

-> deterministic request Safeguard
-> common normalized context + Qwen review output contract
-> Qwen natural-language Safeguard review
-> same context + separate Proposal output contract
-> separate Qwen Manifest Proposal
-> manifest Safeguard

LLMOperationResult
  status + safeguard-review evidence + decision + optional DeploymentManifest
  + handoff submission state and informational next route
~~~

공식 종단 진입점은 `llmop.SafeguardedPlanner`다. 내부 `llmop.Planner`는 allow 이후 Proposal 단계의 구성 요소이므로 geon 통합 코드가 직접 호출하면 자연어 Safeguard review를 우회하게 된다.

1차 구현의 잠정 Guard 기본값은 사용자 요청 8,000 rune, 관측 age 10분, 미래·부모 timestamp skew 1분, 입력 log 500개, 보존 log 50개, resource/runtime-health/alarm 각 100개, status bucket 50개, message 2,000 rune, 짧은 필드 128 rune, Qwen user message 128 KiB(system message 제외), completion content 64 KiB다. parameters는 JSON 64 KiB·깊이 16·node 1,000·key 128 rune으로 제한하며 credential·runtime·Target·endpoint·command 같은 책임 경계 key/value를 거부한다. Manifest 생성 action은 confidence 0.5 이상이어야 하고 CPU 256·GPU 16·memory 2Ti·storage 64Ti 상한을 지켜야 한다. 이 값들은 실제 운영 데이터와 AppDeployer 정책을 확인하기 전의 provisional defaults다.

기존 AppDeployer 연동에 필요한 최소 API는 아래와 같다.

~~~text
POST /api/v1/deployments
GET  /api/v1/deployments/{deployment_id}
GET  /api/v1/deployments/{deployment_id}/logs
GET  /api/v1/monitoring/summary
GET  /api/v1/monitoring/runtime-health
GET  /api/v1/resources/inventory
~~~

## geon 작업자에게 요청하는 협업 사항

1. Planner Guard와 Manifest Guard의 정책 버전·금지 필드가 바뀌면 LLM_Op에 알려 준다.
2. 공식 통합 경로를 ControlRun으로 할지, 기존 POST /api/v1/planner/deployments 확장으로 할지 결정 전에 LLM_Op과 검토한다.
3. AppDeployer client 모델이나 Manifest Schema snapshot을 바꿀 때 호환성 fixture를 함께 갱신한다.
4. status, log, monitoring 관측값을 기존 ControlRun에 저장할 계획이 있으면 실제 JSON 예제를 공유한다.
5. Qwen provider 설정은 endpoint·비밀값이 아닌 caller가 지정한 candidate_id, provider capability, 오류 코드 수준에서만 공유한다. 모델 비교·추천·선택 로직은 LLM_Op에 추가하지 않는다.
6. `internal/llmopbridge`의 Profile minima + 선택 Resource exact 단일-node 투영을 검토하고, ApplicationProfile/Common JSON 경로와 AppDeploy `DeploymentCreateRequest` 중 공식 결합점을 결정한다. SLO·비용·GPU device memory·replica 최소값처럼 현재 fail-closed하는 필드의 향후 소유자도 명시한다.
7. 현재 LocalRequirementAnalyzer의 자연어 GPU Profile에는 device-memory minimum이 있으나 AppDeploy Manifest에는 대응 필드가 없다. 값을 버리지 않고 계약을 확장할지, 별도 placement evidence로 강제할지 결정한다.

## 충돌을 피하는 작업 방식

- LLM_Op은 새 internal/llmop, internal/llmopbridge 패키지와 docs/llm-op 아래에 우선 작업한다.
- geon의 `go/service-control-api/internal/deploymentplanner`, `go/service-control-api/internal/api/server.go`, 기존 OpenAPI를 수정해야 하는 순간에는 선행 협의를 한다.
- 공유는 LLM_Op Draft PR에서만 수행하고 geon 브랜치에 직접 push·cherry-pick하지 않는다. 병합 가능한 변경이 합의되면 PR 안에서 작은 독립 커밋으로 분리한다.
- AppDeployer 변경은 계약 불일치가 확인된 경우에만 별도 이슈 또는 협업 요청으로 제안한다.

## 검토가 필요한 미결정 사항

- 후속 실제 Qwen 연결을 맡을 상위 계층의 endpoint·모델·인증 정보와 운영 책임자(현재 LLM_Op 범위 밖)
- Scheduler Agent A/B가 줄 resource snapshot·로그 데이터 형식
- 잠정 10분 freshness·1분 skew·50개 retained log·redaction 규칙을 실제 갱신 주기와 보안 정책에 맞게 유지할지 조정할지
- 비-`prepare_only` mode를 향후 허용할 경우 사용할 승인 reference와 사용자 principal 확인 방법
- AppDeployer 개발·통합 환경의 base URL 및 인증 방식

## 별도 버그 노트

docs/coordination/geon-bug-notes.md에 다음 정적 발견을 기록했다.

- AppDeployer COMPLETED 성공 상태를 geon polling이 terminal로 처리하지 않는 문제
- 한글 요청 길이 byte/rune 기준 불일치
- Manifest JSON Schema와 OpenAPI/Go 모델의 requirements 및 엄격성 drift
- request_id 전달과 idempotency, 승인 verifier 계약 부재
- 기존 full-Manifest Generator와 새 `SafeguardedPlanner` 경로를 동시에 공개할 때의 Guard·상태 drift 위험
- LocalRequirementAnalyzer 자연어 GPU device-memory minimum을 AppDeploy v1 Manifest에서 표현할 수 없는 계약 공백

## 기존 예제와 보완 필요 시나리오

기존 data/ops_llm_eval_scenarios.jsonl은 GPU/CPU 요구, 모호성, secret 거부, Target hint, 상태 polling, retry 정책을 다룬다.

LLM_Op은 caller가 제공한 최근 관측의 정제·prompt 포함 여부, 로그 redaction, Safeguard review의 allow/clarify/reject, Proposal LLM의 금지 필드 생성, Qwen 미연결, 비-`prepare_only` mode·임의 approval reference 거부를 추가 검증 대상으로 둔다. stale 관측은 본문을 Qwen prompt에서 제외하고 evidence의 `stale_sources`에 기록하는 계약이다. 이 evidence는 관측 출처의 진위나 Qwen의 실제 근거 사용을 증명하지 않는다. Repair는 현재 자동 수정 loop가 아니라 clarify/reject로 닫으며, `retryable=false`를 결정적 재시도 금지 상태로 바꾸는 동작과 사용자 명확화 왕복 흐름은 후속 범위다. 이 추가 fixture는 geon의 기존 평가 시나리오를 대체하지 않고 보완한다.
