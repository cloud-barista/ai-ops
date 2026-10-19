# Deployment Agent

`ApplicationProfile`과 `ResourceRecommendation`을 받아 배포 및 운영 변경을 판단합니다.

## 핵심 기능

- 자원 제약과 Agent 판단에 대한 Guard 검증
- 승인된 `DeploymentPlan`과 `Revision` 관리
- 실행 후 관측에 따른 유지, 확장, 축소 판단
- AppDeploy로의 실행 구성 전달과 상태 추적

Revision은 승인된 실행 구성의 버전입니다. 판단 승인과 실제 변경 적용은 별도 동작입니다.

## 실행

Go 1.25 이상이 필요합니다.

```bash
cd deployment_agent/go/service-control-api
go run ./cmd/service-control-api
```

## 검증

서비스 모듈과 `go/aiops-guard`에서 각각 `go test ./...`와 `go vet ./...`를 실행합니다.

[연동 흐름](docs/integration_pipeline.md) / [API 계약](docs/openapi_deployment_agent.yaml) / [상세 가이드](docs/usage.md)
