# Core Submission Summary

## Scope

This repository is packaged as a Go-based 1st-year prototype for an AI-based
service control and management automation framework. The package demonstrates
functional behavior for research evaluation and demo use. It is not a
production-ready AIOps platform.

## Research Scope Mapping

| Research item | Implemented artifact |
| --- | --- |
| Ops analysis and optimal LLM selection | Go LLM selection API/CLI using `config/ops_llm_benchmark.json` |
| AI LLM operation-management structure | Go service-operations readiness pipeline |
| AI agent registration-management prototype | Agent registry config plus Go list/show/validate actions |
| CPU/GPU VM-based AI application deployment/control strategy | Go CPU/GPU placement and Kubernetes deployment-plan generation |
| Safety and validation boundary | Standalone Go `aiops-guard` contract and service-control guard-readiness output |

## Main Deliverables

| Deliverable | Location |
| --- | --- |
| LLM operation-management structure design | `docs/design/ops_llm_selection_guide.md`, `docs/design/go_and_llm_cross_validation.md` |
| Agent registration-management prototype | `config/agent_registry.json`, `go/service-control-api` |
| AI application deployment/control inference optimization strategy | `config/inference_optimization.json`, `docs/design/inference_optimization_guide.md`, `docs/design/ai_application_deployment_strategy.md` |
| Runnable API/CLI prototype | `go/service-control-api` |
| Bounded action guard | `go/aiops-guard` |
| OpenAPI contract | `docs/submission/openapi_service_control.yaml` |
| Functional evaluation summary | `docs/submission/evaluation_summary.md` |

## Package Boundary

The submitted package keeps the Go API/CLI, Go guard, core JSON configs, and
submission/design documents. Non-core legacy modules, external experiment
runners, local environment scripts, and generated artifacts are outside the
source package boundary.

The LLM selection values are manually defined prototype policy baselines. They
are not final standardized benchmark results and must be regenerated through
controlled per-model Ops evaluation runs before quantitative reporting.

## Validation

Common validation is performed with the Go CLI:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

Expected signals:

```text
team-validation valid = true
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
run-service-operations valid = true
guard_backend = go
guard_validation.valid = true
```
