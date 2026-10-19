# LLM_Op Guard-first 연결 기준 — geon 동료 전달용

## 전달 메시지

LLM_Op 팀은 사용자 자연어가 시스템의 어느 live LLM에도 전달되기 전에 최초 Safeguard를 실행하는 기준 구현을 제공한다. geon 팀이 같은 기능을 별도로 구현하더라도 이 문서의 실행 순서, 허용 action, fail-closed 결과와 Manifest 소유권 경계를 호환 기준으로 삼아 달라.

이 기준은 geon의 자동화·운영 최적화 연구를 대체하지 않는다. 서로 다른 구현이 같은 요청에 대해 서로 다른 안전 판단이나 배포 요청을 생성하는 것을 막기 위한 통합 계약이다.

## 기준 실행 순서

```text
untrusted user request
  -> trusted server Registry builder가 app_id/app_version과
     AppDeploy app_version_id를 결합하여 최초 Request에 고정
  -> LLM_Op ReviewRequest / ReviewWithConfig
     deterministic preflight / redaction / normalization
  -> LLM_Op SafeguardStageResult: allow_request | request_clarification | reject_request
  -> allow_request only
  -> geon Requirement Analyzer / ResourceRecommendation / DEPLOY decision / Go Guards
  -> geon canonical Flow + DesiredDeploymentSpec + INITIAL ManifestRevision
  -> ProjectApprovedInitialFlow이 같은 AppVersion registry binding 재검증
  -> trusted in-process orchestration이 LLM_Op PrepareApproved /
     PrepareApprovedWithConfig 호출
     continuation 재검산 + bounded resource Proposal + semantic/AppDeploy Guard
  -> AppDeploy DeploymentCreateRequest, prepare_only / not_submitted

deployment feedback
  -> geon OperationOptimizationAgent
  -> KEEP | SCALE_OUT | SCALE_IN
  -> OPTIMIZED revision (LLM_Op 범위 밖)
```

`request_clarification`, `reject_request`, Guard 오류, 모델 오류는 legacy Requirement Analyzer나 full-Manifest Generator로 fallback하지 않는다. allow 이외 결과는 그 요청의 모델·Manifest 경로를 종료한다.

단계 1 LLM output의 normative action은 `SafeguardReview.decision`의 `allow_request | request_clarification | reject_request`다. 외부 Result envelope는 공통 안전 종료 action을 재사용하므로 reject가 `Decision.action=reject_unsafe_request`로 표시될 수 있다. 별도 구현은 LLM output enum과 pipeline 종료 action을 한 필드로 혼용하지 않는다.

정상 성공 흐름은 Safeguard completion 1회와 Proposal completion 1회다. `PrepareApproved`는 Safeguard 모델을 다시 호출하지 않는다. 최초 SHA-256 continuation binding에서 예외적으로 제외되는 값은 geon이 보강하는 `PlanningConstraints`뿐이며, 사용자 요청·관측·candidate·policy·`app_version_id` 등 다른 값의 변경은 Proposal 전에 거부한다. 이 binding은 감사·연결 증거일 뿐 배포 승인이나 bearer token이 아니다.

`PrepareApproved`/`PrepareApprovedWithConfig`와 plain SHA-256 continuation은 **같은 Go module 안의 신뢰된 orchestration 전용**이다. 후자는 Go integration API이지 HTTP request 계약이 아니다. 외부 HTTP 요청이 stage나 `PlanningConstraints`를 직접 제공하도록 노출하지 않는다. 프로세스·신뢰 경계를 넘기는 후속 구현은 server-side opaque record ID 또는 HMAC/서명 seal을 만들고, Flow join과 continuation 검증을 원자적으로 수행해야 한다.

continuation 자체에는 아직 만료·nonce·소비 상태가 없다. 따라서 live resume 경로를 공개하기 전에는 server-side one-time/idempotency record, expiry와 replay 방지를 추가해야 한다. 현재 코드는 실제 제출 권한을 주지 않지만 반복 Proposal 호출과 비용을 막는 저장소까지 구현됐다는 뜻은 아니다.

## 역할과 단일 권위

| 소유자 | 소유하는 것 | 소유하지 않는 것 |
| --- | --- | --- |
| LLM_Op | 최초 자연어 Safeguard, 관측 정규화·redaction, bounded Proposal, AppDeploy exact body의 prepare-only 투영과 증거 | canonical geon Flow, target/runtime 선택, 실제 POST, scaling, `OPTIMIZED` revision |
| geon | Requirement/Profile/Recommendation, DEPLOY·REJECT·RETRY, Go Guards, canonical `Flow`, desired runtime class·Resource candidate, `DesiredDeploymentSpec`, `ManifestRevision`, Operation Optimization | LLM_Op reject 우회, 실제 Target/Runtime ID 확정, AppDeploy exact body를 별도 무검증 생성, 최초 Safeguard 이전 live LLM 호출 |
| AppDeploy/후단 | 실제 Target·Runtime ID와 배치, 실제 제출·상태·로그 | 자연어 안전 판단과 모델 선택 |

geon `agentcontrol.DeploymentManifest`와 LLM_Op `appdeploy.DeploymentManifest`는 이름만 같고 다른 타입이다. 전자는 canonical Common JSON revision이고 후자는 AppDeploy HTTP body다. 둘을 캐스팅하거나 병렬 제출하지 않는다.

## 승인된 초기 Flow 연결 불변식

AppDeploy prepare-only 투영은 다음 조건을 모두 만족해야 한다.

