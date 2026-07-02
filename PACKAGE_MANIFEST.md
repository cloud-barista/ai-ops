# 제출 패키지 매니페스트

이 패키지는 Go 기반 AI 서비스 제어 및 관리 자동화 기능 프로토타입을 제출/시연하기 위한 구성입니다.

## 포함 소스 구성요소

| 경로 | 설명 |
| --- | --- |
| `go/service-control-api/` | LLM 정책 선정, 에이전트 registry 검증, CPU/GPU 배치 추천, 배포 계획 생성, 서비스 운영 준비도 검증을 수행하는 Go Echo API/CLI |
| `go/aiops-guard/` | 서비스 제어 action을 허용 범위 안에서 검증하는 독립 Go 안전 게이트 |
| `config/agent_registry.json` | 에이전트 registry와 bounded action 메타데이터 |
| `config/ops_llm_benchmark.json` | 수동 정의된 프로토타입 LLM 정책 기준값과 선정 가중치 |
| `config/inference_optimization.json` | CPU/GPU VM 자원 프로파일과 워크로드 요구사항 |
| `data/ops_llm_eval_scenarios.jsonl` | Ops LLM 평가 scenario set |

## 필수 제출 산출물

| 경로 | 설명 |
| --- | --- |
| `docs/README.md` | 제출/시연 문서 지도 |
| `docs/submission/requirements_definition.md` | 요구사항 정의서 원본 |
| `docs/submission/requirements_definition.docx` | 요구사항 정의서 제출/검토용 변환본 |
| `docs/submission/functional_api_guide.md` | 기능/API 가이드 |
| `docs/submission/openapi_service_control.yaml` | Swagger/OpenAPI 계약 |
| `docs/submission/install_and_run_guide.md` | 설치 및 실행 가이드 |
| `docs/submission/test_guide.md` | 테스트 가이드 |
| `docs/evidence/증적_패키지_가이드.md` | 검증 증적 구성 기준 |
| `docs/release/1차년도_제출_패키지_체크리스트.md` | 제출 전 점검표 |
| `docs/ops/로그_에러_가이드.md` | 상태값과 오류 메시지 해석 기준 |

## 공식 설계 산출물

| 경로 | 설명 |
| --- | --- |
| `docs/deliverables/01_llm_operation_management_design.md` | LLM 운영 관리 구조 설계서 원본 |
| `docs/deliverables/02_agent_registration_management_prototype.md` | 에이전트 등록 관리 프로토타입 원본 |
| `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | AI 응용 배포·제어 추론 최적화 전략 설계서 원본 |
| `docs/deliverables/docx/01_LLM_Operation_Management_Design.docx` | DOCX 제출/검토용 변환본 |
| `docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx` | DOCX 제출/검토용 변환본 |
| `docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx` | DOCX 제출/검토용 변환본 |

## 개발 검증 문서

| 경로 | 설명 |
| --- | --- |
| `docs/submission/coding_agent_cross_validation.md` | LLM/코딩 에이전트 역할과 교차 검증 절차 기록 |
| `docs/submission/prompt_usage_log.md` | 정리된 프롬프트 범주와 공유 정책 |
| `docs/submission/development_validation_log.md` | 검증 명령, 기대 출력, 로그 정책, 사람 검토 항목 |
| `docs/submission/evaluation_summary.md` | 기능 프로토타입 평가 요약 |
| `docs/core_submission_summary.md` | 전체 패키지 범위와 산출물 매핑 |

## 보조 설계 문서

| 경로 | 설명 |
| --- | --- |
| `docs/design/` | 구현 수준의 보조 설계 문서 |
| `docs/team_setup.md` | 팀 단위 개발 환경 설정 참고 문서 |
| `docs/diagrams/` | Mermaid 구조도 원본 |
| `docs/images/` | README와 산출물 문서에 삽입되는 PNG 구조도 및 수정용 SVG 구조도 |
| `examples/requests/` | API 시연용 request JSON |
| `examples/responses/` | API 시연용 response JSON |

## 변환 도구

| 경로 | 설명 |
| --- | --- |
| `scripts/generate_docx_deliverables.sh` | 변환 도구가 준비된 환경에서 Markdown 산출물을 DOCX 제출본으로 변환 |

## 제외 항목

| 제외 항목 | 사유 |
| --- | --- |
| 핵심 범위 밖 legacy 코드와 테스트 | 제출/시연 패키지를 Go 중심 범위로 유지하기 위함 |
| 외부 벤치마크/오케스트레이션 실험 통합 | 담당 산출물 범위를 흐릴 수 있는 실험 경로 |
| 특정 provider 전용 모니터링 adapter | provider 중립적인 Ops 입력/설정을 우선 적용 |
| 로컬 클러스터 실험 manifest와 helper 도구 | 환경 의존 검증 자료이며 핵심 산출물이 아님 |
| `runs/` | 로컬 실행 결과와 검증 출력물 |
| 가상환경, cache, build 산출물 | 로컬 생성 파일 |
| `.env`, kubeconfig, API key | 민감한 로컬 credential |

## 제출 범위 요약

```text
LLM 운영 관리 구조 설계서,
에이전트 등록 관리 프로토타입,
AI 응용 배포·제어 추론 최적화 전략 설계서,
Go 기반 API/CLI 구현 및 기능 검증
```

## 경계 조건

이 저장소는 1차년도 기능 프로토타입입니다. 운영 환경용 완성형 AIOps 플랫폼이 아니며, LLM 정책 값은 수동 정의된 프로토타입 기준값입니다. 실제 GPU VM 프로비저닝은 기본 로컬 검증 범위 밖입니다.
