# 핵심 제출 요약

## 1. 범위

이 저장소는 1차년도 Go 기반 기능 프로토타입으로 패키징되었습니다. 대상 연구 범위는 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**이며, 대학원 연구 검토와 외부 협력 검토를 모두 고려해 구성했습니다.

프로토타입은 다음 기능 동작을 검증합니다.

- Ops 분석 시험 및 최적 LLM 선정 정책 흐름
- AI LLM 운영 관리 구조 검증
- AI 에이전트 등록 및 bounded action 검증
- CPU/GPU VM 배치 추천
- AI 응용 배포·제어 계획 생성
- mock 서비스 운영 준비도 보고

본 패키지는 운영 환경 투입을 위한 완성형 시스템이 아니며, 최종 표준 LLM 벤치마크 결과를 주장하지 않습니다.

## 2. 연구 범위 매핑

| 연구 항목 | 구현 산출물 |
| --- | --- |
| Ops 분석 시험 및 최적 LLM 선정 | `config/ops_llm_benchmark.json`을 사용하는 Go API/CLI 정책 선정 흐름 |
| Ops LLM 평가 확장 구조 | `data/ops_llm_eval_scenarios.jsonl`, `config/ops_llm_eval_candidates.json`, Go dry-run/evaluator CLI |
| AI LLM 운영 관리 구조 | 통합 Go 서비스 운영 준비도 pipeline |
| AI 에이전트 등록 관리 프로토타입 | 에이전트 registry 설정과 Go list/show/validate action |
| CPU/GPU VM 기반 AI 응용 배포·제어 전략 | Go CPU/GPU 배치 추천과 AI 응용 배포·제어 계획 생성 |
| 안전 검증 경계 | 독립 Go `aiops-guard` 계약과 service-control guard 준비도 출력 |

## 3. 필수 제출 산출물

| 산출물 | 경로 |
| --- | --- |
| 요구사항 정의서 원본 | `docs/submission/requirements_definition.md` |
| 요구사항 정의서 DOCX 변환본 | `docs/submission/requirements_definition.docx` |
| 기능/API 가이드 | `docs/submission/functional_api_guide.md` |
| Swagger/OpenAPI 계약 | `docs/submission/openapi_service_control.yaml` |
| 설치 및 실행 가이드 | `docs/submission/install_and_run_guide.md` |
| 테스트 가이드 | `docs/submission/test_guide.md` |
| Ops LLM 평가 방법 | `docs/submission/ops_llm_benchmark_method.md` |

Markdown과 YAML 파일은 원본 산출물입니다. DOCX 파일은 Markdown 원본에서 만든 제출/검토용 변환본입니다.

## 4. 공식 설계 산출물

| 설계 산출물 | Markdown 원본 | DOCX 변환본 |
| --- | --- | --- |
| LLM 운영 관리 구조 설계서 | `docs/deliverables/01_llm_operation_management_design.md` | `docs/deliverables/docx/01_LLM_Operation_Management_Design.docx` |
| 에이전트 등록 관리 프로토타입 | `docs/deliverables/02_agent_registration_management_prototype.md` | `docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx` |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | `docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx` |

`docs/design` 디렉터리는 구현 수준의 보조 설계 문서입니다. 공식 1:1 설계 산출물 원본은 `docs/deliverables`에 있습니다.

## 5. 개발 검증 산출물

| 산출물 | 경로 |
| --- | --- |
| LLM/코딩 에이전트 교차 검증 기록 | `docs/submission/coding_agent_cross_validation.md` |
| 프롬프트 사용 및 공유 기록 | `docs/submission/prompt_usage_log.md` |
| 개발/테스트 검증 로그 | `docs/submission/development_validation_log.md` |
| 기능 평가 요약 | `docs/submission/evaluation_summary.md` |
| Ops LLM dry-run/evaluator 방법 | `docs/submission/ops_llm_benchmark_method.md` |

## 6. 패키지 경계

제출 패키지는 Go API/CLI, Go guard, 핵심 JSON 설정, 제출/설계 문서를 포함합니다. 핵심 범위 밖 legacy 모듈, 외부 실험 runner, 로컬 환경 script, 로컬 실행 결과는 source package 경계 밖에 둡니다.

LLM 선정 값은 수동 정의된 프로토타입 정책 기준값입니다. 최종 표준 벤치마크 결과가 아니며, 정량 보고 전에는 통제된 per-model Ops 평가를 통해 재생성해야 합니다.

Ops LLM dry-run/evaluator는 Go로 구현되어 있으며, 기본 상태에서는 실제 provider API를 호출하지 않습니다.

실제 GPU VM 프로비저닝은 AI-Infra 또는 CB-Tumblebug 연동 경계에 있습니다. 기본 로컬 검증 경로는 mock 실행을 사용합니다.

## 7. 검증

일반 검증은 Go 테스트와 Go CLI로 수행합니다.

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
go run ./cmd/aiops-service-control team-validation
```

기대되는 프로토타입 수준의 신호는 다음과 같습니다.

```text
team-validation valid = true
selected_model = primary-ops-llm
selected_actual_model = to-be-evaluated-primary-model
benchmark_status = not_executed
selected_resource = gpu-vm-l4
run-service-operations valid = true
guard_backend = go
guard_validation.valid = true
```
