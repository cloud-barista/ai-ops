# 🏛️ Kyung Hee AIOps 🦁

> AI 기반 서비스 제어 및 관리 자동화 프레임워크
> 1차년도 Go 기반 service-control prototype

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 🧭 개요

이 저장소는 경희대학교 1차년도 연구 범위 중 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**를 위한 제출용/시연용 패키지입니다.

핵심 구현은 Go 언어로 구성되어 있습니다. Go Request Guard가 자연어 요청의 권한·1차년도 VM 범위·민감정보 유입을 먼저 검사하고, 실제 LLM endpoint가 승인된 요구를 AppDeploy `DeploymentManifest`로 변환합니다. 이어 Go Manifest Guard가 계약·자원 값·보안 정책을 검증한 뒤 AppDeploy에 전달합니다. AppDeploy가 실제 Target과 Runtime Adapter를 선택하며, 본 프로젝트는 배포 상태와 로그를 조회해 결과를 반환합니다.

현재 기본 Planner 모델은 **Qwen 3.5 4B (`qwen3.5:4b`)**입니다. Go 구현은 OpenAI-compatible endpoint 계약을 사용하므로 Ollama, vLLM 또는 연구 서버는 Qwen을 제공하는 실행 런타임으로 교체할 수 있습니다. 약 3.4GB의 Ollama 양자화 모델을 사용해 로컬과 AWS NVIDIA L4 24GB VM에서 같은 Planner 설정을 검증합니다.

## 🎯 담당 범위

- Ops 분석 시험 및 최적 LLM 선정 흐름
- AI LLM 운영 관리 구조 설계 및 검증
- `AIApplicationAutomationAgent`를 LLM Deployment Planner로 등록·관리
- 자연어 App 요구 분석과 CPU·메모리·GPU·디스크·accelerator 요구량 결정
- AppDeploy 공식 Deployment Manifest 생성과 요청·Manifest 이중 Go Guard 검증
- 승인된 Manifest의 AppDeploy 전달, 배포 상태 polling, 로그 조회와 재시도 가능 여부 판단
- 인프라 계층이 제공한 실제 CPU/GPU VM snapshot과 workload 요구사항의 보조 적합성 검증

이 저장소는 VM 후보를 임의로 만들거나 VM을 직접 프로비저닝하지 않습니다. 실제 인프라 생성은 인프라 계층이, App Spec 조회·Target 선택·Adapter 선택·배포 실행은 AppDeploy가 담당합니다. LLM 또는 AppDeploy 호출 실패를 가짜 성공 결과로 대체하지 않습니다.

## 🗂️ 코드 구조

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM Deployment Planner, Agent Registry, 이중 Go Guard, AppDeploy 연계를 제공하는 Go API/CLI |
| [`contracts/appdeploy/`](contracts/appdeploy/) | 최신 Target 자동 선택 방식과 호환되는 AppDeploy Deployment Manifest 계약 snapshot |
| [`go/aiops-guard/`](go/aiops-guard/) | 서비스 제어 action의 허용 범위를 검증하는 Go guard |
| [`config/`](config/) | LLM 후보, 에이전트 registry, workload별 VM 요구사항 설정 |
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
| [LLM Deployment Planner·Go Guard 흐름](docs/design/main_llm_go_guard_control_flow.md) | 자연어 요구 분석, Manifest 검증, AppDeploy 전달과 상태 조회 구조 |
| [플래너·AppDeploy 책임 경계](docs/design/integration_boundary.md) | 최신 AppDeploy 계약, 통합 준비 순서와 Planner·배포 실행 책임 구분 |
| [Ops LLM 평가 방법](docs/submission/ops_llm_benchmark_method.md) | dry-run과 실제 endpoint 실행 기준 |
| [1차년도 VM 통합·개별 동작 시나리오](docs/design/year1_vm_operation_scenarios.md) | 컨테이너를 제외한 VM-only 통합 흐름과 개별 시험 초안 |
| [검증 증적 가이드](docs/evidence/증적_패키지_가이드.md) | 실행 결과와 증적 정리 기준 |
| [AWS GPU VM 검증 결과](docs/evidence/vm_validation_20260707.md) | `validate-system --target vm` 실행 결과와 GPU 증적 |

## 🛠️ 개발 환경

- 개발 언어: Go
- Go 기준 버전: Go 1.25+
- 검증 기준: `geon` 브랜치는 Go 1.25 기준으로 검증됨
- 백엔드 프레임워크: Echo
- 소스 코드 관리: GitHub
- 라이선스: Apache 2.0

## License

ai-ops는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
