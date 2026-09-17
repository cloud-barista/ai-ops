# LLM_Op·Resource Ops·Deployment Agent·AppDeploy 책임 경계

## 연계 원칙

정식 deployment_agent는 자연어를 분석하거나 VM 후보를 점수화하지 않습니다. LLM_Op의 `ApplicationProfile`과 Resource Ops의 `ResourceRecommendation`을 받아 상관관계와 적합성을 검증하고, 배포 판단과 `DeploymentPlan`을 생성합니다. 실제 배포 실행은 AppDeploy 책임입니다.

## 책임 구분

| 단계 | LLM_Op | Resource Ops | Deployment Agent | AppDeploy |
| --- | --- | --- | --- | --- |
| 요구 해석 | 자연어/App Spec → ApplicationProfile | - | Profile 재검증 | - |
| 자원 후보 | - | 수집·정규화·필터·점수·추천 | 추천 결과 재검증·placement 최적화 | 최종 readiness 재검증 |
| 결정 | - | - | DEPLOY/REJECT/RETRY + Guard | - |
| 계획/Manifest | - | - | DeploymentPlan·DesiredDeploymentSpec 생성 | 형식 재검증·플랫폼 투영 |
| 실제 배포 | - | - | handoff | artifact 준비와 실행 |
| 상태/피드백 | - | snapshot source 제공 가능 | 성공·실패 원인 정리·최적화 판단 | 상태·이벤트·로그 제공 |

## 연계 계약

- 호환성 확인 기준: AppDeployer `bb77e53` (`2026-07-20`, Runtime Profile 제거 및 Target 자동 선택 반영)
- AppDeploy OpenAPI: `AppDeploy/contracts/openapi/openapi.yaml`
- Manifest Schema: `AppDeploy/contracts/schemas/deployment_manifest.schema.json`
- 배포 요청: `POST /api/v1/deployments`
- 상태 조회: `GET /api/v1/deployments/{deployment_id}`
- 로그 조회: `GET /api/v1/deployments/{deployment_id}/logs`

저장소의 `contracts/appdeploy/deployment_manifest.schema.json`은 위 커밋의 Manifest Schema와 의미상 동일한 계약 snapshot입니다. 실제 통합 시에는 AppDeployer OpenAPI와 해당 snapshot의 호환성을 함께 확인합니다.

최신 계약에서 별도 `Runtime Profile`과 `runtime_profile_id`는 사용하지 않습니다. Planner는 Target이나 Runtime을 확정하지 않고 `app_version_id`와 자원 요구사항을 포함한 Manifest만 전달합니다. `target_profile_id`가 생략되면 AppDeploy가 등록된 Target의 VM readiness와 자원 조건을 검사해 실제 Target을 선택합니다.

## 실제 통합 준비 순서

```text
AppDeploy: Credential 등록
  -> Runtime 정보가 포함된 Target 등록
  -> App/Package 등록 및 app_version_id 확보
LLM_Op: 자연어 요구 -> ApplicationProfile
Resource Ops: ApplicationProfile -> ResourceRecommendation
Deployment Agent: 두 결과 correlation/Guard 검증
  -> DEPLOY/REJECT/RETRY
  -> DeploymentPlan/DesiredDeploymentSpec 생성
  -> POST /api/v1/deployments
AppDeploy: Target 자동 선택 및 배포
geon: 상태와 로그 조회
```

Credential 등록, Package Build, Target 관리와 웹 콘솔은 AppDeploy의 준비·실행 기능입니다. `geon`은 이를 중복 구현하지 않으며 등록이 끝난 `app_version_id`부터 Planner 흐름을 시작합니다.

## 구현 경계

- `target_profile_id`는 hint이며 최종 Target 선택 결과가 아닙니다.
- `runtime_profile_id`는 생성하거나 전송하지 않습니다.
- credential, private key, token은 Manifest와 Git에 포함하지 않습니다.
- 1차년도 경로는 VM 기반이며 컨테이너와 Kubernetes 배포를 요구하지 않습니다.
- AppDeploy endpoint가 없는 환경에서는 계약 테스트만 가능하며 실제 배포 완료를 주장하지 않습니다.
