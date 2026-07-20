# Planner / App Deployer 역할 경계

현재 연동은 `POST /api/v1/deployments` 한 곳을 경계로 둔다. Planner는 배포 의도를 만들고, App Deployer는 실행 가능한 Target을 결정한다.

## Planner

- App 요구사항과 실행 파라미터를 해석한다.
- `DeploymentManifest`를 생성한다. `spec.app_version_id`는 필수이고 `accelerator`, `resources.cpu`, `resources.memory`, `resources.gpu`, `resources.storage`를 요구사항으로 채운다.
- `spec.target_profile_id`는 알고 있는 경우에만 선택적 hint로 전달한다. Target의 VM 상태나 현재 자원을 확정하지 않는다.
- `GET /api/v1/deployments/{deployment_id}` 및 logs/monitoring API로 상태를 관찰한다.
- retryable 오류와 정책에 따른 재시도를 판단한다. 실제 재시도 요청은 동일한 Manifest로 Deployment API를 다시 호출한다.

## App Deployer

- Package/artifact를 생성하고 App Registry에 AppVersion을 만든다.
- 등록된 Target Profile을 열거한다. hint가 있으면 후보를 좁히되 동일한 검사를 수행한다.
- 각 후보에 대해 Target/VM 구성 검증, Runtime Adapter readiness/health check, Manifest resource/runtime matching을 수행한다.
- 검사를 통과한 Target을 결정론적으로 선택하고, 선택된 `target_profile_id`를 normalized Manifest와 DeploymentResponse에 기록한다.
- Runtime Adapter를 통해 prepare/deploy/status/stop을 실행하고 표준 Deployment status와 ErrorResponse를 반환한다.
- `POST /api/v1/resources/check`는 운영자용 개별 점검 API로 유지되며, 배포 시 필수 선행 호출은 아니다.

## 연동 예시

```json
{
  "manifest": {
    "schema_version": "deployment.khu.ai/v1alpha1",
    "kind": "DeploymentManifest",
    "spec": {
      "app_version_id": "appver-example",
      "accelerator": "nvidia",
      "resources": {"cpu": "4", "memory": "16Gi", "gpu": "1", "storage": "20Gi"},
      "requested_by": "ai-ops-geon-planner"
    }
  }
}
```

응답의 `target_profile_id`와 `manifest.spec.target_profile_id`가 App Deployer가 선택한 실제 대상이다. 이 경계를 유지하면 이후 Planner와 App Deployer를 하나의 자동화 흐름으로 합칠 때 Planner 호출부만 교체할 수 있다.
