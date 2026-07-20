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

- AppDeploy OpenAPI: `AppDeploy/contracts/openapi/openapi.yaml`
- Manifest Schema: `AppDeploy/contracts/schemas/deployment_manifest.schema.json`
- 배포 요청: `POST /api/v1/deployments`
- 상태 조회: `GET /api/v1/deployments/{deployment_id}`
- 로그 조회: `GET /api/v1/deployments/{deployment_id}/logs`

저장소의 `contracts/appdeploy/deployment_manifest.schema.json`은 구현 검증에 사용한 계약 snapshot입니다. 실제 통합 시에는 양쪽 브랜치의 계약 버전을 함께 확인해야 합니다.

## 구현 경계

- `target_profile_id`는 hint이며 최종 Target 선택 결과가 아닙니다.
- credential, private key, token은 Manifest와 Git에 포함하지 않습니다.
- 1차년도 경로는 VM 기반이며 컨테이너와 Kubernetes 배포를 요구하지 않습니다.
- AppDeploy endpoint가 없는 환경에서는 계약 테스트만 가능하며 실제 배포 완료를 주장하지 않습니다.
