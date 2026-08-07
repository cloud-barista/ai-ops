# 제출 패키지 매니페스트

이 패키지는 Go 기반 AI 서비스 제어 및 관리 자동화 기능 프로토타입을 제출/시연하기 위한 구성입니다.

## 포함 소스 구성요소

| 경로 | 설명 |
| --- | --- |
| `go/service-control-api/` | LLM Deployment Manifest 생성, 요청·Manifest 이중 Go Guard, AppDeploy 상태 추적과 Agent Registry를 수행하는 Go Echo API/CLI |
| `contracts/appdeploy/deployment_manifest.schema.json` | Planner 연계에 사용하는 AppDeploy Manifest 계약 snapshot |
| `go/aiops-guard/` | 서비스 제어 action을 허용 범위 안에서 검증하는 독립 Go 안전 게이트 |
| `config/agent_registry.json` | 에이전트 registry와 bounded action 메타데이터 |
| `config/planner_guard_policy.json` | LLM 호출 전 요청자·VM 범위·민감 파라미터를 검사하는 Go Request Guard 정책 |
| `config/ops_llm_benchmark.json` | 수동 정의된 프로토타입 LLM 정책 기준값과 선정 가중치 |
| `config/ops_llm_eval_candidates.local_ollama.json` | Qwen 3.5 4B 로컬·VM 공통 OpenAI-compatible 실행 설정 |
| `config/ops_llm_eval_candidates.openai_compatible.example.json` | OpenAI-compatible provider 교체 예시 |
| `config/vm_workload_requirements.json` | 실제 CPU/GPU VM 적합성 검증을 위한 workload 요구사항 |
| `docs/evidence/artifacts/vm_20260707_resource_snapshot.json` | redacted 실제 GPU VM resource snapshot 예시 |
| `docs/evidence/artifacts/local_20260716_llm_automation_action.json` | 보조 bounded Action API의 실제 로컬 LLM 제안과 Go Guard 검증 결과 |
| `docs/evidence/artifacts/local_20260721_qwen35_ops_llm_evaluation_summary.json` | Qwen 3.5 4B 로컬 Ops 시나리오 실제 실행 요약 |
| `data/ops_llm_eval_scenarios.jsonl` | Ops LLM 평가 scenario set |
| `examples/requests/plan-llm-automation-action.json` | 보조 bounded Action API 요청 예시 |
| `examples/responses/plan-llm-automation-action-success.json` | 보조 bounded Action 검증 응답 예시 |
| `examples/requests/run-appdeploy-planner.json` | LLM Deployment Planner API 요청 예시 |
| `examples/responses/run-appdeploy-planner-success.json` | AppDeploy 상태·로그를 포함한 Planner 응답 예시 |
| `examples/appdeploy/deployment-create-request.json` | Planner가 최신 AppDeploy에 전달하는 Manifest handoff 예시 |
| `examples/appdeploy/deployment-response.json` | AppDeploy가 선택한 실제 Target과 배포 상태 응답 예시 |
| `go/service-control-api/internal/llmop/` | 자연어 Safeguard review, bounded Manifest Proposal, 결정적 guard와 prepare-only AppDeploy handoff 선행 PoC |
| `go/service-control-api/internal/llmopbridge/` | geon Common JSON의 supported single-node subset을 LLM_Op 계약으로 투영하는 bridge |
| `go/service-control-api/cmd/llmop-demo/` | 실제 API·model weight·AppDeploy POST가 없는 offline fixture demo CLI |
| `examples/llm-op/` | AI 서비스 metadata, 고정 Qwen intended-model binding, golden fixture, 47개 정적 scenario catalog |
| go/service-control-api/internal/webui/static/llm_op_demo* | 두 단계 prompt 복사·raw JSON 붙여넣기·대표 8개 흐름을 제공하는 no-call 브라우저 lab |
| schemas/llm-op/ | Safeguard review와 bounded Manifest Proposal의 JSON shape 계약 |

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
| `docs/evidence/vm_validation_20260707.md` | AWS GPU VM 기반 `validate-system --target vm` 검증 결과 |
| `docs/evidence/artifacts/local_20260707_ops_llm_evaluation_summary.json` | 실제 LLM endpoint Ops 평가 결과 |
| `docs/release/1차년도_제출_패키지_체크리스트.md` | 제출 전 점검표 |
| `docs/ops/로그_에러_가이드.md` | 상태값과 오류 메시지 해석 기준 |
| `docs/llm-op/` | LLM_Op 계약, 완성도 감사, Safeguard/policy LLM 가이드, 수동 브라우저 시연과 상태 Adapter 계약 |

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
| `docs/design/llm_provider_abstraction.md` | LLM provider abstraction과 candidate config 경계 |
| `docs/design/integration_boundary.md` | service-control과 연계 프레임워크 책임 경계 |
| `docs/team_setup.md` | 팀 단위 개발 환경 설정 참고 문서 |
| `docs/diagrams/` | Mermaid 구조도 원본 |
| `docs/images/` | README와 산출물 문서에 삽입되는 PNG 구조도 및 수정용 SVG 구조도 |
| `examples/requests/` | API 시연용 request JSON |
| `examples/responses/` | API 시연용 response JSON |

## 변환 도구

| 경로 | 설명 |
| --- | --- |
| `scripts/generate_docx_deliverables.sh` | 변환 도구가 준비된 환경에서 Markdown 산출물을 DOCX 제출본으로 변환 |
| `scripts/validate-llmop-demo-data.ps1` | offline 실행 불변식, AI 서비스/model/scenario 교차참조 정적 검증 |

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
