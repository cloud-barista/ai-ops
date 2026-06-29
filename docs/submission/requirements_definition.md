# 요구사항 정의서

English title: Requirements Definition

## 1. Document Overview

This document defines the functional, non-functional, submission, development,
and validation requirements for the 1st-year Go-based service-control functional
prototype. The prototype supports AI LLM operation management, AI agent
registration management, CPU/GPU VM placement recommendation, and AI
application deployment/control readiness validation.

The document is maintained as the Markdown source for the requirements
definition submission artifact. A DOCX submission copy can be generated from
this source by using `scripts/generate_docx_deliverables.sh` or an equivalent
Pandoc command.

## 2. Project Scope

The project scope is limited to a 1st-year functional prototype. The repository
provides Go API/CLI components, JSON configuration files, design deliverables,
and validation documents required to demonstrate the assigned service-control
research scope.

Included scope:

- Ops LLM selection policy prototype.
- AI LLM operation-management structure validation.
- Agent registry listing and bounded-action validation.
- CPU/GPU VM placement recommendation for AI application workloads.
- Kubernetes deployment-plan generation.
- Mock deployment dry-run and service-operations readiness reporting.

Out-of-scope for the default validation path:

- Production-ready AIOps operation.
- Final standardized LLM benchmark reporting.
- Real GPU VM provisioning.
- Live Kubernetes mutation by default.
- Replacement of CB-Tumblebug or AI-Infra provisioning components.

## 3. Functional Requirements

| ID | Functional Requirement | Implementation Status |
| --- | --- | --- |
| FR-01 | Provide an Ops LLM selection policy prototype based on JSON-defined candidate roles and policy weights. | Implemented |
| FR-02 | Provide Go API/CLI commands for policy-based LLM candidate ranking. | Implemented |
| FR-03 | Provide agent registry listing, single-agent lookup, and bounded-action validation. | Implemented |
| FR-04 | Recommend CPU/GPU VM placement based on accelerator requirement, SLO, throughput, cost, and capacity. | Implemented |
| FR-05 | Generate Kubernetes deployment/control plans for selected AI application resources. | Implemented |
| FR-06 | Generate an integrated service-operations readiness report that combines LLM selection, placement recommendation, deployment-plan generation, agent review, mock dry-run, and guard-readiness validation. | Implemented |
| FR-07 | Expose the main functions through an Echo-based Go HTTP API. | Implemented |
| FR-08 | Provide an OpenAPI contract for the service-control API. | Implemented |

## 4. Non-Functional Requirements

| ID | Non-Functional Requirement | Rationale |
| --- | --- | --- |
| NFR-01 | Keep the implementation Go-centered for the submission/demo path. | Aligns with the development-language requirement and avoids mixed-language prototype ambiguity. |
| NFR-02 | Keep LLM, agent, and placement policies reproducible through JSON configuration. | Supports reviewable and repeatable functional validation. |
| NFR-03 | Use mock validation as the default execution path. | Allows local validation without live Kubernetes or GPU VM infrastructure. |
| NFR-04 | Clearly separate prototype validation from production operation. | Prevents the prototype from being represented as an operational production system. |
| NFR-05 | Clearly separate prototype policy baselines from final benchmark results. | Prevents manually defined LLM policy values from being interpreted as standardized model evaluation results. |
| NFR-06 | Avoid unnecessary external dependencies and non-core experiment runners. | Keeps the repository focused on the assigned research deliverables. |

## 5. Submission Artifact Requirements

| Required Artifact | Format | Repository Path | Requirement |
| --- | --- | --- | --- |
| 요구사항 정의서 Source | Markdown | `docs/submission/requirements_definition.md` | Must define scope, requirements, validation method, and boundaries. |
| 요구사항 정의서 Submission Copy | DOCX | `docs/submission/requirements_definition.docx` | Must be generated from the Markdown source when conversion tooling is available. |
| Functional/API Guide | Markdown | `docs/submission/functional_api_guide.md` | Must describe API server execution, endpoints, request examples, and response fields. |
| Swagger/OpenAPI | YAML | `docs/submission/openapi_service_control.yaml` | Must describe the HTTP API contract. |
| Installation and Usage Guide | Markdown | `docs/submission/install_and_run_guide.md` | Must describe Go setup, CLI execution, API execution, mock mode, and expected outputs. |
| Test Guide | Markdown | `docs/submission/test_guide.md` | Must describe Go tests, team-validation, expected signals, and log preservation. |

## 6. Development Guide Requirements

The development guide and supporting records must document:

- Go language development for API/CLI prototype implementation.
- Cross-validation with at least two LLM or coding-agent roles.
- Prompt-sharing documentation using cleaned representative prompt templates.
- Log and error-message-based validation records.
- Human testing and review of generated documents, README links, DOCX existence,
  and prototype boundary statements.

Specific LLM vendor names or coding-agent product names must not be invented if
their actual use is unknown. Neutral role labels such as `Agent A`, `Agent B`,
`primary coding agent`, and `secondary review agent` are acceptable for process
documentation.

## 7. Validation Method

The default validation method is local Go execution:

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
```

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

Expected prototype-level validation signals:

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

The validation confirms functional prototype behavior. It does not prove
production performance, standardized LLM benchmark quality, or actual GPU VM
provisioning.

## 8. Prototype Boundary

The current LLM policy values are manually defined prototype policy baselines in
`config/ops_llm_benchmark.json`. They are not final standardized benchmark
results. Final quantitative reporting must regenerate these values through
controlled per-model Ops evaluation runs with fixed prompts, fixed datasets,
repeatable metrics, and documented scoring rules.

The CPU/GPU VM placement logic is a recommendation and deployment-plan
generation prototype. It does not replace production cloud schedulers,
Kubernetes schedulers, GPU device plugins, or CB-Tumblebug provisioning.

The default `mock` mode does not mutate a live Kubernetes cluster.

## 9. Related Artifacts

| Artifact | Path |
| --- | --- |
| Core submission summary | `docs/core_submission_summary.md` |
| Functional/API guide | `docs/submission/functional_api_guide.md` |
| OpenAPI contract | `docs/submission/openapi_service_control.yaml` |
| Install and run guide | `docs/submission/install_and_run_guide.md` |
| Test guide | `docs/submission/test_guide.md` |
| LLM 운영 관리 구조 설계서 | `docs/deliverables/01_llm_operation_management_design.md` |
| 에이전트 등록 관리 프로토타입 | `docs/deliverables/02_agent_registration_management_prototype.md` |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` |
| LLM policy config | `config/ops_llm_benchmark.json` |
| Agent registry config | `config/agent_registry.json` |
| Inference optimization config | `config/inference_optimization.json` |
