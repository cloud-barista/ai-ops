# Core Submission Summary

## 1. Scope

This repository is packaged as a 1st-year Go-based functional prototype for an
AI-based service control and management automation framework. The package is
organized for graduate research review and external collaboration review.

The prototype demonstrates functional behavior for:

- Ops LLM selection policy flow validation.
- AI LLM operation-management structure validation.
- AI agent registration and bounded-action validation.
- CPU/GPU VM placement recommendation.
- Kubernetes deployment-plan generation.
- Mock service-operations readiness reporting.

It is not intended for production operation and does not claim final
standardized LLM benchmark results.

## 2. Research Scope Mapping

| Research item | Implemented artifact |
| --- | --- |
| Ops analysis and optimal LLM selection | Go API/CLI policy selection flow using `config/ops_llm_benchmark.json` |
| AI LLM operation-management structure | Integrated Go service-operations readiness pipeline |
| AI agent registration-management prototype | Agent registry config plus Go list/show/validate actions |
| CPU/GPU VM-based AI application deployment/control strategy | Go CPU/GPU placement recommendation and Kubernetes deployment-plan generation |
| Safety and validation boundary | Standalone Go `aiops-guard` contract and service-control guard-readiness output |

## 3. Required Submission Artifacts

| Artifact | Path |
| --- | --- |
| Requirements definition source | `docs/submission/requirements_definition.md` |
| Requirements definition DOCX copy | `docs/submission/requirements_definition.docx` |
| Functional/API guide | `docs/submission/functional_api_guide.md` |
| Swagger/OpenAPI contract | `docs/submission/openapi_service_control.yaml` |
| Installation and usage guide | `docs/submission/install_and_run_guide.md` |
| Test guide | `docs/submission/test_guide.md` |

The Markdown and YAML files are source deliverables. The DOCX file is a
submission/review conversion copy generated from the Markdown source.

## 4. Official Design Deliverables

| Design deliverable | Source Markdown | DOCX copy |
| --- | --- | --- |
| LLM operation-management structure design | `docs/deliverables/01_llm_operation_management_design.md` | `docs/deliverables/docx/01_LLM_Operation_Management_Design.docx` |
| Agent registration-management prototype | `docs/deliverables/02_agent_registration_management_prototype.md` | `docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx` |
| AI application deployment/control inference optimization strategy | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | `docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx` |

The `docs/design` directory remains as supporting design notes. The official
one-to-one design deliverable sources are in `docs/deliverables`.

## 5. Development Validation Artifacts

| Artifact | Path |
| --- | --- |
| LLM/coding agent cross-validation record | `docs/submission/coding_agent_cross_validation.md` |
| Prompt usage and sharing log | `docs/submission/prompt_usage_log.md` |
| Development/test validation log | `docs/submission/development_validation_log.md` |
| Functional evaluation summary | `docs/submission/evaluation_summary.md` |

## 6. Package Boundary

The submitted package keeps the Go API/CLI, Go guard, core JSON configs, and
submission/design documents. Non-core legacy modules, external experiment
runners, local environment scripts, and generated local execution outputs are
outside the source package boundary.

The LLM selection values are manually defined prototype policy baselines. They
are not final standardized benchmark results and must be regenerated through
controlled per-model Ops evaluation runs before quantitative reporting.

Actual GPU VM provisioning remains an AI-Infra or CB-Tumblebug integration
boundary. The local default validation path uses mock execution.

## 7. Validation

Common validation is performed with Go tests and the Go CLI:

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
go run ./cmd/aiops-service-control team-validation
```

Expected prototype-level signals:

```text
team-validation valid = true
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
run-service-operations valid = true
guard_backend = go
guard_validation.valid = true
```
