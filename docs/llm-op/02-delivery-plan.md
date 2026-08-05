# LLM_Op 구현 계획

## 1. 목표와 완료 정의

목표는 사용자 자연어 요구와 caller가 제공한 상태·로그·모니터링 관측값을 입력받아, 입력 파싱 → 요구·관측 분석 → 결정적 요청 Safeguard → 자연어 Safeguard LLM review → 별도 제한 Proposal LLM → Manifest mapping → 결정적 출력 Safeguard를 한 흐름으로 처리하고 AppDeployer 타입과 호환되는 prepare-only DeploymentManifest 초안을 후속 파트에 넘기는 것이다.

근거 범위는 1차 기준인 통합 다이어그램 초안의 자연어 요청부터 Manifest 생성까지와 요구사항정의서 v0.1의 OPS-02, SFR-OPS-04~06이다. SFR-OPS-04~06은 요구사항 표상 2027 항목이므로 현재 개발은 2026 선행 PoC·부분 구현이며 공식 완료가 아니다. 2026 SFR-OPS-01~03에는 LLM/Agent 결합·권한 경계 준비 수준으로만 기여한다. LLM 등록·비교·선택, 실제 Qwen 호출, 배포·업데이트 자동화(SFR-OPS-07 이후)는 현재 목표가 아니다.

완료는 다음을 모두 만족할 때로 정의한다.

1. 입력·출력·Safeguard 계약과 예제가 저장소에 있다.
2. Qwen이 없는 환경에서도 Safeguard review와 Proposal용 무네트워크 fixture completion client로 정상·명확화·요청 거부·모델 장애·Manifest 거부 경로를 재현하며 API 비용을 발생시키지 않는다.
3. 모든 통과 Manifest가 internal/appdeploy 모델과 ValidateManifest를 통과한다. JSON Schema drift가 해소되기 전에는 Schema 단독 통과를 완료 기준으로 삼지 않는다.
4. 승인 없는 요청, 민감정보, Target/Runtime 확정, 책임 경계 parameter, 상한을 넘은 자원값이 handoff 경로까지 도달하지 않는다.
5. 1차 구현은 구문·책임 경계·자원 상한과 명시 자원값/Profile 최소값/선택 Resource exact 값/fresh readiness의 결정적 모순 검사를 통과한 prepare-only 초안을 뜻하는 HANDOFF_READY까지만 제공하며 AppDeployer를 호출하지 않는다. 승인 검증 계약이 마련된 뒤 제출 adapter를 별도 단계로 추가한다.
6. 실제 VM 배포 성공은 별도 AppDeployer 통합 증적 없이는 주장하지 않는다.
7. HANDOFF_READY와 `*_included`를 일반 자연어 의미의 정답, 관측 provenance, 실제 capacity·feasibility 또는 Qwen의 실제 근거 사용 증적으로 해석하지 않는다.

## 2. 브랜치 및 결합 전략

- LLM_Op은 origin/geon의 87c82ce를 기반으로 생성한다.
- 초기 구현은 geon의 llmclient, appdeploy 모델·client, plannerguard와 문서 경계를 재사용한다.
- AppDeployer 브랜치를 LLM_Op에 병합하지 않는다. AppDeployer OpenAPI와 Manifest Schema를 계약 기준으로만 사용한다.
- AppDeployer 계약이 바뀌면 Schema snapshot과 예제를 먼저 비교하고, 호환성 판단을 문서화한 뒤 필요한 최소 변경만 한다.
- geon의 기존 deploymentplanner.GenerateInput 및 기존 Planner API는 초기에 직접 변경하지 않는다. 새 관측 입력은 `internal/llmop`의 별도 계약과 정규화 코드로 분리해 충돌을 낮춘다.
- 결합 시에는 후속 adapter가 기존 ControlRun 또는 Planner Service 중 어느 경로에서 호출될지 geon 작업자와 합의한다.
- 원격 공유는 base=`geon`, head=`LLM_Op` Draft PR 하나를 계약·버그 협의 채널로 사용한다. geon 브랜치에 직접 push하거나 별도 cherry-pick을 요구하지 않는다.

## 현재 구현 진행

