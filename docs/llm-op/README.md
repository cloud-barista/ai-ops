# LLM_Op: 운영 요구 해석 및 AppDeployer Manifest 연계

## 문서 상태

- 상태: prepare-only 정적 구현 및 계약 작성 완료(Go 실행 미검증)
- 작업 브랜치: LLM_Op
- 기준 커밋: origin/geon 87c82ce (2026-08-05 확인)
- 작성일: 2026-08-05
- 원격 공유 예정 경로: base=`geon`, head=`LLM_Op` Draft PR

## 목적

LLM_Op은 사용자의 자연어 운영 요청과 관측된 서버 상태, 모니터링 요약, 배포 로그를 해석하여, AppDeployer가 수용하는 DeploymentManifest 초안을 안전하게 만드는 계층이다. 현재 하네스는 자연어 Safeguard review LLM과 별도 Manifest Proposal LLM의 두 단계를 명시적으로 분리한다.

이 계층은 최신 로컬 `통합 다이어그램 (초안)`의 자연어 입력 → 요구 분석/ApplicationProfile → Safe Guard·Repair → Manifest → 후속 Orchestrator 흐름을 1차 기준으로 삼는다. `구조도 제안안 B`는 Main LLM과 양쪽 Safeguard의 상세 참고다. LLM이 VM, Cloud A/B/C, Runtime Adapter, 자격증명 또는 실행 명령을 결정하거나 직접 실행하지 않는다.

2026-07-29 통합 다이어그램 초안과 2026-07-10 요구사항정의서 v0.1을 다시 대조했다. OPS-02의 운영 요구 입력·분석·실행 계획 생성과 SFR-OPS-04~06의 자연어 작업 단위 변환, 요구·자원 상태 분석, 실행 계획 생성이 현재 근거다. 요구사항 표에서 SFR-OPS-04~06은 2027 항목이므로 이 산출물은 2026년의 선행 PoC·부분 구현이며 공식 완료 주장이 아니다. 2026 공식 SFR-OPS-01~03 중 LLM/Agent 결합, 권한·승인 경계와 생명주기 준비에 필요한 계약만 보조한다. 실제 배포·업데이트 자동화(SFR-OPS-07 이후), LLM 등록·선택, Target/Runtime 선택은 현재 범위가 아니다. 다이어그램의 LLM 선택기는 선택 요소로 취급하며 `candidate_id`는 상위 계층이 이미 정한 설정을 결합하기 위한 식별자일 뿐, LLM_Op이 모델을 비교·추천하는 기능이 아니다.

## 현재 구현 상태

