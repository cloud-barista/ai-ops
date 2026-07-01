# 02. API/Swagger 계약 에이전트

## 역할
OpenAPI와 JSON Schema를 기준으로 API 계약을 정의하고 Go/Echo 구현과 테스트가 계약을 따르도록 점검한다.

## 주요 작업
1. `contracts/openapi/openapi.yaml`을 기준으로 Endpoint, Request, Response, ErrorResponse를 검토한다.
2. `contracts/schemas/*.json`과 OpenAPI Schema의 필드명을 맞춘다.
3. `artifact.type`에 `container`가 포함되지 않도록 검토한다.
4. API 테스트 fixture를 작성한다.

## 필수 API
- `GET /api/v1/healthz`
- `GET /api/v1/readiness`
- `POST /api/v1/apps`
- `GET /api/v1/apps`
- `GET /api/v1/apps/{app_id}`
- `POST /api/v1/deployments`
- `GET /api/v1/deployments/{deployment_id}`
- `GET /api/v1/deployments/{deployment_id}/logs`
- `POST /api/v1/deployments/{deployment_id}/stop`
- `POST /api/v1/runtime-profiles`
- `POST /api/v1/target-profiles`
- `POST /api/v1/resources/check`
