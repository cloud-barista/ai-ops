# 🏛️ Kyung Hee AIOps 🦁

> AI-Based Service Control and Management Automation Framework
> 1st-year Go-based functional prototype for AI LLM operation management,
> AI agent registration management, and AI application deployment/control
> strategy validation.

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 📌 Overview

This repository is organized as an official graduate research and external
collaboration deliverable for an AI-based service control and management
automation framework. The current scope is a 1st-year Go-based functional
prototype.

The prototype validates the following research functions through Go API/CLI
execution and reproducible JSON configuration:

- Ops LLM selection policy prototype using manually defined policy baselines.
- AI LLM operation-management flow validation.
- AI agent registry and bounded-action validation.
- CPU/GPU VM placement recommendation for AI application workloads.
- Kubernetes deployment-plan generation and mock service-control readiness.

This repository is not intended for production operation. It does not claim
final standardized LLM benchmark results, actual GPU VM provisioning results,
or live cluster mutation in the default validation path.

## 🧪 Prototype Boundary

The LLM policy values in `config/ops_llm_benchmark.json` are manually defined
prototype policy baselines. They are used to validate the Go API/CLI-based LLM
selection flow, not to report final model performance. Final quantitative
reporting would require controlled per-model Ops evaluation runs, fixed prompts,
fixed datasets, repeatable metrics, and documented scoring rules.

The default execution path uses `mock` validation. A live Kubernetes cluster,
actual GPU VM provisioning, and CB-Tumblebug/AWS GPU VM integration are outside
the default local validation path.

## 🧩 Repository Structure

| Path | Purpose |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | Go Echo API and CLI for LLM policy selection, agent registry validation, CPU/GPU placement, deployment-plan generation, and integrated service-operations readiness |
| [`go/aiops-guard/`](go/aiops-guard/) | Standalone Go bounded-action validator for service-control actions |
| [`config/`](config/) | JSON configuration for LLM policy candidates, agent registry, and CPU/GPU VM placement policy |
| [`docs/deliverables/`](docs/deliverables/) | Official design deliverable Markdown sources and DOCX conversion copies |
| [`docs/design/`](docs/design/) | Supporting design notes for implementation-level details |
| [`docs/submission/`](docs/submission/) | Required submission artifacts, API guide, OpenAPI contract, install guide, test guide, and validation records |

## 📦 Submission Artifacts

| Required Artifact | Format | Repository Path | Status |
| --- | --- | --- | --- |
| 요구사항 정의서 Source | `.md` | [`docs/submission/requirements_definition.md`](docs/submission/requirements_definition.md) | Available |
| 요구사항 정의서 Submission Copy | `.docx` | [`docs/submission/requirements_definition.docx`](docs/submission/requirements_definition.docx) | Available |
| Functional/API Guide | `.md` | [`docs/submission/functional_api_guide.md`](docs/submission/functional_api_guide.md) | Available |
| Swagger/OpenAPI | `.yaml` | [`docs/submission/openapi_service_control.yaml`](docs/submission/openapi_service_control.yaml) | Available |
| Installation and Usage Guide | `.md` | [`docs/submission/install_and_run_guide.md`](docs/submission/install_and_run_guide.md) | Available |
| Test Guide | `.md` | [`docs/submission/test_guide.md`](docs/submission/test_guide.md) | Available |

## 🧾 Design Deliverables

| Design Deliverable | Source Markdown | DOCX |
| --- | --- | --- |
| LLM 운영 관리 구조 설계서 | [`docs/deliverables/01_llm_operation_management_design.md`](docs/deliverables/01_llm_operation_management_design.md) | [`docs/deliverables/docx/01_LLM_Operation_Management_Design.docx`](docs/deliverables/docx/01_LLM_Operation_Management_Design.docx) |
| 에이전트 등록 관리 프로토타입 | [`docs/deliverables/02_agent_registration_management_prototype.md`](docs/deliverables/02_agent_registration_management_prototype.md) | [`docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx`](docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [`docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md`](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) | [`docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx`](docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx) |

The Markdown files are the official source documents. The DOCX files are
submission/review conversion copies generated from those sources.

## 🔎 Development Validation Artifacts

| Artifact | Format | Repository Path | Purpose |
| --- | --- | --- | --- |
| LLM/Coding Agent Cross Validation | `.md` | [`docs/submission/coding_agent_cross_validation.md`](docs/submission/coding_agent_cross_validation.md) | Records the use of at least two neutral LLM/coding-agent roles and the cross-validation process |
| Prompt Usage Log | `.md` | [`docs/submission/prompt_usage_log.md`](docs/submission/prompt_usage_log.md) | Records representative framework prompts and prompt-sharing policy |
| Development Validation Log | `.md` | [`docs/submission/development_validation_log.md`](docs/submission/development_validation_log.md) | Records validation commands, expected outputs, logging policy, and human review items |

## 🚀 Running the Prototype

Run the integrated validation path:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/my-first-validation
```

Expected prototype-level signals:

```text
valid = true
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
guard_backend = go
guard_validation.valid = true
```

Run the API server:

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

Then call the integrated service-operations API from another terminal:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## 📝 DOCX Conversion

The repository includes a Bash conversion script:

```bash
bash scripts/generate_docx_deliverables.sh
```

For Windows environments where Bash is not convenient, run the equivalent
Pandoc commands from PowerShell:

```powershell
pandoc docs/submission/requirements_definition.md -o docs/submission/requirements_definition.docx
pandoc docs/deliverables/01_llm_operation_management_design.md -o docs/deliverables/docx/01_LLM_Operation_Management_Design.docx
pandoc docs/deliverables/02_agent_registration_management_prototype.md -o docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx
pandoc docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md -o docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx
```

## 📚 Reference Documents

| Document | Purpose |
| --- | --- |
| [Core Submission Summary](docs/core_submission_summary.md) | Overall package scope and deliverable mapping |
| [Functional/API Guide](docs/submission/functional_api_guide.md) | HTTP API execution and response guide |
| [OpenAPI Contract](docs/submission/openapi_service_control.yaml) | Swagger/OpenAPI deliverable |
| [Install and Run Guide](docs/submission/install_and_run_guide.md) | Go CLI/API execution guide |
| [Test Guide](docs/submission/test_guide.md) | Go tests and team-validation guide |
| [Evaluation Summary](docs/submission/evaluation_summary.md) | Functional prototype evaluation boundary |

## 🛠️ Development Environment

- Development language: Go
- Go version baseline: Go 1.25
- Backend framework: Echo for the Go HTTP API
- Source code management: GitHub
- License: Apache 2.0

Both Go modules use Go 1.25 because the service-control API dependency set is
normalized by `go mod tidy` to `go 1.25.0`.

## 📄 License

ai-ops is licensed under the [Apache License 2.0](./LICENSE).
