# 실행 코드 가이드

## 핵심 Go 코드

| 코드 | 목적 |
| --- | --- |
| `cmd/service-control-api/main.go` | Echo HTTP API entrypoint |
| `cmd/aiops-service-control/main.go` | CLI와 team-validation |
| `cmd/aiops-service-control/validate_system.go` | 로컬/VM 환경 검증과 라이브 VM snapshot 수집 |
| `internal/api/service.go` | 실제 LLM Action 제안, registry, VM 적합성, Go Guard와 handoff 통합 |
| `internal/api/server.go` | HTTP route와 validation |
| `internal/benchmark/` | Ops LLM benchmark runner와 evaluator |
| `internal/automation/` | LLM Action prompt, 엄격한 JSON 파싱과 제안 결과 |
| `internal/llmclient/` | OpenAI-compatible provider 공용 클라이언트 |
| `internal/deploymentplanner/` | 자연어 요구 분석, Manifest 생성, 상태 polling과 재시도 판단 |
| `internal/appdeploy/` | AppDeploy 계약 모델, Go Guard, HTTP client |
| `go/aiops-guard/` | bounded VM Action 검증 |

## 기본 검증

```bash
make test
make vet
```

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/team-validation
```

## 실제 VM snapshot 검증

```bash
go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

```bash
go run ./cmd/aiops-service-control plan-ai-application-control \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

## System validation

로컬:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-local
```

VM 내부:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --output-dir ../../runs/full-validation-vm
```

VM 명령은 `06_vm_resource_snapshot.json`을 만들고 해당 snapshot을 같은 실행의 team-validation에 전달합니다.

## 실제 Ops LLM endpoint 포함

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --run-llm-decision \
  --llm-decision-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --llm-decision-candidate-id qwen3.5-ops-planner \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-vm-complete
```

Ollama는 OpenAI-compatible endpoint 예시일 뿐 필수 런타임이 아닙니다. `benchmark_status=executed`는 Ops 평가, `decision_execution_status=executed`는 자동화 Action 제안이 실제 endpoint에서 수행되었음을 각각 뜻합니다.

## AppDeploy Planner

```bash
go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1
```

실행 순서는 Go Request Guard, LLM 호출, Go Manifest Guard, AppDeploy 배포 요청, 상태 polling, 로그 조회입니다. 요청 Guard가 거부하면 외부 호출을 시작하지 않습니다. 실제 Target과 Runtime Adapter는 AppDeploy가 선택합니다.
