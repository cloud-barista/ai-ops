# AI-MCMP 개발 정책 반영 현황

English title: AI-MCMP Development Policy Alignment

## 목적

ETRI에서 공유한 `AI-MCMP Project Skills` 문서는 연구 산출물의 주제를 바꾸는 문서가 아니라, AI-MCMP 저장소에 기여할 때 따라야 하는 개발 방식과 코드 품질 기준이다. 본 프로젝트는 기존 연구 범위인 `AI 기반 서비스 제어 및 관리 자동화 프레임워크`를 유지하되, Go 기반 구현과 API 서버 품질을 아래 기준에 맞춰 정리한다.

## 반영 기준

| 항목 | 반영 내용 | 현재 상태 |
| --- | --- | --- |
| 개발 환경 | Ubuntu LTS, WSL, AWS GPU VM에서 동일한 Go 검증 명령 사용 | 반영 |
| 개발 언어 | Go 중심 구현 유지 | 반영 |
| Web framework | Echo 기반 REST API 서버 사용 | 반영 |
| 설정 관리 | `viper` 기반 환경 변수 로딩, `conf/template-setup.env` 제공 | 반영 |
| 구조화 로그 | `zerolog` 기반 API request/error 로그 사용 | 반영 |
| context 전달 | Echo request context를 service layer까지 전달 | 반영 |
| API 응답 | 내부 error string을 그대로 노출하지 않고 사용자 관점 message 반환 | 반영 |
| Swagger/API 문서 | REST handler에 Swagger godoc 주석 유지, OpenAPI YAML 별도 제공 | 반영 |
| 의존성 정책 | Apache 2.0 호환 가능한 Go package만 사용 | 반영 |
| 검증 절차 | `go test`, `team-validation`, `validate-system` 단계 검증 | 반영 |

## 코드 반영 위치

| 영역 | 경로 | 설명 |
| --- | --- | --- |
| Echo API 서버 | `go/service-control-api/internal/api/server.go` | REST handler, request logging, 사용자용 error response |
| Service layer | `go/service-control-api/internal/api/service.go` | `context.Context` 전달 및 `kubectl` command context 적용 |
| 설정 관리 | `go/service-control-api/internal/api/config.go` | `AIOPS_*` 환경 변수 로딩 |
| 실행 환경 템플릿 | `conf/template-setup.env` | 로컬/VM 실행 설정 예시 |
| 검증 CLI | `go/service-control-api/cmd/aiops-service-control/` | team/system/API/LLM benchmark 검증 명령 |

## 남은 관리 항목

아래 항목은 기능 미완료라기보다, 프로젝트가 AI-MCMP 본 저장소나 통합 브랜치에 들어갈 때 추가로 점검해야 하는 운영성 항목이다.

- `golangci-lint`가 설치된 환경에서는 PR 전 정적 분석을 수행한다.
- Swagger 산출물을 자동 생성하는 경우 `make swag` 또는 동일한 generation command를 통합한다.
- 신규 Go package를 추가할 때는 라이선스, 유지보수 상태, 대체 가능성을 기록한다.
- 실제 cloud credential, kubeconfig, API key는 저장소에 포함하지 않는다.

## 보고 문구

본 프로젝트는 AI-MCMP 개발 정책에 맞춰 Ubuntu LTS, Go, Echo 기반으로 개발되며, `zerolog` 구조화 로그, `context.Context` 전파, `viper` 설정 관리, Apache 2.0 호환 의존성, 단계별 Go 검증 절차를 적용한다.