- internal/llmop에 상태·로그·metric 정규화, wrapper·내부 timestamp freshness, 입력 상한, redaction, Request Guard 코드를 작성했다.
- 첫 Qwen 단계는 정규화된 입력을 `allow_request`, `request_clarification`, `reject_request` 중 하나로만 review한다. allow일 때만 둘째 Qwen 단계가 제한된 action·reason·resources Proposal을 만들며, 어느 단계도 전체 Manifest를 작성하지 않는다.
- 구조화 제약이 없는 create Proposal은 자연어에 정확히 나타난 CPU·GPU·memory·storage 값과 단위 정규화 후 일치해야 한다. Common JSON bridge가 Profile 최소값과 이미 선택된 ResourceRecommendation의 정확한 단일-node 자원값을 함께 제공하면 자연어의 양수 자원값과 Profile 값은 하한으로, 명시적 `GPU 0`은 exact no-GPU 제약으로, 선택 자원값은 exact contract로 검사한다. CPU·memory·storage의 명시적 0과 모호·범위·상충 표현은 fail-closed한다.
- fresh resource snapshot이 있으면 CPU·memory·storage와 필요 시 GPU availability가 같은 Target에서 동시에 true인지 확인한다. boolean readiness만 확인하므로 실제 수량 capacity나 배포 가능성을 증명하지는 않는다.
- Completion의 실행 상태와 candidate/provider/model 구성 일치를 adapter 경계에서 확인한 뒤, Go mapper가 신뢰 ID와 AppDeployer DeploymentManifest를 구성하고 기존 Go Manifest Guard로 검증한다. 이 일치는 provider가 실제 모델을 증명한 attestation이 아니라 adapter/config 일관성 검사다.
- 기존 OpenAI 호환 Candidate 설정에서 caller가 지정한 항목을 결합하는 연결 함수는 작성했지만, 모델 선택 로직은 구현하지 않았고 실제 Qwen 연결도 검증하지 않았다. 공식 종단 진입점은 `SafeguardedPlanner`이며 내부 `Planner`는 allow 이후 Proposal 전용 구성 요소다. `PrepareWithConfig`는 같은 caller-bound candidate를 Safeguard review와 Proposal에 사용하며, 기본값이 네트워크 차단이라 통합 계층이 `AllowLiveCompletion`을 명시적으로 켜야만 최대 두 completion 호출이 HTTP client에 도달한다. 현재 demo의 두 completion은 각각 JSON fixture를 반환하는 `fixtureCompletionClient`뿐이다.
- Safe Guard·Repair 중 현재 구현된 동작은 deterministic reject/clarify와 모델 review다. 잘못된 값을 LLM이 자동 수정해 재제출하는 repair loop는 승인·반복 한도 계약이 없어 구현하지 않았다.
- 이 단계의 검증은 무네트워크·무과금이 원칙이다. 실제 Qwen endpoint, API key 또는 과금 API는 호출하지 않으며, AppDeployer POST도 호출하지 않는다.
- `internal/llmopbridge`는 geon의 ApplicationProfile/ResourceRecommendation Common JSON 체인을 검증하고 Profile 최소값과 이미 선택된 단일-node Resource candidate의 정확한 자원값을 분리해 `PlanningConstraints`로 투영한다. 이로써 기본 CPU Profile보다 큰 카탈로그 후보도 값 손실 없이 연결한다. ModelRecommendation, artifact, resource hint, VM/Runtime 값은 제외하며, 현재 geon 자연어 GPU Profile의 device-memory minimum은 AppDeploy v1에 표현할 수 없어 bridge에서 fail-closed한다.
- 초기 구현의 성공 경로는 `prepare_only` 입력을 구문·책임 경계·자원 상한·결정적 자원값 정합성 Guard가 통과한 초안인 `HANDOFF_READY`까지 준비한다. 일반 자연어 의미 이해의 정답, 실제 capacity, 배포 가능성 또는 실행 성공을 보장하지 않는다. AppDeployer 제출 adapter와 승인 검증 adapter는 아직 없다.
- 현재 증적은 소스 정적 검토와 fixture JSON 구문 확인뿐이다. Go 테스트·demo 실행과 공식 HTTP route 결합은 아직 수행하지 않았다.

## 책임 경계

~~~text
사용자 요청 + 상태/로그
  -> 결정적 요청 Safeguard
  -> Qwen 자연어 Safeguard review
  -> 별도 Qwen Manifest Proposal
  -> Manifest 초안
  -> 결정적 semantic/AppDeploy 출력 Safeguard
  -> HANDOFF_READY (현재 구현 경계)
  -> [후속 제출 adapter]
  -> AppDeployer DeploymentManifest API
  -> [AppDeployer 책임] Target/Runtime 선택 및 배포
~~~

## 구현 흐름과 하네스

