# AI 응용 배포·제어 추론 최적화 전략

## 목적

이 문서는 1차년도 산출물인 다음 항목에 대응합니다.

```text
CPU/GPU VM 환경을 위한 AI 응용 배포·제어 추론 최적화 전략
```

현재 구현은 AI workload를 어디에 배치할지 결정하고, 선택 결과를 Kubernetes deployment/control plan으로 표현하는 Go prototype입니다. cloud VM을 직접 생성하지 않습니다.

## 입력 데이터

```text
config/inference_optimization.json
```

| Section | 의미 |
| --- | --- |
| `resources` | CPU/GPU VM candidate performance, cost, capacity, node selector, resource limit |
| `workloads` | AI workload type, VRAM requirement, latency SLO, throughput SLO, service name, namespace, container image |

## 전략

1. workload accelerator requirement를 확인합니다.
2. supported model type 기준으로 VM candidate를 filter합니다.
3. GPU memory requirement를 검증합니다.
4. latency와 throughput SLO를 검증합니다.
5. eligible candidate를 latency, throughput, cost, capacity 기준으로 scoring합니다.
6. score가 가장 높은 VM resource를 선택합니다.
7. 결과를 Kubernetes deployment/control plan으로 변환합니다.
8. manifest를 생성하고 mock/server dry-run validation을 수행합니다.

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

## Deployment Plan Field

| Field | 의미 |
| --- | --- |
| `selected_resource` | 선택된 CPU/GPU VM resource candidate |
| `deployment_plan.kubernetes.namespace` | AI application workload용 namespace |
| `deployment_plan.kubernetes.deployment` | AI application workload용 deployment name |
| `deployment_plan.kubernetes.node_selector` | CPU/GPU VM placement condition |
| `deployment_plan.kubernetes.resources` | CPU, memory, GPU, VRAM request/limit hint |
| `deployment_plan.control_actions` | deploy, scale, monitor, rollback control action |

## Agent와의 관계

- `AIApplicationManagementAgent`는 AI application deployment/control plan을 평가합니다.
- `AISemiconductorInfraOpsAgent`는 CPU/GPU VM feasibility를 평가합니다.
- `CostOptimizationAgent`는 cost efficiency를 평가합니다.
- `AIServiceHASupportAgent`는 alert input이 제공될 때 recovery workflow와 연결할 수 있습니다.

## 산출물 매핑

| 산출물 | 파일 |
| --- | --- |
| AI 응용 배포·제어 추론 최적화 전략 | `docs/design/ai_application_deployment_strategy.md` |
| CPU/GPU VM 및 workload 설정 | `config/inference_optimization.json` |
| Placement recommendation CLI | `go run ./cmd/aiops-service-control recommend-inference-placement` |
| Deployment/control plan CLI | `go run ./cmd/aiops-service-control plan-inference-deployment` |
