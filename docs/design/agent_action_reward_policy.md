# Agent별 Action 및 Reward 설계

## 목적

이 문서는 AI 기반 서비스 제어/관리 자동화 프레임워크에서 4개 Agent가 어떤
action을 승인하고 어떤 reward signal을 반환하는지 정의한다.

현재 단계의 reward는 강화학습을 직접 수행하기 위한 값이 아니라, 향후 학습 및
평가로 확장하기 위한 정책 점수의 초기 기준이다. Go 구현에서는 이 기준을
`service-operations` readiness report의 Agent review로 노출한다.

## 기본 원칙

- 각 Agent는 `action`, `approved`, `reward`, `reason`을 반환한다.
- `approved=true`는 해당 Agent 관점에서 실행 가능한 action이라는 의미이다.
- `approved=false`는 해당 action을 통합 실행에서 차단해야 한다는 의미이다.
- 최종 readiness는 모든 필수 Agent review가 승인될 때만 true가 된다.
- 실제 실행 전에는 `go/aiops-guard`가 namespace, deployment, replica 범위를 다시 검증한다.

## Agent별 정책

| Agent | 승인 Action 예시 | Reward 기준 |
| --- | --- | --- |
| `AIServiceHASupportAgent` | `ha_scale_out_required`, `ha_no_action` | SLO/가용성 회복 필요성 |
| `AIApplicationManagementAgent` | `app_scale_deployment`, `app_select_inference_vm` | AI 응용 배포/제어 적합성 |
| `AISemiconductorInfraOpsAgent` | `infra_capacity_approved`, `infra_select_cpu_gpu_vm` | CPU/GPU VM 자원 제약 만족도 |
| `CostOptimizationAgent` | `cost_budget_approved`, `cost_budget_rejected` | 비용 상한 및 자원 효율성 |

## Readiness 예시

입력:

```text
namespace=online-boutique
deployment=paymentservice
workload=llm-chat-inference
mode=mock
guard_backend=go
```

예상 Agent review:

```text
AIServiceHASupportAgent: approved
AIApplicationManagementAgent: approved
AISemiconductorInfraOpsAgent: approved
CostOptimizationAgent: approved
```

최종 결과:

```text
valid = true
recovery_pipeline_ready = true
guard_backend = go
```

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --namespace online-boutique \
  --deployment paymentservice \
  --mode mock \
  --guard-backend go
```

## 향후 확장

- 실제 운영 metric을 reward 보정 값으로 반영
- latency, availability, cost 변화량 기반의 reward 재산정
- Agent별 reward table을 별도 설정 파일로 분리
- AI 반도체/NPU accelerator profile을 인프라 Agent 정책에 추가
