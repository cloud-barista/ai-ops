# AI 응용 배포·제어 추론 최적화 전략 설계서

영문 제목: AI Application Deployment and Control Optimization Strategy

## 1. 한눈에 보는 구조

| 항목 | 내용 |
| --- | --- |
| 목적 | AI workload 요구사항에 맞는 CPU/GPU VM resource를 선택하고 배포 계획을 생성한다. |
| 입력 | `config/inference_optimization.json`의 workload와 resource profile |
| 처리 | accelerator, SLO, throughput, cost, capacity 기반 scoring |
| 출력 | selected resource, deployment action, Kubernetes deployment plan |
| 검증 | Go CLI/API로 placement와 deployment plan을 재현 |

## 2. 판단 흐름

```text
workload requirements
-> resource candidate filtering
-> CPU/GPU placement scoring
-> selected resource
-> Kubernetes deployment plan
-> mock/dry-run readiness
```

## 3. 입력 기준

| 구분 | 주요 항목 |
| --- | --- |
| Workload | model type, accelerator 필요 여부, VRAM, latency SLO, throughput, image, replicas |
| Resource | CPU/GPU type, latency, throughput, cost, capacity, node selector, resource limit |

## 4. 배치 판단 규칙

| 단계 | 판단 내용 |
| --- | --- |
| 1 | workload가 accelerator를 요구하는지 확인 |
| 2 | resource가 model type을 지원하는지 확인 |
| 3 | GPU workload인 경우 VRAM 조건 확인 |
| 4 | latency SLO와 throughput 요구사항 확인 |
| 5 | cost와 capacity를 포함해 score 계산 |
| 6 | 가장 적합한 resource를 선택하고 제외 사유를 기록 |

## 5. Resource 예시

| Resource | Accelerator | 용도 |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | 경량 text classification |
| `gpu-vm-l4` | GPU | 비용/성능 균형형 LLM inference |
| `gpu-vm-a100` | GPU | 고성능 LLM 또는 vision inference |

## 6. 기대 결과

| Workload | 선택 resource | 이유 |
| --- | --- | --- |
| `llm-chat-inference` | `gpu-vm-l4` | GPU가 필요하고 SLO와 비용 균형을 만족 |
| `text-classifier` | `cpu-vm-standard` | CPU로도 요구 SLO를 만족 |

## 7. 검증 방법

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

기대 신호:

```text
selected_resource = gpu-vm-l4
```

## 8. 설계 경계

- 실제 GPU VM을 생성하는 기능이 아니다.
- Kubernetes scheduler, GPU device plugin, CB-Tumblebug provisioning을 대체하지 않는다.
- 기본 검증은 mock/dry-run이며 live cluster 변경을 전제로 하지 않는다.
- 실제 AWS GPU VM 검증은 AI-Infra 또는 CB-Tumblebug 연동 환경이 준비된 뒤 수행한다.
