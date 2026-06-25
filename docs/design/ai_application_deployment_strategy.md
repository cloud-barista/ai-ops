# AI 응용 배포/제어 추론 최적화 전략 설계서

## 목적

이 문서는 1차년도 개발 항목 중 다음 산출물에 대응한다.

```text
CPU/GPU VM 기반 AI 응용 배포/제어 추론 최적화 전략 설계서
```

현재 구현은 실제 모델 서버를 GPU VM에 상시 운영하는 단계가 아니라, AI 응용을
어떤 VM 자원에 배치하고 어떤 Kubernetes 제어 계획으로 운영할지 결정하는 Go
프로토타입이다.

## 입력 데이터

설정 파일:

```text
config/inference_optimization.json
```

| 구분 | 내용 |
| --- | --- |
| `resources` | CPU/GPU VM 후보의 성능, 비용, 용량, node selector |
| `workloads` | AI 응용 workload의 모델 종류, VRAM 요구량, latency SLO, throughput 요구량 |

## 배포/제어 전략

전략은 다음 순서로 결정한다.

1. workload의 accelerator 요구 여부를 확인한다.
2. 각 resource의 model type 지원 여부를 확인한다.
3. GPU memory 요구량을 만족하는지 확인한다.
4. latency SLO와 최소 throughput을 만족하는지 확인한다.
5. latency, throughput, cost, capacity 가중 점수를 계산한다.
6. 가장 높은 점수의 VM 자원을 선택한다.
7. 선택 결과를 Kubernetes deployment/control plan으로 변환한다.

## Go CLI

배치 추천:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Kubernetes 배포/제어 계획 생성:

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

## 배포 계획 출력 항목

| 필드 | 의미 |
| --- | --- |
| `selected_resource` | 선택된 CPU/GPU VM 후보 |
| `deployment_plan.kubernetes.namespace` | 배포 대상 namespace |
| `deployment_plan.kubernetes.deployment` | 생성 또는 제어할 Deployment 이름 |
| `deployment_plan.kubernetes.node_selector` | CPU/GPU VM 배치 조건 |
| `deployment_plan.kubernetes.resources` | CPU, memory, GPU resource request/limit |
| `deployment_plan.control_actions` | 배포, scale, latency monitoring, rollback 제어 action |

## 연구적 의미

이 전략은 4-Agent 구조에서 `AIApplicationManagementAgent`와
`AISemiconductorInfraOpsAgent`가 함께 판단해야 하는 영역이다.

- 응용관리 Agent는 어떤 AI 응용을 배포/제어할지 결정한다.
- 인프라 Agent는 CPU/GPU VM 자원 관점에서 배치 가능성을 판단한다.
- 비용 Agent는 고성능 GPU 사용 필요성과 비용 효율성을 검토한다.
- HA Agent는 배포 이후 latency, throughput, availability가 SLO를 만족하는지 감시한다.

## 산출물 대응

| 산출물 | 파일 |
| --- | --- |
| AI 응용 배포/제어 추론 최적화 전략 설계서 | `docs/design/ai_application_deployment_strategy.md` |
| CPU/GPU VM 자원 및 workload 설정 | `config/inference_optimization.json` |
| 배치 추천 CLI | `go run ./cmd/aiops-service-control recommend-inference-placement` |
| 배포/제어 계획 CLI | `go run ./cmd/aiops-service-control plan-inference-deployment` |
