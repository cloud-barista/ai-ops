# AI-Based Service Control and Management Automation Framework

> Initial Go-based prototype for AI LLM operation management, agent registration management, and CPU/GPU VM-based AI application deployment-control planning.

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Overview

This repository contains a 1st-year research prototype for an AI-based service
control and management automation framework. It is intended for functional
validation and demonstration, not as a production-ready AIOps platform.

The current implementation scope corresponds to:

- AI LLM operation-management design and prototype
- AI agent registration-management prototype
- CPU/GPU VM-based AI application deployment/control optimization strategy
- Go-based bounded-action validation boundary

## Project Structure

| Path | Description |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM selection, Agent registry, CPU/GPU VM placement, and operation-management pipeline |
| [`go/aiops-guard/`](go/aiops-guard/) | Standalone bounded-action validator for service-control actions |
| [`config/`](config/) | LLM candidates, Agent registry, and CPU/GPU VM policy configuration |
| [`docs/`](docs/) | Design overview, submission documents, and execution/validation guides |

## Reference Documents

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

## Development Environment

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

## Prototype Boundary

The LLM policy values in this repository are manually defined prototype policy
baselines. They are not final standardized benchmark results. Final quantitative
reporting must regenerate those values through controlled per-model Ops
evaluation runs.

## License

ai-ops is licensed under the [Apache License 2.0](./LICENSE).