- 코드 작성: internal/llmop의 요청 모델, 관측 정규화, 10분 freshness·1분 skew, collection·문자열 상한, 로그 제한·redaction
- 코드 작성: 기존 Planner Guard 재사용, 비-`prepare_only` mode와 `approval_reference`, bounded parameter를 벗어난 secret·runtime·Target·endpoint·command key/value 거부
- 코드 작성: 제한된 Qwen Proposal parser, trusted-field Manifest mapper, AppDeploy Go Manifest Guard 호출
- 코드 작성: allow/clarify/reject만 반환하는 자연어 Safeguard LLM 계약과 `SafeguardedPlanner`. allow일 때만 별도 Manifest Proposal completion 실행
- 코드 작성: Completion content 64 KiB와 adapter/config 일관성, confidence 존재와 create 최소값 0.5, reason·assumptions 비밀값·책임 경계, CPU 256·GPU 16·memory 2Ti·storage 64Ti 상한 검증
- 코드 작성: 자연어 정확 자원값·단위 정규화, 구조화 Profile minima와 exact recommended resources, accelerator와 fresh 동일-Target availability의 결정적 semantic Guard. 자동 repair 없이 모호·범위·상충·불일치는 거부
- 코드 작성: Agent Control Common JSON correlation/trace/causation과 Profile/Recommendation join을 검증하는 `internal/llmopbridge`. ModelRecommendation·artifact·resource hint를 제외하고 Profile minima와 선택된 single-node Resource candidate exact 값을 분리 투영. 실제 LocalRequirementAnalyzer 기본 CPU Profile보다 큰 CatalogResourceRecommender 후보의 호환성 test와 실제 GPU analyzer/catalog device-memory fail-closed test 포함
- 코드 작성: caller가 지정한 OpenAI 호환 Candidate 설정을 결합하는 `PrepareWithConfig` 연결 함수. 후보 비교·모델 선택 로직은 없고 `AllowLiveCompletion=false`가 기본이라 명시적 허용 전에는 HTTP client에 도달하지 않음. true는 동일 candidate의 최대 두 completion을 허용하므로 현재 켜지 않으며 실제 Qwen endpoint는 호출·검증하지 않음
- 코드 작성: 두 JSON fixture completion client 기반 무서버 demo command, 요청·Safeguard review·Proposal fixture, Golden fixture 및 단위 테스트 코드
- 정적 확인: fixture JSON 구문과 소스 계약을 확인했으며 Go 실행 결과는 아직 없음
- 대기: Go 테스트·demo 실행은 보안 정책상 사용자 별도 허가 필요
- 대기: HTTP route와 geon ControlRun 결합은 Draft PR에서 geon 작업자와 결합점 합의 후 진행
- 대기: 승인 reference 검증 주체가 없으므로 AppDeployer 제출 adapter와 승인 adapter는 구현하지 않음

## 3. 단계별 계획

| 단계 | 작업 | 산출물 | 수용 기준 |
| --- | --- | --- | --- |
| P0 | 기준 커밋, 기존 계약·예제·평가 자산을 기록 | 본 문서와 계약 문서 | geon/AppDeployer 책임 경계가 명시됨 |
| P1 | 상태·로그를 포함하는 입력, 결과 상태, 정책 모드 정의 | LLMOperationRequest/Result 문서와 JSON 예제 | 기존 Manifest Schema와 충돌하지 않음 |
| P2 | 입력 normalizer와 전 Safeguard 구현 | 관측 freshness, redaction, 요청 검증 모듈 | 민감 로그와 오래된 관측값이 LLM 입력에서 통제됨 |
| P3 | 외부 지정 Qwen provider 결합 경계와 두 단계 출력 parser 구현 | 기존 llmclient 재사용, 명시적 candidate 결합, Safeguard/Proposal fixture completion client | 모델 선택 없이 두 completion 계약과 서버 부재·잘못된 JSON 경계를 코드로 정의하고, 실제 API를 호출하지 않는 정적 test case를 작성하며 사용자 허가 후 오프라인 실행 기록을 확보 |
| P4 | semantic/output Safeguard, Manifest 변환, prepare-only handoff 구현 | 자연어 정확값·Profile 최소값·선택 Resource exact 값·fresh readiness 검증기, 변환기, HANDOFF_READY 결과 | Target/Runtime/credential을 생성하거나 AppDeployer에 제출하지 않으며 HANDOFF_READY를 실제 capacity·feasibility 보장으로 사용하지 않음 |
| P5 | 계약·단위·통합 mock 검증과 증적 작성 | Golden fixture, 호환성 표, 실행 기록 | HANDOFF_READY·요청 거부·모델 장애·Manifest 거부 경로를 재현 |
| P6 | 승인 계약 합의 후 제출 연계 | 승인 verifier와 별도 제출 adapter | 사용자 승인·중복 방지 계약이 없으면 시작하지 않음 |

