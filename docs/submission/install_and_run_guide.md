# Install And Run Guide

## Scope

This guide describes the Go-based submission/demo path for the AI service
control and management automation prototype.

## Requirements

- Go 1.25 or newer
- Optional: Kubernetes access only for server-side dry-run or real cluster checks

Both Go modules use Go 1.25 because the service-control API dependency set is
normalized by `go mod tidy` to `go 1.25.0`.

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
  --recovery-namespace online-boutique \
  --recovery-deployment paymentservice \
  --mode mock \
  --guard-backend go
```

`recovery_namespace` and `recovery_deployment` describe the service operation
or recovery target. The AI application deployment namespace is generated from
`config/inference_optimization.json` and appears under
`deployment_plan.kubernetes.namespace`.

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
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"online-boutique","recovery_deployment":"paymentservice","mode":"mock","guard_backend":"go"}'
```

## Expected Result

```text
selected_llm = primary-ops-llm
runtime_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```
