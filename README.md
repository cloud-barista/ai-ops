# Kyunghee AIOps Go

AI 기반 서비스 제어 및 관리 자동화 프레임워크의 Go 기반 초기 프로토타입입니다.

## 개요

본 프로젝트는 AI LLM 운영관리와 AI 응용 자동화 에이전트 구조를 Go 기반으로
구현하기 위한 초기 버전입니다.

주요 구현 범위는 다음과 같습니다.

- Ops 분석 및 최적 LLM 선정
- AI LLM 운영관리 구조 설계
- AI 에이전트 등록관리 프로토타입
- CPU/GPU VM 기반 AI 응용 배포/제어 추론 최적화 전략
- 서비스 제어 action 검증을 위한 Go 기반 guard 구조

## 구성

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM 선정, Agent registry, CPU/GPU VM 배치, 운영관리 pipeline 구현 |
| [`go/aiops-guard/`](go/aiops-guard/) | 서비스 제어 action 안전성 검증 구현 |
| [`config/`](config/) | LLM 후보, Agent registry, CPU/GPU VM 정책 설정 |
| [`docs/`](docs/) | 설계 개요, 제출 문서, 실행/검증 가이드 |

## 참고 문서

| 문서 | 내용 |
| --- | --- |
| [전체 구현 범위 요약](docs/core_submission_summary.md) | 담당 구현 범위와 산출물 매핑 |
| [연구 항목과 Go 구현 구조 매핑](docs/design/research_task_integration_design.md) | 제안서 항목과 Go 구현의 연결 관계 |
| [LLM 선정 구조](docs/design/ops_llm_selection_guide.md) | Ops 분석 및 최적 LLM 선정 방식 |
| [Agent 등록관리 구조](docs/design/agent_registry_guide.md) | Agent registry와 bounded action 관리 |
| [CPU/GPU VM 기반 추론 최적화 구조](docs/design/inference_optimization_guide.md) | CPU/GPU VM 배치 추천 정책 |
| [실행 방법](docs/submission/install_and_run_guide.md) | Go API/CLI 실행 방법 |
| [검증 방법](docs/submission/test_guide.md) | Go 테스트 및 team-validation 절차 |

## 개발 언어

핵심 실행 로직은 Go로 구성되어 있으며, JSON 설정과 Markdown 문서는 보조
산출물로 사용합니다.
