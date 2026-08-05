# Common JSON → LLM_Op 브리지 계약

## 목적

`internal/llmopbridge`는 geon의 기존 Common JSON 흐름을 수정하지 않고, 다음 세 메시지를 LLM_Op의 prepare-only 요청으로 투영한다.

1. `application.analysis.request`
2. `application.context.created`
3. `resource.recommendation.created`

이 브리지는 모델을 선택하지 않는다. caller가 미리 바인딩한 `llmop.Request.CandidateID`를 그대로 보존하며, `ApplicationContext.ModelRecommendation`과 `InferenceConfiguration`은 읽지 않는다.

## 입력과 출력

입력은 caller-bound `llmop.Request` template과 Common JSON 세 envelope다. template에는 등록된 `app_version_id`, 요청자, completion candidate binding, 선택적 deployment/Target hint 및 관측값만 caller가 제공한다. 자연어 요청, correlation/trace, prepare-only policy와 planning constraints는 브리지가 Common JSON에서 채운다.

출력은 다음 두 부분이다.

- `Projection.Request`: 공식 종단 진입점인 `llmop.SafeguardedPlanner`에 전달할 요청
- `Projection.Evidence`: Common JSON join에 사용한 message/profile/recommendation/snapshot/resource-candidate ID

Evidence의 Profile ID, Resource candidate ID, Snapshot ID는 AppDeploy `target_profile_id`가 아니다. Resource candidate ID와 completion `candidate_id`도 서로 다른 namespace다.

## 결정적 검증

브리지는 아래 조건을 통과하지 못하면 request를 만들지 않는다.

- Common JSON `contract_version=1.0`, message type, RFC3339 `occurred_at`, source/target 필수값
- 세 메시지의 `correlation_id`와 `trace_id` 일치
- Context `causation_id = analysis.message_id`
- Resource Recommendation `causation_id = context.message_id`
- analysis의 App ID/version과 ApplicationProfile의 App ID/version 일치
- ResourceRecommendation의 Profile ID와 ApplicationProfile의 Profile ID 일치
- recommendation status가 `FOUND`
- selected resource candidate가 정확히 한 개 존재하고 `feasible=true`
- 선택 후보가 단일-node이고 CPU·memory·storage·accelerator Profile 최소값을 만족하며 LLM_Op 상한 안에 있음. Profile보다 큰 카탈로그 후보는 거부하거나 축소하지 않고 별도 exact 추천값으로 보존
- Profile과 선택 후보의 isolation이 모두 기존 `ONE_MAJOR_APP_PER_VM` 계약과 일치
- caller template과 Common JSON의 correlation/trace가 충돌하지 않음
- mode는 비어 있거나 `prepare_only`, approval reference는 비어 있음

## 무손실 매핑

| 원본 | LLM_Op 대상 | 규칙 |
| --- | --- | --- |
| analysis `user_request` | `application.user_request` | 문자열을 그대로 보존 |
| Common JSON `correlation_id`, `trace_id` | 같은 이름의 LLM_Op 필드 | 세 envelope가 일치해야 함 |
| caller의 등록 `app_version_id` | `application.app_version_id` | App ID/version에서 합성하지 않음 |
| caller의 Target hint | `application.target_profile_id` | Profile/Resource candidate ID로 대체하지 않음 |
| Profile CPU·memory·storage 최소값 | `planning_constraints` | 양수·상한 내 값만 투영 |
| Profile GPU 필요 여부·개수 | `planning_constraints` | 현재 AppDeploy 범위에서 GPU/NVIDIA만 `nvidia`로 투영 |
| 선택 Resource candidate의 CPU·memory·GPU·storage·accelerator | `planning_constraints.recommended_resources` | 단일-node exact 값으로 투영하고 Proposal이 그대로 재현해야 함 |
| Profile/Recommendation ID | constraints source ID와 bridge evidence | join·redaction용이며 Manifest Target으로 사용하지 않음 |

대표 투영 shape는 다음과 같다. source ID 두 개는 Request Guard와 correlation evidence에만 쓰고 Qwen prompt·Manifest에서는 제거한다.

~~~json
{
  "planning_constraints": {
    "source_profile_id": "profile-bridge-001",
    "source_recommendation_id": "resource-rec-bridge-001",
    "recommendation_feasible": true,
    "cpu_cores_min": 2,
    "memory_mib_min": 4096,
    "gpu_count_min": 0,
    "storage_gib_min": 20,
    "accelerator": "none",
    "recommended_resources": {
      "cpu_cores": 4,
      "memory_mib": 8192,
      "gpu_count": 0,
      "storage_gib": 100,
      "accelerator": "none"
    }
  }
}
~~~

## 투영하지 않는 값

다음 값은 LLM prompt나 AppDeploy Manifest로 복사하지 않는다.

- `ModelRecommendation`, SelectedModel, InferenceConfiguration
- Artifact URI와 entrypoint
- Resource hint, VM ID, provider, runtime
- Resource candidate의 ID를 completion candidate 또는 Target hint로 사용하는 변환
- `AppID + AppVersion`을 등록 `app_version_id`로 합성하는 변환