## 4. 제안 구현 구조

초기 코드 배치는 기존 geon과 겹치지 않도록 다음처럼 둔다.

~~~text
go/service-control-api/internal/llmop/
  models.go                 입력·출력 내부 모델
  normalizer.go             상태·로그 표준화와 redaction
  request_guard.go          요청·관측 Safeguard
  safeguard_harness.go      자연어 Safeguard review와 공식 2단계 진입점
  planner.go                allow 이후 제한 Proposal, Manifest mapping, 출력 Guard
  provider.go               llmclient 기반 OpenAI 호환 Qwen 연결
  *_test.go                 fixture completion client 기반 검증 코드

go/service-control-api/internal/llmopbridge/
  bridge.go                 Common JSON Profile minima + exact Resource 추천 투영
  bridge_test.go            namespace 분리·join·fail-closed 계약

docs/llm-op/
  README.md
  01-contract.md
  02-delivery-plan.md
  03-common-json-bridge.md
~~~

공식 HTTP 경로는 아직 고정하지 않는다. 첫 구현은 내부 package, demo command, fixture를 우선 만들었고 HTTP route는 추가하지 않았다. 공식 노출 경로는 geon ControlRun과 Planner API의 유지 방향을 확인한 후 결정한다.

## 5. 시나리오 계획

기존 data/ops_llm_eval_scenarios.jsonl에는 다음이 이미 있다.

- GPU·CPU 배포 요구
- GPU 여부가 모호한 요청의 명확화
- API key 주입 거부
- app_version_id와 optional Target hint 보존
- 잘못된 자원 단위·음수 거부
- AppDeployer의 Target 선택 경계
- 상태 polling과 retryable=false 실패 처리

LLM_Op은 아래 시나리오를 추가한다.

| ID 제안 | 입력 | 기대 결과 |
| --- | --- | --- |
| llmop-001 | caller가 제공한 최근 latency alarm과 GPU 가용 snapshot | HANDOFF_READY, 정제 관측의 prompt 포함 여부 기록. provenance·feasibility 보장은 아님 |
| llmop-002 | 오래된 resource snapshot | snapshot 본문은 Qwen prompt에서 제외되고 evidence의 `stale_sources`에 기록. 최종 상태는 남은 사용자 요청과 fresh 관측에 따라 결정 |
| llmop-003 | 로그에 token 또는 private key 형식 | 로그 값을 redaction하고 `redacted_values`에 반영 |
| llmop-004 | retryable=false 배포 실패 로그 | 후속 범위: 결정적 재시도 금지 정책 검증. 현재는 값이 제한된 컨텍스트에 포함될 뿐이며 제출·재시도 adapter가 없음 |
| llmop-005 | LLM이 VM ID 또는 Runtime을 반환 | MANIFEST_REJECTED |
| llmop-006 | Qwen endpoint/transport 미설정, 어느 단계든 Completion envelope·identity 오류, 또는 Safeguard review JSON 오류 | MODEL_UNAVAILABLE |
| llmop-006b | Proposal JSON·output contract 오류 | MANIFEST_REJECTED |
| llmop-007 | `prepare_only`, approval reference 없는 정상 요청 | HANDOFF_READY, POST 미호출 |
| llmop-008 | 비-`prepare_only` mode 또는 임의 approval reference | REQUEST_REJECTED, POST 미호출 |
| llmop-009 | request_id·correlation_id·app_version_id 등 필수값 누락 | REQUEST_REJECTED, Qwen·POST 미호출 |
| llmop-010 | CPU 256·GPU 16·memory 2Ti·storage 64Ti 중 하나라도 초과하거나 create confidence가 0.5 미만 | MANIFEST_REJECTED |
| llmop-011 | parameters가 64 KiB·깊이 16·node 1,000·key 128 rune을 넘거나 runtime·Target·endpoint·command·credential key/value 포함 | REQUEST_REJECTED, Qwen·POST 미호출 |
| llmop-012 | fresh wrapper지만 내부 항목이 모두 stale 또는 malformed | usable resource/log 또는 deployment-scoped alarm 본문이 없으면 해당 `*_included=false`; global monitoring aggregate가 남으면 true일 수 있음. drop/stale evidence와 timestamp 종류별 drop-vs-reject 계약 유지 |
| llmop-013 | 자연어 정확 자원값과 Qwen Proposal 값 불일치, 범위·근사·상충 표현 | MANIFEST_REJECTED, Manifest와 prepared request 없음 |
| llmop-014 | 구조화 Profile minima보다 작은 Proposal, 선택 Resource exact 값 불일치 또는 CPU-only Profile의 GPU 승격 | MANIFEST_REJECTED |
| llmop-015 | fresh snapshot의 availability가 여러 Target에 분산되거나 지정 Target이 준비되지 않음 | MANIFEST_REJECTED. stale/없는 snapshot은 음성 증거로 단정하지 않음 |
| llmop-016 | Common JSON correlation/trace/causation·Profile join 불일치 또는 infeasible 후보 | bridge 단계에서 fail-closed, Qwen 미호출 |
| llmop-017 | Common JSON에 SLO·cost·GPU device memory·multi-replica minimum처럼 현재 무손실 투영 불가 값 | bridge 단계에서 명시적 오류, 값을 버리거나 추측 Manifest를 만들지 않음 |
| llmop-018 | geon LocalRequirementAnalyzer 기본 CPU 최소값보다 CatalogResourceRecommender 후보가 큼 | Profile 최소값과 선택 후보 exact 값을 모두 보존하고 SafeguardedPlanner가 exact 추천값으로 HANDOFF_READY 준비 |

