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
- Deployment-plan generation
- Integrated service-operations readiness

## Expected Signals

```text
selected_model = gpt-5.5
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
```
