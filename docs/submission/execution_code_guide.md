# Execution Code Guide

## Core Go Code

| Code | Purpose |
| --- | --- |
| `go/service-control-api/cmd/service-control-api/main.go` | HTTP API server entrypoint |
| `go/service-control-api/cmd/aiops-service-control/main.go` | CLI entrypoint |
| `go/service-control-api/internal/api/service.go` | LLM selection, agent registry, CPU/GPU placement, readiness pipeline |
| `go/service-control-api/internal/api/server.go` | HTTP routes |
| `go/service-control-api/internal/api/models.go` | request/response models |
| `go/aiops-guard/` | bounded action guard |

## Validation Commands

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
  --namespace online-boutique \
  --deployment paymentservice \
  --mode mock \
  --guard-backend go
```

```bash
go run ./cmd/aiops-service-control team-validation
```

## Output Evidence

The Go team-validation command saves JSON outputs under:

```text
runs/team-validation/<timestamp>/
```
