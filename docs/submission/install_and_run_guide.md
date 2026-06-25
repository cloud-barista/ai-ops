# Install And Run Guide

## Scope

This guide describes the Go-based submission/demo path for the AI
service-control prototype.

## Requirements

- Go 1.25 or newer
- Optional: Kubernetes access only for server-side dry-run or real cluster checks

## Team Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

## Go CLI

```bash
cd go/service-control-api

go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first

go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json

go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference

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

## Go HTTP API

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
```

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","namespace":"online-boutique","deployment":"paymentservice","mode":"mock","guard_backend":"go"}'
```

## Expected Result

```text
selected_llm = gpt-5.5
runtime_model = gpt-5.5
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
```