Safeguard review와 Proposal 모두 `request_clarification`을 낼 수 있지만, 필수 ID 누락이나 stale 관측을 이 상태로 결정적으로 라우팅하는 계약은 현재 수용 범위가 아니다. 사용자에게 명확화 질문을 전달하고 수정 입력을 받는 API 흐름은 후속 단계에서 별도로 정의한다.

## 6. 검증 방식

현재는 실제 Qwen 서버와 운영 VM이 없다. 아래는 검증 계획이며, 지금까지 완료된 증적은 PowerShell 기반 fixture JSON 구문 확인과 소스 정적 검토뿐이다. Go 도구는 실행하지 않았다.

1. JSON fixture로 입력·Safeguard review·Proposal의 구문과 금지 필드를 정적으로 확인한다.
2. 작성된 단위 테스트의 두 fixture completion client로 단계별 Qwen 요청 본문, short-circuit, 오류, JSON parsing 경로를 검증한다.
3. 작성된 demo가 공식 `SafeguardedPlanner` 경로를 통해 HANDOFF_READY를 만드는지 검증한다. demo는 endpoint·API key·HTTP client를 받지 않는 fixture-only 경로만 사용한다.
4. AppDeployer Manifest Schema snapshot과 생성 결과를 비교한다.
5. AppDeployer 제출·승인 adapter가 추가된 뒤에만 POST·상태·중복 방지 통합 mock을 작성한다.
6. Go 도구 실행은 작업 환경의 보안 정책상 별도 사용자 허가를 받은 경우에만 수행한다.

## 7. 의존성과 결정 필요 사항

| 결정 | 필요한 정보 | 영향 |
| --- | --- | --- |
| Qwen 연결(후속) | 상위 계층이 선택한 endpoint, actual_model, 인증 방식, timeout | 현재 구현에는 결합 계약만 유지하고 실제 호출·선택·연결 시험은 하지 않음 |
| 관측 소스 | Scheduler Agent A/B 및 AppDeployer의 실제 JSON·갱신 주기 | 현재 10분 age·1분 skew·50개 retained log 등 잠정 normalizer 기본값을 운영값으로 확정하거나 조정 |
| 승인 체계 | principal, approval_reference 발급·검증 방식 | 제출 허용 조건 |
| AppDeployer 환경 | base URL, 인증, 지원 API 버전 | handoff adapter와 통합 시험 |
| geon 결합점 | ControlRun 또는 Planner Service 중 선택 | 공개 API와 저장 위치 |

## 8. 명시적 비범위

- 실제 Cloud A/B/C 선택이나 VM 예약
- SSH·XFTP·계정·자격증명 관리
- Qwen 서버 구축, 모델 다운로드, 성능 벤치마크
- LLM 후보 비교·추천·자동 선택과 실제 Qwen API 호출·비용 발생 검증
- 승인 없는 자율 scale, restart, stop 또는 재배포
- AppDeployer 내부 Scheduler, Runtime Adapter, Artifact 관리 변경
