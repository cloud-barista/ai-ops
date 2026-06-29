# AI 응용 배포·제어 추론 최적화 전략 설계서

영문 제목: AI Application Deployment and Control Optimization Strategy

## 1. 목적

이 문서는 1차년도 Go 기반 service-control prototype에 구현된 AI 응용 배포·제어 최적화 전략을 설명합니다. 목적은 AI workload를 accelerator 요구사항, SLO 제약, capacity, cost policy에 따라 CPU/GPU VM candidate와 매칭하는 방법을 정의하는 것입니다.

본 문서는 공식 설계 산출물 원본입니다. 추론 최적화 설정, Go service-control 구현, API 계약, 검증 증거와 함께 검토해야 합니다.

## 2. 프로토타입 범위

프로토타입 범위는 다음과 같습니다.

- CPU/GPU VM 기반 AI 응용 배치 추천
- AI workload와 resource profile 정의
- latency, throughput, 비용, capacity 기반 scoring
- Kubernetes 배포 계획 생성
- mock dry-run readiness validation
- GPU/NPU 및 AI semiconductor infrastructure 확장 방향

현재 구현은 실제 GPU scheduler 또는 cloud VM provisioning platform을 대체하지 않습니다. Go 기반 recommendation and planning prototype입니다. live Kubernetes node, GPU telemetry, CB-Tumblebug VM inventory, AI semiconductor cluster manager와 연결하기 전에 deployment-control decision을 어떤 구조로 만들지 보여줍니다.

## 3. 입력 설정

배치 정책은 다음 파일에서 관리합니다.

```text
config/inference_optimization.json
```

설정은 크게 resource와 workload로 구성됩니다. resource section은 CPU/GPU VM candidate, 예상 latency, throughput, cost, available capacity, node selector, resource limit, 지원 model type을 설명합니다. workload section은 model type, accelerator 필요 여부, 예상 VRAM, latency SLO, 최소 throughput, service name, namespace, container image, replica count를 설명합니다.

## 4. Resource 후보

| Resource | Accelerator | 용도 |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | 경량 text classification 및 embedding workload |
| `gpu-vm-l4` | GPU | 비용/성능 균형이 필요한 LLM 또는 vision inference |
| `gpu-vm-a100` | GPU | 고성능 LLM 또는 vision inference |

이 resource들은 prototype resource profile입니다. 기본 검증 경로에서 실제 cloud VM이 프로비저닝되었다는 의미가 아닙니다.

## 5. Workload Profile

| Workload | Model type | Accelerator 필요 | 목적 |
| --- | --- | --- | --- |
| `llm-chat-inference` | `llm` | `true` | GPU acceleration이 필요한 LLM inference service |
| `text-classifier` | `text-classification` | `false` | CPU에서도 실행 가능한 경량 text classification service |

## 6. 배치 판단 로직

배치 판단 로직은 다음 순서로 동작합니다.

1. workload가 accelerator를 요구하는지 확인합니다.
2. resource가 workload model type을 지원하는지 확인합니다.
3. GPU/NPU resource 사용 시 GPU memory 요구사항을 확인합니다.
4. latency SLO를 확인합니다.
5. 최소 throughput 요구사항을 확인합니다.
6. available capacity를 확인합니다.
7. eligible resource를 weighted score로 ranking합니다.

부적합 resource는 `rejected_resources`에 보고하여 판단 근거를 추적할 수 있게 합니다.

## 7. Scoring 전략

```text
score =
  latency_weight * latency_score
+ throughput_weight * throughput_score
+ cost_weight * cost_score
+ capacity_weight * capacity_score
```

| Factor | 의미 |
| --- | --- |
| `latency_score` | 예상 latency가 workload SLO를 만족할수록 높음 |
| `throughput_score` | 예상 throughput이 최소 요구량을 만족할수록 높음 |
| `cost_score` | eligible candidate 중 비용이 낮을수록 높음 |
| `capacity_score` | 사용 가능한 replica 여유가 많을수록 높음 |

기본 weight는 다음과 같습니다.

| Factor | Weight |
| --- | --- |
| latency | 0.35 |
| throughput | 0.30 |
| cost | 0.20 |
| capacity | 0.15 |

## 8. 기대 프로토타입 결과

| Workload | 선택 resource | Action | 이유 |
| --- | --- | --- | --- |
| `llm-chat-inference` | `gpu-vm-l4` | `deploy_on_gpu_vm` | GPU acceleration이 필요하며 L4 candidate가 설정된 SLO와 cost balance를 만족 |
| `text-classifier` | `cpu-vm-standard` | `deploy_on_cpu_vm` | CPU에서도 SLO를 만족할 수 있어 더 비용 효율적 |

이 결과는 현재 JSON policy 아래의 prototype-level output입니다. production scheduling guarantee가 아닙니다.

## 9. Kubernetes 배포 계획

resource가 선택되면 Go service-control API는 Kubernetes 배포·제어 계획을 생성합니다. 계획에는 service name, container image, target resource, target accelerator, namespace, deployment name, replica count, node selector, resource request, resource limit, control action, monitoring metric, SLO value가 포함됩니다.

선택 resource가 GPU VM이면 생성된 resource limit에 `nvidia.com/gpu`가 포함됩니다. node selector는 선택 resource profile에서 유도되어, 이후 accelerator-aware Kubernetes scheduling으로 연결할 수 있습니다.

현재 프로토타입은 deployment plan과 mock dry-run readiness output을 생성합니다. 기본값으로 실제 GPU VM을 생성하거나 live Kubernetes cluster를 변경하지 않습니다.

## 10. Service-Control 통합 흐름

```text
LLM selection
-> Agent registry validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment-plan generation
-> manifest dry-run
-> agent review
-> guard validation
-> service-operations readiness report
```

이 흐름은 Go service-control API와 CLI에 구현되어 있습니다. 통합 output은 deployment/control decision이 LLM policy selection 및 bounded-action validation과 결합되는 방식을 보여줍니다.

## 11. CLI 검증

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

기대 신호:

```text
selected_resource = gpu-vm-l4
```

## 12. 설계 경계

이 전략은 1차년도 prototype design입니다. production cloud scheduler, Kubernetes scheduler, GPU device plugin, CB-Tumblebug resource provisioning을 대체하지 않습니다. 대신 실제 AI semiconductor infrastructure와 통합하기 전에 필요한 decision structure를 정의합니다. 향후 live GPU metrics, NPU profile, Kubernetes node status, multi-cloud VM inventory, closed-loop control을 추가할 수 있습니다.

## 13. 관련 산출물

| 산출물 | 경로 |
| --- | --- |
| 추론 최적화 설정 | `config/inference_optimization.json` |
| 추론 최적화 가이드 | `docs/design/inference_optimization_guide.md` |
| AI 응용 배포 전략 | `docs/design/ai_application_deployment_strategy.md` |
| Go 서비스 로직 | `go/service-control-api/internal/api/service.go` |
| API/CLI model | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| OpenAPI 계약 | `docs/submission/openapi_service_control.yaml` |
| 기능/API 가이드 | `docs/submission/functional_api_guide.md` |
