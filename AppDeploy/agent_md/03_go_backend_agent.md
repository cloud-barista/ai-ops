# 03. Go/Echo 백엔드 에이전트

## 역할
Go/Echo 기반 AI App Deployer API 서버를 구현한다.

## 구현 기준
- `cmd/server/main.go`에서 Echo 서버를 시작한다.
- 모든 API는 `/api/v1` prefix를 사용한다.
- request_id middleware를 구현한다.
- Handler는 OpenAPI 계약을 따른다.
- Service 계층에서 비즈니스 로직을 처리한다.
- Repository 계층은 Interface로 분리하고 1차년도는 파일 기반 또는 SQLite를 허용한다.

## 권장 패키지 구조
```text
internal/app
internal/deployment
internal/runtime
internal/resource
internal/external
internal/logger
internal/errors
internal/config
```

## 금지 사항
- Dockerfile, docker-compose.yml, Kubernetes manifest를 기본 구현으로 만들지 않는다.
- 컨테이너 이미지 기반 App Spec을 기본 예제로 만들지 않는다.
