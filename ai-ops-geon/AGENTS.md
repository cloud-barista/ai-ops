# AGENTS.md

이 저장소를 수정하는 coding agent는 아래 규칙을 따른다.

## Project Scope

- 본 브랜치는 AI-MCMP 개발 컨벤션을 반영한 Go 기반 service-control prototype이다.
- 기존 구조를 갈아엎지 말고 최소 변경으로 수정한다.
- production-ready platform 또는 실제 운영 배포 완료를 임의로 주장하지 않는다.

## Go Development Rules

- 핵심 구현은 Go + Echo 기반으로 유지한다.
- 구조화 로그는 `zerolog`를 사용한다.
- 설정 관리는 `viper`와 환경 변수를 사용한다.
- request-scoped 로직에는 `context.Context`를 전파한다.
- API 응답에 raw `err.Error()`를 그대로 노출하지 않는다.
- Swagger/OpenAPI godoc 주석과 generated OpenAPI 산출물을 함께 관리한다.

## Security Rules

- credential, token, kubeconfig, API key, cloud secret을 커밋하지 않는다.
- 예시가 필요한 경우 실제 값 대신 환경 변수명 또는 placeholder를 사용한다.

## Validation Rules

작업 후 관련 Go 파일에 `gofmt`를 적용하고 다음 검증을 수행한다.

```bash
make test
make vet
```

정적 분석이 가능한 환경에서는 다음도 수행한다.

```bash
make lint
```
