# 🏛️ Kyung Hee AIOps 🦁

> AI 기반 서비스 제어 및 관리 자동화 프레임워크
>
> 1차년도 Go 기반 기능 프로토타입

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 🧭 개요

이 저장소는 경희대학교 1차년도 연구 범위인 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**의 제출용/시연용 패키지입니다. 핵심 구현은 Go 언어로 구성되어 있으며, AI LLM 운영 관리, 에이전트 등록 관리, CPU/GPU VM 기반 AI 응용 배포·제어 판단을 하나의 기능 프로토타입으로 검증합니다.

현재 프로토타입은 다음 기능을 제공합니다.

- Ops 분석 시험 및 최적 LLM 선정 흐름
- AI LLM 운영 관리 구조 검증
- AI 에이전트 등록 관리 및 bounded action 검증
- CPU/GPU VM 기반 AI 응용 추론 배치 추천
- AI 응용 배포·제어 계획 생성
- 서비스 운영 준비도 통합 검증

이 저장소는 운영 환경에 바로 투입하는 완성형 AIOps 플랫폼이 아닙니다. 기본 검증 경로는 로컬 Go 실행과 mock/dry-run 검증을 중심으로 하며, 실제 AWS GPU VM 생성과 CB-Tumblebug 연동은 AI-Infra 환경이 준비된 뒤 확장하는 영역입니다.

## 🧩 프로토타입 범위

`config/ops_llm_benchmark.json`의 LLM 정책 값은 1차년도 기능 검증을 위한 수동 정의 기준값입니다. 최종 표준 LLM 벤치마크 결과가 아니며, 정량 보고를 위해서는 고정 프롬프트, 고정 데이터셋, 반복 가능한 지표 수집, 점수 산정 규칙을 포함한 별도 평가가 필요합니다.

기본 실행 모드는 `mock`입니다. 로컬 환경에서는 Go CLI/API와 Docker Desktop 기반 Kubernetes dry-run으로 기능 흐름을 검증할 수 있습니다. 실제 GPU VM 프로비저닝, 운영 클러스터 변경, CB-Tumblebug 기반 AWS GPU VM 생성은 기본 로컬 검증 범위 밖입니다.

## 🗂️ 저장소 구조

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM 선정, 에이전트 검증, CPU/GPU 배치 추천, 배포 계획 생성, 서비스 운영 준비도 검증을 수행하는 Go Echo API/CLI |
| [`go/aiops-guard/`](go/aiops-guard/) | 서비스 제어 action의 허용 범위를 검증하는 독립 Go 안전 게이트 |
| [`config/`](config/) | LLM 정책 후보, 에이전트 registry, CPU/GPU VM 배치 정책 JSON 설정 |
| [`docs/deliverables/`](docs/deliverables/) | 공식 설계 산출물 Markdown 원본과 DOCX 변환본 |
| [`docs/design/`](docs/design/) | 구현 수준의 보조 설계 문서 |
| [`docs/submission/`](docs/submission/) | 요구사항 정의서, 기능/API 가이드, OpenAPI 계약, 설치/실행 가이드, 테스트 가이드, 검증 기록 |

## 📦 제출 산출물

| 산출물 | 형식 | 저장소 경로 |
| --- | --- | --- | 
| 요구사항 정의서 원본 | `.md` | [`docs/submission/requirements_definition.md`](docs/submission/requirements_definition.md) 
| 요구사항 정의서 제출본 | `.docx` | [`docs/submission/requirements_definition.docx`](docs/submission/requirements_definition.docx) 
| 기능/API 가이드 | `.md` | [`docs/submission/functional_api_guide.md`](docs/submission/functional_api_guide.md) 
| Swagger/OpenAPI 계약 | `.yaml` | [`docs/submission/openapi_service_control.yaml`](docs/submission/openapi_service_control.yaml) 
| 설치 및 실행 가이드 | `.md` | [`docs/submission/install_and_run_guide.md`](docs/submission/install_and_run_guide.md) 
| 테스트 가이드 | `.md` | [`docs/submission/test_guide.md`](docs/submission/test_guide.md) 

## 📝 공식 설계 산출물

| 번호 | 설계 산출물 | Markdown 원본 | DOCX 제출본 |
| --- | --- | --- | --- |
| 1 | LLM 운영 관리 구조 설계서 | [원본 보기](docs/deliverables/01_llm_operation_management_design.md) | [DOCX 열기](docs/deliverables/docx/01_LLM_Operation_Management_Design.docx) |
| 2 | 에이전트 등록 관리 프로토타입 | [원본 보기](docs/deliverables/02_agent_registration_management_prototype.md) | [DOCX 열기](docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx) |
| 3 | AI 응용 배포·제어 추론 최적화 전략 설계서 | [원본 보기](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) | [DOCX 열기](docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx) |

Markdown 파일이 공식 원본이며, DOCX 파일은 제출/검토용 변환본입니다.

## 🧪 개발 검증 문서

| 문서 | 저장소 경로 | 목적 |
| --- | --- | --- | --- |
| LLM/코딩 에이전트 교차 검증 기록 |  [`docs/submission/coding_agent_cross_validation.md`](docs/submission/coding_agent_cross_validation.md) | 2종 이상 LLM/코딩 에이전트 역할과 교차 검증 절차 기록 |
| 프롬프트 사용 기록 |  [`docs/submission/prompt_usage_log.md`](docs/submission/prompt_usage_log.md) | 대표 프레임워크 프롬프트와 공유 정책 기록 |
| 개발 검증 로그 |  [`docs/submission/development_validation_log.md`](docs/submission/development_validation_log.md) | 검증 명령, 기대 출력, 로그 정책, 사람 검토 항목 기록 |

## 🚀 프로토타입 실행

통합 검증을 실행합니다.

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/my-first-validation
```

기대 신호는 다음과 같습니다.

```text
valid = true
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
guard_backend = go
guard_validation.valid = true
```

API 서버 실행:

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

다른 터미널에서 통합 API 호출:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## 📄 DOCX 변환본

DOCX 제출본은 이미 `docs/submission/`과 `docs/deliverables/docx/`에 포함되어 있습니다. 재생성이 필요한 경우 [`docs/submission/install_and_run_guide.md`](docs/submission/install_and_run_guide.md)와 [`scripts/generate_docx_deliverables.sh`](scripts/generate_docx_deliverables.sh)를 참고합니다.

## 📚 참고 문서

| 문서 | 설명 |
| --- | --- |
| [핵심 제출 요약](docs/core_submission_summary.md) | 패키지 범위와 산출물 매핑 |
| [기능/API 가이드](docs/submission/functional_api_guide.md) | HTTP API 실행과 응답 구조 |
| [OpenAPI 계약](docs/submission/openapi_service_control.yaml) | Swagger/OpenAPI 산출물 |
| [설치 및 실행 가이드](docs/submission/install_and_run_guide.md) | Go CLI/API 실행 절차 |
| [테스트 가이드](docs/submission/test_guide.md) | Go 테스트와 team-validation 절차 |
| [평가 요약](docs/submission/evaluation_summary.md) | 기능 프로토타입 평가 범위 |

## 🛠️ 개발 환경

- 개발 언어: Go
- Go 기준 버전: Go 1.25
- 백엔드 프레임워크: Echo
- 소스 코드 관리: GitHub
- 라이선스: Apache 2.0

두 Go 모듈은 `go mod tidy` 기준으로 Go 1.25 계열에 맞춰져 있습니다.

## License

ai-ops는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
