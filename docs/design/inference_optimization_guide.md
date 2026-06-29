# CPU/GPU VM 추론 최적화 가이드

## 목적

이 문서는 다음 전략의 prototype 설계를 설명합니다.

```text
CPU/GPU VM-based AI application deployment/control inference optimization
```

현재 구현은 Go 기반 recommendation prototype입니다. 실제 GPU scheduler나 cloud VM provisioning system을 대체하지 않습니다. resource constraint, SLO, capacity, cost policy를 사용해 AI workload에 적합한 CPU/GPU VM resource candidate를 선택합니다.

## 입력 설정

설정 파일:

```text
config/inference_optimization.json
```

| Section | 의미 |
| --- | --- |
| `resources` | CPU/GPU VM candidate, latency, throughput, cost, capacity, node selector, resource limit |
| `workloads` | AI workload type, accelerator need, VRAM need, latency SLO, throughput SLO, service name, container image |

## 현재 Resource 후보

| Resource | Accelerator | 용도 |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | 경량 text classification 및 embedding workload |
| `gpu-vm-l4` | GPU | 비용/성능 균형이 필요한 LLM 또는 vision inference |
| `gpu-vm-a100` | GPU | 고성능 LLM 또는 vision inference |

## 배치 판단

배치 추천은 다음을 확인합니다.

1. workload가 accelerator를 요구하는지 확인합니다.
2. resource가 workload model type을 지원하는지 확인합니다.
3. accelerator resource가 필요한 경우 GPU memory requirement를 확인합니다.
4. latency와 throughput SLO 만족 여부를 확인합니다.
5. eligible resource를 weighted score로 ranking합니다.

Score:

```text
score =
  latency_weight * latency_score
+ throughput_weight * throughput_score
+ cost_weight * cost_score
+ capacity_weight * capacity_score
```

기본 weight:

| 항목 | Weight |
| --- | --- |
| latency | 0.35 |
| throughput | 0.30 |
| cost | 0.20 |
| capacity | 0.15 |

## Go CLI

Placement recommendation:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Deployment/control plan 생성:

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

## 기대 프로토타입 결과

| Workload | Selected resource | Action | 이유 |
| --- | --- | --- | --- |
| `llm-chat-inference` | `gpu-vm-l4` | `deploy_on_gpu_vm` | GPU가 필요하고 L4 candidate가 SLO/cost balance를 만족 |
| `text-classifier` | `cpu-vm-standard` | `deploy_on_cpu_vm` | CPU capacity가 설정 SLO를 만족 |

## 향후 확장

- live Kubernetes node label과 device-plugin 상태 통합
- 실제 AI-Infra 환경의 GPU memory telemetry 수집
- 공유 infrastructure가 준비되면 CB-Tumblebug-managed VM inventory 연결
- NPU 또는 AI 반도체 accelerator profile 추가