현재 AppDeploy/LLM_Op v1alpha1에서 의미를 보존할 수 없는 아래 입력은 조용히 버리지 않고 fail-closed한다.

- replica 최소값이 1이 아니거나 선택 후보가 multi-node인 topology
- Profile 또는 선택 Resource candidate의 GPU device-memory minimum
- 선택 Resource candidate의 자원값이 Profile 최소값보다 작거나 LLM_Op 상한을 넘는 경우
- SLO와 cost 값
- 0 또는 지원 상한 밖의 compute/storage minimum
- optional accelerator에 type/count/memory minimum이 섞인 입력

따라서 이 브리지는 Common JSON 전체의 round-trip 변환기가 아니다. 현재 지원하는 VM-oriented 단일-node 자원값의 compatibility adapter다. Profile 최소값은 요구 하한으로, 이미 선택된 카탈로그 후보는 exact 추천값으로 분리하므로 geon 기본 CPU 요구량(2 core/4Gi/20Gi)보다 큰 CPU catalog 후보도 값 손실 없이 연결할 수 있다. SLO·비용·GPU device memory·다중 replica를 지원하려면 AppDeploy Manifest와 LLM_Op의 신뢰 필드 계약을 먼저 확장해야 한다.

특히 geon의 현재 `LocalRequirementAnalyzer`는 자연어 GPU 요구에 기본 device-memory minimum을 부여한다. AppDeploy v1 Manifest에는 이를 강제할 필드가 없으므로 해당 Common JSON GPU 경로는 현재 bridge에서 의도적으로 거부된다. 작성된 회귀 test는 실제 `LocalRequirementAnalyzer`와 checked-in catalog를 연결한 GPU 경로도 이 오류로 종료하도록 고정한다. 이 값을 버리고 성공시키는 것보다, AppDeploy 계약 확장 또는 별도 신뢰 placement 계약을 먼저 합의하는 것이 필요하다. 직접 LLM_Op GPU 요청은 사용자가 CPU·GPU·memory·storage만 명시하고 device-memory 요구를 만들지 않는 현재 fixture 범위에서 별도로 동작한다.

## 대표 CPU 연결 예시

작성된 호환성 fixture는 실제 geon 구성 요소가 만드는 다음 차이를 보존한다.

| 단계 | 값 |
| --- | --- |
| 사용자 요청 | `추론 서비스의 준비 전용 배포 매니페스트를 작성해줘.` |
| LocalRequirementAnalyzer Profile | CPU 2, memory 4096MiB, GPU 0, storage 20GiB |
| CatalogResourceRecommender 선택 후보 | CPU 4, memory 8192MiB, GPU 0, storage 100GiB |
| LLM_Op planning constraints | 위 Profile 값은 minima, 위 선택 후보 값은 `recommended_resources` exact contract |
| Proposal fixture | CPU `4`, memory `8Gi`, GPU `0`, storage `100Gi`, accelerator `none` |
| 결과 | 두 Qwen fixture 단계와 결정적 Guard 통과 후 같은 자원값의 `DeploymentCreateRequest{manifest}`를 `HANDOFF_READY`로 준비, POST 0회 |

이 예시는 선택 Resource candidate를 Qwen 모델 candidate나 AppDeploy Target으로 바꾸지 않는다. 현재 증적은 작성된 test code의 정적 검토이며 Go 실행 결과가 아니다.

## 이후 AppDeploy 인계

브리지 출력은 `llmop.SafeguardedPlanner`의 입력이다. 결정적 Request Guard → 자연어 Safeguard review → 별도 Proposal → semantic/AppDeploy Guard를 모두 통과한 뒤에만 다음 exact body가 준비된다. 내부 `llmop.Planner` 직접 호출은 첫 review를 우회하므로 공식 통합 경로로 사용하지 않는다.

~~~go
appdeploy.DeploymentCreateRequest{
    Manifest: approvedManifest,
}
~~~

이 body는 Agent Control의 Common JSON `DeploymentCreateRequestEnvelope`가 아니다. `HANDOFF_READY`에서도 POST는 수행하지 않으며, 승인·인증·idempotency adapter가 마련되기 전까지 `submission_mode=not_submitted`를 유지한다.

## 검증 상태

브리지의 성공·namespace 분리·model recommendation 무시·join 불일치·infeasible 후보·손실 입력 거부 테스트를 작성했다. 실제 geon `LocalRequirementAnalyzer` 기본 CPU Profile과 `CatalogResourceRecommender`의 더 큰 후보를 함께 사용하는 호환성 테스트, 그리고 Safeguard review부터 exact AppDeploy prepared request까지의 fixture-only 통합 테스트도 작성했다. 작업 환경 보안 정책에 따라 Go 도구는 아직 실행하지 않았으며, 현재 증적은 정적 검토다.
