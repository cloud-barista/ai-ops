# 기능/API 가이드

## 1. 목적

이 가이드는 1차년도 service-control 기능 프로토타입의 Go Echo HTTP API를 설명합니다. API는 Go CLI와 동일한 핵심 기능인 Ops LLM 정책 선정, 에이전트 registry 조회, CPU/GPU VM 배치 추천, 배포 계획 생성, 통합 서비스 운영 준비도 검증을 제공합니다.

Swagger/OpenAPI 산출물은 `docs/submission/openapi_service_control.yaml`에 있습니다.

## 2. API 서버 실행

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

기대 시작 신호:

```text
http server started on [::]:8080
```

## 3. Endpoint 목록

| Method | Path | 기능 |
| --- | --- | --- |
| `GET` | `/healthz` | 상태 확인 |
| `GET` | `/openapi.yaml` | OpenAPI YAML 계약 반환 |
| `GET` | `/api/v1/agents` | 등록된 AI agent 목록 조회 |
| `POST` | `/api/v1/ops-llm/select` | Ops LLM policy 기반 candidate selection 실행 |
| `POST` | `/api/v1/apps/placement` | AI workload의 CPU/GPU VM 배치 추천 |
| `POST` | `/api/v1/apps/deployment-plan` | AI 응용 배포·제어 계획 생성 |
| `POST` | `/api/v1/service-operations/run` | 통합 service-operations readiness pipeline 실행 |

## 4. 기본 확인

```bash
curl http://127.0.0.1:8080/healthz
```

기대 응답:

```json
{"service":"service-control-api","status":"ok"}
```

OpenAPI 계약 확인:

```bash
curl http://127.0.0.1:8080/openapi.yaml
```

## 5. Agent Registry API

```bash
curl http://127.0.0.1:8080/api/v1/agents
```

응답에는 등록된 agent name, role, responsibility, bounded action, reward-signal description, enabled status가 포함됩니다. 이 endpoint는 `config/agent_registry.json`의 API-facing view입니다.

## 6. Ops LLM 선정 API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

주요 응답 필드:

| Field | 의미 |
| --- | --- |
| `selected_model` | 요청 policy로 선택된 candidate |
| `selected_actual_model` | 실제 provider model 연결 placeholder 또는 향후 평가 모델명 |
| `selected_provider` | 실제 model provider 연결 정보 |
| `evaluation_source` | 현재 점수의 출처 |
| `evaluation_type` | 현재 평가 값의 성격 |
| `benchmark_status` | `not_executed`, `dry_run`, `executed` 중 benchmark 실행 상태 |
| `selected_score` | weighted prototype policy score |
| `ranking` | score 기준 candidate ranking |
| `rationale` | 선정 이유 설명 |
| `valid` | 요청이 성공적으로 처리되었는지 여부 |

현재 policy 값은 수동 정의된 prototype policy baseline이며 최종 표준 benchmark result가 아닙니다. `primary-ops-llm`은 내부 역할 label이고, 실제 provider model은 `selected_actual_model`로 분리합니다.

## 7. 배치 추천 API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/placement \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}'
```

주요 응답 필드:

| Field | 의미 |
| --- | --- |
| `selected_resource` | 추천된 CPU/GPU VM resource profile |
| `action` | 추천 deployment action |
| `score` | weighted placement score |
| `slo_satisfied` | 선택 resource가 설정 SLO를 만족하는지 여부 |
| `ranked_candidates` | eligible candidate ranking |
| `rejected_resources` | 제약 조건으로 제외된 resource |

## 8. 배포 계획 API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/apps/deployment-plan \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}'
```

배포 계획 응답에는 service name, container image, target resource, target accelerator, namespace, deployment name, replicas, node selector, resource requests, resource limits, control actions, monitoring metrics, SLO values가 포함됩니다.

## 9. Service Operations API

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

주요 응답 필드:

| Field | 의미 |
| --- | --- |
| `valid` | 통합 readiness 결과 |
| `selected_llm` | 선택된 LLM policy candidate |
| `runtime_model` | readiness flow에서 사용된 runtime model label |
| `selected_actual_model` | 실제 provider model 연결 placeholder 또는 향후 평가 모델명 |
| `selected_provider` | 실제 provider 연결 정보 |
| `benchmark_status` | LLM benchmark 실행 상태 |
| `selected_resource` | 선택된 CPU/GPU VM candidate |
| `deployment_plan` | AI 응용 배포·제어 계획 |
| `deployment_manifest` | 생성된 Kubernetes Deployment manifest |
| `deployment_dry_run` | mock 또는 dry-run 배포 검증 결과 |
| `agent_reviews` | application, infrastructure, cost 관점 검토 결과 |
| `recovery_pipeline_ready` | 서비스 운영/recovery context 준비 여부 |
| `guard_backend` | guard 검증 backend, 기본 기대값은 `go` |
| `guard_validation` | bounded-action readiness validation 결과 |

## 10. Ops LLM 평가 Dry-Run CLI

실제 provider API를 호출하지 않고 LLM 평가 scenario/candidate 연결을 확인하려면 CLI를 사용합니다.

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.json \
  --output-dir ../../runs/ops-llm-evaluation-dry-run \
  --dry-run

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-dry-run/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

dry-run 결과는 실제 LLM API benchmark 결과가 아닙니다.

## 11. Recovery Context 경계

`recovery_namespace`와 `recovery_deployment`는 service operation/recovery context field입니다. readiness와 guard validation에 사용하는 service-control target을 식별합니다. 이는 `deployment_plan.kubernetes.namespace` 안에서 생성되는 AI application deployment namespace와 다릅니다.

예:

- `recovery_namespace = aiops-demo`
- `recovery_deployment = aiops-service`
- `deployment_plan.kubernetes.namespace = ai-inference`

## 12. OpenAPI 산출물

OpenAPI 문서는 필수 제출 산출물입니다.

```text
docs/submission/openapi_service_control.yaml
```

이 가이드와 `go/service-control-api/internal/api/server.go`의 Go route definition을 함께 검토해야 합니다.
