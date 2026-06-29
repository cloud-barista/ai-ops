# 서비스 제어 프레임워크 매핑

## 연구 범위 매핑

| 연구 범위 | 공식 설계 산출물 | Go 구현 |
| --- | --- | --- |
| AI LLM 운영 관리 설계 | `docs/deliverables/01_llm_operation_management_design.md` | Ops LLM policy ranking 및 runtime candidate selection |
| AI 에이전트 등록 관리 | `docs/deliverables/02_agent_registration_management_prototype.md` | Agent registry와 bounded-action validation |
| AI 응용 자동화 에이전트 설계 | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | application, infrastructure, cost review output |
| CPU/GPU VM 기반 AI 응용 배포·제어 | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | placement recommendation 및 deployment-plan generation |
| 안전 검증 | `docs/submission/test_guide.md` | standalone `aiops-guard`와 service-control guard-readiness response |

## Pipeline

```text
config/ops_llm_benchmark.json
-> select-ops-llm
-> config/agent_registry.json
-> validate-agent-action
-> config/inference_optimization.json
-> recommend-inference-placement
-> plan-inference-deployment
-> run-service-operations
```

## 안전 경계

Go layer는 service operation이 ready로 판단되기 전에 선택 action과 deployment plan을 검증합니다. 기본 team validation은 `mock` mode를 사용하며 cluster credential을 요구하지 않습니다.

`aiops-guard`는 standalone bounded-action validator로 유지됩니다. `service-control-api`와 `aiops-guard` 사이의 full runtime wiring은 다음 단계의 integration item입니다.

이 매핑은 1차년도 기능 prototype mapping입니다. production readiness, final standardized LLM benchmark completion, 기본 검증 경로에서의 actual GPU VM provisioning을 주장하지 않습니다.
