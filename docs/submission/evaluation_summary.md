# 평가 요약

## 1. 범위

이 문서는 1차년도 Go 기반 service-control prototype의 현재 기능 평가 항목을 요약합니다. 평가는 prototype behavior와 integration readiness를 보여주기 위한 것입니다. 최종 production performance benchmark나 final standardized LLM benchmark quality를 주장하지 않습니다.

## 2. 평가 항목

| 항목 | 검증 방법 | 현재 증거 유형 |
| --- | --- | --- |
| Ops LLM selection policy prototype | `select-ops-llm`, `team-validation` | policy 기반 candidate ranking output |
| Ops LLM evaluation dry-run | `run-ops-llm-benchmark`, `evaluate-ops-llm-outputs` | Go 기반 scenario/candidate 연결 및 dry-run evaluation summary |
| Agent registry 및 bounded-action validation | `list-agents`, `show-agent`, `validate-agent-action` | registered agents와 allowed action check |
| CPU/GPU VM placement recommendation | `recommend-inference-placement` | selected resource와 rejected-resource explanation |
| AI 응용 배포·제어 계획 생성 | `plan-inference-deployment` | namespace, deployment, node selector, resource limit, control action 생성 결과 |
| Mock dry-run 및 guard validation | `run-service-operations` | manifest dry-run output과 `guard_validation.valid = true` |
| Go unit test | 각 Go module의 `go test ./...` | module-level test pass/fail output |
| Integrated readiness | `team-validation` | `runs/<output-dir>/` 아래 JSON output files |

## 3. 기대 프로토타입 신호

```text
selected_model = primary-ops-llm
selected_actual_model = to-be-evaluated-primary-model
benchmark_status = not_executed
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

이 신호는 현재 prototype의 functional wiring을 확인합니다. production performance, actual cloud provisioning, final model quality를 증명하지 않습니다.

## 4. 산출물 관계

| 평가 영역 | 관련 산출물 |
| --- | --- |
| LLM policy selection | `docs/deliverables/01_llm_operation_management_design.md` |
| Agent registry validation | `docs/deliverables/02_agent_registration_management_prototype.md` |
| CPU/GPU placement 및 deployment-control plan | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` |
| API behavior | `docs/submission/functional_api_guide.md`, `docs/submission/openapi_service_control.yaml` |
| Test procedure | `docs/submission/test_guide.md` |
| Development validation records | `docs/submission/development_validation_log.md` |

## 5. Benchmark 경계

현재 LLM policy score는 `config/ops_llm_benchmark.json`에 수동 정의된 prototype baseline입니다. Go selection flow의 기능 검증 input으로 해석해야 합니다.

Go 기반 dry-run/evaluator는 다음 파일을 사용합니다.

| 파일 | 의미 |
| --- | --- |
| `data/ops_llm_eval_scenarios.jsonl` | project-specific Ops LLM scenario set |
| `config/ops_llm_eval_candidates.json` | role label과 future actual model 후보 연결 |
| `runs/ops-llm-evaluation-dry-run/model_outputs.jsonl` | dry-run output evidence |
| `runs/ops-llm-evaluation-dry-run/evaluation_summary.json` | dry-run evaluation summary |

dry-run의 `benchmark_status`는 `dry_run`이며, 실제 LLM API benchmark 결과가 아닙니다. 최종 정량 보고를 위해서는 통제된 per-model Ops evaluation run, 고정 prompt, 고정 dataset, 반복 가능한 metric, 문서화된 scoring rule이 필요합니다.

## 6. Infrastructure 경계

기본 service-control path는 mock validation을 사용하며 live cluster를 변경하지 않습니다. 실제 GPU VM provisioning은 AI-Infra 또는 CB-Tumblebug integration boundary입니다. prototype은 placement recommendation과 deployment plan을 생성하지만, 기본 로컬 검증 경로에서 actual GPU VM creation을 주장하지 않습니다.

## 7. 한계

- LLM policy score는 수동 정의된 prototype baseline입니다.
- 현재 package는 standardized LLM benchmark result를 주장하지 않습니다.
- Ops LLM dry-run은 provider API를 호출하지 않습니다.
- 별도 Python 기반 실험 runner는 제출용 핵심 구현에 포함하지 않습니다.
- 기본 service-control path는 mock validation을 사용합니다.
- `aiops-guard`는 standalone module로 구현되어 있으며, `service-control-api`에서의 full runtime invocation은 다음 integration step입니다.
- actual GPU VM provisioning과 live cluster scheduling에는 external infrastructure와 credential이 필요합니다.
