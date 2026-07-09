# AI-MCMP 개발 컨벤션 반영 현황

English title: AI-MCMP Development Policy Alignment

## 목적

이 문서는 ETRI에서 공유한 AI-MCMP 개발 컨벤션을 기준으로, 본 `geon` 브랜치의 Go 기반 service-control prototype이 어떤 항목을 반영했는지 정리한다.

본 저장소의 연구 범위는 다음 산출물에 맞춰져 있다.

- LLM 운영 관리 구조 설계서
- 에이전트 등록 관리 프로토타입
- AI 응용 배포·제어 추론 최적화 전략 설계서

## 컨벤션 반영 요약

| 항목 | 반영 내용 | 상태 |
| --- | --- | --- |
| 개발 환경 | WSL Ubuntu 22.04, AWS GPU VM Ubuntu 22.04에서 동일 Go 명령 검증 | 반영 |
| 개발 언어 | 핵심 구현을 Go로 구성 | 반영 |
| 백엔드 프레임워크 | Echo 기반 REST API 서버 | 반영 |
| 설정 관리 | `viper` 기반 환경 변수 로딩, `conf/template-setup.env` 제공 | 반영 |
| 구조화 로그 | `zerolog` 기반 request/error 로그 | 반영 |
| context 전달 | Echo request context를 service layer까지 전달 | 반영 |
| API 응답 메시지 | raw internal error 대신 사용자용 `message` 응답 | 반영 |
| request validation | `go-playground/validator/v10` 기반 `validate:"required"` 검사 | 반영 |
| Swagger/OpenAPI | Swagger godoc 주석, `make swag`, 생성된 `swagger.yaml/json`, 제출용 OpenAPI YAML 제공 | 반영 |
| 의존성 정책 | Apache 2.0 호환 중심의 Go package 사용, third-party license report 제공 | 반영 |
| 코드 검증 | `gofmt`, `go test`, `go vet`, `golangci-lint`, `team-validation` 수행 | 반영 |
| 민감정보 관리 | credential, kubeconfig, API key는 저장소에 포함하지 않음 | 반영 |

## 주요 반영 위치

| 영역 | 경로 | 설명 |
| --- | --- | --- |
| Echo API 서버 | `go/service-control-api/internal/api/server.go` | REST handler, validator, request logging, 사용자용 error response |
| Service layer | `go/service-control-api/internal/api/service.go` | `context.Context` 전달 및 service-control 판단 로직 |
| 설정 관리 | `go/service-control-api/internal/api/config.go` | `AIOPS_*` 환경 변수 로딩 |
| 실행 환경 템플릿 | `conf/template-setup.env` | 로컬/VM 실행 설정 예시 |
| Swagger 자동 생성 | `Makefile`, `go/service-control-api/docs/swagger/` | `make swag`로 swagger JSON/YAML 생성 |
| 제출용 OpenAPI | `docs/submission/openapi_service_control.yaml` | 제출 문서용 API 계약 |
| 의존성 보고 | `docs/submission/third_party_license_report.md` | third-party Go package license 검토 |
| 모듈 inventory | `docs/submission/go_module_inventory.txt` | `go list -m all` 기반 모듈 목록 |
| 검증 CLI | `go/service-control-api/cmd/aiops-service-control/` | team/system/API/LLM benchmark 검증 명령 |

## 검증 명령

```bash
cd go/aiops-guard
go test ./...
go vet ./...
golangci-lint run ./...

cd ../service-control-api
go test ./...
go vet ./...
golangci-lint run ./...

cd ../..
make swag

cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

## 결론

본 프로젝트는 AI-MCMP 개발 컨벤션에 맞춰 Ubuntu 기반 Go 개발 환경, Echo API, zerolog logging, viper configuration, context propagation, go-playground validator, Swagger/OpenAPI generation, Apache 2.0 호환 의존성 검토, 단계별 Go 검증 절차를 반영하였다.

다만 본 저장소는 1차년도 service-control prototype이므로, 실제 운영 플랫폼 전체 배포 완료나 production-grade AIOps platform 완성을 주장하지 않는다.
