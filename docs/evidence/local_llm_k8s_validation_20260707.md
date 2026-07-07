# 실제 LLM 및 Kubernetes 검증 결과

본 문서는 `geon` 브랜치의 Go 기반 service-control prototype을 로컬 WSL 환경에서 실제 LLM endpoint 및 Kubernetes cluster와 연결해 실행한 검증 결과입니다.

## 검증 환경

| 항목 | 값 |
| --- | --- |
| 실행 일자 | 2026-07-07 |
| 실행 위치 | WSL Ubuntu 22.04 |
| LLM endpoint | Ollama OpenAI-compatible endpoint |
| 실제 모델 | `llama3.2:3b` |
| Kubernetes cluster | `kind-aiops-live` |
| Kubernetes version | v1.35.0 |
| 검증 언어 | Go |

## 실제 LLM benchmark

다음 명령으로 10개 Ops scenario를 실제 LLM endpoint에 전송했습니다.

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-local-llm-api-executed-20260707
```

| 항목 | 결과 |
| --- | --- |
| `validate-system` | valid |
| `ops-llm-evaluation` | valid |
| `ops-llm-evaluator` | valid |
| `api-integration-validation` | valid |
| `benchmark_status` | `executed` |
| `selected_actual_model` | `llama3.2:3b` |
| scenario count | 10 |
| executed count | 10 |
| error count | 0 |
| average score | 0.9182 |

## Kubernetes server dry-run

Go service-control prototype이 생성한 AI application deployment manifest를 실제 Kubernetes API server에 `kubectl apply --dry-run=server`로 전달했습니다.

| 항목 | 결과 |
| --- | --- |
| workload | `llm-chat-inference` |
| selected resource | `gpu-vm-l4` |
| target accelerator | GPU |
| dry-run command | `kubectl apply -f - --dry-run=server` |
| dry-run result | valid |
| stdout | `deployment.apps/llm-chat-inference created (server dry run)` |

## Kubernetes live deployment

Kubernetes live path 검증을 위해 `kind-aiops-live` cluster에 검증용 workload를 실제 배포했습니다.

```bash
kubectl create namespace aiops-demo --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops-demo create deployment aiops-service --image=nginx:1.27-alpine --replicas=1 --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops-demo expose deployment aiops-service --port=80 --target-port=80 --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops-demo rollout status deployment/aiops-service --timeout=180s
```

이후 `aiops-guard`를 `real` mode로 실행하여 실제 Kubernetes scale command를 수행했습니다.

```bash
go run ./cmd/aiops-guard --input 06_guard_scale_real_request.json
```

| 항목 | 결과 |
| --- | --- |
| namespace | `aiops-demo` |
| deployment | `aiops-service` |
| image | `nginx:1.27-alpine` |
| initial rollout | success |
| service endpoint check | success |
| guard mode | `real` |
| guard action | `scale_out` |
| guard command | `kubectl scale deployment aiops-service --replicas=2 -n aiops-demo` |
| final replicas | 2/2 Running |

## 대표 증적 파일

| 파일 | 설명 |
| --- | --- |
| [`artifacts/local_20260707_llm_api_system_validation_summary.json`](artifacts/local_20260707_llm_api_system_validation_summary.json) | LLM benchmark와 API integration을 포함한 `validate-system` summary |
| [`artifacts/local_20260707_ops_llm_evaluation_summary.json`](artifacts/local_20260707_ops_llm_evaluation_summary.json) | 실제 LLM benchmark evaluator summary |
| [`artifacts/local_20260707_k8s_live_summary.json`](artifacts/local_20260707_k8s_live_summary.json) | Kubernetes live deployment summary |
| [`artifacts/local_20260707_guard_scale_real_result.json`](artifacts/local_20260707_guard_scale_real_result.json) | `aiops-guard` real mode scale result |
| [`artifacts/local_20260707_k8s_objects_after_guard.txt`](artifacts/local_20260707_k8s_objects_after_guard.txt) | scale 이후 Kubernetes object 상태 |

## 해석 경계

이번 검증으로 실제 LLM endpoint 호출, Go evaluator 평가, service-control API integration, Kubernetes API server dry-run, live deployment, service endpoint 확인, `aiops-guard` real mode scale을 확인했습니다.

다만 Kubernetes live deployment에 사용한 `aiops-service`는 검증용 `nginx:1.27-alpine` workload입니다. 따라서 이 결과는 production AI application image 배포 완료가 아니라, service-control prototype이 연결될 Kubernetes live execution path와 guard real mode가 동작함을 보여주는 증적입니다.
