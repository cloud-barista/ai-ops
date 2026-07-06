# 🏛️ Kyung Hee AIOps 🦁

> AI 기반 서비스 제어 및 관리 자동화 프레임워크
> 1차년도 Go 기반 service-control prototype

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 🧭 개요

이 저장소는 경희대학교 1차년도 연구 범위 중 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**를 위한 제출용/시연용 패키지입니다.

핵심 구현은 Go 언어로 구성되어 있으며, Ops 분석 기반 LLM 선정, AI 에이전트 등록 관리, CPU/GPU VM 기반 AI 응용 배포·제어 판단을 하나의 service-control prototype으로 검증합니다.

## 🎯 담당 범위

- Ops 분석 시험 및 최적 LLM 선정 흐름
- AI LLM 운영 관리 구조 설계 및 검증
- AI 에이전트 등록 관리와 bounded action 검증
- CPU/GPU VM 기반 AI 응용 추론 배치 추천
- AI 응용 배포·제어 계획 생성

이 저장소는 실제 인프라 생성이나 운영 배포 완료를 직접 주장하지 않습니다. 실제 VM, GPU, Kubernetes 적용 결과는 외부 인프라/배포 계층의 실행 결과와 구분합니다.

## 🗂️ 코드 구조

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM 선정, Agent registry, CPU/GPU 배치, 배포·제어 계획을 수행하는 Go API/CLI |
| [`go/aiops-guard/`](go/aiops-guard/) | 서비스 제어 action의 허용 범위를 검증하는 Go guard |
| [`config/`](config/) | LLM 후보, 에이전트 registry, CPU/GPU 배치 정책 설정 |
| [`data/`](data/) | Ops LLM 평가 scenario |
| [`docs/`](docs/) | 산출물, 실행 가이드, 검증 문서, 구조도 |
| [`examples/`](examples/) | API 요청/응답 예제 |

## 📦 공식 산출물

| 산출물 | 원본 | DOCX |
| --- | --- | --- |
| 요구사항 정의서 | [Markdown](docs/submission/requirements_definition.md) | [DOCX](docs/submission/requirements_definition.docx) |
| LLM 운영 관리 구조 설계서 | [Markdown](docs/deliverables/01_llm_operation_management_design.md) | [DOCX](docs/deliverables/docx/01_LLM_Operation_Management_Design.docx) |
| 에이전트 등록 관리 프로토타입 | [Markdown](docs/deliverables/02_agent_registration_management_prototype.md) | [DOCX](docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [Markdown](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) | [DOCX](docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx) |

## 📚 문서 바로가기

| 문서 | 설명 |
| --- | --- |
| [문서 지도](docs/README.md) | 전체 문서와 산출물 진입점 |
| [설치 및 실행 가이드](docs/submission/install_and_run_guide.md) | 로컬/VM 실행 절차 |
| [테스트 가이드](docs/submission/test_guide.md) | Go 테스트와 검증 명령 |
| [기능/API 가이드](docs/submission/functional_api_guide.md) | API 기능과 응답 구조 |
| [OpenAPI 계약](docs/submission/openapi_service_control.yaml) | Swagger/OpenAPI 산출물 |
| [Ops LLM 평가 방법](docs/submission/ops_llm_benchmark_method.md) | dry-run과 실제 endpoint 실행 기준 |
| [검증 증적 가이드](docs/evidence/증적_패키지_가이드.md) | 실행 결과와 증적 정리 기준 |

## 🛠️ 개발 환경

- 개발 언어: Go
- Go 기준 버전: Go 1.25+
- 백엔드 프레임워크: Echo
- 소스 코드 관리: GitHub
- 라이선스: Apache 2.0

## License

ai-ops는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
