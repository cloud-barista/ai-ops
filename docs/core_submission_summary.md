# Core Submission Summary

## Scope

This project is packaged as a Go-based AI service-control and management
automation prototype.

The submission scope maps to:

| Task item | Implemented artifact |
| --- | --- |
| Ops analysis and optimal LLM selection | Go LLM selection API/CLI using `config/ops_llm_benchmark.json` |
| AI LLM operation-management structure | Go service-operations readiness pipeline |
| AI agent registration-management prototype | Agent registry config plus Go list/show/validate actions |
| CPU/GPU VM-based AI application deployment/control strategy | Go CPU/GPU placement and deployment-plan generation |

## Main Deliverables

| Deliverable | Location |
| --- | --- |
| LLM operation-management structure design | `docs/design/ops_llm_selection_guide.md`, `docs/design/go_and_llm_cross_validation.md` |
| Agent registration-management prototype | `config/agent_registry.json`, `go/service-control-api` |
| AI application deployment/control inference optimization strategy | `config/inference_optimization.json`, `docs/design/inference_optimization_guide.md` |
| Runnable API/CLI prototype | `go/service-control-api` |
| Bounded action guard | `go/aiops-guard` |
| OpenAPI contract | `docs/submission/openapi_service_control.yaml` |

## Package Boundary

The submitted package keeps only the Go API/CLI, Go guard, core JSON configs,
and submission/design documents. Non-core legacy modules, local environment
experiments, and generated artifacts are outside the source package boundary.

## Validation

Common validation is performed with the Go CLI:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

Expected result:

```text
go test ./... passes
selected_model = gpt-5.5
selected_resource = gpu-vm-l4
run-service-operations valid = true
guard_backend = go
```
