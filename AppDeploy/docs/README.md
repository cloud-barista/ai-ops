# Documentation map

AI App Deployer is a headless Go/Echo service. The automation agent calls the
REST API; the service owns App validation, placement, runtime execution, state,
events, and logs.

## Source of truth

- API: [`contracts/openapi/openapi.yaml`](../contracts/openapi/openapi.yaml)
- Runtime configuration: [`conf/template-setup.env`](../conf/template-setup.env)
- Architecture: [`architecture.md`](architecture.md)
- Local runbook: [`install/설치_활용_가이드_초안.md`](install/설치_활용_가이드_초안.md)
- Test runbook: [`test/시험_가이드_초안.md`](test/시험_가이드_초안.md)
- ETRI boundary: [`architecture.md`](architecture.md#etri-replacement-points)

## Current compatibility rules

- Go 1.25 or newer; Go 1.25.0 is verified in this workspace.
- REST prefix: `/api/v1`.
- Allowed artifacts: `package`, `git`, `binary`, `script`.
- Local provider defaults: `RESOURCE_PROVIDER=local`,
  `PLACEMENT_PROVIDER=local`.
- ETRI provider mode requires its endpoint settings and never silently falls
  back to local mode.
- Docker, Kubernetes, OCI images, and container registries are out of scope.

## Maintenance rule

Update the OpenAPI document, request examples, tests, and this map together
when an API contract changes. Keep historical evidence under
`deliverables/evidence`; do not use it as the current verification result.
