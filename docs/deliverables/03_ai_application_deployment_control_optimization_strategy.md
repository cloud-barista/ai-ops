# AI 응용 배포·제어 추론 최적화 전략 설계서

영문 제목: AI Application Deployment and Control Optimization Strategy

## 1. 설계 목적

본 설계서는 AI workload 요구사항을 기준으로 CPU/GPU VM resource를 선택하고, 선택 결과를 AI 응용 배포·제어 계획으로 변환하는 전략을 정의합니다. 기본 검증은 로컬 Go 실행과 mock/dry-run을 사용하며, 실제 GPU VM 생성은 AI-Infra 또는 CB-Tumblebug 연동 단계로 분리합니다.

## 2. 한눈에 보는 구조

| 항목 | 내용 |
| --- | --- |
| 입력 | workload 요구사항, CPU/GPU VM resource profile |
| 판단 기준 | accelerator, VRAM, latency SLO, throughput, cost, capacity |
| 처리 | resource filtering, scoring, selected resource 결정 |
| 출력 | deployment action, Kubernetes deployment plan, dry-run result |
| 검증 | Go CLI/API 기반 placement 및 plan 재현 |

## 3. 판단 흐름

```text
workload requirements
-> resource candidate filtering
-> CPU/GPU placement scoring
-> selected resource
-> Kubernetes deployment plan
-> mock/dry-run readiness
```

## 4. 입력 데이터

| 구분 | 주요 항목 | 설명 |
| --- | --- | --- |
| Workload | model type, accelerator 필요 여부, VRAM, latency SLO, throughput | AI 응용 실행 요구사항 |
| Resource | accelerator type, latency, throughput, cost, capacity | CPU/GPU VM 후보 특성 |
| Kubernetes hint | namespace, deployment, node selector, resource limit | 배포 계획 생성에 필요한 정보 |

설정 파일:

```text
config/inference_optimization.json
```

## 5. 배치 판단 규칙

| 단계 | 판단 내용 | 결과 |
| --- | --- | --- |
| 1 | workload가 accelerator를 요구하는지 확인 | CPU/GPU 후보 분리 |
| 2 | resource가 model type을 지원하는지 확인 | 미지원 후보 제외 |
| 3 | GPU workload의 VRAM 조건 확인 | 부족 후보 제외 |
| 4 | latency SLO와 throughput 조건 확인 | SLO 미달 후보 제외 |
| 5 | cost와 capacity를 포함해 score 계산 | eligible 후보 ranking |
| 6 | 최고 score resource 선택 | selected resource 산출 |

## 6. Resource 후보 예시

| Resource | Accelerator | 용도 |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | 경량 text classification |
| `gpu-vm-l4` | GPU | 비용/성능 균형형 LLM inference |
| `gpu-vm-a100` | GPU | 고성능 LLM 또는 vision inference |

## 7. Scoring 기준

```text
score =
  latency_weight * latency_score
+ throughput_weight * throughput_score
+ cost_weight * cost_score
+ capacity_weight * capacity_score
```

| 요소 | 의미 |
| --- | --- |
| latency | SLO를 만족할수록 높은 score |
| throughput | 최소 처리량을 만족할수록 높은 score |
| cost | 비용이 낮을수록 높은 score |
| capacity | 사용 가능한 replica 여유가 클수록 높은 score |

## 8. 배포 계획 출력

| 출력 항목 | 설명 |
| --- | --- |
| `selected_resource` | 선택된 CPU/GPU VM profile |
| `deployment_plan.kubernetes.namespace` | AI 응용 배포 namespace |
| `deployment_plan.kubernetes.deployment` | deployment 이름 |
| `deployment_plan.kubernetes.node_selector` | CPU/GPU VM 배치 조건 |
| `deployment_plan.kubernetes.resources` | CPU, memory, GPU request/limit |
| `deployment_plan.control_actions` | deploy, scale, monitor, rollback action |

## 9. 검증 방법

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

## 10. 설계 경계

| 경계 | 설명 |
| --- | --- |
| VM 생성 경계 | 실제 AWS GPU VM을 생성하지 않는다. |
| Scheduler 경계 | Kubernetes scheduler나 GPU device plugin을 대체하지 않는다. |
| CB-Tumblebug 경계 | CB-Tumblebug은 향후 연동 대상이며 대체 대상이 아니다. |
| 검증 경계 | 기본 검증은 mock/dry-run이며 live cluster 변경을 전제로 하지 않는다. |