| 단계 | 구현 | 입력 → 출력 | 결정적 경계 |
| --- | --- | --- | --- |
| 1. 입력 파싱·정규화 | `models.go`, `normalizer.go` | 자연어 요청·resource/monitoring/log wrapper → bounded normalized context | timestamp, freshness, collection/문자열 상한, redaction |
| 2. 요청 Safeguard | `request_guard.go` | normalized request → 허용 또는 `REQUEST_REJECTED` | prepare-only, 신뢰 ID, secret·Target·Runtime·command 경계 |
| 3. 자연어 Safeguard LLM | `SafeguardedPlanner`, `safeguard_harness.go` | 공통 정규화 context + review 전용 output contract → allow/clarify/reject JSON | allow만 다음 단계로 전달. fixture-only 시연은 네트워크 0회 |
| 4. Manifest Proposal LLM | 내부 `Planner`의 `CompletionClient` 경계 | 같은 정규화 context + Proposal 전용 output contract → 제한 Proposal JSON | Safeguard allow 뒤 별도 completion; 전체 Manifest 생성 금지. 직접 `Planner` 호출은 공식 종단 경로가 아님 |
| 5. 출력 파싱·검증 | `planner.go`, `semantic_guard.go` | Proposal JSON → 검증된 action/reason/resources | 단일 JSON, 허용 action, confidence, 자원 상한, 명시값·Profile 하한·선택 Resource exact 값·fresh readiness 모순 검사 |
| 6. Manifest 생성 | Go mapper + `appdeploy.ValidateManifest` | 신뢰 caller 필드 + 검증 Proposal → DeploymentManifest | LLM이 신뢰 ID·Target/Runtime/credential을 생성하지 못함 |
| 7. 후속 인계 | `LLMOperationResult.handoff` | Manifest 초안 → AppDeploy `DeploymentCreateRequest` 형태의 `prepared_request` + `HANDOFF_READY` | 정확한 요청 body를 준비하되 POST·승인·배포는 수행하지 않음 |

`PrepareWithConfig`는 향후 통합을 위한 별도 live 경계다. 기본 옵션으로는 차단되며, 이 단계의 demo와 검증 절차는 해당 경계를 활성화하지 않는다.

| 구성 요소 | LLM_Op 책임 | 기존 구성 요소 책임 |
| --- | --- | --- |
| 입력 해석 | 요청, 상태, 로그를 정규화하고 누락·오래됨·민감정보를 판정 | geon은 기존 자연어 요청과 ControlRun 흐름을 유지 |
| LLM | Qwen Safeguard review와 별도 bounded Proposal의 두 JSON 계약을 제공 | Qwen은 분류·초안 생성만 수행하고 deterministic Guard를 우회하지 못함 |
| 정책 | 권한, 현재 PoC의 VM-oriented subset, 비밀값, 출력 형식, CPU 256·GPU 16·memory 2Ti·storage 64Ti 상한과 create confidence 0.5 이상을 검증 | geon의 Planner Guard/Manifest Guard와 동일한 금지 원칙을 유지 |
| 산출물 | Guard 통과 시 prepare-only DeploymentManifest 초안, 같은 Manifest를 감싼 AppDeploy `DeploymentCreateRequest`, HANDOFF_READY. 실패 시 명시적 오류 상태 | AppDeployer는 향후 제출받은 Manifest를 재검증하고 실제 가용 자원에 따라 최종 Target·Runtime을 선택 |
| 실행 | 현재 실행·제출 책임 없음. AppDeployer·승인 adapter도 미구현 | AppDeployer와 인프라 계층이 실제 배포와 상태·로그를 관리 |

## 확인한 기존 기반

