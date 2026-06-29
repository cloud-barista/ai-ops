# 에이전트 Action 및 Reward 정책

## 목적

이 문서는 AI 기반 service-control framework에서 네 개 prototype agent가 action approval과 reward signal을 어떻게 노출하는지 정의합니다.

현재 reward value는 prototype review signal입니다. reinforcement learning으로 생성된 값이 아닙니다. Go 구현은 이를 `service-operations` readiness report에 포함하여 reviewer가 agent decision boundary를 확인할 수 있게 합니다.

## 원칙

- 각 agent는 `action`, `approved`, `reward`, `reason` field를 반환합니다.
- `approved=true`는 해당 agent boundary 안에서 action이 허용됨을 의미합니다.
- `approved=false`는 integrated readiness path를 차단합니다.
- final readiness는 required agent review, manifest dry-run, recovery context, guard-readiness check가 모두 valid일 때만 true입니다.
- `go/aiops-guard`는 namespace, deployment, replica constraint를 검증하는 standalone bounded-action validator로 유지됩니다.

## Agent 정책 표

| Agent | 승인 action 예시 | Reward signal 의미 |
| --- | --- | --- |
| `AIServiceHASupportAgent` | `ha_scale_out_required`, `ha_no_action` | service health, availability, recovery need |
| `AIApplicationManagementAgent` | `app_scale_deployment`, `app_select_inference_vm` | AI application deployment/control suitability |
| `AISemiconductorInfraOpsAgent` | `infra_capacity_approved`, `infra_select_cpu_gpu_vm` | CPU/GPU VM feasibility |
| `CostOptimizationAgent` | `cost_budget_approved`, `cost_budget_rejected` | cost 및 resource-efficiency boundary |

## Readiness 예시

입력 context:

```text
recovery_namespace=aiops-demo
recovery_deployment=aiops-service
workload=llm-chat-inference
mode=mock
guard_backend=go
```

기대 final result:

```text
valid = true
recovery_pipeline_ready = true
guard_backend = go
guard_validation.valid = true
```

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace aiops-demo \
  --recovery-deployment aiops-service \
  --mode mock \
  --guard-backend go
```

## 향후 확장

- prototype reward value를 측정된 operation outcome으로 대체
- latency, availability, cost delta를 reward calibration input으로 추가
- policy tuning이 최종 평가 범위가 되면 reward table을 별도 versioned config로 분리
- infrastructure agent에 AI 반도체/NPU accelerator policy 추가
