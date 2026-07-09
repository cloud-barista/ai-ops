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
- 모든 구조화 로그는 `github.com/rs/zerolog/log`를 사용한다.
- `fmt.Println`, 표준 라이브러리 `log`, bare `logrus` 호출로 운영 로그를 남기지 않는다.
- Handler는 `ctx := c.Request().Context()`를 사용하고, service/core 함수에는 `context.Context`를 첫 번째 인자로 전달한다.
- Handler 내부에서 `context.Background()`를 새로 만들지 않는다. 테스트 fixture 외의 server/library code도 request context 또는 호출자가 준 context를 전파한다.
- error는 무시하지 않는다. `_ = err` 또는 bare `_`로 실패를 버리지 말고 처리하거나 반환한다.
- service/core 계층에서 error를 반환할 때는 `fmt.Errorf("failed to ...: %w", err)`처럼 문맥을 감싼다.
- Handler는 내부 error를 zerolog로 기록한 뒤 표준 ErrorResponse와 의미 있는 HTTP status(`400`, `404`, `409`, `500` 등)로 변환한다.
- API 응답 message에는 raw `err.Error()`나 내부 stack trace, DB/SSH 연결 문자열을 그대로 넣지 않는다.
- 사용자에게 보이는 message는 호출자 관점으로 작성한다. 불필요한 `Failed to`, `Error:`, `Invalid request:` 접두어를 피하고 조치 가능한 문구를 쓴다.
- 장시간 작업 응답에는 가능하면 elapsed time을 포함한다.
- 설정 로딩과 접근은 `viper`를 기준으로 하며, runtime override는 환경변수로 받고 `conf/template-setup.env` 같은 템플릿에 문서화한다.
- credential, token, secret 값은 source file에 hard-code하지 않는다.
- request/response struct는 전용 model package에 두고 exported field에는 `json` tag를 붙인다. Swagger/godoc을 쓰는 필드에는 필요 시 `example` tag를 붙인다.
- 입력 검증이 필요한 필드는 `validate:"required"` 등 validator tag를 사용한다.

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
- library/server code에서 `panic`을 사용하지 않는다.
- 신규 goroutine을 만들 때 context cancellation, channel close, `sync.WaitGroup` 또는 `errgroup`으로 종료 경로를 보장하지 않은 채 방치하지 않는다.

## Dependency 기준
- 새 Go package 추가 전 표준 라이브러리 또는 기존 dependency로 해결 가능한지 먼저 확인한다.
- Apache-2.0, MIT, BSD-2/3-Clause, ISC 등 상업적 사용과 Apache-2.0 프로젝트에 호환되는 라이선스만 사용한다.
- GPL, LGPL, AGPL, SSPL, Commons Clause 등 copyleft 또는 상업적 제한이 있는 dependency는 사용하지 않는다.
- 가능하면 `go-licenses` 또는 동등한 도구로 라이선스를 확인하고, GitHub stars, pkg.go.dev Imported By, archived 여부, 최근 commit 날짜 같은 유지보수 지표를 기록한다.
- 새 dependency 채택은 사용자 승인 후 진행한다. 단, `zerolog`와 `viper`는 본 문서의 프로젝트 표준으로 취급한다.

## OpenAPI/Swagger 기준
- 이 저장소의 API source of truth는 `contracts/openapi/openapi.yaml`이다.
- Handler godoc 또는 swaggo 주석을 추가하는 경우에도 OpenAPI 계약과 충돌하지 않게 유지한다.
- Handler 변경으로 API 표면이 바뀌면 OpenAPI, 예제, smoke script, 테스트를 함께 갱신한다.
