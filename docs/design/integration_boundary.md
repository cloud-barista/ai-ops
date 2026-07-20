# 플래너·AppDeploy·인프라 책임 경계

## 연계 원칙

`geon` 브랜치는 자연어 요구를 AppDeploy가 이해하는 `DeploymentManifest`로 변환하고 검증하는 LLM Deployment Planner입니다. 실제 VM 선택과 배포 실행을 중복 구현하지 않습니다.

## 책임 구분

| 단계 | Planner (`geon`) | AppDeploy | 인프라 계층 |
| --- | --- | --- | --- |
| 요구 해석 | 자연어 요구 분석 | - | - |
| 자원 요구 | CPU·메모리·GPU·디스크·accelerator 결정 | App Spec 기본값과 병합 | 실제 자원 정보 제공 |
| Manifest | 생성 및 Go Guard 검증 | 형식 재검증 | - |
| Target | 선택적 hint만 제공 | 후보 조회, readiness, 최종 Target 선택 | VM 제공 |
| Runtime | 선택하지 않음 | Target 기반 Adapter 선택 | 실행 환경 제공 |
| 배포 | API 요청 전달 | artifact 준비와 VM/외부 AI-Infra 배포 | 배포 대상 제공 |
| 상태 | polling과 결과 해석 | 상태·이벤트·로그 저장 | Runtime 상태 제공 |
| 재시도 | 명시된 `retryable`을 근거로 권고 | 오류 코드와 retryable 제공 | 실패 원인 제공 |

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
geon: 자연어 요구 수신
  -> Go Request Guard
  -> LLM DeploymentManifest 생성
  -> Go Manifest Guard
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
