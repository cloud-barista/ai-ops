# 평가 요약

| 평가 항목 | 명령·API | 판정 기준 |
| --- | --- | --- |
| Ops LLM 선정 | `select-ops-llm` | model/provider/score/status 분리 |
| 실제 LLM Action | `plan-llm-automation-action` | actual model, latency, bounded Action, 실패 상태 보존 |
| 에이전트 등록 | `/api/v1/agents` | capability와 bounded Action 보존 |
| VM 적합성 | `validate-vm-suitability` | 실제 snapshot의 조건별 pass/fail |
| 제어 계획 | `plan-ai-application-control` | 범용 executor capability와 `not_executed` |
| 통합 검증 | `validate-system` | Go test, API, VM 증적과 단계별 결과 |

## 판정 신호

```text
resource_checks_passed = true
compatibility_status = provisionally_compatible | compatible
performance_status = not_measured | measured
decision_execution_status = executed | rejected | llm_failed
guard.status = approved | rejected
executor_type = registered_external_agent
execution_status = not_executed
operation_pipeline_ready = false
```

실제 workload 성능이 없으면 `provisionally_compatible`이며 최종 최적화 완료로 해석하지 않습니다. 실제 외부 실행 결과가 없으면 배포 완료로 해석하지 않습니다.

## 실제 LLM 자동화 확인

2026-07-16 로컬 OpenAI-compatible endpoint의 `llama3.2:3b`를 기록된 AWS L4 VM snapshot과 함께 실행했습니다.

```text
decision_execution_status = executed
proposal.action = observe_status
guard.status = approved
status = pending_executor
```

이는 실제 LLM 판단과 Go Guard 검증이 동작했음을 뜻합니다. 외부 실행 주체를 등록하지 않았으므로 실제 배포·제어 완료 결과는 아닙니다. Redacted 결과는 `docs/evidence/artifacts/local_20260716_llm_automation_action.json`에 보존합니다.
