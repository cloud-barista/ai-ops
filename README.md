# 🏛️ Kyunghee AIOps 🦁

> A Go-based initial prototype for an AI-powered service control and management automation framework.

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 📌 Overview

This project is a 1st-year Go-based prototype for AI LLM operation management
and AI application automation agents. It is intended for functional validation
and demonstration, not as a production-ready AIOps platform.

The main implementation scope includes:

- 🧠 Ops analysis and optimal LLM selection
- 🏗️ AI LLM operation-management structure design
- 🤖 AI agent registration-management prototype
- ⚙️ CPU/GPU VM-based AI application deployment and control optimization strategy
- 🛡️ Go-based bounded-action validation boundary

## 🧩 Project Structure

| Path | Description |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM selection, Agent registry, CPU/GPU VM placement, deployment-plan generation, and operation-management pipeline |
| [`go/aiops-guard/`](go/aiops-guard/) | Standalone bounded-action validator for service-control actions |
| [`config/`](config/) | LLM candidates, Agent registry, and CPU/GPU VM policy configuration |
| [`docs/`](docs/) | Design overview, submission documents, and execution/validation guides |

## 📦 Deliverables

The formal 1st-year service-control deliverables are the design documents,
prototype implementation, and strategy documents below. Runtime JSON files are
validation evidence, not replacements for the design deliverables.

| Deliverable | Primary Artifact | Supporting Implementation |
| --- | --- | --- |
| LLM 운영 관리 구조 설계서 | [Research Task Integration Design](docs/design/research_task_integration_design.md), [Ops LLM Selection Guide](docs/design/ops_llm_selection_guide.md), [Go and LLM Cross Validation](docs/design/go_and_llm_cross_validation.md) | [Ops LLM Benchmark Config](config/ops_llm_benchmark.json), [`go/service-control-api/`](go/service-control-api/) |
| 에이전트 등록 관리 프로토타입 | [Agent Registry Guide](docs/design/agent_registry_guide.md), [Agent Action Reward Policy](docs/design/agent_action_reward_policy.md) | [Agent Registry Config](config/agent_registry.json), [`go/service-control-api/`](go/service-control-api/) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [AI Application Deployment Strategy](docs/design/ai_application_deployment_strategy.md), [Inference Optimization Guide](docs/design/inference_optimization_guide.md) | [Inference Policy Config](config/inference_optimization.json), [`go/service-control-api/`](go/service-control-api/) |

## 🧪 Validation Evidence

After running `team-validation`, the generated JSON files provide execution
evidence for the deliverables:

| Validation File | Meaning |
| --- | --- |
| `runs/<output-dir>/01_select_ops_llm.json` | Ops analysis and optimal LLM selection result |
| `runs/<output-dir>/02_list_agents.json` | Registered AI agent list |
| `runs/<output-dir>/03_validate_agent_action.json` | Agent bounded-action validation result |
| `runs/<output-dir>/04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation result |
| `runs/<output-dir>/05_plan_inference_deployment.json` | AI application deployment/control plan result |
| `runs/<output-dir>/06_run_service_operations.json` | Integrated service-control execution result |

To generate the validation evidence, run:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/my-first-validation
```

## 📚 Reference Documents

| Document | Description |
| --- | --- |
| [Core Submission Summary](docs/core_submission_summary.md) | Overall implementation scope and deliverable mapping |
| [Research Task Integration Design](docs/design/research_task_integration_design.md) | Mapping between research items and Go implementation structure |
| [Ops LLM Selection Guide](docs/design/ops_llm_selection_guide.md) | Ops analysis and optimal LLM selection structure |
| [Agent Registry Guide](docs/design/agent_registry_guide.md) | Agent registry and bounded-action management |
| [Inference Optimization Guide](docs/design/inference_optimization_guide.md) | CPU/GPU VM placement recommendation policy |
| [Evaluation Summary](docs/submission/evaluation_summary.md) | Functional prototype evaluation summary |
| [Install and Run Guide](docs/submission/install_and_run_guide.md) | Go API/CLI execution guide |
| [Test Guide](docs/submission/test_guide.md) | Go test and team-validation guide |

## 🛠️ Development Environment

- Development language: Go
- Go version baseline: Go 1.25
- Source code management: GitHub
- Backend framework: Echo (Go)
- License: Apache 2.0

The core execution logic is implemented in Go. JSON files are used for
configuration, and Markdown files are used as supporting design and submission
documents.

Both Go modules use Go 1.25 because the service-control API dependency set is
normalized by `go mod tidy` to `go 1.25.0`.

## 🧪 Prototype Boundary

The LLM policy values in this repository are manually defined prototype policy
baselines. They are not final standardized benchmark results. Final quantitative
reporting must regenerate those values through controlled per-model Ops
evaluation runs.

The default validation path uses mock execution and does not require a live
Kubernetes cluster or an AWS GPU VM. Actual GPU VM provisioning remains an
AI-Infra or CB-Tumblebug boundary.

## 📄 License

Kyunghee AIOps is licensed under the [Apache License 2.0](./LICENSE).
