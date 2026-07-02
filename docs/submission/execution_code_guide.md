# 실행 코드 가이드

## 핵심 Go 코드

| 코드 | 목적 |
| --- | --- |
| `go/service-control-api/cmd/service-control-api/main.go` | HTTP API server entrypoint |
| `go/service-control-api/cmd/aiops-service-control/main.go` | CLI entrypoint |
| `go/service-control-api/cmd/aiops-service-control/validate_system.go` | local/vm 공통 system validation runner |
| `go/service-control-api/internal/api/service.go` | LLM selection, agent registry, CPU/GPU placement, readiness pipeline |
| `go/service-control-api/internal/api/server.go` | HTTP route |
| `go/service-control-api/internal/api/models.go` | request/response model |
| `go/service-control-api/internal/benchmark/` | Ops LLM benchmark runner와 evaluator |
| `go/aiops-guard/` | standalone bounded-action guard |

## 기본 검증 명령

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
```

```bash
go run ./cmd/aiops-service-control team-validation
```

```bash
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace aiops-demo \
  --recovery-deployment aiops-service \
  --mode mock \
  --guard-backend go
```

## Local/VM System Validation

로컬:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --output-dir ../../runs/full-validation-local
```

AWS GPU VM 내부:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --output-dir ../../runs/full-validation-vm
```

실제 LLM benchmark 포함:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/full-validation-local-executed
```

VM 내부에서는 `--target vm`으로 변경합니다.

## Ops LLM Dry-Run

```bash
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

## Ops LLM 실제 실행

OpenAI-compatible endpoint가 준비된 경우:

```bash
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/ops-llm-evaluation-executed

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-executed/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-executed/evaluation_summary.json
```

기대 신호:

```text
benchmark_status = executed
dry_run = false
selected_actual_model = llama3.1:8b
```

## 출력 증거

`runs/` directory는 local evidence이며 committed source package에는 포함하지 않습니다.

주요 evidence:

```text
runs/full-validation-local/
runs/full-validation-vm/
runs/ops-llm-evaluation-dry-run/
runs/ops-llm-evaluation-executed/
```

`benchmark_status = dry_run`은 평가 파이프라인 검증이고, `benchmark_status = executed`만 실제 provider 응답 기반 평가입니다.