| 자산 | 확인 결과 | LLM_Op에서의 활용 |
| --- | --- | --- |
| geon Deployment Planner | 자연어 요청, app_version_id, 후보 LLM으로 Manifest를 생성하고 Guard를 통과시킴 | 기존 흐름은 보존하고 상태·로그 입력 계약과 정규화 계층을 추가 |
| geon llmclient | OpenAI 호환 Chat Completions, JSON mode, 환경변수 API 키 지원 | Qwen Provider 연결 코드의 기반으로 재사용 |
| Qwen 예시 설정 | 로컬 Ollama용 qwen3.5:4b 예시와 일반 OpenAI 호환 예시가 있음 | 실제 서버 주소·모델·키는 환경 설정으로만 주입 |
| AppDeployer 계약 | Go `DeploymentManifest`/`ValidateManifest`, 배포·상태·로그·모니터링 OpenAPI와 drift가 있는 JSON Schema가 있음 | 현재 canonical 검증은 Go 모델·Guard, exact 후속 body는 `DeploymentCreateRequest{manifest}`로 고정 |
| 평가 시나리오 | GPU/CPU 요청, 모호성, 비밀값, 자원 형식, Target 경계, 상태 polling, 재시도 시나리오가 있음 | 상태·로그 사전 입력 시나리오를 추가 |

현재 geon의 Deployment Planner 입력에는 자연어 요청, app_version_id, 선택 Target hint, 요청자, 파라미터와 사전 요구사항이 있다. 통합 다이어그램의 상태·피드백 경로와 구조도 B의 서버 상태 및 Log Data는 기존 Manifest 생성 전 입력 계약으로 분리되어 있지 않다. 이것이 LLM_Op의 주된 보완 지점이다.

## 연동 원칙

1. 현재 canonical 최종 검증 계약은 AppDeployer Go `DeploymentManifest` 모델과 `ValidateManifest`이며, exact 후속 요청 body는 `DeploymentCreateRequest{manifest}`다. JSON Schema는 확인된 drift가 있어 참고 계약으로만 사용한다.
2. app_version_id는 신뢰된 호출자가 제공한 값을 보존한다.
3. target_profile_id는 선택적 hint일 뿐이며, 최종 Target 선택 결과가 아니다.
4. runtime_profile_id, VM ID, Cloud 공급자 결정, credential, API key, private key, shell 명령은 출력에서 금지한다.
5. Qwen이 사용할 수 없거나 JSON 검증에 실패하면 자동으로 그럴듯한 Manifest를 만들지 않는다.
6. 1차 구현의 성공 경로는 prepare_only 입력에서 HANDOFF_READY까지만 진행한다. 승인 검증·중복 방지 계약이 합의되기 전에는 배포 요청을 수행하지 않는다.
7. `evidence.input.*_included`는 freshness·timestamp·redaction·길이 제한을 거친 해당 관측 본문이 Qwen prompt에 포함되었음을 뜻할 뿐, Qwen의 실제 사용이나 Manifest의 의미적 타당성을 증명하지 않는다.
8. 관측값은 현재 caller가 요청 본문으로 제공하며 출처 서명·소유권·AppDeployer 조회를 검증하지 않는다.
9. `handoff.next_endpoint`는 후속 연동을 위한 상대 경로 안내일 뿐이며, 호출·승인·도달 가능성을 뜻하지 않는다.

## 문서 목록

- 00-existing-assets-audit.md: 기존 사용자 시나리오·연결 API·증적의 정적 점검 결과
- 01-contract.md: LLM_Op 입력·출력·Safeguard·AppDeployer 매핑
- 02-delivery-plan.md: 구현 범위, 단계, 검증 및 완료 기준
- 03-common-json-bridge.md: geon ApplicationProfile/ResourceRecommendation의 lossless subset 투영 계약
- ../coordination/to-geon-llm-op-handoff.md: geon 작업자에게 전달할 협업 경계와 요청 사항
- ../coordination/geon-bug-notes.md: geon과 상호 공유할 버그·계약 불일치
- ../../examples/llm-op/README.md: 무서버 demo fixture와 예상 trace

## 범위 밖

- 실제 Qwen 서버 설치·운영, API 키 발급 또는 저장
- 실제 VM, Cloud A/B/C, SSH, Runtime Adapter 배포 실행
- Kubernetes·컨테이너 오케스트레이션
- 사용자 승인 없는 재시도·확장·중지 같은 자율 실행
- AppDeployer의 Target Scheduler 또는 Credential 저장소 구현 변경
