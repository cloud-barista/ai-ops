# Test Guide

## Go Tests

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
```

## Team Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

The validation checks:

- Go guard bounded-action validation
- Ops LLM selection
- Agent registry listing
- Agent bounded-action validation
- CPU/GPU VM placement recommendation
- Kubernetes deployment-plan generation
- Mock deployment dry-run and guard-readiness validation
- Integrated service-operations readiness

## Expected Signals

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

## Evaluation Boundary

These tests validate functional prototype behavior. They do not prove final
production performance, live GPU scheduling, or standardized LLM benchmark
quality.
