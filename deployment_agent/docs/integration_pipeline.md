# LLM_Op → Resource Ops → Deployment Agent

## 정식 흐름

```text
POST llmop /api/v1/application-profiles
  → ApplicationProfile
POST resource_ops /api/v1/resource-recommendations
  → ResourceRecommendation
POST deployment_agent /api/v1/deployment-plans
  → DEPLOY / REJECT / RETRY + DeploymentPlan
```

호출자는 하나의 흐름 ID를 생성해 resource-ops의 `request_id`와 deployment_agent의 `correlation_id`에 동일하게 넣는다. deployment_agent는 다음 조건을 fail-closed로 검사한다.

- `resource_recommendation.request_id == correlation_id`
- `resource_recommendation.profile_id == application_profile.profile_id`
- `trace_id` 존재
- resource-ops schema version, 관측 시각, 반환 개수 일관성
- ApplicationProfile의 최소 자원과 replica 범위
- 선택 후보의 feasibility와 최소 자원 충족 여부

deployment_agent는 resource-ops의 후보 순위나 원본 snapshot을 다시 계산하지 않는다. 0~100 점수는 Agent Control 계약의 0~1 범위로 정규화하고, 추천된 resource ID를 `resource_hints`로 보존한다. `replicas_min > 1`이면 상위 N개 서로 다른 추천 resource를 하나의 placement 후보로 묶는다. 후보가 부족하면 인프라를 만들어내지 않고 `RETRY`와 resource recommendation 수정 요청을 반환한다.

## 책임 경계

| 서비스 | 입력 | 출력 | 수행하지 않는 일 |
| --- | --- | --- | --- |
| llmop | 자연어 또는 structured App Spec | ApplicationProfile, LLM 비교 결과 | 자원 조회, 후보 추천, 배포 계획 |
| resource_ops | ApplicationProfile | ResourceRecommendation | 자연어 분석, Agent 결정, Manifest |
| deployment_agent | ApplicationProfile + ResourceRecommendation | DEPLOY/REJECT/RETRY, DeploymentPlan | 자연어 재분석, resource 수집·점수 재계산 |

## Legacy

기존 all-in-one API는 `internal/api/legacy_routes.go`에서만 등록한다. 정식 프로세스는 focused server를 사용하며 아래 설정이 없으면 자체 Requirement Analyzer, Mock Resource Recommender, LLM_Op safeguard, Autonomy, legacy 웹 UI를 노출하지 않는다.

```bash
export AIOPS_LEGACY_API_ENABLED=true
```

## 재현 검증

작업공간에 `llmop`, `resource_ops`, `deployment_agent`가 나란히 있을 때 다음 스크립트가 세 바이너리를 별도 포트로 시작하고 자연어 요청부터 실제 Prometheus 추천과 배포 계획까지 검증한다.

```bash
./scripts/verify-three-service-integration.sh
```

기본 포트는 llmop `18081`, resource_ops `18082`, deployment_agent `18083`이며 각각 `LLMOP_INTEGRATION_PORT`, `RESOURCE_OPS_INTEGRATION_PORT`, `DEPLOYMENT_AGENT_INTEGRATION_PORT`로 바꿀 수 있다.
