# 실행 코드 가이드

## 핵심 Go 코드

| 코드 | 목적 |
| --- | --- |
| `go/service-control-api/cmd/service-control-api/main.go` | HTTP API server entrypoint |
| `go/service-control-api/cmd/aiops-service-control/main.go` | CLI entrypoint |
| `go/service-control-api/internal/api/service.go` | LLM selection, agent registry, CPU/GPU placement, readiness pipeline |
| `go/service-control-api/internal/api/server.go` | HTTP route |
| `go/service-control-api/internal/api/models.go` | request/response model |
| `go/service-control-api/internal/benchmark/` | Ops LLM dry-run runner와 evaluator |
| `go/aiops-guard/` | standalone bounded-action guard |

## 검증 명령

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
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

```bash
go run ./cmd/aiops-service-control team-validation
```

```bash
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.json \
  --output-dir ../../runs/ops-llm-evaluation-dry-run \
  --dry-run
```

```bash
go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-dry-run/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

## 출력 증거

Go `team-validation` 명령은 JSON 출력을 다음 위치에 저장할 수 있습니다.

```text
runs/team-validation/<timestamp>/
```

`runs/` directory는 local evidence이며 committed source package에는 포함하지 않습니다.

Ops LLM dry-run/evaluator 명령은 다음 파일을 생성할 수 있습니다.

```text
runs/ops-llm-evaluation-dry-run/model_outputs.jsonl
runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

위 파일은 local evidence이며, 실제 provider API가 실행된 benchmark evidence가 아닌 경우 `benchmark_status = dry_run`으로 해석합니다.
