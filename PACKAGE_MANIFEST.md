# Delivery Package Manifest

This package is scoped to the Go-based AI service-control and management
automation functional prototype.

## Included Source Components

| Path | Purpose |
| --- | --- |
| `go/service-control-api/` | Go Echo API and Go CLI for LLM policy selection, agent registry validation, CPU/GPU placement, deployment-plan generation, and service-operations readiness |
| `go/aiops-guard/` | Standalone Go bounded-action validator for service-control actions |
| `config/agent_registry.json` | Agent registry and bounded-action metadata |
| `config/ops_llm_benchmark.json` | Manually defined prototype LLM policy baselines and selection weights |
| `config/inference_optimization.json` | CPU/GPU VM resource profiles and workload requirements |

## Required Submission Artifacts

| Path | Purpose |
| --- | --- |
| `docs/submission/requirements_definition.md` | Requirements definition source |
| `docs/submission/requirements_definition.docx` | Requirements definition submission/review conversion copy |
| `docs/submission/functional_api_guide.md` | Functional/API guide |
| `docs/submission/openapi_service_control.yaml` | Swagger/OpenAPI contract |
| `docs/submission/install_and_run_guide.md` | Installation and usage guide |
| `docs/submission/test_guide.md` | Test guide |

## Official Design Deliverables

| Path | Purpose |
| --- | --- |
| `docs/deliverables/01_llm_operation_management_design.md` | LLM operation-management structure design source |
| `docs/deliverables/02_agent_registration_management_prototype.md` | Agent registration-management prototype source |
| `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | AI application deployment/control optimization strategy source |
| `docs/deliverables/docx/01_LLM_Operation_Management_Design.docx` | DOCX conversion copy |
| `docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx` | DOCX conversion copy |
| `docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx` | DOCX conversion copy |

## Development Validation Artifacts

| Path | Purpose |
| --- | --- |
| `docs/submission/coding_agent_cross_validation.md` | LLM/coding-agent role and cross-validation process record |
| `docs/submission/prompt_usage_log.md` | Cleaned prompt categories and sharing policy |
| `docs/submission/development_validation_log.md` | Validation commands, expected outputs, log policy, and human review items |
| `docs/submission/evaluation_summary.md` | Functional prototype evaluation summary |
| `docs/core_submission_summary.md` | Overall package scope and deliverable mapping |

## Supporting Design Documents

| Path | Purpose |
| --- | --- |
| `docs/design/` | Supporting implementation-level design notes |
| `docs/team_setup.md` | Team-oriented setup notes |

## Conversion Tooling

| Path | Purpose |
| --- | --- |
| `scripts/generate_docx_deliverables.sh` | Converts Markdown deliverable sources into DOCX submission/review copies when conversion tooling is available |

## Excluded

| Excluded item | Reason |
| --- | --- |
| Non-core legacy code and tests | Removed or excluded so the submission/demo package remains Go-centered |
| External benchmark/orchestration integrations | Experimental paths that blur the assigned deliverable scope |
| Provider-specific monitoring adapters | Excluded in favor of provider-neutral Ops input/config |
| Local cluster experiment manifests and helper tooling | Environment-specific checks, not core deliverables |
| `runs/` | Local execution artifacts and validation outputs |
| virtualenv/cache/build artifacts | Local generated artifacts |
| `.env`, kubeconfig, API keys | Sensitive local credentials |

## Submission Scope

```text
AI LLM operation-management structure design and prototype
AI agent registration-management prototype
CPU/GPU VM-based AI application deployment/control inference optimization strategy
Go-based API/CLI implementation and functional validation
```

## Boundary Notes

The repository is a 1st-year functional prototype. It is not a production-ready
AIOps platform. The LLM policy values are manually defined prototype baselines
and are not final standardized benchmark results. Actual GPU VM provisioning is
outside the local default validation path.