1. 최초 LLM_Op Safeguard가 `allow_request`를 반환했다.
2. `Flow.State == DEPLOY_APPROVED`, `Decision.Action == DEPLOY`, `Guard.Status == APPROVED`다.
3. 최초 safeguarded request와 analysis의 user request·correlation·trace가 정확히 같고, analysis/context/recommendation/Flow의 correlation, trace, profile, app identity가 일치한다.
4. active request와 최신 revision의 message/request/decision identity가 일치한다.
5. 현재 버전은 `Revision == 1`, `Phase == INITIAL`, `TriggerAction == DEPLOY`만 허용한다.
6. optimization feedback, scaling decision 또는 revision 2 이상이면 fail-closed한다.
7. 최초 Safeguard 전에 인증된 서버 Registry가 `app_id/app_version -> app_version_id`를 결합하고, approved bridge가 동일 binding을 다시 검증한다. caller 문자열만 신뢰하지 않는다.
8. `llm_candidate_id`와 `selected_resource_candidate_id`를 별도 namespace로 유지한다.
9. 현재는 structured provenance code가 없으므로 Profile에 missing field, assumption 또는 warning이 하나라도 있으면 bridge error로 fail-closed한다. 상위 orchestrator가 이를 terminal clarification/reject로 매핑해야 하며 legacy 경로로 우회하지 않는다. 명시값만 있는 Profile만 통과한다.
10. bridge는 continuation의 공개 형태와 Flow identity를 검사하고, `PrepareApproved`가 정규화 context·policy·candidate까지 포함한 SHA-256 binding을 최종 재계산한다.
11. 외부 caller가 continuation이나 enriched constraints를 직접 주입하는 공개 route는 현재 허용하지 않는다.

orchestrator는 `ProjectApprovedInitialFlow`가 한 묶음으로 반환한 `projection.Request`와 `projection.Safeguard`를 그대로 후반 진입점에 전달한다. plain SHA-256은 `PlanningConstraints`의 geon Flow 출처를 인증하지 않으므로, bridge를 건너뛰어 임의 constraints를 만드는 호출은 호환 구현이 아니다.

현재 geon `LocalRequirementAnalyzer`는 replica 미지정을 default assumption으로 남기고, 대표 CPU 요청의 `GPU 0`도 GPU-required로 해석한다. GPU 요청에는 device-memory 기본값도 넣는다. 따라서 이런 대표 CPU 요청과 device-memory 기본값이 붙은 GPU 요청은 현재 approved bridge의 성공 경로가 아니다. geon이 field-level provenance와 `GPU 0` 의미를 고치거나 AppDeploy가 device memory를 지원하기 전에는 structured/명시 provenance를 가진 Flow만 통과시킨다. 이를 우회해서 성공으로 바꾸지 않는다.

## 별도 구현의 호환 조건

별도 서비스나 Agent로 구현해도 다음은 바꾸지 않는다.

- standalone/demo 또는 pre-decision 호환용 `Prepare/PrepareWithConfig`와 기존 `Project`를 geon 통합의 권위 경로로 사용하지 않는다. 권위 경로는 split Safeguard API + `ProjectApprovedInitialFlow`다.
- 최초 live LLM 호출 전에 deterministic preflight와 Safeguard review를 완료한다.
- 최초 Safeguard 입력에는 `PlanningConstraints`가 없어야 하며, geon 승인 뒤 trusted bridge만 이를 보강한다.
- Safeguard action은 `allow_request`, `request_clarification`, `reject_request`로 제한한다.
- Proposal action은 `create_deployment_manifest`, `request_clarification`, `reject_unsafe_request`로 제한한다.
- LLM은 credential, endpoint, provider, VM/Target, runtime adapter, command를 선택하지 않는다.
- 거부·명확화·오류를 성공으로 바꾸거나 다른 Manifest 생성기로 우회하지 않는다.
- 실제 제출은 `prepare_only/not_submitted`와 분리하고 승인·idempotency 계약 전에는 자동 POST하지 않는다.

구현 언어나 프레임워크가 달라도 위 순서와 결과 의미가 같아야 한다. 계약 확장이 필요하면 LLM_Op Draft PR에서 먼저 합의하고 양쪽 fixture·Guard test를 동시에 갱신한다.

## 협업 방법

- 주 구현과 전체 문서는 `LLM_Op` Draft PR #1에서 검토한다.
- geon에는 이 파일만 포함한 작은 문서 PR을 열어 작업자가 브랜치 안에서 바로 발견할 수 있게 한다.
- geon 작업자는 Guard, Flow, revision 또는 AppDeploy 계약 변경을 Draft PR #1에 알려 준다.
- LLM_Op 작업자는 재현 가능한 충돌·버그를 파일, 상태, 예상 영향과 함께 같은 PR에 공유한다.

geon 대상 docs-only PR은 이 기준 파일만 전달하며 geon runtime에 연결 코드가 이미 구현됐다는 뜻이 아니다. 실행 가능한 기준 구현과 테스트는 LLM_Op Draft PR #1에 있고, 실제 route/orchestrator 연결은 아직 release boundary다.

기준 구현 파일:

- `go/service-control-api/internal/llmop/safeguard_stage.go`
- `go/service-control-api/internal/llmop/safeguard_harness.go`
- `go/service-control-api/internal/llmop/provider.go`
- `go/service-control-api/internal/llmopbridge/approved_flow.go`

이 문서를 동료 GPT에 첨부할 때는 다음과 같이 요청한다.

> 이 파일을 읽고 geon 구현에서 LLM_Op 최초 Safeguard를 어느 지점에 배치해야 하는지, 두 Manifest의 소유권과 금지된 fallback을 설명해 줘. 별도 구현을 제안하더라도 이 계약과 호환되는지 검토해 줘.
