# 🏛️ Kyunghee AIOps 🦁

> A Go-based initial prototype for an AI-powered service control and management automation framework.

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Branch](https://img.shields.io/badge/Branch-geon-lightgrey)](https://github.com/cloud-barista/ai-ops/tree/geon)

## 📌 Overview

This project is an initial Go-based prototype for AI LLM operation management
and AI application automation agents.

The main implementation scope includes:

- 🧠 Ops analysis and optimal LLM selection
- 🏗️ AI LLM operation-management structure design
- 🤖 AI agent registration-management prototype
- ⚙️ CPU/GPU VM-based AI application deployment and control optimization strategy
- 🛡️ Go-based guard structure for validating service-control actions

## 🧩 Project Structure

| Path | Description |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM selection, Agent registry, CPU/GPU VM placement, and operation-management pipeline |
| [`go/aiops-guard/`](go/aiops-guard/) | Safety validation for service-control actions |
| [`config/`](config/) | LLM candidates, Agent registry, and CPU/GPU VM policy configuration |
| [`docs/`](docs/) | Design overview, submission documents, and execution/validation guides |

## 📚 Reference Documents

| Document | Description |
| --- | --- |
| [Core Submission Summary](docs/core_submission_summary.md) | Overall implementation scope and deliverable mapping |
| [Research Task Integration Design](docs/design/research_task_integration_design.md) | Mapping between research items and Go implementation structure |
| [Ops LLM Selection Guide](docs/design/ops_llm_selection_guide.md) | Ops analysis and optimal LLM selection structure |
| [Agent Registry Guide](docs/design/agent_registry_guide.md) | Agent registry and bounded-action management |
| [Inference Optimization Guide](docs/design/inference_optimization_guide.md) | CPU/GPU VM placement recommendation policy |
| [Install and Run Guide](docs/submission/install_and_run_guide.md) | Go API/CLI execution guide |
| [Test Guide](docs/submission/test_guide.md) | Go test and team-validation guide |

## 🛠️ Development Environment

- Development language: Go
- Source code management: GitHub
- Backend framework: Echo (Go)
- License: Apache 2.0

The core execution logic is implemented in Go. JSON files are used for
configuration, and Markdown files are used as supporting design and submission
documents.

## License

Kyunghee AIOpslicensed under the [Apache License 2.0](./LICENSE).
